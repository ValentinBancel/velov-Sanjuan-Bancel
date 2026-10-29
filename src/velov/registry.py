"""Accès au registre MLflow : retrouver la version qui porte un alias et récupérer son paquet vérifié.

Une version ne change jamais ; ce sont les alias (`challenger`, `champion`, `previous-champion`) qui bougent.
Le paquet (model.joblib + metadata.json) est celui de S1 : on le charge avec la même vérification d'empreinte
que l'API, jamais sans.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import joblib
import mlflow
from mlflow import MlflowClient
from mlflow.exceptions import MlflowException

from velov.tracking import MODEL_NAME, PACKAGE_DIR, tracking_uri
from velov.train import METADATA_FILENAME, sha256_of


class RegistryError(RuntimeError):
    """Alias absent, paquet manquant ou empreinte invalide."""


@dataclass(frozen=True)
class Package:
    version: str
    alias: str
    run_id: str
    directory: Path
    metadata: dict

    def load_pipeline(self):
        return joblib.load(self.directory / self.metadata["artifact"]["file"])


def client() -> MlflowClient:
    mlflow.set_tracking_uri(tracking_uri())
    return MlflowClient()


def version_of(alias: str, c: MlflowClient | None = None):
    """Version du registre portant `alias`, ou None."""
    try:
        return (c or client()).get_model_version_by_alias(MODEL_NAME, alias)
    except MlflowException as exc:
        if exc.error_code in ("RESOURCE_DOES_NOT_EXIST", "INVALID_PARAMETER_VALUE"):
            return None
        raise


def fetch(alias: str, dest: Path, c: MlflowClient | None = None) -> Package:
    """Télécharge le paquet de la version portant `alias` dans `dest` et vérifie son empreinte SHA-256."""
    c = c or client()
    mv = version_of(alias, c)
    if mv is None:
        raise RegistryError(f"Aucune version de {MODEL_NAME} ne porte l'alias @{alias}")
    local = Path(mlflow.artifacts.download_artifacts(run_id=mv.run_id, artifact_path=PACKAGE_DIR, dst_path=str(dest)))
    try:
        metadata = json.loads((local / METADATA_FILENAME).read_text(encoding="utf-8"))
        actual = sha256_of(local / metadata["artifact"]["file"])
    except (OSError, KeyError, ValueError) as exc:
        raise RegistryError(f"Paquet de la version {mv.version} illisible : {exc}") from exc
    if actual != metadata["artifact"]["sha256"]:
        raise RegistryError(f"Empreinte invalide pour la version {mv.version} : le paquet a été modifié")
    return Package(version=str(mv.version), alias=alias, run_id=mv.run_id, directory=local, metadata=metadata)
