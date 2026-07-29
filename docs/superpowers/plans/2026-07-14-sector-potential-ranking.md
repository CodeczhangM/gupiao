# Sector Potential Ranking Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a scan-backed “板块潜力” feature that ranks future sector upside potential and shows each sector’s leading stocks.

**Architecture:** Reuse `run_quant_scan()` data already loaded from the 100-trading-day market cache. Add a focused sector-potential scoring function in `strategy.py`, persist the new report field through `database.py`, return it from scan responses in `quant_service.py`, and render it in the static Vue dashboard.

**Tech Stack:** Python 3, pandas, FastAPI, PyMySQL, unittest, Vue 3 global build, static HTML/CSS/JS.

## Global Constraints

- Use existing `scan` flow and cached 100-workday market data; do not add a new external data source.
- Return both `short_score` and `swing_score`, with `potential_score = short_score * 0.45 + swing_score * 0.55`.
- Each high-potential sector must include 3-5 leader stocks when enough candidates exist.
- Old reports without `sector_potential` must decode as `[]`.
- Missing or insufficient history must not break scan; return `[]` or low-confidence rows.
- Do not change existing reversal, breakout, or first-limit strategy behavior.
- Write failing tests before production code.

---

## File Structure

- Modify `strategy.py`: add `rank_sector_potential()` plus small private helpers for normalization, history enrichment, sector metrics, and leader selection.
- Modify `quant_service.py`: call `rank_sector_potential()` and add `sector_potential` to the scan response.
- Modify `database.py`: add `sector_potential_json` column, save it, and decode it with backward compatibility.
- Modify `tests/test_advantage_stock_scoring.py`: add unit tests for scoring, leader selection, scan integration, and database decode compatibility.
- Modify `quantClient/main.js`: add `SectorPotentialTable` component, derived summary helpers, and page title support.
- Modify `quantClient/index.html`: add sidebar nav, metrics card, overview panel, and full “板块潜力” page.
- Modify `quantClient/styles.css`: add compact leader-list and score badge styles only if existing table styles are insufficient.

---

### Task 1: Sector Potential Ranking Function

**Files:**
- Modify: `strategy.py`
- Test: `tests/test_advantage_stock_scoring.py`

**Interfaces:**
- Consumes: `market_df: pd.DataFrame`, `history_df: pd.DataFrame`, optional `breakout_pool` and `first_limit_pool`.
- Produces: `rank_sector_potential(market_df, history_df, breakout_pool=None, first_limit_pool=None, limit=20, leaders_per_sector=5) -> pd.DataFrame`.

- [ ] **Step 1: Write failing tests**

Add these imports to the existing `from strategy import (...)` block in `tests/test_advantage_stock_scoring.py`:

```python
    rank_sector_potential,
```

Add these helpers near the existing fixture builders:

```python
def build_sector_potential_fixture():
    dates = pd.date_range("2026-02-02", periods=100, freq="B").strftime("%Y%m%d").tolist()
    sector_specs = {
        "能源强势": {"base": 10.0, "daily": 0.035, "latest": 6.0, "prev": -0.2, "amount": 600_000_000},
        "有色反包": {"base": 12.0, "daily": 0.015, "latest": 5.0, "prev": -4.8, "amount": 520_000_000},
        "科技弱势": {"base": 18.0, "daily": -0.01, "latest": -0.8, "prev": -5.5, "amount": 380_000_000},
    }
    market_rows = []
    history_rows = []
    for sector_index, (industry, spec) in enumerate(sector_specs.items()):
        for stock_index in range(10):
            ts_code = f"600{sector_index}{stock_index:02d}.SH"
            close = spec["base"]
            for date_index, trade_date in enumerate(dates):
                close = close * (1 + spec["daily"] / 100)
                pct_chg = spec["daily"]
                if date_index == len(dates) - 2:
                    pct_chg = spec["prev"]
                    close = close * (1 + pct_chg / 100)
                if date_index == len(dates) - 1:
                    pct_chg = spec["latest"] + (1.2 if stock_index == 0 else 0.0)
                    close = close * (1 + pct_chg / 100)
                history_rows.append({
                    "ts_code": ts_code,
                    "trade_date": trade_date,
                    "close": round(close, 4),
                    "high": round(close * 1.01, 4),
                    "low": round(close * 0.99, 4),
                    "vol": 100 + date_index + stock_index,
                    "amount": spec["amount"] * (1.5 if date_index == len(dates) - 1 else 1.0) / 10,
                    "pct_chg": pct_chg,
                })
            latest = history_rows[-1]
            market_rows.append({
                "ts_code": ts_code,
                "name": f"{industry}{stock_index}",
                "industry": industry,
                "close": latest["close"],
                "high": latest["high"],
                "low": latest["low"],
                "pct_chg": latest["pct_chg"],
                "turnover_rate": 8.0 + stock_index,
                "volume_ratio": 1.3 + stock_index / 10,
                "amount": latest["amount"],
                "total_mv": 1_000_000 + stock_index * 1000,
            })
    return pd.DataFrame(market_rows), pd.DataFrame(history_rows)
```

