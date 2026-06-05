# 云服务器统一部署文档

本文档用于把量化选股系统部署到云服务器，包含：

- Python 量化服务：拉取行情、执行策略、写入 MySQL
- Spring Boot 服务：统一对外提供 `/api/quant` 接口
- Vue 静态前端：展示选股结果
- Nginx：代理前端和后端接口
- systemd：守护 Python 和 Spring 服务

## 1. 目录结构

建议服务器目录保持如下结构：

```text
/mnt/d/piao
├── app.py
├── database.py
├── data_service.py
├── quant_service.py
├── strategy.py
├── requirements.txt
├── .env
├── quantClient
│   ├── index.html
│   ├── main.js
│   └── styles.css
└── quantServer
    └── quantServer
        ├── pom.xml
        └── src
```

如果部署到 Linux 云服务器，推荐放到：

```text
/opt/quant
```

后文以 `/opt/quant` 为例。若你继续用 `/mnt/d/piao`，把命令里的路径替换即可。

## 2. 服务器基础环境

需要：

```text
Python 3.10+
Java 17
Maven
MySQL
Nginx
```

Ubuntu/Debian 示例：

```bash
sudo apt update
sudo apt install -y python3 python3-pip openjdk-17-jdk maven mysql-server nginx
```

检查版本：

```bash
python3 --version
java -version
mvn -v
mysql --version
nginx -v
```

## 3. 上传代码

假设代码上传到：

```bash
/opt/quant
```

进入目录：

```bash
cd /opt/quant
```

## 4. MySQL 初始化

登录 MySQL：

```bash
mysql -uroot -p
```

创建数据库和用户：

```sql
CREATE DATABASE IF NOT EXISTS quant DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

CREATE USER IF NOT EXISTS 'quant_user'@'localhost' IDENTIFIED BY '你的强密码';
GRANT ALL PRIVILEGES ON quant.* TO 'quant_user'@'localhost';
FLUSH PRIVILEGES;
```

退出：

```sql
exit;
```

## 5. 配置环境变量

在 `/opt/quant` 下创建 `.env`：

```bash
cd /opt/quant
cp .env.example .env
```

编辑：

```bash
vim .env
```

示例：

```text
TUSHARE_TOKEN=你的tushare_token
TUSHARE_HTTP_URL=https://fastapia.stockai888.top

MYSQL_HOST=127.0.0.1
MYSQL_PORT=3306
MYSQL_USER=quant_user
MYSQL_PASSWORD=你的强密码
MYSQL_DATABASE=quant

OLLAMA_MODEL=deepseek-r1:7b
```

注意：`.env` 不要提交到公开仓库。

## 6. 部署 Python 量化服务

安装依赖：

```bash
cd /opt/quant
python3 -m pip install -r requirements.txt
```

手动启动测试：

```bash
cd /opt/quant
uvicorn app:app --host 127.0.0.1 --port 8000
```

新开一个终端验证：

```bash
curl http://127.0.0.1:8000/health
curl http://127.0.0.1:8000/health/db
```

预期：

```json
{"status":"ok"}
```

停止手动进程后，创建 systemd 服务：

```bash
sudo vim /etc/systemd/system/quant-python.service
```

写入：

```ini
[Unit]
Description=Quant Python API
After=network.target mysql.service

[Service]
Type=simple
WorkingDirectory=/opt/quant
ExecStart=/usr/bin/python3 -m uvicorn app:app --host 127.0.0.1 --port 8000
Restart=always
RestartSec=5
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
```

启动：

```bash
sudo systemctl daemon-reload
sudo systemctl enable quant-python
sudo systemctl start quant-python
sudo systemctl status quant-python
```

查看日志：

```bash
journalctl -u quant-python -f
```

## 7. 部署 Spring Boot 服务

打包：

```bash
cd /opt/quant/quantServer/quantServer
mvn clean package
```

手动启动测试：

```bash
java -jar target/quantServer-0.0.1-SNAPSHOT.jar
```

新开终端验证：

```bash
curl http://127.0.0.1:8080/api/quant/health
curl http://127.0.0.1:8080/api/quant/health/db
```

停止手动进程后，创建 systemd 服务：

```bash
sudo vim /etc/systemd/system/quant-spring.service
```

写入：

