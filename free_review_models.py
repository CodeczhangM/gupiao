from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


ALLOWED_RANGE_FIELDS = {
    "total_score", "trend_score", "volume_price_score", "momentum_score",
    "valuation_score", "financial_quality_score", "financial_growth_score",
    "risk_penalty", "data_completeness",
    "pct_chg", "amount", "turnover_rate", "turnover_rate_f",
    "volume_ratio", "pe", "pe_ttm", "pb", "ps", "ps_ttm", "dv_ttm",
    "total_mv", "circ_mv",
    "ret_5", "ret_10", "ret_20", "ret_60",
    "drawdown_20", "drawdown_60",
    "vol_ratio_ma5", "vol_ratio_ma10", "vol_ratio_ma20",
    "ma20_slope", "ma60_slope", "rsi6", "rsi12", "rsi24", "atr_pct",
    "roe", "roe_dt", "roa", "roic", "grossprofit_margin",
    "netprofit_margin", "current_ratio", "debt_to_assets",
    "ocf_to_or", "tr_yoy", "netprofit_yoy", "dt_netprofit_yoy",
    "ocf_yoy",
}
ALLOWED_SORT_FIELDS = ALLOWED_RANGE_FIELDS | {
    "ts_code", "name", "industry", "financial_improvement_count",
}


class ReviewRange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    min: float | None = None
    max: float | None = None

    @model_validator(mode="after")
    def validate_bounds(self):
        if self.min is not None and self.max is not None and self.min > self.max:
            raise ValueError("筛选下限不能大于上限")
        return self


class FreeReviewQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    trade_date: str | None = None
    score_version: Literal["free-review-v1"] | None = None
    keyword: str | None = None
    industries: list[str] = Field(default_factory=list)
    areas: list[str] = Field(default_factory=list)
    markets: list[str] = Field(default_factory=list)
    profit_state: Literal["profit", "loss"] | None = None
    volume_state: Literal["active", "normal", "quiet"] | None = None
    growth_state: Literal["growth", "stable", "decline"] | None = None
    ranges: dict[str, ReviewRange] = Field(default_factory=dict)
    sort_by: str = "total_score"
    sort_direction: Literal["asc", "desc"] = "desc"
    page: int = Field(default=1, ge=1)
    page_size: int = 50
    visible_columns: list[str] = Field(default_factory=list)

    @field_validator("trade_date")
    @classmethod
    def validate_trade_date(cls, value: str | None):
        if value is not None and (len(value) != 8 or not value.isdigit()):
            raise ValueError("trade_date 必须为 YYYYMMDD")
        return value

    @field_validator("keyword")
    @classmethod
    def normalize_keyword(cls, value: str | None):
        text = (value or "").strip()
        return text[:64] or None

    @field_validator("ranges")
    @classmethod
    def validate_ranges(cls, value: dict[str, ReviewRange]):
        unknown = sorted(set(value) - ALLOWED_RANGE_FIELDS)
        if unknown:
            raise ValueError(f"不支持的筛选字段: {', '.join(unknown)}")
        return value

    @field_validator("sort_by")
    @classmethod
    def validate_sort_field(cls, value: str):
        if value not in ALLOWED_SORT_FIELDS:
            raise ValueError(f"不支持的排序字段: {value}")
        return value

    @field_validator("page_size")
    @classmethod
    def validate_page_size(cls, value: int):
        if value not in {50, 100, 200}:
            raise ValueError("page_size 只支持 50、100 或 200")
        return value