Add tests:

```python
class SectorPotentialRankingTests(unittest.TestCase):
    def test_rank_sector_potential_prefers_resilient_volume_expanding_sector(self):
        market, history = build_sector_potential_fixture()

        result = rank_sector_potential(market, history, limit=5, leaders_per_sector=3)

        self.assertFalse(result.empty)
        self.assertEqual(result.iloc[0]["industry_name"], "能源强势")
        self.assertGreater(result.iloc[0]["short_score"], result.iloc[1]["short_score"])
        self.assertEqual(result.iloc[0]["signal_type"], "短线延续")
        self.assertGreaterEqual(len(result.iloc[0]["leader_stocks"]), 3)

    def test_rank_sector_potential_labels_rebound_sector(self):
        market, history = build_sector_potential_fixture()

        result = rank_sector_potential(market, history, limit=5, leaders_per_sector=3)
        rebound = result[result["industry_name"] == "有色反包"].iloc[0]

        self.assertEqual(rebound["signal_type"], "超跌反包")
        self.assertGreater(rebound["avg_pct_chg"], 4)
        self.assertLess(rebound["prev_avg_pct_chg"], -3)

    def test_rank_sector_potential_marks_existing_pool_leaders(self):
        market, history = build_sector_potential_fixture()
        breakout_pool = pd.DataFrame([{"ts_code": "600000.SH"}])
        first_limit_pool = pd.DataFrame([{"ts_code": "600001.SH"}])

        result = rank_sector_potential(
            market,
            history,
            breakout_pool=breakout_pool,
            first_limit_pool=first_limit_pool,
            limit=5,
            leaders_per_sector=3,
        )
        leaders = result.iloc[0]["leader_stocks"]
        tags = {item["ts_code"]: item.get("pool_tag") for item in leaders}

        self.assertEqual(tags.get("600000.SH"), "趋势突破")
        self.assertEqual(tags.get("600001.SH"), "主升浪启动")

    def test_rank_sector_potential_returns_empty_for_missing_history(self):
        market, _history = build_sector_potential_fixture()

        result = rank_sector_potential(market, pd.DataFrame(), limit=5)

        self.assertTrue(result.empty)
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```bash
env HOME=/tmp python3 -m unittest tests.test_advantage_stock_scoring.SectorPotentialRankingTests -v
```

Expected: FAIL because `rank_sector_potential` cannot be imported from `strategy`.

- [ ] **Step 3: Implement minimal sector ranking**

Add to `strategy.py` near other sector helper functions:

```python
def _numeric_series(frame: pd.DataFrame, column: str, default=0.0) -> pd.Series:
    if column not in frame.columns:
        return pd.Series(default, index=frame.index, dtype="float64")
    return pd.to_numeric(frame[column], errors="coerce").fillna(default)


def _normalize_score(series: pd.Series, lower=None, upper=None) -> pd.Series:
    values = pd.to_numeric(series, errors="coerce").fillna(0.0)
    if lower is not None or upper is not None:
        values = values.clip(lower=lower, upper=upper)
    min_value = values.min()
    max_value = values.max()
    if pd.isna(min_value) or pd.isna(max_value) or max_value == min_value:
        return pd.Series(50.0, index=values.index)
    return ((values - min_value) / (max_value - min_value) * 100).clip(0, 100)


