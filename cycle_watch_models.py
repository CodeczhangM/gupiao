from pydantic import BaseModel, Field


class CycleWatchCreateRequest(BaseModel):
    ts_code: str = Field(min_length=6, max_length=9)
    note: str | None = Field(default=None, max_length=500)
    planned_low_price: float | None = Field(default=None, gt=0)
    planned_high_price: float | None = Field(default=None, gt=0)


class CycleWatchUpdateRequest(BaseModel):
    note: str | None = Field(default=None, max_length=500)
    planned_low_price: float | None = Field(default=None, gt=0)
    planned_high_price: float | None = Field(default=None, gt=0)
    enabled: bool | None = None


class CycleWatchCheckRequest(BaseModel):
    ts_code: str | None = Field(default=None, min_length=6, max_length=9)
    schedule_slot: str | None = Field(
        default=None,
        pattern=r"^(0935|1035|1125|1330|1430|1455|manual)$",
    )


class CycleWatchReadAlertsRequest(BaseModel):
    trade_date: str | None = Field(default=None, pattern=r"^\d{8}$")
