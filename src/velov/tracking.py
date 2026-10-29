"""Traçage d'un entraînement dans MLflow : un run répond à « quel code, quelles données, quels réglages,
meilleur que quoi ».

Module à part : velov.train n'importe pas MLflow (l'image de serving ne l'embarque pas) et ne l'appelle
que sur `--mlflow`.

Aucune adresse en dur : le serveur vient de MLFLOW_TRACKING_URI, le même code trace donc en local ou sur
le serveur du binôme. À défaut, repli sur une base SQLite locale (mlflow.db, ignorée par git).
"""

from __future__ import annotations

import math
import os
import subprocess  # noqa: S404 (git en lecture seule)
import warnings
from dataclasses import dataclass
from pathlib import Path

import joblib
import mlflow
import pandas as pd
from mlflow import MlflowClient
from mlflow.models import infer_signature

from velov.features import FEATURES, make_training_frame

MODEL_NAME = "velov-availability"
EXPERIMENT = "velov-availability"
FALLBACK_URI = "sqlite:///mlflow.db"
# Depuis MLflow 3.x, sklearn est sérialisé par défaut en skops (format sûr, sans exécution de code arbitraire).
# skops refuse les types qu'il ne connaît pas : on déclare explicitement ceux qu'on a vérifiés.
SKOPS_TRUSTED_TYPES = ["sklearn.ensemble._hist_gradient_boosting.predictor.TreePredictor"]
PACKAGE_DIR = "package"  # sous-dossier d'artefacts : model.joblib + metadata.json, le paquet de S1


@dataclass(frozen=True)
class RunInfo:
    run_id: str
    version: str
    tracking_uri: str


def tracking_uri() -> str:
    return os.environ.get("MLFLOW_TRACKING_URI") or FALLBACK_URI


def git_state(cwd: Path | None = None) -> dict[str, str]:
    """Commit courant et état du dépôt. `git_dirty=true` : le run ne correspond à aucun commit exact."""

    def git(*args: str) -> str:
        return subprocess.run(  # noqa: S603 (arguments constants)
            ["git", *args],  # noqa: S607 (git du PATH)
            capture_output=True,
            text=True,
            check=True,
            cwd=cwd,
        ).stdout.strip()

    try:
        return {"git_commit": git("rev-parse", "HEAD"), "git_dirty": str(bool(git("status", "--porcelain"))).lower()}
    except (OSError, subprocess.CalledProcessError):
        return {"git_commit": "unknown", "git_dirty": "unknown"}


def _signature_sample() -> pd.DataFrame:
    raw = pd.DataFrame(
        {
            "station_id": [1, 1],
            "timestamp": pd.to_datetime(["2026-06-01T06:00Z", "2026-06-01T07:00Z"]),
            "capacity": [20, 20],
            "bikes_available": [8, 5],
            "temperature": [21.0, 22.0],
            "is_raining": [False, False],
        }
    )
    return make_training_frame(raw)[FEATURES]


def log_run(metadata: dict, out_dir: Path, *, register: bool = True) -> RunInfo:
    """Trace l'entraînement décrit par `metadata` (écrit par velov.train dans `out_dir`).

    Le modèle est enregistré dans le registre et reçoit l'alias `challenger` : il ne sert à rien tant que
    velov.promote ne l'a pas fait passer champion (EX-14).
    """
    uri = tracking_uri()
    mlflow.set_tracking_uri(uri)
    mlflow.set_experiment(EXPERIMENT)

    pipeline = joblib.load(out_dir / metadata["artifact"]["file"])
    sample = _signature_sample()
    metrics = {k: v for k, v in metadata["metrics"].items() if k.startswith("mae") and not math.isnan(v)}
    period = metadata["data_period"]
    tags = {
        **git_state(),
        "data_sha256": metadata["data_sha256"],
        "data_source": metadata["data_source"],
        "data_start": period["start"],
        "data_end": period["end"],
        "model_sha256": metadata["artifact"]["sha256"],
    }

    with warnings.catch_warnings():
        # Conseil d'MLflow sur les colonnes entières et les valeurs manquantes : sans objet, l'API refuse
        # les valeurs manquantes (422). Ignoré ici seulement, sinon il fait échouer l'inférence sous -W error.
        warnings.filterwarnings("ignore", message="Hint: Inferred schema contains integer column")
        signature = infer_signature(sample, pipeline.predict(sample))

    with mlflow.start_run(run_name=f"v{metadata['model_version']}") as run:
        mlflow.log_params(metadata["params"])
        mlflow.log_metrics(metrics)
        mlflow.set_tags(tags)
        info = mlflow.sklearn.log_model(
            pipeline,
            name="model",
            signature=signature,
            input_example=sample,
            serialization_format="skops",
            skops_trusted_types=SKOPS_TRUSTED_TYPES,
            registered_model_name=MODEL_NAME if register else None,
        )
        for name in (metadata["artifact"]["file"], "metadata.json"):
            mlflow.log_artifact(str(out_dir / name), artifact_path=PACKAGE_DIR)

    version = str(info.registered_model_version) if register else ""
    if register:
        MlflowClient().set_registered_model_alias(MODEL_NAME, "challenger", version)
    return RunInfo(run_id=run.info.run_id, version=version, tracking_uri=uri)
