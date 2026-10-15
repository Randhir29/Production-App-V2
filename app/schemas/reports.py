"""Report request/response models (agent.md §5.7.7, §5.7.8, §8.2)."""

from __future__ import annotations

from datetime import date
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

PLANT_PATTERN = r"^[a-z0-9]+$"


class PerformanceV1Query(BaseModel):
    from_ms: int = Field(ge=0, description="Window start, epoch milliseconds.")
    to_ms: int = Field(ge=0, description="Window end, epoch milliseconds.")

    @model_validator(mode="after")
    def _ordered(self) -> Self:
        if self.to_ms < self.from_ms:
            raise ValueError("to_ms must be >= from_ms")
        return self


class PerformanceV2Query(BaseModel):
    from_date: date = Field(description="First day (inclusive), local date.")
    to_date: date = Field(description="Last day (inclusive), local date.")

    @model_validator(mode="after")
    def _ordered(self) -> Self:
        if self.to_date < self.from_date:
            raise ValueError("to_date must be >= from_date")
        return self


class ReportResponse(BaseModel):
    """``{status, message, data}`` as in the legacy report endpoints."""

    status: Literal["success"] = "success"
    message: str = "Data Fetched Successfully!"
    data: dict[str, Any] = Field(description="Report blocks; keys are listed in agent.md §5.7.7.")


class MailRowsRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    from_: date = Field(alias="from", description="First day (inclusive).")
    to: date = Field(description="Last day (inclusive).")
    plant: str = Field(pattern=PLANT_PATTERN, description="Plant short_name.")

    @model_validator(mode="after")
    def _ordered(self) -> Self:
        if self.to < self.from_:
            raise ValueError("to must be >= from")
        return self
