# 个股技术面详情与 AI 提示词 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让用户从任一候选股进入技术面详情，查看 120 日技术指标、K 线/量能图、策略信号和可复制的专属 AI 分析提示词。

**Architecture:** Python 新增 `stock_detail_service.py` 作为行情标准化、技术指标计算、报告候选池关联和提示词构造的唯一入口；FastAPI 与 Spring 分别暴露及转发详情端点。Vue 前端将选股行改为可打开详情页，使用无依赖的 SVG 渲染行情和均线，并展示指标卡片与提示词复制动作。

**Tech Stack:** Python 3、pandas、FastAPI、Spring Boot 4 / RestClient、Vue 3 CDN、原生 SVG、unittest、JUnit 5 / MockMvc。

---

## File structure

- Create: `stock_detail_service.py` — 验证代码、获取单股 120 日行情、计算指标、合并报告信号、生成 API DTO 与 AI 提示词。
- Create: `tests/test_stock_detail_service.py` — 指标、短历史、提示词和服务编排单测。
- Create: `tests/test_stock_detail_api.py` — FastAPI 路由的成功、非法代码、报告不存在与数据源失败测试。
- Modify: `data_service.py` — 增加只拉取指定股票、包含 open/high/low/close/vol/pct_chg 的 120 日行情函数。
- Modify: `app.py` — 新增 `GET /api/stocks/{ts_code}/technical`。
- Modify: `quantServer/.../service/QuantPythonClient.java` — 透明调用 Python 详情端点。
- Modify: `quantServer/.../controller/QuantController.java` — 增加 `/api/quant/stocks/{tsCode}/technical`。
- Modify: `quantServer/.../controller/QuantControllerTest.java` — 断言网关端点将查询参数和代码转给 client。
- Modify: `quantClient/main.js` — 行点击事件、详情状态、请求/复制方法、SVG 数据几何函数。
- Modify: `quantClient/index.html` — 详情视图及各候选表的 `open-stock` 事件绑定。
- Modify: `quantClient/styles.css` — 行可点击、K 线图、指标网格、提示词区域和窄屏样式。

### Task 1: 单股历史数据查询

**Files:**
- Modify: `data_service.py:150-181`
- Test: `tests/test_stock_detail_service.py`

- [ ] **Step 1: 写入失败测试，要求只请求指定代码且保留 OHLCV。**

```python
@patch("data_service.get_trade_dates", return_value=["20260615", "20260612"])
@patch("data_service._query_tushare")
def test_get_stock_daily_history_queries_one_stock(query, _dates):
    query.return_value = pd.DataFrame([
        {"ts_code": "600001.SH", "trade_date": "20260615", "open": 10,
         "high": 11, "low": 9, "close": 10.5, "vol": 100, "pct_chg": 5},
    ])
    result = data_service.get_stock_daily_history("600001.SH", "20260615", n=120)
    assert list(result.columns) == ["ts_code", "trade_date", "open", "high", "low", "close", "vol", "pct_chg"]
    assert query.call_args.kwargs["ts_code"] == "600001.SH"
```

- [ ] **Step 2: 运行测试确认失败。**

Run: `python -m unittest tests.test_stock_detail_service.StockHistoryTests.test_get_stock_daily_history_queries_one_stock -v`  
Expected: FAIL，提示 `data_service` 没有 `get_stock_daily_history`。

- [ ] **Step 3: 在 `data_service.py` 实现最小查询函数。**

```python
def get_stock_daily_history(ts_code: str, end_trade_date: str, n: int = 120) -> pd.DataFrame:
    dates = get_trade_dates(n=n, end_date=end_trade_date)
    fields = "ts_code,trade_date,open,high,low,close,vol,pct_chg"
    history = _query_tushare(
        "daily", ts_code=ts_code, start_date=dates[-1], end_date=end_trade_date, fields=fields,
    )
    if history.empty:
        return pd.DataFrame(columns=fields.split(","))
    return (history[history["trade_date"].astype(str).isin(dates)]
            .sort_values("trade_date").drop_duplicates("trade_date").copy())
```

- [ ] **Step 4: 运行该测试确认通过。**

Run: `python -m unittest tests.test_stock_detail_service.StockHistoryTests -v`  
Expected: PASS。

- [ ] **Step 5: 提交该任务。**

```bash
git add data_service.py tests/test_stock_detail_service.py
git commit -m "feat: load single-stock price history"
```

### Task 2: 技术指标与 AI 提示词服务

**Files:**
- Create: `stock_detail_service.py`
- Test: `tests/test_stock_detail_service.py`

- [ ] **Step 1: 为指标、短历史和提示词编写失败测试。**