def _sector_return_by_window(history: pd.DataFrame, window: int) -> pd.DataFrame:
    rows = []
    for industry, group in history.groupby("industry"):
        pivot = (
            group.dropna(subset=["trade_date", "close"])
            .sort_values("trade_date")
            .groupby(["trade_date", "ts_code"])["close"]
            .last()
            .unstack()
        )
        if pivot.empty:
            continue
        latest = pivot.iloc[-1]
        if len(pivot) > window:
            base = pivot.iloc[-window - 1]
        else:
            base = pivot.iloc[0]
        stock_ret = (latest / base - 1) * 100
        rows.append({
            "industry": industry,
            f"ret_{window}": float(stock_ret.replace([float("inf"), float("-inf")], pd.NA).dropna().mean()) if not stock_ret.dropna().empty else None,
        })
    return pd.DataFrame(rows)


def _sector_position_by_window(history: pd.DataFrame, window: int) -> pd.DataFrame:
    rows = []
    for industry, group in history.groupby("industry"):
        tail = group.sort_values("trade_date").groupby("ts_code").tail(window)
        latest = tail.sort_values("trade_date").groupby("ts_code")["close"].last()
        low = tail.groupby("ts_code")["low"].min()
        high = tail.groupby("ts_code")["high"].max()
        position = ((latest - low) / (high - low).replace(0, pd.NA)).dropna()
        rows.append({
            "industry": industry,
            f"position_{window}": float(position.mean()) if not position.empty else None,
        })
    return pd.DataFrame(rows)


def _leader_pool_tags(breakout_pool=None, first_limit_pool=None) -> dict:
    tags = {}
    if isinstance(breakout_pool, pd.DataFrame) and "ts_code" in breakout_pool.columns:
        tags.update({str(code): "趋势突破" for code in breakout_pool["ts_code"].dropna().astype(str)})
    if isinstance(first_limit_pool, pd.DataFrame) and "ts_code" in first_limit_pool.columns:
        tags.update({str(code): "主升浪启动" for code in first_limit_pool["ts_code"].dropna().astype(str)})
    return tags


def _select_sector_leaders(
    sector_market: pd.DataFrame,
    leader_tags: dict,
    leaders_per_sector: int,
) -> list[dict]:
    if sector_market.empty:
        return []
    candidates = sector_market.copy()
    candidates["pct_chg_num"] = _numeric_series(candidates, "pct_chg")
    candidates["amount_num"] = _numeric_series(candidates, "amount")
    candidates["turnover_num"] = _numeric_series(candidates, "turnover_rate")
    candidates["volume_ratio_num"] = _numeric_series(candidates, "volume_ratio")
    candidates["pool_bonus"] = candidates["ts_code"].astype(str).map(lambda code: 12 if code in leader_tags else 0)
    candidates["leader_score"] = (
        _normalize_score(candidates["pct_chg_num"], -5, 10) * 0.38
        + _normalize_score(candidates["amount_num"]) * 0.24
        + _normalize_score(candidates["turnover_num"], 0, 20) * 0.18
        + _normalize_score(candidates["volume_ratio_num"], 0, 3) * 0.12
        + candidates["pool_bonus"]
    ).round(2)
    leaders = []
    for row in candidates.sort_values("leader_score", ascending=False).head(leaders_per_sector).to_dict("records"):
        pool_tag = leader_tags.get(str(row.get("ts_code")))
        reason_parts = [f"涨幅{row.get('pct_chg_num', 0):.2f}%"]
        if pool_tag:
            reason_parts.append(pool_tag)
        if row.get("volume_ratio_num", 0) >= 1.5:
            reason_parts.append("量比活跃")
        leaders.append({
            "ts_code": row.get("ts_code"),
            "name": row.get("name"),
            "pct_chg": row.get("pct_chg"),
            "close": row.get("close"),
            "turnover_rate": row.get("turnover_rate"),
            "volume_ratio": row.get("volume_ratio"),
            "amount": row.get("amount"),
            "leader_score": row.get("leader_score"),
            "leader_reason": "、".join(reason_parts),
            "pool_tag": pool_tag or "",
        })
    return leaders


