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
├── financial_cache.py
├── free_review_models.py
├── free_review_repository.py
├── free_review_scoring.py
├── free_review_service.py
├── indicator_settings.py
├── indicator_settings_models.py
├── intraday_monitor_service.py
├── market_cache.py
├── quant_service.py
├── realtime_cache.py
├── realtime_info_service.py
├── realtime_market_source.py
├── stock_detail_service.py
├── strategy.py
├── trade_review_service.py
├── requirements.txt
├── .env
├── quantClient
│   ├── index.html
│   ├── free-review-utils.js
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

实时查询缓存不需要手工执行 SQL 文件。新版本首次部署时会通过
`realtime_cache.init_realtime_cache()` 自动创建：

```text
realtime_minute_cache   实时 1 分钟、60 分钟行情缓存
realtime_result_cache   实时共振、实时信息最终筛选结果
```

缓存写入后自动只保留最近 5 个交易日。应用数据库用户需要对 `quant`
库具有 `CREATE`、`SELECT`、`INSERT`、`UPDATE`、`DELETE` 权限；上面的
`GRANT ALL PRIVILEGES ON quant.*` 已覆盖这些权限。

自由复盘选股会另外自动创建：

```text
financial_indicator_cache  最近 8 个季度财务指标
financial_cache_sync       财务季度同步状态
review_stock_snapshot      每个完整交易日的全市场筛选宽表
review_snapshot_build      快照构建阶段、进度和失败原因
indicator_settings         全局指标参数及配置版本（MACD 默认 5/34/5）
```

财务同步使用 `fina_indicator_vip`，Tushare 账号需要至少 5000 积分权限。

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

### 10.8 实时共振或实时信息快速查看为空

首次部署后数据库结果缓存为空，需要先在页面点击一次“强制刷新”，或者
调用带 `force_refresh=true` 的接口。只有强制刷新成功并筛选出股票后，
结果才会写入 MySQL。

检查缓存表：

```bash
mysql -uquant_user -p quant -e "
SELECT cache_scope, cache_key, trade_date, updated_at
FROM realtime_result_cache
ORDER BY updated_at DESC
LIMIT 10;"
```

如果表不存在或日志提示权限错误，重新执行第 11.5 节的缓存表初始化命令，
并确认数据库用户拥有 `quant.*` 的建表和读写权限。

数据库暂时不可用时，实时接口会回退原有行情源继续查询，因此不会直接中断，
但查询时间会明显变长。

## 11. 更新部署

本节用于通过 SCP 将本地当前版本更新到服务器。命令以服务器目录
`/opt/quant`、SSH 用户 `root` 为例；如果实际目录、用户或服务器地址不同，
请统一替换。

本次版本同时更新 Python、Spring 和 Vue 静态文件，不能只上传单个文件。
推荐先在本地生成干净发布包，再上传到服务器 `/tmp`。

### 11.1 本地生成发布包

以下命令在本地电脑执行，不是在服务器执行。先进入 `piao` 项目的父目录：

```bash
cd /本地项目父目录

RELEASE_NAME="quant-release-$(date +%Y%m%d-%H%M%S).tar.gz"

tar \
  --exclude='piao/.env' \
  --exclude='piao/.env.example' \
  --exclude='piao/.git' \
  --exclude='piao/.runtime' \
  --exclude='piao/.worktrees' \
  --exclude='*/__pycache__' \
  --exclude='*.pyc' \
  --exclude='piao/quantServer/quantServer/target' \
  -czf "$RELEASE_NAME" piao

echo "发布包：$RELEASE_NAME"
tar -tzf "$RELEASE_NAME" | head -n 20
```

发布包不会包含 `.env`、`.env.example`、Git 历史、Python 缓存、Java
构建产物和运行日志。服务器会继续使用原有 `.env`，避免本地配置或令牌进入
发布包。
如果本地项目目录不叫 `piao`，先改名或同步调整上面所有 `piao` 路径。

### 11.2 通过 SCP 上传

仍在本地电脑执行。将上一节输出的实际包名替换到命令中：

