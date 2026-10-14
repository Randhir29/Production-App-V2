"""Static plant configuration ported from ``Config\\Secure`` (agent.md §4.3). Pure, no settings."""

from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field

logger = logging.getLogger("prodash.domain.static_config")


class StaticTimes(BaseModel):
    model_config = ConfigDict(frozen=True)

    start: str | None = None
    end: str | None = None
    breaks: tuple[dict[str, str], ...] | None = None


class StaticCarousel(BaseModel):
    model_config = ConfigDict(frozen=True)

    heads: int
    stdOutput: int
    times: StaticTimes | None = None


class StaticPlantConfig(BaseModel):
    """Frozen view of ``config/static_plant_config.yaml``.

    YAML duplicate keys resolve last-wins, exactly like PHP array literals (D-12).
    """

    model_config = ConfigDict(frozen=True)

    plants: dict[str, str] = Field(description="short_name -> display name")
    plant_name: str
    carousal_speed: dict[int, int]
    interruption_to_exclude_minutes: int
    plants_with_device_id: tuple[str, ...]
    plants_with_48_head: dict[str, tuple[int, ...]] = Field(default_factory=dict)
    carousal_config: dict[str, dict[int, StaticCarousel]] = Field(default_factory=dict)

    def uses_device_id(self, plant: str) -> bool:
        """Scale-identifier rule input (§5.1)."""
        return plant in self.plants_with_device_id


def parse_static_config(text: str) -> StaticPlantConfig:
    """Parse YAML text into the frozen model; raises ``TypeError``/``ValueError`` when invalid."""
    raw = yaml.safe_load(text)
    if not isinstance(raw, dict):
        raise TypeError("static plant config must be a YAML mapping")
    return StaticPlantConfig.model_validate(raw)


@lru_cache
def load_static_config(path: str) -> StaticPlantConfig:
    """Load and cache the static config from ``path`` (fails fast when missing or invalid)."""
    config = parse_static_config(Path(path).read_text(encoding="utf-8"))
    if not config.plants:
        logger.warning("static config %s has no plants; plant-gated endpoints will reject all", path)
    return config