def rank_sector_potential(
    market_df: pd.DataFrame,
    history_df: pd.DataFrame,
    breakout_pool: pd.DataFrame | None = None,
    first_limit_pool: pd.DataFrame | None = None,
    limit: int = 20,
    leaders_per_sector: int = 5,
) -> pd.DataFrame:
    if market_df is None or market_df.empty or history_df is None or history_df.empty:
        return pd.DataFrame()
    if "industry" not in market_df.columns or "ts_code" not in market_df.columns:
        return pd.DataFrame()

    market = market_df.copy()
    market["industry"] = market["industry"].fillna("")
    market = market[market["industry"] != ""].copy()
    if market.empty:
        return pd.DataFrame()

    for column in ["pct_chg", "amount", "turnover_rate", "volume_ratio", "close", "high", "low"]:
        market[column] = _numeric_series(market, column)

    info_cols = ["ts_code", "industry", "name"]
    history = history_df.copy()
    history["ts_code"] = history["ts_code"].astype(str)
    history = history.merge(market[info_cols].drop_duplicates("ts_code"), on="ts_code", how="inner")
    for column in ["pct_chg", "amount", "close", "high", "low"]:
        history[column] = _numeric_series(history, column)
    history["trade_date"] = history["trade_date"].astype(str)
    dates = sorted(history["trade_date"].dropna().unique().tolist())
    if len(dates) < 2:
        return pd.DataFrame()

    latest_date = dates[-1]
    prev_date = dates[-2]
    previous = history[history["trade_date"] == prev_date]

    latest_group = market.groupby("industry").agg(
        stock_count=("ts_code", "count"),
        avg_pct_chg=("pct_chg", "mean"),
        up_ratio=("pct_chg", lambda s: float((s > 0).mean())),
        strong_ratio=("pct_chg", lambda s: float((s >= 5).mean())),
        limit_up_count=("pct_chg", lambda s: int((s >= 9.8).sum())),
        amount_sum=("amount", "sum"),
        turnover_rate=("turnover_rate", "mean"),
        volume_ratio=("volume_ratio", "mean"),
    ).reset_index()
    prev_group = previous.groupby("industry").agg(
        prev_avg_pct_chg=("pct_chg", "mean"),
        prev_amount_sum=("amount", "sum"),
    ).reset_index()
    result = latest_group.merge(prev_group, on="industry", how="left")
    result = result[result["stock_count"] >= 8].copy()
    if result.empty:
        return pd.DataFrame()

    for window in [5, 20, 60, 100]:
        result = result.merge(_sector_return_by_window(history, window), on="industry", how="left")
    for window in [60, 100]:
        result = result.merge(_sector_position_by_window(history, window), on="industry", how="left")

    market_return_20 = float(result["ret_20"].mean()) if "ret_20" in result else 0.0
    market_return_60 = float(result["ret_60"].mean()) if "ret_60" in result else 0.0
    result["rs_20"] = result["ret_20"] - market_return_20
    result["rs_60"] = result["ret_60"] - market_return_60
    result["amount_expand_rate"] = (result["amount_sum"] / result["prev_amount_sum"].replace(0, pd.NA)).fillna(1.0)
    result["resilience_score"] = result["prev_avg_pct_chg"].fillna(0).map(lambda value: 15 if value >= 0 else 8 if value >= -1 else 0)

    short_raw = (
        _normalize_score(result["avg_pct_chg"], -5, 8) * 0.28
        + _normalize_score(result["up_ratio"], 0, 1) * 0.18
        + _normalize_score(result["strong_ratio"], 0, 0.7) * 0.18
        + _normalize_score(result["limit_up_count"], 0, 5) * 0.12
        + _normalize_score(result["amount_expand_rate"], 0.5, 2.0) * 0.14
        + _normalize_score(result["volume_ratio"], 0, 2.5) * 0.06
        + result["resilience_score"]
    )
    swing_raw = (
        _normalize_score(result["ret_20"], -20, 35) * 0.24
        + _normalize_score(result["ret_60"], -30, 55) * 0.18
        + _normalize_score(result["rs_20"], -20, 30) * 0.18
        + _normalize_score(result["rs_60"], -30, 40) * 0.16
        + _normalize_score(result["ret_5"], -10, 18) * 0.10
        + _normalize_score(result["position_60"].fillna(0.5), 0, 1) * 0.08
        + _normalize_score(result["up_ratio"], 0, 1) * 0.06
    )
    overheat_penalty = ((result["position_60"].fillna(0) > 0.92) & (result["ret_20"].fillna(0) > 25)).astype(int) * 8
    result["short_score"] = short_raw.clip(0, 100).round(2)
    result["swing_score"] = (swing_raw - overheat_penalty).clip(0, 100).round(2)
    result["potential_score"] = (result["short_score"] * 0.45 + result["swing_score"] * 0.55).round(2)

    def signal_type(row):
        if row["prev_avg_pct_chg"] <= -3 and row["avg_pct_chg"] >= 3:
            return "超跌反包"
        if row["short_score"] >= 70 and row["avg_pct_chg"] >= 2:
            return "短线延续"
        if row["swing_score"] >= 70 and row["rs_20"] > 0:
            return "波段主线"
        return "观察"

    result["signal_type"] = result.apply(signal_type, axis=1)
    leader_tags = _leader_pool_tags(breakout_pool, first_limit_pool)
    result["leader_stocks"] = result["industry"].map(
        lambda industry: _select_sector_leaders(
            market[market["industry"] == industry],
            leader_tags,
            leaders_per_sector,
        )
    )
    result["reason"] = result.apply(
        lambda row: (
            f"{row['signal_type']}：今日均涨{row['avg_pct_chg']:.2f}%，"
            f"上涨率{row['up_ratio'] * 100:.0f}%，放量{row['amount_expand_rate']:.2f}倍"
        ),
        axis=1,
    )
    result = result.sort_values("potential_score", ascending=False).head(limit).reset_index(drop=True)
    result["rank"] = result.index + 1
    result = result.rename(columns={"industry": "industry_name"})
    columns = [
        "rank", "industry_name", "potential_score", "short_score", "swing_score", "signal_type",
        "avg_pct_chg", "prev_avg_pct_chg", "up_ratio", "strong_ratio", "limit_up_count",
        "amount_expand_rate", "volume_ratio", "turnover_rate", "ret_5", "ret_20", "ret_60",
        "ret_100", "rs_20", "rs_60", "position_60", "position_100", "leader_stocks", "reason",
    ]
    return result.reindex(columns=columns)
