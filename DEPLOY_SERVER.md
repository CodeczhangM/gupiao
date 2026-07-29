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
├── ai_agent.py
├── database.py
├── data_service.py
├── quant_service.py
├── stock_detail_service.py
├── strategy.py
├── trade_review_service.py
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

如果服务器实际目录是 `/opt/quaut`，同样把下文所有 `/opt/quant` 统一替换成 `/opt/quaut`，Python、Spring 和 Nginx 的目录必须保持一致。

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

AI_PROVIDER=traecli
TRAECLI_PERSONAL_ACCESS_TOKEN=你的_traecli_login_token
TRAECLI_BIN=/root/.local/bin/traecli
TRAECLI_MODEL=DeepSeek-V4-Pro
TRAECLI_TIMEOUT_SECONDS=300
HOME=/root
XDG_CACHE_HOME=/root/.cache
XDG_CONFIG_HOME=/root/.config
XDG_DATA_HOME=/root/.local/share

# 如需临时切回本地 Ollama，改为 AI_PROVIDER=ollama。
OLLAMA_MODEL=deepseek-r1:7b

# 0 表示最新可用交易日；1 表示前一交易日。
MARKET_DATE_OFFSET=0
```

注意：`.env` 不要提交到公开仓库。

## 6. 部署 Python 量化服务

安装依赖：

```bash
cd /opt/quant
python3 -m pip install -r requirements.txt
```

确认 TRAE CLI 可用：

```bash
traecli --help
```

如果需要验证 AI 调用：

```bash
echo "用一句话回复：TRAE CLI 已接入" | traecli --print --output-format text --query-timeout 60s
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
# TRAE CLI 在 systemd 下需要可用的 HOME/XDG 缓存目录。
Environment=HOME=/root
Environment=XDG_CACHE_HOME=/root/.cache
Environment=XDG_CONFIG_HOME=/root/.config
Environment=XDG_DATA_HOME=/root/.local/share

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
curl http://127.0.0.1:8081/api/quant/health
curl http://127.0.0.1:8081/api/quant/health/db
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
Environment=SERVER_PORT=8081
Environment=QUANT_PYTHON_BASE_URL=http://127.0.0.1:8000
Environment=QUANT_PYTHON_READ_TIMEOUT=600s

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
        # 如果服务器已有 Tomcat 占用 8080，Spring Boot 建议使用 8081。
        proxy_pass http://127.0.0.1:8081/api/quant/;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # 回测和 AI 分析可能耗时较长，避免 Nginx 默认 60 秒超时。
        proxy_connect_timeout 30s;
        proxy_send_timeout 600s;
        proxy_read_timeout 600s;
        send_timeout 600s;
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
curl http://127.0.0.1:8081/api/quant/health
curl http://127.0.0.1:8081/api/quant/health/db
curl http://你的域名或服务器IP/api/quant/health
```

运行一次扫描：

```bash
curl -X POST http://127.0.0.1:8081/api/quant/scan/run \
  -H 'Content-Type: application/json' \
  -d '{"includeAi":false,"limit":20}'
```

查看最新报告：

```bash
curl http://127.0.0.1:8081/api/quant/reports/latest
```

验证个股技术详情：

```bash
curl 'http://127.0.0.1:8081/api/quant/stocks/600000.SH/technical?tradeDate=20260620'
```

验证 AI 交易复盘。`positionStatus` 可为 `holding`（仍持仓）或 `sold`（已卖出）；已卖出时必须带上卖出日期和卖出价格：

```bash
curl -X POST http://127.0.0.1:8081/api/quant/trade-review/analyze \
  -H 'Content-Type: application/json' \
  -d '{
    "tsCode":"600000.SH",
    "buyDate":"20260601",
    "buyPrice":12.35,
    "positionStatus":"holding",
    "lossStatus":"当前浮亏，未设置明确止损",
    "holdingNote":"突破后追入，回落时曾补仓一次"
  }'
```

已卖出示例：

```bash
curl -X POST http://127.0.0.1:8081/api/quant/trade-review/analyze \
  -H 'Content-Type: application/json' \
  -d '{
    "tsCode":"600000.SH",
    "buyDate":"20260601",
    "buyPrice":12.35,
    "positionStatus":"sold",
    "sellDate":"20260618",
    "sellPrice":11.80,
    "lossStatus":"止损卖出",
    "holdingNote":"未按计划设置止损，跌破均线后才卖出"
  }'
```

## 10. 常见问题

### 10.1 前端显示接口失败

确认前端左侧接口地址：

```text
/api/quant
```

如果是直接打开本地 HTML，则使用：

```text
http://127.0.0.1:8081/api/quant
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
curl http://127.0.0.1:8081/api/quant/health
```

查看 Spring 配置：

```bash
systemctl cat quant-spring
```

确认：

```text
QUANT_PYTHON_BASE_URL=http://127.0.0.1:8000
```

### 10.5 页面返回 504 Gateway Time-out

504 通常表示 Nginx 已经把请求转给 Spring 了，但 Spring 或 Python 在 Nginx 超时时间内没有返回。回测、AI 分析、首次拉取行情数据都可能触发这个问题。

先确认 Nginx 代理到了 Spring 的实际端口。如果服务器已有 Tomcat 使用 8080，Spring Boot 应该使用 8081：

```nginx
location /api/quant/ {
    proxy_pass http://127.0.0.1:8081/api/quant/;
    proxy_connect_timeout 30s;
    proxy_send_timeout 600s;
    proxy_read_timeout 600s;
    send_timeout 600s;
}
```

修改后重载 Nginx：

```bash
sudo nginx -t
sudo systemctl reload nginx
```

再绕过 Nginx 直接测试 Spring：

```bash
curl -X POST http://127.0.0.1:8081/api/quant/backtest/run \
  -H 'Content-Type: application/json' \
  -d '{"lookbackDays":5,"holdDays":1,"limit":3}'