```python
def test_build_technical_snapshot_calculates_latest_indicators():
    snapshot = build_technical_snapshot(build_history(days=120))
    assert snapshot["history_complete"] is True
    assert set(snapshot["latest"]["moving_averages"]) == {"ma5", "ma10", "ma20", "ma60"}
    assert set(snapshot["latest"]["macd"]) == {"dif", "dea", "histogram"}
    assert set(snapshot["latest"]["kdj"]) == {"k", "d", "j"}
    assert set(snapshot["latest"]["rsi"]) == {"rsi6", "rsi12", "rsi24"}
    assert "upper" in snapshot["latest"]["bollinger"]

def test_short_history_is_returned_without_claiming_complete():
    assert build_technical_snapshot(build_history(days=20))["history_complete"] is False

def test_prompt_contains_metrics_and_conditional_action_boundary():
    prompt = build_ai_prompt({"identity": {"ts_code": "600001.SH", "name": "测试股份"},
                              "trade_date": "20260615", "latest": sample_latest(), "strategy_signals": []})
    assert "MACD" in prompt and "建仓" in prompt and "不执行交易" in prompt
```

- [ ] **Step 2: 运行测试确认失败。**

Run: `python -m unittest tests.test_stock_detail_service.TechnicalSnapshotTests -v`  
Expected: FAIL，提示 `stock_detail_service` 不存在。

- [ ] **Step 3: 创建 `stock_detail_service.py`，并实现固定指标口径。**

```python
def _rsi(close, period):
    delta = close.diff()
    gain = delta.clip(lower=0).rolling(period, min_periods=period).mean()
    loss = (-delta.clip(upper=0)).rolling(period, min_periods=period).mean()
    return 100 - 100 / (1 + gain / loss.replace(0, pd.NA))

def build_technical_snapshot(history):
    frame = history.sort_values("trade_date").copy()
    for column in ("open", "high", "low", "close", "vol", "pct_chg"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    close, high, low = frame["close"], frame["high"], frame["low"]
    for period in (5, 10, 20, 60): frame[f"ma{period}"] = close.rolling(period, min_periods=period).mean()
    ema12, ema26 = close.ewm(span=12, adjust=False).mean(), close.ewm(span=26, adjust=False).mean()
    frame["dif"] = ema12 - ema26; frame["dea"] = frame["dif"].ewm(span=9, adjust=False).mean()
    frame["histogram"] = (frame["dif"] - frame["dea"]) * 2
    low9, high9 = low.rolling(9, min_periods=9).min(), high.rolling(9, min_periods=9).max()
    rsv = ((close - low9) / (high9 - low9).replace(0, pd.NA) * 100)
    frame["k"] = rsv.ewm(com=2, adjust=False).mean(); frame["d"] = frame["k"].ewm(com=2, adjust=False).mean(); frame["j"] = 3 * frame["k"] - 2 * frame["d"]
```

继续在同一模块：计算 `rsi6/12/24`、`bollinger`（20 日均线 ± 2 倍标准差）、`volume_ma5`、`support_20/60=low` 滚动最小值及 `resistance_20/60=high` 滚动最大值；以 `None` 序列化 NaN。返回 `history_complete=len(frame)>=120`、`candles`（每根含全部图表指标）、最新指标快照。

- [ ] **Step 4: 实现报告关联与提示词，不访问 AI。**

```python
def find_strategy_signals(report, ts_code):
    pools = report.get("pools") or {"reversal": report.get("dip", []), "breakout": report.get("strong", []), "first_limit": report.get("first_limit", [])}
    return [{"pool": pool, "score": row.get("score"),
             "reason": row.get("strong_reason") or row.get("breakout_reason") or row.get("first_limit_reason") or row.get("dip_reason")}
            for pool, rows in pools.items() for row in rows if row.get("ts_code") == ts_code]
```

`build_ai_prompt(detail)` 必须逐项插入代码、名称、日期、OHLCV 摘要、均线、MACD、KDJ、RSI、布林、量能、支撑阻力和信号；末尾固定要求输出“技术结论、关键依据、风险点、观察/确认条件、仅供参考的条件化建仓/减仓/止损建议”，并包含“不执行交易、不保证收益”。

- [ ] **Step 5: 运行服务测试确认通过。**

Run: `python -m unittest tests.test_stock_detail_service -v`  
Expected: PASS。

- [ ] **Step 6: 提交该任务。**

```bash
git add stock_detail_service.py tests/test_stock_detail_service.py
git commit -m "feat: build stock technical snapshot and AI prompt"
```

### Task 3: FastAPI 详情端点

**Files:**
- Modify: `app.py:1-103`
- Create: `tests/test_stock_detail_api.py`

- [ ] **Step 1: 编写路由失败测试。**

