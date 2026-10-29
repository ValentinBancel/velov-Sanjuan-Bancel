"""Test de modèle (EX-05 devenu automatique) : un modèle qui se dégrade sans qu'aucun test de code n'échoue.

Petit jeu simulé (30 jours, 5 stations, test sur 7 jours) : la CI reste sous quelques minutes.
"""

import json

import numpy as np
import pandas as pd
import pytest

from velov.data import simulate
from velov.features import FEATURES, make_training_frame
from velov.train import temporal_split, train


@pytest.fixture(scope="module")
def history(tmp_path_factory) -> pd.DataFrame:
    return simulate(days=30, n_stations=5, seed=0)


def _train(history, tmp_path, name, seed=42):
    csv = tmp_path / f"{name}.csv"
    history.to_csv(csv, index=False)
    out = tmp_path / name
    return train(csv, out, version="0.0.0-test", test_days=7, seed=seed), out


def test_model_beats_persistence_baseline(history, tmp_path):
    metadata, _ = _train(history, tmp_path, "quality")
    m = metadata["metrics"]
    assert m["mae_model"] < m["mae_baseline_persistence"], m


def test_peak_hours_error_is_reported_and_sane(history, tmp_path):
    m = _train(history, tmp_path, "peak")[0]["metrics"]
    assert 0 < m["mae_model_peak_hours"] < 3 * m["mae_baseline_persistence"], m


def test_same_seed_gives_same_model(history, tmp_path):
    """Reproductibilité : mêmes données, même seed, mêmes métriques et mêmes prédictions."""
    first, out1 = _train(history, tmp_path, "a")
    second, out2 = _train(history, tmp_path, "b")
    assert first["metrics"] == second["metrics"]
    import joblib

    _, test_df = temporal_split(make_training_frame(history), 7)
    p1 = joblib.load(out1 / "model.joblib").predict(test_df[FEATURES])
    p2 = joblib.load(out2 / "model.joblib").predict(test_df[FEATURES])
    np.testing.assert_array_equal(p1, p2)


def test_metadata_records_params_and_data_period(history, tmp_path):
    metadata, out = _train(history, tmp_path, "meta", seed=7)
    assert metadata["params"]["seed"] == 7
    assert metadata["params"]["learning_rate"] == 0.1
    assert metadata["data_period"]["start"] < metadata["data_period"]["end"]
    assert json.loads((out / "metadata.json").read_text())["params"] == metadata["params"]