```

如果直连 Spring 成功，但域名访问 504，就是 Nginx 超时配置或反代端口问题。如果直连 Spring 也超时，继续看 Spring 和 Python 日志：

```bash
journalctl -u quant-spring -f
journalctl -u quant-python -f
```

### 10.6 TRAE CLI 无法执行或 AI 复盘失败

确认 `.env` 至少包含：

```text
AI_PROVIDER=traecli
TRAECLI_BIN=/root/.local/bin/traecli
TRAECLI_MODEL=DeepSeek-V4-Pro
TRAECLI_PERSONAL_ACCESS_TOKEN=你的登录令牌
```

确认程序和缓存目录可用：

```bash
ls -l /root/.local/bin/traecli
mkdir -p /root/.cache /root/.config /root/.local/share
/root/.local/bin/traecli --help
```

如果日志出现 `failed to get cache directory`，检查 `quant-python.service` 是否包含本文件第 6 节列出的 `HOME` 和 `XDG_*` 环境变量，然后执行：

```bash
sudo systemctl daemon-reload
sudo systemctl restart quant-python
```

### 10.7 交易复盘提示没有可用 K 线

检查股票代码必须为 Tushare 格式，例如 `600000.SH`、`000001.SZ`；买入日期必须是 `YYYYMMDD`。已卖出记录的卖出日期不得早于买入日期。

复盘服务会以实际交易日对齐：非交易日买入会使用买入日之后的第一个有日线数据的交易日，持仓状态会以当前可获得的最近交易日作为截止日。

## 11. 更新部署

本次交易复盘、个股技术详情或前端页面更新后，按以下顺序更新。命令以 `/opt/quant` 为例；实际使用 `/opt/quaut` 时替换路径。

### 11.1 备份配置和数据库

`.env` 不应被代码覆盖：

```bash
cp /opt/quant/.env /opt/quant/.env.backup.$(date +%Y%m%d_%H%M%S)
mysqldump -uquant_user -p quant > /opt/quant/quant_backup_$(date +%Y%m%d_%H%M%S).sql
```

### 11.2 同步代码

将本地项目文件同步到服务器，保留服务器 `.env`：

```bash
rsync -avz --delete \
  --exclude '.env' \
  --exclude '__pycache__' \
  --exclude '.git' \
  /本地项目目录/ root@你的服务器:/opt/quant/
```

至少确认以下新增/更新文件已上传：

```text
ai_agent.py
app.py
data_service.py
stock_detail_service.py
trade_review_service.py
quantClient/index.html
quantClient/main.js
quantClient/styles.css
quantServer/quantServer/src/
quantServer/quantServer/pom.xml
```

### 11.3 更新 Python 服务

```bash
cd /opt/quant
python3 -m pip install -r requirements.txt
python3 -m py_compile \
  app.py ai_agent.py database.py data_service.py quant_service.py strategy.py \
  stock_detail_service.py trade_review_service.py

sudo systemctl daemon-reload
sudo systemctl restart quant-python
sudo systemctl status quant-python --no-pager
```

如果 Python 服务启动失败：

```bash
sudo journalctl -u quant-python -n 100 --no-pager
```

### 11.4 更新 Spring 网关

交易复盘新增了 `TradeReviewRequest` 和 `/api/quant/trade-review/analyze` 转发端点，因此必须重新打包 Spring：

```bash
cd /opt/quant/quantServer/quantServer
mvn clean package -DskipTests

sudo systemctl restart quant-spring
sudo systemctl status quant-spring --no-pager
```

生产环境建议先执行完整测试再打包：

```bash
mvn test
mvn clean package -DskipTests
```

### 11.5 更新 Vue 静态文件与 Nginx

前端为静态文件，无需 npm 构建。确认 `quantClient/index.html` 中 `main.js` 和 `styles.css` 的版本参数已随本次发布更新，然后：

```bash
sudo nginx -t
sudo systemctl reload nginx
```

浏览器使用 `Ctrl+F5` 强制刷新，或清理站点缓存后打开“交易复盘”。

### 11.6 发布后验证

```bash
curl http://127.0.0.1:8000/health
curl http://127.0.0.1:8081/api/quant/health
curl 'http://127.0.0.1:8081/api/quant/stocks/600000.SH/technical?tradeDate=20260620'
```

然后执行本文件第 9 节的交易复盘 `curl` 示例。确认返回 JSON 中至少包含：

```text
trade
metrics
entry_snapshot
exit_snapshot
trade_kline
ai_summary
```

## 12. 建议的安全加固

上线后建议：

- Nginx 配 HTTPS
- 不把 Python 8000 和 Spring 8081 暴露到公网，只开放 80/443
- `.env` 权限收紧：

```bash
chmod 600 /opt/quant/.env
```

- MySQL 不使用 root 账号给应用连接
- 云服务器安全组只开放必要端口
- AI 分析接口耗时较长，前端可按需启用

## 查看启动问题
sudo journalctl -u quant-python -f
# 行情缓存配置

Python 服务默认把扫描所需行情增量缓存到 MySQL：

```bash
export MARKET_CACHE_ENABLED=true
export MARKET_CACHE_BOOTSTRAP_DAYS=120
export MARKET_CACHE_REQUIRED_DAYS=100
```

首次扫描前可手动初始化，后续只同步缺失日期；交易日 15:30 前会刷新当天数据：

```bash
curl -X POST http://127.0.0.1:8000/api/cache/sync
curl http://127.0.0.1:8000/api/cache/status
```
