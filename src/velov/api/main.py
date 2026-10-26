"""API de serving du modèle Vélo'v.

Endpoints :
    GET  /health       liveness : le process répond (ne dépend pas du modèle)
    GET  /ready        readiness : 200 si le modèle est chargé, 503 sinon
    POST /v1/predict   une prédiction (journalisée dans PostgreSQL si DATABASE_URL est défini)

Lancement :
    uvicorn velov.api.main:app --reload
"""

from __future__ import annotations

import json
import logging
import os
from contextlib import asynccontextmanager
from datetime import timedelta
from pathlib import Path

import joblib
import pandas as pd
from fastapi import FastAPI, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from velov.api import db
from velov.api.schemas import PredictionRequest, PredictionResponse
from velov.features import FEATURES, add_features
from velov.train import METADATA_FILENAME, sha256_of

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger("velov.api")

STATE: dict = {"model": None, "metadata": None, "pool": None}


def load_model(model_dir: Path) -> tuple[object, dict]:
    """Charge le modèle APRÈS avoir vérifié son empreinte SHA-256."""
    metadata_path = model_dir / METADATA_FILENAME
    if not metadata_path.exists():
        raise FileNotFoundError(f"{metadata_path} introuvable")
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    model_path = model_dir / metadata["artifact"]["file"]
    if sha256_of(model_path) != metadata["artifact"]["sha256"]:
        raise RuntimeError(f"Empreinte invalide pour {model_path}")
    return joblib.load(model_path), metadata


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Exécuté une fois au démarrage (avant yield) et à l'arrêt (après yield)."""
    model_dir = Path(os.getenv("MODEL_DIR", "models"))
    try:
        STATE["model"], STATE["metadata"] = load_model(model_dir)
        logger.info("Modèle %s chargé", STATE["metadata"]["model_version"])
    except Exception:
        logger.exception("Échec du chargement du modèle depuis %s", model_dir)
    try:
        STATE["pool"] = await db.open_pool()
    except Exception:
        logger.exception("Échec de connexion à PostgreSQL : prédictions non enregistrées")
    yield
    if STATE["pool"] is not None:
        await STATE["pool"].close()
    STATE.update(model=None, metadata=None, pool=None)


app = FastAPI(title="Vélo'v availability API", version="1.0.0", lifespan=lifespan)


@app.exception_handler(RequestValidationError)
async def validation_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    """422 sans recopier la valeur reçue : une valeur comme NaN n'est pas sérialisable en JSON (EX-01)."""
    errors = [{"loc": list(e["loc"]), "msg": e["msg"]} for e in exc.errors()]
    return JSONResponse(status_code=422, content={"detail": errors})


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/ready")
async def ready():
    if STATE["model"] is None:
        raise HTTPException(status_code=503, detail="Modèle non chargé")
    return {"status": "ready", "model_version": STATE["metadata"]["model_version"]}


def _predict_one(request: PredictionRequest) -> float:
    """Calcul bloquant (CPU) : exécuté dans le pool de threads pour ne pas bloquer la boucle d'événements."""
    X = add_features(pd.DataFrame([request.model_dump()]))[FEATURES]
    y = float(STATE["model"].predict(X)[0])
    return min(max(y, 0.0), request.capacity)


@app.post("/v1/predict", response_model=PredictionResponse)
async def predict(request: PredictionRequest) -> PredictionResponse:
    if STATE["model"] is None:
        raise HTTPException(status_code=503, detail="Modèle non chargé")

    predicted = await run_in_threadpool(_predict_one, request)
    response = PredictionResponse(
        station_id=request.station_id,
        target_timestamp=request.timestamp + timedelta(hours=1),
        predicted_bikes=round(predicted, 2),
        model_version=STATE["metadata"]["model_version"],
    )
    if STATE["pool"] is not None:
        try:
            await db.save_prediction(STATE["pool"], request, response)
        except Exception:
            logger.exception("Échec de l'enregistrement de la prédiction")
    return response
