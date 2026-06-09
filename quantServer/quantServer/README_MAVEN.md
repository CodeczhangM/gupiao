# quantServer Maven 启动说明

这个 Spring Boot 服务负责调用旁边的 Python FastAPI 量化服务，并把接口统一暴露给前端或 Nginx。

## 1. 先启动 Python 量化服务

在 `/mnt/d/piao` 目录：

```bash
uvicorn app:app --host 0.0.0.0 --port 8000
```

## 2. 启动 Spring 服务

在 `quantServer/quantServer` 目录：

```bash
mvn spring-boot:run
```

或者打包：

```bash
mvn clean package
java -jar target/quantServer-0.0.1-SNAPSHOT.jar
```

## 3. 配置 Python 服务地址

默认地址是：

```text
http://127.0.0.1:8000
```

如果 Python 服务部署在别的地址：

```bash
export QUANT_PYTHON_BASE_URL=http://127.0.0.1:8000
export QUANT_PYTHON_READ_TIMEOUT=600s
```

## 4. Spring 暴露的接口

```text
GET  /api/quant/health
GET  /api/quant/health/db
POST /api/quant/scan/run
GET  /api/quant/reports
GET  /api/quant/reports/latest
GET  /api/quant/reports/{reportId}
GET  /api/quant/scan/latest/strong
GET  /api/quant/scan/latest/dip
```

运行一次扫描：

```bash
curl -X POST http://127.0.0.1:8080/api/quant/scan/run \
  -H 'Content-Type: application/json' \
  -d '{"includeAi":false,"limit":20}'
```
