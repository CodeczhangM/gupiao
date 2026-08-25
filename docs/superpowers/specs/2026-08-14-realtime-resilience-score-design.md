# Realtime Resilience Score Design

## Goal

Add a realtime table column named `抗跌力` that scores whether a stock has historically resisted market weakness over the previous 20 trading days.

## Data Source

Use the existing `history` dataframe already loaded by realtime info with recent daily bars. The score uses daily `pct_chg` rows only and excludes the current realtime trading date when possible.

## Scoring

For each stock, collect up to 20 latest trading-day rows, ordered oldest to newest. Build an equal-weight daily market benchmark from allowed mainboard non-ST stocks for each `trade_date`, then compute `relative = stock_pct_chg - market_pct_chg`.

Weights increase from older to newer rows: `1..n`.

Expose:

- `historical_resilience_score`: 0-100 score.
- `historical_resilience_label`: `强抗跌`, `抗跌`, `一般`, `偏弱`, or `历史不足`.
- `historical_resilience_reason`: explanation containing weighted relative strength, down-market relative strength, beat ratio, and sample count.
- Supporting numeric fields for debugging and future sorting.

Score formula:

`50 + weighted_relative * 8 + down_market_relative * 10 + (beat_ratio - 0.5) * 30`

Clamp to `0..100`. If there are fewer than 5 usable rows or no benchmark, leave score empty and label as `历史不足`.

## UI

Add one realtime confluence table column named `抗跌力`. Show `82分 · 强抗跌` when available and `--` otherwise. Use the reason as the cell title.

## Testing

Add backend tests for weighted scoring and insufficient history. Add frontend utility tests for display text and missing values.