```python
@patch("app.get_stock_technical_detail", return_value={"trade_date": "20260615", "prompt": "x"})
def test_technical_endpoint_returns_detail(service):
    client = TestClient(app.app)
    response = client.get("/api/stocks/600001.SH/technical", params={"trade_date": "20260615", "report_id": 3})
    assert response.status_code == 200
    service.assert_called_once_with("600001.SH", "20260615", 3)
```

- [ ] **Step 2: 运行测试确认失败。**

Run: `python -m unittest tests.test_stock_detail_api -v`  
Expected: FAIL，返回 404。

- [ ] **Step 3: 接入端点与错误映射。**

```python
@app.get("/api/stocks/{ts_code}/technical")
def stock_technical_detail(ts_code: str, trade_date: str = Query(..., pattern=r"^\d{8}$"), report_id: int | None = Query(None, ge=1)):
    try:
        return get_stock_technical_detail(ts_code, trade_date, report_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("获取个股技术面失败")
        raise HTTPException(status_code=502, detail=str(exc)) from exc
```

服务函数必须用 `re.fullmatch(r"\d{6}\.(SH|SZ)", ts_code)` 验证代码；`report_id` 存在时用 `get_report`，不存在时用 `get_latest_report`，报告缺失抛 `LookupError`；将报告的候选池信号交给 Task 2 的函数。

- [ ] **Step 4: 运行 API 与现有回归测试。**

Run: `python -m unittest tests.test_stock_detail_api tests.test_advantage_stock_scoring -v`  
Expected: PASS。

- [ ] **Step 5: 提交该任务。**

```bash
git add app.py stock_detail_service.py tests/test_stock_detail_api.py
git commit -m "feat: expose stock technical detail API"
```

### Task 4: Spring 网关转发

**Files:**
- Modify: `quantServer/quantServer/src/main/java/com/codec/quantserver/service/QuantPythonClient.java`
- Modify: `quantServer/quantServer/src/main/java/com/codec/quantserver/controller/QuantController.java`
- Modify: `quantServer/quantServer/src/test/java/com/codec/quantserver/controller/QuantControllerTest.java`

- [ ] **Step 1: 编写控制器失败测试。**

```java
@Test
void technicalDetailForwardsCodeDateAndReportId() throws Exception {
    QuantPythonClient client = mock(QuantPythonClient.class);
    when(client.stockTechnicalDetail("600001.SH", "20260615", 7L)).thenReturn(Map.of("prompt", "x"));
    MockMvc mvc = MockMvcBuilders.standaloneSetup(new QuantController(client)).build();
    mvc.perform(get("/api/quant/stocks/600001.SH/technical").param("tradeDate", "20260615").param("reportId", "7")).andExpect(status().isOk());
    verify(client).stockTechnicalDetail("600001.SH", "20260615", 7L);
}
```

- [ ] **Step 2: 运行测试确认失败。**

Run: `cd quantServer/quantServer && ./gradlew test --tests com.codec.quantserver.controller.QuantControllerTest`  
Expected: FAIL，方法不存在。

- [ ] **Step 3: 添加 client 方法和控制器端点。**

```java
public Map<String, Object> stockTechnicalDetail(String tsCode, String tradeDate, Long reportId) {
    return restClient.get().uri(builder -> builder.path("/api/stocks/{tsCode}/technical")
        .queryParam("trade_date", tradeDate).queryParamIfPresent("report_id", Optional.ofNullable(reportId))
        .build(tsCode)).retrieve().body(mapType());
}

@GetMapping("/stocks/{tsCode}/technical")
public Map<String, Object> stockTechnicalDetail(@PathVariable String tsCode, @RequestParam String tradeDate, @RequestParam(required = false) Long reportId) {
    return quantPythonClient.stockTechnicalDetail(tsCode, tradeDate, reportId);
}
```

添加 `import java.util.Optional;`，保留现有异常处理器对上游异常的行为。

- [ ] **Step 4: 运行相关和完整 Java 测试。**

Run: `cd quantServer/quantServer && ./gradlew test`  
Expected: BUILD SUCCESSFUL。

- [ ] **Step 5: 提交该任务。**

```bash
git add quantServer/quantServer/src/main/java quantServer/quantServer/src/test/java
git commit -m "feat: proxy stock technical detail endpoint"
```

### Task 5: 前端详情页和可复制提示词

**Files:**
- Modify: `quantClient/main.js:46-429`
- Modify: `quantClient/index.html:1-360`
- Modify: `quantClient/styles.css:1-458`

- [ ] **Step 1: 将 `StockTable` 发出行点击事件，并添加失败前的静态检查。**

在组件声明增加 `emits: ['open-stock']`，将行改为：

```html
<tr v-for="row in rows" :key="row.ts_code + mode" class="stock-row" tabindex="0"
    @click="$emit('open-stock', row)"
    @keydown.enter="$emit('open-stock', row)">
```

