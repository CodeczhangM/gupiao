# Financial Event Filters For Free Review Design

## Goal

Extend the existing free-review stock screener with financial-report event factors. The screener should identify stocks whose latest reported deducted net profit is strong, expose those factors as filters and sortable columns, and combine the stock reaction after announcement with sector context for scoring.

The feature stays inside the current free-review workflow. It does not create a separate page or separate stock pool in the first version.

## User-Facing Behavior

The free-review screen adds a financial-event filter group.

Default filters are available but not forced:

- Deducted net profit minimum, default preset: `50000000`.
- Deducted net profit growth minimum, default preset: `50`.
- Financial event hit, a boolean filter for stocks that pass both default thresholds.
- Announcement return ranges for 3, 5, and 10 trading days after the announcement.
- Financial event score range.

The table/export can show these new fields:

- `deducted_netprofit`
- `deducted_netprofit_growth`
- `financial_growth_basis`
- `deducted_netprofit_threshold_hit`, stored as `0` or `1`
- `financial_growth_threshold_hit`, stored as `0` or `1`
- `financial_event_hit`, stored as `0` or `1`
- `financial_statement_end_date`
- `financial_statement_ann_date`
- `announcement_return_3d`
- `announcement_return_5d`
- `announcement_return_10d`
- `announcement_max_return_10d`
- `financial_event_score`
- `sector_financial_event_score`

Users can sort by `financial_event_score`, `sector_financial_event_score`, `deducted_netprofit`, `deducted_netprofit_growth`, or announcement-return fields.

## Financial Data

The existing `financial_indicator_cache` is extended because Tushare financial indicators include `profit_dedt`, the deducted net profit field needed for this feature. The current project already syncs `fina_indicator_vip`; the first implementation adds `profit_dedt` to that cache instead of creating a separate financial-event cache.

The existing key remains `(ts_code, end_date, ann_date)`, preserving point-in-time behavior. Schema initialization adds a nullable `profit_dedt` column for existing databases. New syncs request it in `FINANCIAL_FIELDS`.

If `profit_dedt` is not returned by the deployed account or data provider, the free-review build continues with null financial-event fields and records a warning. A later fallback can be added for `express` or other announcement APIs, but this first version does not mix incompatible profit definitions silently.

## Growth Basis

Growth is computed with this priority:

1. `single_quarter_qoq`: Convert cumulative report-period `profit_dedt` into single-quarter deducted net profit by subtracting the previous period in the same fiscal year, then compare with the previous quarter single-quarter value.
2. `cumulative_period`: If single-quarter data is incomplete, compare the latest cumulative `profit_dedt` with the previous available report period.
3. `unavailable`: If neither comparison is possible, growth remains null.

The chosen basis is stored in `financial_growth_basis`.

The threshold flags are:

- `deducted_netprofit_threshold_hit`: latest usable deducted net profit is at least 50 million yuan.
- `financial_growth_threshold_hit`: growth is at least 50%.
- `financial_event_hit`: both threshold flags are true.

Negative or zero previous-period profit is handled conservatively:

- If previous profit is positive, growth is `(current / previous - 1) * 100`.
- If previous profit is zero or negative and current profit is positive, growth is null and a score bonus is given only for the absolute-profit threshold. This avoids misleading infinite growth percentages.

## Announcement Price Reaction

Announcement reaction is calculated from cached daily history around `ann_date`.

For each stock:

- Find the first trading day on or after `ann_date`.
- Use the previous close before that trading day as the baseline when available.
- Compute cumulative close return after 3, 5, and 10 trading days.
- Compute max high return within 10 trading days.

These values are informational and filterable. They do not remove stocks by default. This keeps newly announced strong reports visible even when price has not reacted yet.

## Scoring

`financial_event_score` is a 0-100 event score:

- 35 points for deducted net profit amount, scaled from 50 million to 500 million yuan.
- 35 points for deducted net profit growth, scaled from 50% to 200%.
- 15 points for announcement reaction, using the best of 3/5/10 day returns and max 10-day high return.
- 15 points for data quality and recency, favoring complete single-quarter basis and announcements within the latest two report periods.

`sector_financial_event_score` is calculated by industry:

- Average `financial_event_score`.
- Ratio of stocks with `financial_event_hit`.
- Existing free-review sector average total score.

The stock-level free-review `total_score` receives a modest additive event contribution so the original technical and quality model remains stable:

`total_score = existing_total + financial_event_score * 0.12 + sector_financial_event_score * 0.06`, clipped to 100.

The new contribution is also exposed separately so users can sort/filter without relying only on the blended score.

## Backend Changes

`financial_cache.py`:

- Add `profit_dedt` to `FINANCIAL_NUMERIC_FIELDS`.
- Add an idempotent migration for the nullable `profit_dedt` column.
- Keep existing `sync_financial_indicators(...)` and `load_financial_as_of(...)` as the only financial-data path.

`free_review_service.py`:

- Continue running the existing financial-indicator sync.
- Add a warning if the loaded financial frame has no usable `profit_dedt` coverage.

`free_review_scoring.py`:

- Add financial-event derivation helpers.
- Join event fields into the review snapshot.
- Compute stock-level and sector-level event scores.
- Preserve existing score columns and score version behavior.

`free_review_repository.py` and `free_review_models.py`:

- Add new text, numeric, and integer `0`/`1` flag columns.
- Add new fields to allowed range filters and sort fields.
- Allow filtering `financial_event_hit` and related flags through numeric ranges, where `min=1` means true.
- Include fields in export.

`app.py`:

- No new endpoint is required. Existing free-review build, query, sectors, meta, and export endpoints are extended.

`quantClient`:

- Add the financial-event filter group to the free-review UI.
- Add column metadata for new fields.
- Add preset action for "财报事件命中": `financial_event_hit` minimum `1`.
- Add optional numeric presets for profit >= 50 million and growth >= 50%.

## Error Handling

If financial indicator sync succeeds but `profit_dedt` is unavailable:

- The free-review build continues.
- Build status warnings include the missing or empty field.
- Financial-event fields are null or false.
- `financial_event_score` defaults to 0.

If announcement-day price history is incomplete:

- Return fields stay null.
- Scoring skips the reaction component.
- Missing history does not block the build.

If a stock has multiple announcements for the same period:

- Prefer rows with `update_flag='1'`.
- Otherwise prefer the latest `ann_date` that is not after the review trade date.

## Testing

Unit tests cover:

- Financial schema migration adds `profit_dedt` and sync skips completed periods.
- Point-in-time event loading excludes future announcements.
- Single-quarter deducted net profit derivation from cumulative reports.
- Fallback to cumulative-period comparison when single-quarter data is incomplete.
- Conservative handling of zero or negative previous profit.
- Announcement reaction calculation from daily OHLC history.
- Snapshot includes new event fields and score columns.
- Query model accepts new range/sort fields.
- Repository persists and returns new columns.
- Frontend query serialization includes financial-event filters and new columns.

## Out Of Scope

- A separate financial-event page.
- Intraday reaction around announcement time.
- Parsing raw exchange announcement text.
- Mixing `express` or forecast profit fields with financial-indicator `profit_dedt` in the same score without an explicit source label.
