"""Response models for plants, meta and connectivity (agent.md §5.8, §8.2)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class PlantOption(BaseModel):
    value: str = Field(description="Plant short_name.", examples=["anantapur"])
    text: str = Field(description="Display name.", examples=["Anantapur LPG Plant"])


class PlantListResponse(BaseModel):
    plants: list[PlantOption]


class RegistryPlant(BaseModel):
    value: str = Field(description="Plant short_name.")
    text: str | None = Field(description="Plant name from the central DB.")
    zone: str | None = Field(description="Zone the plant belongs to.")
    carousals_count: int = Field(description="Number of configured carousels.")


class RegistryResponse(BaseModel):
    plants: list[RegistryPlant]


class ZoneMapEntry(BaseModel):
    zone: str | None
    plant: str = Field(description="Plant short_name.")


class PlantNameResponse(BaseModel):
    name: str


class ClientDeviceResponse(BaseModel):
    client: str | None = Field(description="Reverse-DNS name of the caller, or null.")


class ConnectivityResponse(BaseModel):
    status: Literal["success", "failure"]
    message: str = Field(
        description="'Connection Successful!', 'Connection Error' or 'Unknown Plant'.",
    )
    latency: float | None = Field(
        default=None, description="Round-trip including connection, in ms (success only)."
    )