先运行：`node --check quantClient/main.js`。预期现有代码通过；实现后同一命令仍须通过。手工验证行点击不应触发历史报告“打开”按钮。

- [ ] **Step 2: 加入详情请求状态与方法。**

在 `data()` 添加 `stockDetail: null`、`stockDetailLoading: false`、`stockDetailReturnTab: 'overview'`、`copyStatus: ''`；在 `pageTitle` 添加 `technical: '个股技术面'`。实现：

```javascript
async openStock(row) {
  this.stockDetailLoading = true; this.error = ''; this.stockDetailReturnTab = this.activeTab;
  try {
    this.stockDetail = await this.request(`/stocks/${encodeURIComponent(row.ts_code)}/technical?tradeDate=${this.latest.trade_date}&reportId=${this.latest.id}`);
    this.activeTab = 'technical';
  } catch (error) { this.error = error.message; }
  finally { this.stockDetailLoading = false; }
},
async copyPrompt() {
  try { await navigator.clipboard.writeText(this.stockDetail.prompt); this.copyStatus = '已复制'; }
  catch { this.copyStatus = '复制失败，请手动选择文本复制'; }
}
```

给全部六处 `<stock-table>`（总览 3、各策略页 3、板块代表股 1）加 `@open-stock="openStock"`。

- [ ] **Step 3: 增加纯 SVG 图形辅助函数和详情 DOM。**

在 methods 添加 `chartX(index, count, width)`, `chartY(value, min, max, height)`, `polylinePoints(candles, field)`；均须在 `max === min` 时返回居中值。`index.html` 添加仅在 `activeTab === 'technical'` 时显示的区块：返回按钮、股票名/代码/日期、`v-if="!stockDetailLoading && stockDetail"` 的 SVG K 线与 MA5/10/20/60 折线、成交量柱、五个指标面板，以及 `readonly` 提示词 textarea 和复制按钮。图中使用后端 `candles` 数组，遇到 `null` 指标断开折线，不自行计算指标。

- [ ] **Step 4: 添加样式和响应式规则。**

新增 `.stock-row`、`.technical-chart`、`.chart-legend`、`.indicator-grid`、`.indicator-card`、`.prompt-box`、`.copy-status` 样式；`.stock-row` 用 `cursor:pointer`，`:focus-visible` 有蓝色 outline；图表容器允许横向滚动；在 640px 媒体查询将指标网格收成一列。

- [ ] **Step 5: 验证前端。**

Run: `node --check quantClient/main.js`  
Expected: 退出码 0。

手动验证：运行现有服务后，点击三个策略池及板块代表股的任意股票；确认可返回原 tab，指标缺失显示 `--`，提示词包含实际股票代码，复制按钮显示成功或手动复制提示，后端未出现 AI 调用。

- [ ] **Step 6: 提交该任务。**

```bash
git add quantClient/main.js quantClient/index.html quantClient/styles.css
git commit -m "feat: add stock technical detail view"
```

### Task 6: 全量验证与文档同步

**Files:**
- Modify: `README_BACKEND.md`
- Modify: `DEPLOY_SERVER.md`（仅当部署文档列出 API 路由或前端缓存版本时）

- [ ] **Step 1: 文档化新端点。**

在 `README_BACKEND.md` 的 API 节加入：

```text
GET /api/stocks/600001.SH/technical?trade_date=20260615&report_id=1
```

说明返回 120 日技术指标与可复制 AI 提示词，不调用 AI；Spring 路径为 `/api/quant/stocks/600001.SH/technical?tradeDate=20260615&reportId=1`。

- [ ] **Step 2: 运行所有自动化验证。**

Run: `python -m unittest discover -s tests -v`  
Expected: PASS。

Run: `cd quantServer/quantServer && ./gradlew test`  
Expected: BUILD SUCCESSFUL。

Run: `node --check quantClient/main.js`  
Expected: 退出码 0。

- [ ] **Step 3: 检查变更完整性。**

Run: `git diff --check && git status --short`  
Expected: 无空白错误；仅包含本功能代码、测试和文档。

- [ ] **Step 4: 提交文档和验证完成状态。**

```bash
git add README_BACKEND.md DEPLOY_SERVER.md
git commit -m "docs: document stock technical detail API"
```

## Self-review

- 规格覆盖：Task 1-3 覆盖 120 日数据、指标、提示词、报告信号和异常；Task 4 覆盖 Spring；Task 5 覆盖行点击、SVG、分组指标、复制与窄屏；Task 6 覆盖回归及文档。
- 占位检查：计划没有 TBD/TODO 或未定义的接口名；端点、函数名和前端 query 参数在各任务中保持一致。
- 类型检查：Python API 使用 `trade_date/report_id` snake_case；Spring 与前端通过网关使用 `tradeDate/reportId` camelCase；服务方法返回 `Map<String, Object>`。
