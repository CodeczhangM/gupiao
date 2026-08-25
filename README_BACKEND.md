# 量化选股分析后端

这是一个半量化选股分析后端：负责拉取 Tushare 行情、执行本地策略、保存 MySQL 报告，并提供 HTTP API 给 Spring、前端或 Nginx 调用。

## 1. 安装依赖

```bash
pip install -r requirements.txt
```

## 2. 创建 MySQL 数据库

```sql
CREATE DATABASE IF NOT EXISTS quant DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
```

表 `quant_reports` 会在服务启动时自动创建。

## 3. 配置环境变量

```bash
export TUSHARE_TOKEN=你的tushare_token
export TUSHARE_HTTP_URL=https://fastapia.stockai888.top

export MYSQL_HOST=127.0.0.1
export MYSQL_PORT=3306
export MYSQL_USER=root
export MYSQL_PASSWORD=你的mysql密码
export MYSQL_DATABASE=quant

export OLLAMA_MODEL=deepseek-r1:7b

export MARKET_CACHE_ENABLED=true
export MARKET_CACHE_BOOTSTRAP_DAYS=120
export MARKET_CACHE_REQUIRED_DAYS=100
```

## 4. 启动服务

```bash
source .venv/bin/activate
uvicorn app:app --host 0.0.0.0 --port 8000
```
mvn clean package -DskipTests

SERVER_PORT=8080 QUANT_PYTHON_BASE_URL=http://127.0.0.1:8000 java -jar target/quantServer-0.0.1-SNAPSHOT.jar

## 5. API

健康检查：

```bash
curl http://127.0.0.1:8000/health
```

运行一次选股扫描，不调用 AI：

```bash
curl -X POST "http://127.0.0.1:8000/api/scan/run?include_ai=false&limit=20"
```

首次扫描会把最近 120 个交易日的日线、每日指标、股票基础信息和板块资金流写入 MySQL，后续只补缺失的新交易日；交易日 15:30 前会覆盖刷新当天数据。也可以先手动同步并查看状态：

```bash
curl -X POST "http://127.0.0.1:8000/api/cache/sync"
curl http://127.0.0.1:8000/api/cache/status
```

强制刷新当前交易日：

```bash
curl -X POST "http://127.0.0.1:8000/api/cache/sync?force_current=true"
```

运行一次选股扫描，并调用本地 Ollama：

```bash
curl -X POST "http://127.0.0.1:8000/api/scan/run?include_ai=true&limit=20"
```

查看最近报告：

```bash
curl http://127.0.0.1:8000/api/reports/latest
```

查看报告列表：

```bash
curl http://127.0.0.1:8000/api/reports
```

查看最近强势股：

```bash
curl http://127.0.0.1:8000/api/scan/latest/strong
```

查看最近抄底候选：

```bash
curl http://127.0.0.1:8000/api/scan/latest/dip
```

## 6. Nginx 转发示例

```nginx
location /quant/ {
    proxy_pass http://127.0.0.1:8000/;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
}
```

配置后访问：

```bash
curl http://你的域名/quant/health
```

## 7. Spring 调用方式

Spring 后端可以直接请求 Python 服务：

```text
POST http://127.0.0.1:8000/api/scan/run?include_ai=false&limit=20
GET  http://127.0.0.1:8000/api/reports/latest
```

后续如果要把它迁成纯 Spring Boot 后端，可以保留 `strategy.py` 作为策略计算服务，也可以把策略逻辑翻译成 Java。
