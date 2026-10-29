"""Entraînement du modèle v0 et production de l'artefact distribuable.

Ce qu'on distribue (S1) n'est pas "un modèle" mais un paquet :
- le pipeline sklearn complet (prétraitement + modèle) -> models/model.joblib
- ses métadonnées (version, features, métriques, empreinte SHA-256, versions des
  librairies, hash des données) -> models/metadata.json

Source des données (S3) : la table gold produite par dbt (`gold.gold_velov_hourly` dans DuckDB).
Quelle que soit la source (gold ou CSV du simulateur), le data contract Pandera est vérifié AVANT
d'entraîner : une table qui le viole interrompt l'entraînement (EX-12).

Usage :
    python -m velov.train                                   # lit la table gold (data/velov.duckdb)
    python -m velov.train --data data/velov_history.csv     # ou le CSV du simulateur
    python -m velov.train --mlflow                          # + journalisation MLflow
"""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
from datetime import UTC, datetime
from pathlib import Path

import duckdb
import joblib
import numpy as np
import pandas as pd
import sklearn
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.metrics import mean_absolute_error
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder

from velov.contract import ContractError, validate_gold
from velov.features import (
    CATEGORICAL_FEATURES,
    FEATURES,
    NUMERIC_FEATURES,
    RAW_COLUMNS,
    TARGET,
    make_training_frame,
)

MODEL_FILENAME = "model.joblib"
METADATA_FILENAME = "metadata.json"
GOLD_TABLE = "gold.gold_velov_hourly"
DEFAULT_DB = Path("data/velov.duckdb")
LEARNING_RATE = 0.1
MAX_ITER = 200
# Heures de pointe en heure locale (départs au travail, retours) : là où se joue l'utilité du modèle.
PEAK_HOURS = (7, 8, 9, 17, 18, 19)


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def load_gold(db_path: Path = DEFAULT_DB, table: str = GOLD_TABLE) -> pd.DataFrame:
    """Lit la table gold publiée par dbt (lecture seule : l'entraînement ne modifie jamais le pipeline)."""
    if not db_path.exists():
        raise FileNotFoundError(f"{db_path} introuvable : lancer d'abord `dbt build` (voir README, section Pipeline)")
    con = duckdb.connect(str(db_path), read_only=True)
    try:
        return con.sql(f"select * from {table}").df()  # noqa: S608 (nom de table constant, pas une entrée utilisateur)
    except duckdb.CatalogException as exc:
        raise FileNotFoundError(f"Table {table} absente de {db_path} : lancer `dbt build`") from exc
    finally:
        con.close()


def load_raw(data_path: Path | None = None, db_path: Path = DEFAULT_DB) -> pd.DataFrame:
    """Table d'entraînement : le CSV du simulateur si `data_path` est donné, sinon la table gold."""
    if data_path is None:
        return load_gold(db_path)
    if not data_path.exists():
        raise FileNotFoundError(f"{data_path} introuvable : lancer d'abord `python -m velov.data`")
    return pd.read_csv(data_path, parse_dates=["timestamp"])


def fingerprint(df: pd.DataFrame) -> str:
    """Empreinte du contenu d'un DataFrame (équivalent de sha256_of pour une source qui n'est pas un fichier)."""
    ordered = df.sort_values(["station_id", "timestamp"]).reset_index(drop=True)
    return hashlib.sha256(pd.util.hash_pandas_object(ordered, index=False).to_numpy().tobytes()).hexdigest()


def build_pipeline(seed: int = 42) -> Pipeline:
    preprocess = ColumnTransformer(
        [
            ("station", OneHotEncoder(handle_unknown="ignore"), CATEGORICAL_FEATURES),
            ("num", "passthrough", NUMERIC_FEATURES),
        ]
    )
    model = HistGradientBoostingRegressor(max_iter=MAX_ITER, learning_rate=LEARNING_RATE, random_state=seed)
    return Pipeline([("preprocess", preprocess), ("model", model)])