```

- [ ] **Step 4: Run tests to verify pass**

Run:

```bash
env HOME=/tmp python3 -m unittest tests.test_advantage_stock_scoring.SectorPotentialRankingTests -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

Run:

```bash
git add strategy.py tests/test_advantage_stock_scoring.py
git commit -m "feat: rank sector potential"
```

---

### Task 2: Add Sector Potential To Scan And Reports

**Files:**
- Modify: `quant_service.py`
- Modify: `database.py`
- Test: `tests/test_advantage_stock_scoring.py`

**Interfaces:**
- Consumes: `rank_sector_potential()` from Task 1.
- Produces: `report["sector_potential"]` from `run_quant_scan()`, and persisted `sector_potential_json` in `quant_reports`.

- [ ] **Step 1: Write failing tests**

Add `rank_sector_potential` patch tests near existing `quant_service` tests in `tests/test_advantage_stock_scoring.py`:

```python
    @patch("quant_service.rank_sector_potential", return_value=pd.DataFrame([{
        "rank": 1,
        "industry_name": "能源强势",
        "potential_score": 88.0,
        "short_score": 91.0,
        "swing_score": 85.0,
        "leader_stocks": [],
    }]))
    @patch("quant_service.get_moneyflow_summary", return_value={})
    @patch("quant_service.get_sector_data", return_value=None)
    @patch("quant_service.select_stock_pools", return_value={
        "reversal": pd.DataFrame(),
        "breakout": pd.DataFrame(),
        "first_limit": pd.DataFrame(),
    })
    @patch("quant_service.pick_strong_base_candidates", return_value=pd.DataFrame([{"ts_code": "600001.SH"}]))
    @patch("quant_service.get_recent_daily_data", return_value=pd.DataFrame([{
        "ts_code": "600001.SH", "trade_date": "20260714", "close": 10, "high": 10, "low": 9, "vol": 1, "amount": 1, "pct_chg": 1,
    }]))
    @patch("quant_service.get_market_data", return_value=(build_market(), "20260714"))
    def test_scan_returns_sector_potential(
        self,
        _get_market_data,
        _get_recent_daily_data,
        _pick_strong_base_candidates,
        _select_stock_pools,
        _get_sector_data,
        _get_moneyflow_summary,
        rank_sector_potential_mock,
    ):
        report = quant_service.run_quant_scan(include_ai=False, limit=20)

        self.assertEqual(report["sector_potential"][0]["industry_name"], "能源强势")
        rank_sector_potential_mock.assert_called_once()
```

