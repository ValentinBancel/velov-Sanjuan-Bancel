"""Persistance des prédictions dans PostgreSQL (optionnelle : désactivée sans DATABASE_URL)."""

from __future__ import annotations

import logging
import os

from psycopg_pool import AsyncConnectionPool

from velov.api.schemas import PredictionRequest, PredictionResponse

logger = logging.getLogger("velov.api.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS predictions (
    id               BIGSERIAL PRIMARY KEY,
    created_at       TIMESTAMPTZ      NOT NULL DEFAULT now(),
    station_id       INTEGER          NOT NULL,
    observed_at      TIMESTAMPTZ      NOT NULL,
    target_timestamp TIMESTAMPTZ      NOT NULL,
    capacity         INTEGER          NOT NULL,
    bikes_available  INTEGER          NOT NULL,
    temperature      DOUBLE PRECISION NOT NULL,
    is_raining       SMALLINT         NOT NULL,
    predicted_bikes  DOUBLE PRECISION NOT NULL,
    model_version    TEXT             NOT NULL
);
CREATE INDEX IF NOT EXISTS predictions_station_target_idx ON predictions (station_id, target_timestamp);
"""

INSERT = """
INSERT INTO predictions (station_id, observed_at, target_timestamp, capacity, bikes_available,
                         temperature, is_raining, predicted_bikes, model_version)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
RETURNING id
"""


async def open_pool() -> AsyncConnectionPool | None:
    """Ouvre le pool et crée la table ; None si DATABASE_URL n'est pas défini."""
    url = os.getenv("DATABASE_URL")
    if not url:
        logger.info("DATABASE_URL absent : prédictions non enregistrées")
        return None
    pool = AsyncConnectionPool(url, min_size=1, max_size=5, open=False)
    await pool.open()
    await pool.wait(timeout=30)
    async with pool.connection() as conn:
        await conn.execute(SCHEMA)
    logger.info("Connecté à PostgreSQL")
    return pool


SAVE_TIMEOUT_S = 2.0  # base indisponible : on échoue vite plutôt que de bloquer la réponse


async def save_prediction(pool: AsyncConnectionPool, request: PredictionRequest, response: PredictionResponse) -> int:
    async with pool.connection(timeout=SAVE_TIMEOUT_S) as conn:
        cur = await conn.execute(
            INSERT,
            (
                request.station_id,
                request.timestamp,
                response.target_timestamp,
                request.capacity,
                request.bikes_available,
                request.temperature,
                request.is_raining,
                response.predicted_bikes,
                response.model_version,
            ),
        )
        row = await cur.fetchone()
    return row[0]
