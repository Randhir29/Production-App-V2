"""Mailer row transformation (agent.md §5.7.8 step 4): rounding and missing data."""

from __future__ import annotations

from app.domain.mail_rows import transform_rows

PLANT = {"short_name": "kochi", "name": "Kochi LPG"}
COUNTS = {"kochi": 2}
ZONES = {"kochi": "South"}


def _report() -> dict[str, object]:
    return {
        "carousals": {1: {"heads": 72}, 2: {"heads": 24}},
        "bottlingData": {
            1: {
                "normal": {"prod_14_2": 100, "prod_19": 10},
                "break": {"prod_14_2": 5, "prod_19": 0},
                "overtime": {"prod_14_2": 1, "prod_19": 2},
            }
        },
        "productionHoursData": {1: {"total_normal_gap": 1.005}},
        "productivityData": {
            1: {
                "normal": {"total_production": 112.5, "productivity": 12.5},
                "break": {"total_production": 5.0, "net_hours": 0.125, "productivity": 40.5},
                "overtime": {"total_production": 3.5, "net_hours": -0.255, "productivity": -13.5},
            }
        },
        "checkScaleRejectionSummary": {1: {"total": 3, "totalSortout": 1}},
        "gdRejectionSummary": {1: {"total": 200, "rejected": 1}},
        "ptRejectionSummary": False,
    }


def test_row_values_and_php_rounding() -> None:
    rows = transform_rows(PLANT, _report(), COUNTS, ZONES)
    first = rows[0]
    assert first["isAdditionalCarousel"] is False and rows[1]["isAdditionalCarousel"] is True
    assert (first["short_name"], first["name"], first["zone"]) == ("kochi", "Kochi LPG", "South")
    assert first["carousel"] == 1 and first["filling_heads"] == 72 and first["carousel_count"] == 2
    assert first["bottling"] == {"14_2kg": 106, "19kg": 12, "total": 118}
    prod = first["productivity"]
    assert prod["normal"] == {"production": 112.5, "stoppages": 1.01, "productivity": 13.0}  # 12.5 -> 13
    assert prod["break"] == {"production": 5.0, "net_hours": 0.13, "productivity": 41.0}  # 0.125 -> 0.13
    assert prod["overtime"]["net_hours"] == -0.26 and prod["overtime"]["productivity"] == -14.0
    assert first["rejections"] == {
        "eld": {"percent": 0.5},
        "ort": {"percent": 0.0},
        "cs": {"percent": 33.33},
    }


def test_carousel_without_data_gives_zeroed_row() -> None:
    second = transform_rows(PLANT, _report(), COUNTS, ZONES)[1]
    assert second["bottling"] == {"14_2kg": 0, "19kg": 0, "total": 0}
    assert second["productivity"]["normal"] == {"production": 0, "stoppages": 0.0, "productivity": 0.0}
    assert second["rejections"]["cs"] == {"percent": 0.0}


def test_no_carousals_gives_no_rows() -> None:
    assert transform_rows(PLANT, {}, COUNTS, ZONES) == []