Add database decode compatibility test:

```python
from database import _decode_report


class DatabaseSectorPotentialTests(unittest.TestCase):
    def test_decode_report_defaults_missing_sector_potential_to_empty_list(self):
        row = {
            "id": 1,
            "trade_date": "20260714",
            "status": "success",
            "include_ai": 0,
            "strong_json": "[]",
            "dip_json": "[]",
            "sectors_json": "[]",
            "rep_stocks_json": "[]",
            "moneyflow_json": "{}",
            "ai_analysis": None,
            "error_message": None,
            "created_at": pd.Timestamp("2026-07-14 15:00:00").to_pydatetime(),
        }

        decoded = _decode_report(row)

        self.assertEqual(decoded["sector_potential"], [])
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```bash
env HOME=/tmp python3 -m unittest tests.test_advantage_stock_scoring -v
```

Expected: FAIL because `quant_service.rank_sector_potential` is not imported/called, and `_decode_report()` does not include `sector_potential`.

- [ ] **Step 3: Wire scan response**

Modify `quant_service.py` imports:

```python
from strategy import (
    format_for_ai,
    format_sectors_for_ai,
    pick_dip_sectors,
    pick_sector_tail_buy_stocks,
    pick_strong_base_candidates,
    rank_sector_potential,
    select_stock_pools,
)
```

After `first_limit = pools["first_limit"]`, add:

```python
    sector_potential = rank_sector_potential(
        df,
        hist_df,
        breakout_pool=breakout,
        first_limit_pool=first_limit,
        limit=limit,
    )
```

In the returned dict, add:

```python
        "sector_potential": dataframe_to_records(sector_potential, limit),
```

- [ ] **Step 4: Persist report field**

Modify `database.py` table creation SQL after `moneyflow_json LONGTEXT NULL,`:

```sql
        sector_potential_json LONGTEXT NULL,
```

In `init_db()`, after the existing `moneyflow_json` migration, add:

```python
            cursor.execute("SHOW COLUMNS FROM quant_reports LIKE 'sector_potential_json'")
            if cursor.fetchone() is None:
                cursor.execute("ALTER TABLE quant_reports ADD COLUMN sector_potential_json LONGTEXT NULL AFTER moneyflow_json")
```

Modify the insert column list:

```sql
        rep_stocks_json, moneyflow_json, sector_potential_json, ai_analysis, error_message, created_at
```

Modify the values list:

```sql
        %(sectors_json)s, %(rep_stocks_json)s, %(moneyflow_json)s, %(sector_potential_json)s,
        %(ai_analysis)s, %(error_message)s, %(created_at)s
```

Add to `payload`:

```python
        "sector_potential_json": json.dumps(report.get("sector_potential", []), ensure_ascii=False),
```

Add to `_decode_report()`:

```python
        "sector_potential": json.loads(row.get("sector_potential_json") or "[]"),
