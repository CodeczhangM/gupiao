#!/usr/bin/env bash
# Temporary local/server verification launcher. It does not stop existing services.
set -euo pipefail

BASE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON_PORT="${PYTHON_PORT:-8000}"
SPRING_PORT="${SPRING_PORT:-8081}"
RUNTIME_DIR="$BASE_DIR/.runtime"
SPRING_DIR="$BASE_DIR/quantServer/quantServer"
PYTHON_PID=""
SPRING_PID=""

mkdir -p "$RUNTIME_DIR"

fail() {
  printf '\n[失败] %s\n' "$1" >&2
  exit 1
}

cleanup_on_error() {
  local status=$?
  if [[ "$status" -ne 0 ]]; then
    [[ -n "$SPRING_PID" ]] && kill "$SPRING_PID" 2>/dev/null || true
    [[ -n "$PYTHON_PID" ]] && kill "$PYTHON_PID" 2>/dev/null || true
  fi
}

trap cleanup_on_error EXIT

port_is_busy() {
  local port="$1"
  ss -ltn 2>/dev/null | awk '{print $4}' | grep -Eq "[:.]${port}$"
}

wait_for_health() {
  local name="$1"
  local url="$2"
  local log_file="$3"

  for _ in $(seq 1 30); do
    if curl --silent --fail --max-time 2 "$url" >/dev/null; then
      printf '[完成] %s: %s\n' "$name" "$url"
      return 0
    fi
    sleep 1
  done

  printf '\n%s 启动超时，最近日志：\n' "$name" >&2
  tail -n 60 "$log_file" >&2 || true
  exit 1
}

if port_is_busy "$PYTHON_PORT"; then
  fail "端口 ${PYTHON_PORT} 已被占用。请先检查 ss -ltnp | grep :${PYTHON_PORT}"
fi

if port_is_busy "$SPRING_PORT"; then
  fail "端口 ${SPRING_PORT} 已被占用。请先检查 ss -ltnp | grep :${SPRING_PORT}"
fi

command -v python3 >/dev/null || fail "未找到 python3"
command -v mvn >/dev/null || fail "未找到 Maven"
command -v curl >/dev/null || fail "未找到 curl"
command -v ss >/dev/null || fail "未找到 ss，请安装 iproute2"

printf '[1/5] 校验 Python 代码...\n'
cd "$BASE_DIR"
python3 -m py_compile \
  app.py ai_agent.py database.py data_service.py quant_service.py strategy.py \
  stock_detail_service.py trade_review_service.py

printf '[2/5] 打包 Spring Boot...\n'
cd "$SPRING_DIR"
mvn -q clean package -DskipTests

JAR_FILE="$SPRING_DIR/target/quantServer-0.0.1-SNAPSHOT.jar"
[[ -f "$JAR_FILE" ]] || fail "未找到打包后的 Jar：$JAR_FILE"

printf '[3/5] 启动 Python 服务（%s）...\n' "$PYTHON_PORT"
PYTHON_LOG="$RUNTIME_DIR/python.log"
(
  cd "$BASE_DIR"
  exec python3 -m uvicorn app:app --host 127.0.0.1 --port "$PYTHON_PORT"
) >"$PYTHON_LOG" 2>&1 &
PYTHON_PID=$!
echo "$PYTHON_PID" > "$RUNTIME_DIR/python.pid"
wait_for_health "Python" "http://127.0.0.1:${PYTHON_PORT}/health" "$PYTHON_LOG"

printf '[4/5] 启动 Spring 网关（%s）...\n' "$SPRING_PORT"
SPRING_LOG="$RUNTIME_DIR/spring.log"
(
  cd "$SPRING_DIR"
  exec env \
    SERVER_PORT="$SPRING_PORT" \
    QUANT_PYTHON_BASE_URL="http://127.0.0.1:${PYTHON_PORT}" \
    QUANT_PYTHON_READ_TIMEOUT="600s" \
    java -jar "$JAR_FILE"
) >"$SPRING_LOG" 2>&1 &
SPRING_PID=$!
echo "$SPRING_PID" > "$RUNTIME_DIR/spring.pid"
wait_for_health "Spring" "http://127.0.0.1:${SPRING_PORT}/api/quant/health" "$SPRING_LOG"

printf '[5/5] 复核数据库接口...\n'
curl --silent --fail "http://127.0.0.1:${SPRING_PORT}/api/quant/health/db" >/dev/null \
  || fail "数据库健康检查失败，请查看 $PYTHON_LOG"

cat <<EOF

临时验证服务已启动：
  Python: http://127.0.0.1:${PYTHON_PORT} (PID ${PYTHON_PID})
  Spring: http://127.0.0.1:${SPRING_PORT}/api/quant (PID ${SPRING_PID})
  Python 日志: ${PYTHON_LOG}
  Spring 日志: ${SPRING_LOG}

停止临时服务：
  kill \$(cat "${RUNTIME_DIR}/python.pid")
  kill \$(cat "${RUNTIME_DIR}/spring.pid")
EOF
