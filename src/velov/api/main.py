"""API de serving du modèle Vélo'v.

TP1, partie 3 : exposez le modèle. Mode : IA déclarée autorisée pour cette partie.

Endpoints attendus (niveaux du TP1 : Must, Should, Stretch) :
    POST /v1/predict        [Must]    une prédiction
    GET  /health            [Should]  liveness : le process répond (ne dépend pas du modèle)
    GET  /ready             [Should]  readiness : 200 si le modèle est chargé, 503 sinon
    GET  /v1/model, POST /v1/predict/batch   [Stretch]

Lancement :
    uvicorn velov.api.main:app --reload
"""

from __future__ import annotations

import json
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

import joblib
import pandas as pd
from fastapi import FastAPI, HTTPException

from velov.api import db
from velov.api.schemas import PredictionRequest, PredictionResponse  # noqa: F401
from velov.features import FEATURES, add_features  # noqa: F401
from velov.train import METADATA_FILENAME, sha256_of

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger("velov.api")

STATE: dict = {"model": None, "metadata": None, "pool": None}


def load_model(model_dir: Path) -> tuple[object, dict]:
    """Fourni : charge le modèle APRÈS avoir vérifié son empreinte SHA-256."""
    metadata_path = model_dir / METADATA_FILENAME
    if not metadata_path.exists():
        raise FileNotFoundError(f"{metadata_path} introuvable")
    metadata = json.loads(metadata_path.read_text())
    model_path = model_dir / metadata["artifact"]["file"]
    if sha256_of(model_path) != metadata["artifact"]["sha256"]:
        raise RuntimeError(f"Empreinte invalide pour {model_path}")
    return joblib.load(model_path), metadata


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Fourni : exécuté une fois au démarrage (avant yield) et à l'arrêt (après yield)."""
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


# TODO 5 [Should] : GET /health -> {"status": "ok"}

@app.get("/health")
async def health():
    """Endpoint de liveness : le process répond (ne dépend pas du modèle)."""
    return {"status": "ok"}


# TODO 6 [Should] : GET /ready -> 200 + version du modèle si chargé, sinon HTTPException 503
@app.get("/ready")
async def ready():
    """Endpoint de readiness : 200 si le modèle est chargé, 503 sinon."""
    if STATE["model"] is None:
        from fastapi import HTTPException

        raise HTTPException(status_code=503, detail="Modèle non chargé")
    return {"status": "ready", "model_version": STATE["metadata"]["model_version"]}

# TODO 7 [Must] : POST /v1/predict
#   - entrée : PredictionRequest ; sortie : PredictionResponse
#   - construire un DataFrame d'une ligne, appliquer add_features, sélectionner FEATURES
#   - prédire, borner entre 0 et capacity, target_timestamp = timestamp + 1 h
#     (l'instant porte son fuseau : le contrat l'a validé)
#   - 503 si le modèle n'est pas chargé
#   Question : pourquoi importer add_features plutôt que recalculer les features ici ?

@app.post("/v1/predict", response_model=PredictionResponse)
async def predict(request: PredictionRequest):
    if STATE["model"] is None:
        raise HTTPException(status_code=503, detail="Modèle non chargé")

    df = pd.DataFrame([request.model_dump()])
    df = add_features(df)
    X = df[FEATURES]

    y_pred = STATE["model"].predict(X)
    y_pred_bounded = max(0, min(y_pred[0], request.capacity))
    target_timestamp = request.timestamp + pd.Timedelta(hours=1)
    response = PredictionResponse(
        station_id=request.station_id,
        target_timestamp=target_timestamp,
        predicted_bikes=y_pred_bounded,
        model_version=STATE["metadata"]["model_version"],
    )
    if STATE["pool"] is not None:
        try:
            await db.save_prediction(STATE["pool"], request, response)
        except Exception:
            logger.exception("Échec de l'enregistrement de la prédiction")
    return response
    
