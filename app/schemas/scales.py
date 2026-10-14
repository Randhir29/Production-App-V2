"""v1 request/response models for scales and tables (agent.md §6, §8.2)."""

from __future__ import annotations

from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ScaleWindowQuery(BaseModel):
    """Window of the scales endpoints (§5.2): explicit seconds, or a trailing period."""

    from_ts: int = Field(default=0, ge=0, description="Window start, epoch seconds (0 = unset).")
    to_ts: int = Field(default=0, ge=0, description="Window end, epoch seconds (0 = unset).")
    period_hours: int = Field(
        default=0, ge=0, description="Trailing hours when from/to are unset; 0 = since local midnight."
    )

    @model_validator(mode="after")
    def _ordered(self) -> Self:
        if self.to_ts and self.to_ts < self.from_ts:
            raise ValueError("to_ts must be >= from_ts")
        return self


class UnderPerformingQuery(ScaleWindowQuery):
    row_count: int = Field(default=10, ge=1, le=500)


class InaccurateQuery(ScaleWindowQuery):
    row_count: int = Field(default=5, ge=1, le=500)


class MsWindowQuery(BaseModel):
    from_ms: int = Field(ge=0, description="Window start, epoch milliseconds.")
    to_ms: int = Field(ge=0, description="Window end, epoch milliseconds.")

    @model_validator(mode="after")
    def _ordered(self) -> Self:
        if self.to_ms < self.from_ms:
            raise ValueError("to_ms must be >= from_ms")
        return self


class ScaleEfficiencyQuery(MsWindowQuery):
    carousel: int = Field(default=0, ge=0, le=2, description="0 = both carousels, 1 or 2.")


class FillingStatisticsQuery(MsWindowQuery):
    carousel: int = Field(ge=1, le=2)
    cyl_type: int = Field(ge=1, le=2, description="1 = 14.2 kg, 2 = 19 kg.")


class PeriodicProductionQuery(MsWindowQuery):
    interval_min: int = Field(default=60, ge=1, le=1440)


class AccuracyTrendQuery(BaseModel):
    from_: str = Field(
        alias="from", pattern=r"^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}(:\d{2})?$", description="Start datetime."
    )
    to: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}(:\d{2})?$", description="End datetime.")
    carousel: int = Field(ge=1, le=2)
    cyl_type: int = Field(ge=1, le=2, description="1 = 14.2 kg, 2 = 19 kg.")

    model_config = ConfigDict(populate_by_name=True)


# ---- responses -----------------------------------------------------------------------


class UnderPerformingScale(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    system_id: int
    machine_id: int
    device_id: int
    count: int
    scale: int = Field(description="device_id for device-id plants, otherwise machine_id (§5.1).")
    carousal: int
    efficiency: float = Field(description="Filled count / expected count over the interval.")
    std_output: float = Field(validation_alias="stdOutput", description="Expected cylinders per head.")
    interval: int = Field(description="Seconds.")
    tag: Literal["below-average", "average", "above-average"] | None = None


class EfficiencyMeta(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    car1_eff: float = Field(validation_alias="car1Eff")
    car2_eff: float = Field(validation_alias="car2Eff")


class UnderPerformingResponse(BaseModel):
    rows: list[UnderPerformingScale]
    meta: EfficiencyMeta


class InaccurateScale(BaseModel):
    system_id: int
    machine_id: int
    device_id: int
    total: int
    hundred_plus: int
    accuracy: float
    scale: int
    carousal: int
    interval: int = Field(description="Seconds.")


class AccuracyMeta(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    car1_acc: float = Field(validation_alias="car1Acc")
    car2_acc: float = Field(validation_alias="car2Acc")


class InaccurateResponse(BaseModel):
    rows: list[InaccurateScale]
    meta: AccuracyMeta


class ScaleEfficiencyRow(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    scale_id: int = Field(validation_alias="scaleId")
    car1_count: int | None = Field(default=None, validation_alias="car1Count")
    car1_percent: float | None = Field(default=None, validation_alias="car1Percent")
    car2_count: int | None = Field(default=None, validation_alias="car2Count")
    car2_percent: float | None = Field(default=None, validation_alias="car2Percent")


class ScaleEfficiencyResponse(BaseModel):
    rows: list[ScaleEfficiencyRow]
    meta: EfficiencyMeta
    carousel: int


class FillingStatisticsRow(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    scale_id: int = Field(validation_alias="scaleId")
    n200: int
    n150: int
    n100: int
    n50: int
    nil_var: int = Field(validation_alias="nilVar")
    p50: int
    p100: int
    p150: int
    p200: int
    total: int
    average: float
    std_dev: float = Field(validation_alias="stdDev")


class AccuracyTrendRow(BaseModel):
    carousal: int
    interval_start: str
    variation_eq_0: int
    total_cylinders: int
    variation_eq_0_percentage: float | None


class PeriodicProductionRow(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    from_: str = Field(validation_alias="from", serialization_alias="from")
    to: str
    c1_t1: int
    c1_t2: int
    c1_total: int
    c2_t1: int
    c2_t2: int
    c2_total: int
