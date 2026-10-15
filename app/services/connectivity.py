"""Plant connectivity probe, E06 (agent.md §5.8, D-13)."""

from __future__ import annotations

import logging
import time
from typing import Any

from sqlalchemy.exc import SQLAlchemyError

from app.core.errors import AppError, PlantNotFound
from app.domain.php_compat import php_round
from app.domain.static_config import StaticPlantConfig
from app.repositories.plant.connectivity_repo import ConnectivityRepository
from app.services.plant_access import PlantAccess

logger = logging.getLogger("prodash.services.connectivity")

MSG_OK = "Connection Successful!"
MSG_ERROR = "Connection Error"
MSG_UNKNOWN = "Unknown Plant"


def _failure(message: str) -> dict[str, Any]:
    return {"status": "failure", "message": message}


class ConnectivityService:
    """Reports whether a plant database is reachable, and the latency in milliseconds."""

    def __init__(self, access: PlantAccess, static: StaticPlantConfig) -> None:
        self._access = access
        self._static = static

    async def check(self, plant: str | None) -> dict[str, Any]:
        """§5.8 ``getConnectivityStatus``.

        * Unknown plant (absent, not in the static ``plants``, or no DB credentials): ``Unknown Plant``.
        * Probe ``SELECT production_log_id FROM production_log LIMIT 1``; latency is measured from
          the start of the check, connection included, and rounded with ``php_round``.
        * Any failure yields the generic ``Connection Error`` payload; internals are never echoed.
        """
        started = time.perf_counter()
        if not plant or plant not in self._static.plants:
            return _failure(MSG_UNKNOWN)
        try:
            async with self._access.session(plant) as session:
                found = await ConnectivityRepository(session).probe()
        except PlantNotFound:
            return _failure(MSG_UNKNOWN)
        except (AppError, SQLAlchemyError, OSError, TimeoutError) as exc:
            logger.warning("connectivity failed plant=%s error=%s", plant, type(exc).__name__)
            return _failure(MSG_ERROR)
        if not found:
            return _failure(MSG_ERROR)
        latency = php_round((time.perf_counter() - started) * 1000)
        logger.info("connectivity ok plant=%s latency_ms=%s", plant, latency)
        return {"status": "success", "message": MSG_OK, "latency": latency}