```

- [ ] **Step 5: Run tests to verify pass**

Run:

```bash
env HOME=/tmp python3 -m unittest tests.test_advantage_stock_scoring -v
```

Expected: PASS.

- [ ] **Step 6: Commit**

Run:

```bash
git add quant_service.py database.py tests/test_advantage_stock_scoring.py
git commit -m "feat: include sector potential in scan reports"
```

---

### Task 3: Frontend Sector Potential Page

**Files:**
- Modify: `quantClient/main.js`
- Modify: `quantClient/index.html`
- Modify: `quantClient/styles.css`

**Interfaces:**
- Consumes: `latest.sector_potential: Array<object>` from scan/latest report responses.
- Produces: `SectorPotentialTable` Vue component and `activeTab === "sector_potential"` page.

- [ ] **Step 1: Add Vue component**

In `quantClient/main.js`, after `SectorTable`, add:

```javascript
const SectorPotentialTable = {
  props: {
    rows: { type: Array, default: () => [] },
    compact: { type: Boolean, default: false },
  },
  methods: {
    formatNumber,
    signedClass,
    leaderText(leader) {
      const parts = [leader.name || leader.ts_code || '--'];
      if (leader.pct_chg !== undefined && leader.pct_chg !== null) parts.push(`${formatNumber(leader.pct_chg)}%`);
      if (leader.pool_tag) parts.push(leader.pool_tag);
      return parts.join(' · ');
    },
  },
  template: `
    <div class="table-wrap sector-potential-table" :class="{ 'compact-table': compact }">
      <table>
        <thead>
          <tr>
            <th>排名</th>
            <th>板块</th>
            <th>综合分</th>
            <th>短线</th>
            <th>波段</th>
            <th>信号</th>
            <th v-if="!compact">今日涨幅</th>
            <th v-if="!compact">上涨率</th>
            <th v-if="!compact">放量</th>
            <th v-if="!compact">RS20</th>
            <th v-if="!compact">RS60</th>
            <th>龙头股</th>
            <th v-if="!compact">理由</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in rows" :key="row.industry_name">
            <td>{{ row.rank || '--' }}</td>
            <td><strong>{{ row.industry_name || '--' }}</strong></td>
            <td><strong>{{ formatNumber(row.potential_score) }}</strong></td>
            <td>{{ formatNumber(row.short_score) }}</td>
            <td>{{ formatNumber(row.swing_score) }}</td>
            <td><span class="signal-badge">{{ row.signal_type || '观察' }}</span></td>
            <td v-if="!compact" :class="signedClass(row.avg_pct_chg)">{{ formatNumber(row.avg_pct_chg) }}%</td>
            <td v-if="!compact">{{ formatNumber((row.up_ratio || 0) * 100, 0) }}%</td>
            <td v-if="!compact">{{ formatNumber(row.amount_expand_rate) }}倍</td>
            <td v-if="!compact" :class="signedClass(row.rs_20)">{{ formatNumber(row.rs_20) }}%</td>
            <td v-if="!compact" :class="signedClass(row.rs_60)">{{ formatNumber(row.rs_60) }}%</td>
            <td>
              <div class="leader-list">
                <span v-for="leader in (row.leader_stocks || []).slice(0, compact ? 2 : 5)" :key="leader.ts_code">
                  {{ leaderText(leader) }}
                </span>
                <em v-if="!row.leader_stocks || row.leader_stocks.length === 0">暂无</em>
              </div>
            </td>
            <td v-if="!compact" class="reason-cell">{{ row.reason || '--' }}</td>
          </tr>
          <tr v-if="rows.length === 0">
            <td :colspan="compact ? 7 : 13" class="empty">暂无板块潜力数据</td>
          </tr>
        </tbody>
      </table>
    </div>
  `,
};
```

Register the component:

```javascript
  components: {
    StockTable,
    SectorTable,
    SectorPotentialTable,
    BacktestTable,
  },
```

Add computed helpers:

```javascript
    sectorPotentialRows() {
      return Array.isArray(this.latest.sector_potential) ? this.latest.sector_potential : [];
    },
    topShortSector() {
      const rows = [...this.sectorPotentialRows].sort((a, b) => Number(b.short_score || 0) - Number(a.short_score || 0));
      return rows.length ? rows[0].industry_name : '--';
    },
    topSwingSector() {
      const rows = [...this.sectorPotentialRows].sort((a, b) => Number(b.swing_score || 0) - Number(a.swing_score || 0));
      return rows.length ? rows[0].industry_name : '--';
    },
```

Add page title:

```javascript
        sector_potential: '板块潜力',
```

- [ ] **Step 2: Add HTML navigation and panels**

In `quantClient/index.html`, add a sidebar button after the existing “板块” button:

```html
        <button :class="{ active: activeTab === 'sector_potential' }" @click="activeTab = 'sector_potential'">板块潜力</button>
```

In the metrics section, add after “强势板块”:

```html
        <article>
          <span>潜力板块</span>
          <strong>{{ listCount(sectorPotentialRows) }}</strong>
        </article>