def temporal_split(df: pd.DataFrame, test_days: int) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Découpage temporel : on teste sur le futur, jamais sur un échantillon aléatoire."""
    cutoff = pd.to_datetime(df["timestamp"]).max() - pd.Timedelta(days=test_days)
    ts = pd.to_datetime(df["timestamp"])
    train, test = df[ts <= cutoff], df[ts > cutoff]
    if train.empty or test.empty:
        raise ValueError("Split vide : augmenter l'historique ou réduire test_days")
    return train, test


def evaluate(pipeline: Pipeline, test_df: pd.DataFrame) -> dict[str, float]:
    """MAE du modèle, de la baseline de persistance et du modèle aux heures de pointe, sur `test_df`.

    Partagé avec velov.promote : challenger et champion sont mesurés par le MÊME calcul, sur la même fenêtre.
    """
    y_test = test_df[TARGET]
    pred = pd.Series(np.clip(pipeline.predict(test_df[FEATURES]), 0, test_df["capacity"]), index=test_df.index)
    peak = test_df["hour"].isin(PEAK_HOURS)
    return {
        "mae_model": float(mean_absolute_error(y_test, pred)),
        # Baseline "persistance" : dans 1 h, il y aura autant de vélos que maintenant.
        # Le modèle doit justifier sa complexité face à cette baseline (exigence EX-05).
        "mae_baseline_persistence": float(mean_absolute_error(y_test, test_df["bikes_available"])),
        "mae_model_peak_hours": float(mean_absolute_error(y_test[peak], pred[peak])) if peak.any() else float("nan"),
    }


def train(
    data_path: Path,
    out_dir: Path,
    version: str = "0.1.0",
    test_days: int = 14,
    seed: int = 42,
) -> dict:
    """Entraîne depuis un CSV (simulateur). Retourne les métadonnées."""
    if not data_path.exists():
        raise FileNotFoundError(f"{data_path} introuvable : lancer d'abord `python -m velov.data`")
    raw = pd.read_csv(data_path, parse_dates=["timestamp"])
    return fit_and_write(
        raw,
        out_dir,
        source=str(data_path),
        data_sha256=sha256_of(data_path),
        version=version,
        test_days=test_days,
        seed=seed,
    )


def train_from_gold(
    db_path: Path,
    out_dir: Path,
    version: str = "0.1.0",
    test_days: int = 14,
    seed: int = 42,
) -> dict:
    """Entraîne depuis la table gold de DuckDB. Retourne les métadonnées."""
    raw = load_gold(db_path)
    return fit_and_write(
        raw,
        out_dir,
        source=f"{db_path}:{GOLD_TABLE}",
        data_sha256=fingerprint(raw),
        version=version,
        test_days=test_days,
        seed=seed,
    )


def fit_and_write(
    raw: pd.DataFrame,
    out_dir: Path,
    *,
    source: str,
    data_sha256: str,
    version: str,
    test_days: int,
    seed: int,
) -> dict:
    """Vérifie le contrat, entraîne, évalue contre une baseline, écrit l'artefact.

    Raises:
        ContractError: `raw` viole le data contract : rien n'est entraîné ni écrit (EX-12).
    """
    raw = validate_gold(raw)
    frame = make_training_frame(raw)
    train_df, test_df = temporal_split(frame, test_days)

    pipeline = build_pipeline(seed)
    pipeline.fit(train_df[FEATURES], train_df[TARGET])

    maes = evaluate(pipeline, test_df)

    out_dir.mkdir(parents=True, exist_ok=True)
    model_path = out_dir / MODEL_FILENAME
    joblib.dump(pipeline, model_path)

    metadata = {
        "model_name": "velov-availability",
        "model_version": version,
        "trained_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "task": "Prédire le nombre de vélos disponibles à H+1 par station",
        "input_columns": RAW_COLUMNS,
        "features": FEATURES,
        "target": TARGET,
        "metrics": {
            **{k: round(v, 3) for k, v in maes.items()},
            "test_days": test_days,
            "n_train": int(len(train_df)),
            "n_test": int(len(test_df)),
        },
        "params": {
            "model": "HistGradientBoostingRegressor",
            "learning_rate": LEARNING_RATE,
            "max_iter": MAX_ITER,
            "max_leaf_nodes": pipeline.named_steps["model"].max_leaf_nodes,
            "seed": seed,
            "test_days": test_days,
        },
        "artifact": {"file": MODEL_FILENAME, "sha256": sha256_of(model_path)},
        "data_source": source,
        "data_sha256": data_sha256,
        "data_period": {
            "start": raw["timestamp"].min().isoformat(),
            "end": raw["timestamp"].max().isoformat(),
        },
        "runtime": {
            "python": platform.python_version(),
            "scikit-learn": sklearn.__version__,
            "pandas": pd.__version__,
            "numpy": np.__version__,
        },
    }
    (out_dir / METADATA_FILENAME).write_text(json.dumps(metadata, indent=2, ensure_ascii=False))
    return metadata


def main() -> None:
    parser = argparse.ArgumentParser(description="Entraîne le modèle Vélo'v v0.")
    parser.add_argument("--data", type=Path, help="CSV du simulateur ; sans cette option, on lit la table gold")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB, help="base DuckDB contenant la table gold")
    parser.add_argument("--test-days", type=int, default=14, help="jours de fin réservés au test temporel")
    parser.add_argument("--out", type=Path, default=Path("models"))
    parser.add_argument("--version", default="0.1.0")
    parser.add_argument("--seed", type=int, default=42, help="graine de l'entraînement (même seed, même modèle)")
    parser.add_argument("--mlflow", action="store_true", help="tracer le run dans MLflow (MLFLOW_TRACKING_URI)")
    args = parser.parse_args()

    try:
        if args.data:
            metadata = train(args.data, args.out, args.version, args.test_days, args.seed)
        else:
            metadata = train_from_gold(args.db, args.out, args.version, args.test_days, args.seed)
    except ContractError as exc:
        raise SystemExit(f"Entraînement refusé, {exc}") from exc
    m = metadata["metrics"]
    print(f"MAE modèle   : {m['mae_model']:.3f} vélos")
    print(f"MAE baseline : {m['mae_baseline_persistence']:.3f} vélos (persistance)")
    print(f"MAE pointe   : {m['mae_model_peak_hours']:.3f} vélos (heures de pointe)")
    print(f"Artefact     : {args.out / MODEL_FILENAME} (sha256 {metadata['artifact']['sha256'][:12]}...)")
    if args.mlflow:
        from velov.tracking import log_run  # import local : MLflow n'est jamais requis pour servir le modèle

        run = log_run(metadata, args.out)
        print(f"Run {run.run_id[:8]} tracé dans MLflow ({run.tracking_uri}), version {run.version} du registre")


if __name__ == "__main__":
    main()