```bash
scp quant-release-YYYYMMDD-HHMMSS.tar.gz \
  root@你的服务器:/tmp/quant-release.tar.gz
```

示例：

```bash
scp quant-release-20260730-193000.tar.gz \
  root@203.0.113.10:/tmp/quant-release.tar.gz
```

如果 SSH 不是 22 端口，使用大写 `-P`：

```bash
scp -P 你的SSH端口 quant-release-YYYYMMDD-HHMMSS.tar.gz \
  root@你的服务器:/tmp/quant-release.tar.gz
```

### 11.3 服务器校验发布包

登录服务器：

```bash
ssh root@你的服务器
```

以下命令开始均在服务器执行。先检查压缩包和必需文件，任何一项缺失都会停止：

```bash
set -euo pipefail

RELEASE_ARCHIVE=/tmp/quant-release.tar.gz

test -s "$RELEASE_ARCHIVE"
tar -tzf "$RELEASE_ARCHIVE" >/dev/null

for required_file in \
  piao/app.py \
  piao/financial_cache.py \
  piao/free_review_models.py \
  piao/free_review_repository.py \
  piao/free_review_scoring.py \
  piao/free_review_service.py \
  piao/indicator_settings.py \
  piao/indicator_settings_models.py \
  piao/realtime_cache.py \
  piao/realtime_info_service.py \
  piao/intraday_monitor_service.py \
  piao/quantClient/index.html \
  piao/quantClient/free-review-utils.js \
  piao/quantServer/quantServer/src/main/java/com/codec/quantserver/dto/MacdSettingsRequest.java \
  piao/quantServer/quantServer/src/main/java/com/codec/quantserver/dto/FreeReviewQueryRequest.java \
  piao/quantServer/quantServer/src/main/java/com/codec/quantserver/dto/FreeReviewRange.java \
  piao/quantServer/quantServer/pom.xml
do
  tar -tzf "$RELEASE_ARCHIVE" | grep -Fx "$required_file" >/dev/null
done

echo "发布包校验通过"
```

### 11.4 备份数据库、配置和旧版本

`.env` 不得被新代码覆盖。创建本次发布标识，并备份数据库和配置：

```bash
set -euo pipefail

DEPLOY_DIR=/opt/quant
RELEASE_ARCHIVE=/tmp/quant-release.tar.gz
RELEASE_ID="$(date +%Y%m%d-%H%M%S)"
BACKUP_ROOT=/opt/quant-backups
ROLLBACK_DIR="/opt/quant.rollback-$RELEASE_ID"
ENV_BACKUP="/tmp/quant-env-$RELEASE_ID"
DB_BACKUP="$BACKUP_ROOT/quant-$RELEASE_ID.sql"
DEPLOY_STATE=/tmp/quant-deploy-state

test -f "$DEPLOY_DIR/.env"
test -s "$RELEASE_ARCHIVE"
mkdir -p "$BACKUP_ROOT"
cp "$DEPLOY_DIR/.env" "$ENV_BACKUP"
chmod 600 "$ENV_BACKUP"

mysqldump -uquant_user -p quant > "$DB_BACKUP"

cat > "$DEPLOY_STATE" <<EOF
DEPLOY_DIR='$DEPLOY_DIR'
RELEASE_ARCHIVE='$RELEASE_ARCHIVE'
RELEASE_ID='$RELEASE_ID'
ROLLBACK_DIR='$ROLLBACK_DIR'
ENV_BACKUP='$ENV_BACKUP'
DB_BACKUP='$DB_BACKUP'
STAGING_DIR='/tmp/quant-staging-$RELEASE_ID'
EOF
chmod 600 "$DEPLOY_STATE"

echo "配置备份：$ENV_BACKUP"
echo "数据库备份：$DB_BACKUP"
echo "旧版本回滚目录：$ROLLBACK_DIR"
echo "部署状态：$DEPLOY_STATE"
```

`mysqldump` 会提示输入 `quant_user` 的数据库密码。确认备份文件非空：