```ini
[Unit]
Description=Quant Spring API Gateway
After=network.target quant-python.service

[Service]
Type=simple
WorkingDirectory=/opt/quant/quantServer/quantServer
ExecStart=/usr/bin/java -jar /opt/quant/quantServer/quantServer/target/quantServer-0.0.1-SNAPSHOT.jar
Restart=always
RestartSec=5
Environment=SERVER_PORT=8080
Environment=QUANT_PYTHON_BASE_URL=http://127.0.0.1:8000

[Install]
WantedBy=multi-user.target
```

启动：

```bash
sudo systemctl daemon-reload
sudo systemctl enable quant-spring
sudo systemctl start quant-spring
sudo systemctl status quant-spring
```

查看日志：

```bash
journalctl -u quant-spring -f
```

## 8. 部署 Vue 前端和 Nginx

前端目录：

```text
/opt/quant/quantClient
```

创建 Nginx 配置：

```bash
sudo vim /etc/nginx/sites-available/quant.conf
```

写入：

```nginx
server {
    listen 80;
    server_name 你的域名或服务器IP;

    root /opt/quant/quantClient;
    index index.html;

    location / {
        try_files $uri $uri/ /index.html;
    }

    location /api/quant/ {
        proxy_pass http://127.0.0.1:8080/api/quant/;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

启用配置：

```bash
sudo ln -s /etc/nginx/sites-available/quant.conf /etc/nginx/sites-enabled/quant.conf
sudo nginx -t
sudo systemctl reload nginx
```

如果默认站点冲突，可以移除默认配置：

```bash
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t
sudo systemctl reload nginx
```

访问：

```text
http://你的域名或服务器IP
```

前端左侧接口地址正式部署时使用：

```text
/api/quant
```

## 9. 完整验证流程

按顺序验证：

```bash
curl http://127.0.0.1:8000/health
curl http://127.0.0.1:8000/health/db
curl http://127.0.0.1:8080/api/quant/health
curl http://127.0.0.1:8080/api/quant/health/db
curl http://你的域名或服务器IP/api/quant/health
```

运行一次扫描：

```bash
curl -X POST http://127.0.0.1:8080/api/quant/scan/run \
  -H 'Content-Type: application/json' \
  -d '{"includeAi":false,"limit":20}'
```

查看最新报告：

```bash
curl http://127.0.0.1:8080/api/quant/reports/latest
```

## 10. 常见问题

### 10.1 前端显示接口失败

确认前端左侧接口地址：

```text
/api/quant
```

如果是直接打开本地 HTML，则使用：

```text
http://127.0.0.1:8080/api/quant
```

### 10.2 数据库健康检查失败

查看 Python 日志：

```bash
journalctl -u quant-python -f
```

常见原因：

```text
MYSQL_PASSWORD 错误
MYSQL_USER 权限不足
MySQL 没启动
MYSQL_HOST 配错
```

检查 MySQL：

```bash
sudo systemctl status mysql
mysql -uquant_user -p quant
```

### 10.3 扫描接口失败

先检查 Tushare token：

```bash
cd /opt/quant
grep TUSHARE_TOKEN .env
```

再看 Python 日志：

```bash
journalctl -u quant-python -f
```

### 10.4 Spring 调不到 Python

检查：

```bash
curl http://127.0.0.1:8000/health
curl http://127.0.0.1:8080/api/quant/health
```

查看 Spring 配置：

```bash
systemctl cat quant-spring
```

确认：

```text
QUANT_PYTHON_BASE_URL=http://127.0.0.1:8000
```

## 11. 更新部署

上传新代码后：

```bash
cd /opt/quant
python3 -m py_compile app.py database.py data_service.py quant_service.py strategy.py

cd /opt/quant/quantServer/quantServer
mvn clean package

sudo systemctl restart quant-python
sudo systemctl restart quant-spring
sudo systemctl reload nginx
```

验证：

```bash
curl http://127.0.0.1:8000/health
curl http://127.0.0.1:8080/api/quant/health
```

## 12. 建议的安全加固

上线后建议：

- Nginx 配 HTTPS
- 不把 Python 8000 和 Spring 8080 暴露到公网，只开放 80/443
- `.env` 权限收紧：

```bash
chmod 600 /opt/quant/.env
```

- MySQL 不使用 root 账号给应用连接
- 云服务器安全组只开放必要端口
- AI 分析接口耗时较长，前端可按需启用

