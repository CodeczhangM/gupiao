from pydantic import BaseModel, ConfigDict, Field, model_validator


class MacdSettingsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fast_period: int = Field(ge=2, le=120)
    slow_period: int = Field(ge=2, le=120)
    signal_period: int = Field(ge=2, le=120)

    @model_validator(mode="after")
    def validate_period_order(self):
        if self.fast_period >= self.slow_period:
            raise ValueError("快线周期必须小于慢线周期")
        return self