```bash
source /tmp/quant-deploy-state
test -s "$DB_BACKUP"
ls -lh "$DB_BACKUP" "$ENV_BACKUP"
```

后续步骤都会读取 `/tmp/quant-deploy-state`，即使 SSH 断线后重新登录，也不需要
重新猜测本次发布的时间戳和回滚目录。

### 11.5 解压、检查并构建新版本

先在临时目录构建，构建成功前不停止线上服务：

```bash
set -euo pipefail

source /tmp/quant-deploy-state
test ! -e "$STAGING_DIR"
mkdir -p "$STAGING_DIR"
tar -xzf "$RELEASE_ARCHIVE" -C "$STAGING_DIR"

test -f "$STAGING_DIR/piao/app.py"
test -f "$STAGING_DIR/piao/free_review_service.py"
test -f "$STAGING_DIR/piao/indicator_settings.py"
test -f "$STAGING_DIR/piao/indicator_settings_models.py"
test -f "$STAGING_DIR/piao/financial_cache.py"
test -f "$STAGING_DIR/piao/realtime_cache.py"
test -f "$STAGING_DIR/piao/quantClient/index.html"
test -f "$STAGING_DIR/piao/quantClient/free-review-utils.js"
test -f "$STAGING_DIR/piao/quantServer/quantServer/pom.xml"

cp "$ENV_BACKUP" "$STAGING_DIR/piao/.env"
chmod 600 "$STAGING_DIR/piao/.env"

cd "$STAGING_DIR/piao"
python3 -m pip install -r requirements.txt
python3 -m py_compile \
  app.py database.py data_service.py market_cache.py realtime_cache.py \
  financial_cache.py free_review_models.py free_review_repository.py \
  free_review_scoring.py free_review_service.py \
  indicator_settings.py indicator_settings_models.py \
  realtime_market_source.py realtime_info_service.py \
  intraday_monitor_service.py overnight_monitor_service.py \
  morning_follow_service.py

python3 -c "import settings; settings.load_env_files(); from realtime_cache import init_realtime_cache; init_realtime_cache(); print('realtime cache schema ready')"

python3 -c "
import settings
settings.load_env_files()
from indicator_settings import init_indicator_settings
init_indicator_settings()
print('indicator settings ready')
"

python3 -c "
import settings
settings.load_env_files()
from financial_cache import init_financial_cache
from free_review_repository import init_free_review_schema
init_financial_cache()
init_free_review_schema()
print('free review schema ready')
"

cd "$STAGING_DIR/piao/quantServer/quantServer"
mvn test
mvn clean package -DskipTests
```

缓存表初始化是幂等操作，可以安全重复执行，不会清空已有缓存。

### 11.6 切换版本并重启服务

只有第 11.5 节全部成功后才执行：

```bash
set -euo pipefail

source /tmp/quant-deploy-state

systemctl stop quant-spring quant-python

test ! -e "$ROLLBACK_DIR"
mv "$DEPLOY_DIR" "$ROLLBACK_DIR"
mv "$STAGING_DIR/piao" "$DEPLOY_DIR"

cp "$ENV_BACKUP" "$DEPLOY_DIR/.env"
chmod 600 "$DEPLOY_DIR/.env"

systemctl daemon-reload
systemctl start quant-python
systemctl start quant-spring

nginx -t
systemctl reload nginx

systemctl status quant-python --no-pager
systemctl status quant-spring --no-pager
```

如果服务启动失败，先不要删除 `ROLLBACK_DIR`，直接执行第 11.10 节。

查看最近日志：

```bash
journalctl -u quant-python -n 100 --no-pager
journalctl -u quant-spring -n 100 --no-pager
```

### 11.7 发布后健康检查

```bash
curl --fail http://127.0.0.1:8000/health
curl --fail http://127.0.0.1:8000/health/db
curl --fail http://127.0.0.1:8081/api/quant/health
curl --fail http://127.0.0.1:8081/api/quant/health/db
curl --fail http://你的域名或服务器IP/api/quant/health
```

