from pydantic import BaseModel, ConfigDict, Field


class _PartialSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PressureSettingsUpdate(_PartialSettings):
    history_days: int | None = Field(default=None, ge=20, le=250)
    structure_days: int | None = Field(default=None, ge=10, le=120)
    pivot_left_days: int | None = Field(default=None, ge=1, le=10)
    pivot_right_days: int | None = Field(default=None, ge=1, le=10)
    min_touches: int | None = Field(default=None, ge=2, le=10)
    cluster_pct: float | None = Field(default=None, gt=0, le=5)
    cluster_atr_factor: float | None = Field(default=None, gt=0, le=2)
    cluster_max_pct: float | None = Field(default=None, gt=0, le=5)
    volume_surge_ratio: float | None = Field(default=None, gt=0, le=10)
    rejection_lookahead_days: int | None = Field(default=None, ge=1, le=20)
    rejection_min_pct: float | None = Field(default=None, gt=0, le=20)
    rejection_atr_factor: float | None = Field(default=None, gt=0, le=5)


class BreakoutSettingsUpdate(_PartialSettings):
    trigger_pct: float | None = Field(default=None, gt=0, le=5)
    trigger_atr_factor: float | None = Field(default=None, gt=0, le=2)
    confirm_pct: float | None = Field(default=None, gt=0, le=5)
    confirm_atr_factor: float | None = Field(default=None, gt=0, le=2)
    volume_confirm_ratio: float | None = Field(default=None, gt=0, le=10)
    close_position_min: float | None = Field(default=None, gt=0, le=1)
    long_upper_shadow_ratio: float | None = Field(default=None, gt=0, le=1)


class DistanceSettingsUpdate(_PartialSettings):
    critical_pct: float | None = Field(default=None, gt=0, le=20)
    waiting_pct: float | None = Field(default=None, gt=0, le=20)
    observe_pct: float | None = Field(default=None, gt=0, le=20)


class RiskRewardSettingsUpdate(_PartialSettings):
    minimum_ratio: float | None = Field(default=None, gt=0, le=20)
    good_ratio: float | None = Field(default=None, gt=0, le=20)
    excellent_ratio: float | None = Field(default=None, gt=0, le=20)


class NetworkSettingsUpdate(_PartialSettings):
    enrichment_limit: int | None = Field(default=None, ge=1, le=10)
    workers: int | None = Field(default=None, ge=1, le=10)
    request_timeout_seconds: int | None = Field(default=None, ge=1, le=30)
    stage_budget_seconds: int | None = Field(default=None, ge=1, le=60)
    total_budget_seconds: int | None = Field(default=None, ge=2, le=120)


class PositionStrategySettingsUpdate(_PartialSettings):
    pressure: PressureSettingsUpdate | None = None
    breakout: BreakoutSettingsUpdate | None = None
    distance: DistanceSettingsUpdate | None = None
    risk_reward: RiskRewardSettingsUpdate | None = None
    network: NetworkSettingsUpdate | None = None

    def update_payload(self) -> dict:
        return self.model_dump(exclude_none=True)