```

In the overview grid, before the AI panel, add:

```html
        <div class="panel wide">
          <div class="panel-head">
            <h3>板块潜力 Top</h3>
            <button @click="activeTab = 'sector_potential'">查看全部</button>
          </div>
          <sector-potential-table :rows="topRows(sectorPotentialRows, 8)" compact></sector-potential-table>
        </div>
```

After the existing `activeTab === 'sectors'` section, add:

```html
      <section v-show="activeTab === 'sector_potential'" class="grid two">
        <div class="panel wide">
          <div class="panel-head">
            <h3>板块上涨潜力排序</h3>
            <span>{{ listCount(sectorPotentialRows) }} 条 · 短线 {{ topShortSector }} · 波段 {{ topSwingSector }}</span>
          </div>
          <sector-potential-table :rows="sectorPotentialRows"></sector-potential-table>
        </div>
      </section>
```

- [ ] **Step 3: Add styles**

Append to `quantClient/styles.css`:

```css
.sector-potential-table .reason-cell {
  min-width: 220px;
  color: var(--muted);
}

.signal-badge {
  display: inline-flex;
  align-items: center;
  min-height: 24px;
  padding: 0 8px;
  border-radius: 6px;
  background: #eef3f6;
  color: var(--blue);
  font-weight: 700;
  white-space: nowrap;
}

.leader-list {
  display: grid;
  gap: 4px;
  min-width: 180px;
}

.leader-list span {
  color: var(--ink);
  font-size: 12px;
  line-height: 1.35;
}

.leader-list em {
  color: var(--muted);
  font-style: normal;
}
```

- [ ] **Step 4: Run a static smoke check**

Run:

```bash
node --check quantClient/main.js
```

Expected: no syntax errors.

- [ ] **Step 5: Commit**

Run:

```bash
git add quantClient/main.js quantClient/index.html quantClient/styles.css
git commit -m "feat: show sector potential dashboard"
```

---

### Task 4: End-To-End Verification

**Files:**
- No production edits expected unless verification finds an issue.

**Interfaces:**
- Consumes: all prior tasks.
- Produces: verified scan response and frontend smoke result.

- [ ] **Step 1: Run focused Python tests**

Run:

```bash
env HOME=/tmp python3 -m unittest tests.test_advantage_stock_scoring.SectorPotentialRankingTests -v
```

Expected: PASS.

- [ ] **Step 2: Run broader Python strategy tests**

Run:

```bash
env HOME=/tmp python3 -m unittest tests.test_advantage_stock_scoring -v
```

Expected: PASS.

- [ ] **Step 3: Run frontend syntax check**

Run:

```bash
node --check quantClient/main.js
```

Expected: no syntax errors.

- [ ] **Step 4: Run scan using cached data**

Run:

```bash
env HOME=/tmp python3 -c "from quant_service import run_quant_scan; r=run_quant_scan(include_ai=False, limit=10); print(r['trade_date']); print(len(r.get('sector_potential', []))); print([(x.get('industry_name'), x.get('potential_score'), x.get('signal_type')) for x in r.get('sector_potential', [])[:5]])"
```

Expected: command exits 0 and prints a non-error report. If cache/env data is unavailable, record the exact error in the final handoff.

- [ ] **Step 5: Review git diff**

Run:

```bash
git diff --stat HEAD
git diff -- strategy.py quant_service.py database.py quantClient/main.js quantClient/index.html quantClient/styles.css tests/test_advantage_stock_scoring.py
```

Expected: only sector-potential-related changes are present.

- [ ] **Step 6: Commit verification fixes if any**

If Task 4 required fixes, run:

```bash
git add strategy.py quant_service.py database.py quantClient/main.js quantClient/index.html quantClient/styles.css tests/test_advantage_stock_scoring.py
git commit -m "fix: verify sector potential ranking"
```

If no fixes were required, do not create an empty commit.

---

## Self-Review

- Spec coverage: Task 1 implements scoring and leaders; Task 2 adds scan/report/database compatibility; Task 3 adds frontend page and overview; Task 4 verifies tests and live scan.
- Placeholder scan: no placeholder or deferred implementation language remains.
- Type consistency: `rank_sector_potential()` returns `sector_potential` rows using the field names consumed by `SectorPotentialTable`.