确认 MySQL 缓存表已经创建：

```bash
mysql -uquant_user -p quant -e "
SHOW TABLES LIKE 'realtime_minute_cache';
SHOW TABLES LIKE 'realtime_result_cache';
SELECT COUNT(*) AS minute_cache_rows FROM realtime_minute_cache;
SELECT COUNT(*) AS result_cache_rows FROM realtime_result_cache;"
```

确认自由复盘相关表已经创建：

```bash
mysql -uquant_user -p quant -e "
SHOW TABLES LIKE 'financial_indicator_cache';
SHOW TABLES LIKE 'review_stock_snapshot';
SHOW TABLES LIKE 'review_snapshot_build';
SHOW TABLES LIKE 'indicator_settings';
SELECT COUNT(*) AS financial_rows FROM financial_indicator_cache;
SELECT COUNT(*) AS review_rows FROM review_stock_snapshot;
SELECT COUNT(*) AS build_rows FROM review_snapshot_build;
SELECT setting_key, fast_period, slow_period, signal_period, version,
       updated_at
FROM indicator_settings;"
```

### 11.8 验证全局 MACD 参数

先通过 Spring 代理读取当前设置。首次初始化应为快线 5、慢线 34、信号线 5：

```bash
curl --fail \
  'http://127.0.0.1:8081/api/quant/indicator-settings/macd'
```

验证保存和重算链路：

```bash
curl --fail -X PUT \
  'http://127.0.0.1:8081/api/quant/indicator-settings/macd' \
  -H 'Content-Type: application/json' \
  -d '{"fast_period":5,"slow_period":34,"signal_period":5}'
```

保存会增加配置版本、清理进程内派生结果，并启动自由复盘重建。日线与分钟
原始行情缓存不会被删除。新的 `realtime_result_cache.cache_key` 和
`review_stock_snapshot.score_version` 会包含类似 `macd-5-34-5-v2`
的参数键，旧参数结果可以保留用于排查，但不会被新查询命中。

### 11.9 首次强制刷新与快速缓存验证

新部署的结果缓存可能为空。先执行一次强制刷新，超时设置为 10 分钟：

```bash
curl --fail --max-time 600 \
  'http://127.0.0.1:8081/api/quant/intraday-monitor?force_refresh=true'

curl --fail --max-time 600 \
  'http://127.0.0.1:8081/api/quant/realtime-info?limit=10&force_refresh=true'
```

如果实时共振返回“还没有选股报告”，先运行一次扫描，再重试：

```bash
curl --fail --max-time 600 \
  -X POST http://127.0.0.1:8081/api/quant/scan/run \
  -H 'Content-Type: application/json' \
  -d '{"includeAi":false,"limit":20}'
```

强制刷新成功后检查数据库：

```bash
mysql -uquant_user -p quant -e "
SELECT cache_scope, cache_key, trade_date, data_status, updated_at
FROM realtime_result_cache
ORDER BY updated_at DESC;

SELECT cache_trade_date, freq, COUNT(*) AS row_count
FROM realtime_minute_cache
GROUP BY cache_trade_date, freq
ORDER BY cache_trade_date DESC, freq;"
```

再执行快速查看：

```bash
time curl --fail \
  'http://127.0.0.1:8081/api/quant/intraday-monitor'

time curl --fail \
  'http://127.0.0.1:8081/api/quant/realtime-info?limit=10'
```

返回 JSON 中 `cache_source` 为 `database` 或 `memory` 表示命中快速路径；
`cache_updated_at` 是缓存写入时间。页面上会显示“数据库快速结果”、
“内存快速结果”或“刚刚强制刷新”。

前端为静态文件，无需 npm 构建。浏览器使用 `Ctrl+F5` 强制刷新，确认页面
同时出现“快速查看”和“强制刷新”按钮。

### 11.10 生成并验证自由复盘快照

先确认行情缓存至少有 100 个完整交易日：

```bash
curl --fail 'http://127.0.0.1:8081/api/quant/cache/status'
```

