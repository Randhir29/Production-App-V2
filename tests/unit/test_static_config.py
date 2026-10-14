"""Static plant config loader (agent.md §4.3, D-12)."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from app.domain.static_config import StaticPlantConfig, load_static_config, parse_static_config

SHIPPED = Path(__file__).resolve().parents[2] / "config" / "static_plant_config.yaml"

SAMPLE = """
plants:
  anantapur: Anantapur
  kota: Kota
plant_name: Mysore LPG Plant
carousal_speed: {1: 50, 2: 48}
interruption_to_exclude_minutes: 5
plants_with_device_id: [anantapur]
plants_with_48_head: {chakan: [1]}
carousal_config:
  anantapur:
    1: {heads: 72, stdOutput: 3000, times: {start: "14:00:00", end: "22:30:00",
         breaks: [{from: "19:00:00", to: "19:30:00"}]}}
    2: {heads: 24, stdOutput: 1500}
"""


def test_parse_sample() -> None:
    cfg = parse_static_config(SAMPLE)
    assert cfg.plants["kota"] == "Kota"
    assert cfg.carousal_speed == {1: 50, 2: 48}
    first = cfg.carousal_config["anantapur"][1].times
    assert first is not None
    assert first.breaks == ({"from": "19:00:00", "to": "19:30:00"},)
    assert cfg.carousal_config["anantapur"][2].times is None
    assert cfg.uses_device_id("anantapur") and not cfg.uses_device_id("kota")


def test_duplicate_keys_last_wins_like_php() -> None:
    """D-12: kota -> {1: heads 24, stdOutput 1800}; the earlier definition is discarded."""
    text = SAMPLE + (
        "  kota:\n    1: {heads: 12, stdOutput: 900}\n    2: {heads: 12, stdOutput: 900}\n"
        "  kota:\n    1: {heads: 24, stdOutput: 1800}\n"
    )
    cfg = parse_static_config(text)
    assert set(cfg.carousal_config["kota"]) == {1}
    assert cfg.carousal_config["kota"][1].stdOutput == 1800


def test_model_is_frozen() -> None:
    cfg = parse_static_config(SAMPLE)
    with pytest.raises(ValidationError):
        cfg.plant_name = "x"  # type: ignore[misc]


def test_invalid_documents_rejected() -> None:
    with pytest.raises(TypeError):
        parse_static_config("- just\n- a list\n")
    with pytest.raises(ValidationError):
        parse_static_config("plants: {}\n")


def test_shipped_yaml_has_known_constants() -> None:
    cfg = load_static_config(str(SHIPPED))
    assert isinstance(cfg, StaticPlantConfig)
    assert cfg.plant_name == "Mysore LPG Plant"
    assert cfg.carousal_speed == {1: 50, 2: 48}
    assert cfg.interruption_to_exclude_minutes == 5
    assert len(cfg.plants_with_device_id) == 22
    assert cfg.plants_with_48_head == {"chakan": (1,)}


def test_shipped_plants_are_the_55_from_database_php() -> None:
    cfg = load_static_config(str(SHIPPED))
    assert len(cfg.plants) == 55
    assert {"jabalpur1", "jabalpur2", "anantapur", "kochi", "kota", "jammu"} <= set(cfg.plants)
    # not plants: legacy default connection, test group, and jodhpur (§4.3)
    assert not {"kosan", "testplant", "jodhpur"} & set(cfg.plants)
    assert set(cfg.plants_with_device_id) - {"jodhpur"} <= set(cfg.plants)


def test_shipped_yaml_contains_no_hosts_or_credentials() -> None:
    import re

    text = SHIPPED.read_text(encoding="utf-8")
    assert not re.search(r"\b\d{1,3}(\.\d{1,3}){3}\b", text)
    assert "password" not in text.lower().replace("no hosts or credentials", "")