启动构建。接口会立即返回 `pending` 或 `running`，实际同步和评分在 Python
后台线程中继续执行：

```bash
curl --fail -X POST \
  'http://127.0.0.1:8081/api/quant/free-review/build?force=true'

curl --fail \
  'http://127.0.0.1:8081/api/quant/free-review/build-status'
```

每隔几秒重复查询构建状态，直到 `status` 为 `success`。首次同步 8 个季度
财务数据所需时间较长。如果状态为 `failed` 且错误中包含 `5000` 或“权限”，
说明当前 Tushare Token 没有 `fina_indicator_vip` 的 5000 积分权限；程序
不会退回到数千次逐股票请求，需要更换具备权限的 Token 后重新构建。

构建成功后验证元数据、分页查询和 CSV：

```bash
curl --fail \
  'http://127.0.0.1:8081/api/quant/free-review/meta'

curl --fail -X POST \
  'http://127.0.0.1:8081/api/quant/free-review/query' \
  -H 'Content-Type: application/json' \
  -d '{"page":1,"page_size":50,"sort_by":"total_score","sort_direction":"desc","ranges":{"pe_ttm":{"min":0,"max":40}}}'

curl --fail -X POST \
  'http://127.0.0.1:8081/api/quant/free-review/export' \
  -H 'Content-Type: application/json' \
  -d '{"page":1,"page_size":50}' \
  -o /tmp/free-review.csv
```

检查实际数据量和最新构建状态：

```bash
mysql -uquant_user -p quant -e "
SELECT COUNT(*) AS financial_rows FROM financial_indicator_cache;
SELECT trade_date, score_version, COUNT(*) AS stock_count
FROM review_stock_snapshot
GROUP BY trade_date, score_version
ORDER BY trade_date DESC;
SELECT trade_date, score_version, status, stage, total_count,
       failed_count, financial_coverage, updated_at, error_message
FROM review_snapshot_build
ORDER BY updated_at DESC
LIMIT 5;"
```

### 11.11 发布失败回滚

将下面的 `ROLLBACK_DIR` 替换为第 11.4 节输出的实际目录：

```bash
set -euo pipefail

DEPLOY_DIR=/opt/quant
ROLLBACK_DIR=/opt/quant.rollback-YYYYMMDD-HHMMSS
FAILED_DIR="/opt/quant.failed-$(date +%Y%m%d-%H%M%S)"

test -d "$ROLLBACK_DIR"
test -f "$ROLLBACK_DIR/app.py"

systemctl stop quant-spring quant-python

if test -d "$DEPLOY_DIR"; then
  mv "$DEPLOY_DIR" "$FAILED_DIR"
fi
mv "$ROLLBACK_DIR" "$DEPLOY_DIR"

systemctl start quant-python
systemctl start quant-spring

systemctl status quant-python --no-pager
systemctl status quant-spring --no-pager
curl --fail http://127.0.0.1:8081/api/quant/health
```

程序回滚通常不需要恢复数据库，因为新缓存表与旧版代码互不冲突。如果必须
恢复数据库，确认会覆盖新数据后再执行：

```bash
mysql -uquant_user -p quant < /opt/quant-backups/quant-YYYYMMDD-HHMMSS.sql
```

回滚验证完成前，不要删除 `FAILED_DIR`、数据库备份或 `/tmp` 下的 `.env`
备份。

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

## 13. 行情缓存配置

Python 服务默认把扫描所需的日线行情增量缓存到 MySQL：

```text
MARKET_CACHE_ENABLED=true
MARKET_CACHE_BOOTSTRAP_DAYS=120
MARKET_CACHE_REQUIRED_DAYS=100
```

首次扫描前可手动初始化，后续只同步缺失日期；交易日 15:30 前会刷新当天数据：

```bash
curl -X POST http://127.0.0.1:8000/api/cache/sync
curl http://127.0.0.1:8000/api/cache/status
```

持续查看服务日志：

```bash
journalctl -u quant-python -f
journalctl -u quant-spring -f
```
