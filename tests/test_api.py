"""Tests de l'API. Les fixtures (client, valid_payload, model_dir) sont dans conftest.py."""

import json
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient


def test_predict_valid(client, valid_payload):
    r = client.post("/v1/predict", json=valid_payload)
    assert r.status_code == 200
    body = r.json()
    # 8 h à Lyon (+02:00) + 1 h = 7 h UTC
    assert datetime.fromisoformat(body["target_timestamp"]) == datetime(2026, 10, 6, 7, tzinfo=UTC)
    assert body["target_timestamp"].endswith("Z")
    assert 0 <= body["predicted_bikes"] <= valid_payload["capacity"]
    assert body["model_version"] == "0.0.0-test"


def test_predict_rejects_bikes_above_capacity(client, valid_payload):
    r = client.post("/v1/predict", json={**valid_payload, "bikes_available": 25})
    assert r.status_code == 422


@pytest.mark.parametrize(
    "change",
    [
        {"timestamp": "2026-10-06T08:00:00"},  # sans fuseau
        {"champ_inconnu": 1},
        {"capacity": 0},
        {"station_id": 0},
        {"temperature": 80},
        {"is_raining": "peut-être"},
    ],
)
def test_predict_invalid_inputs_return_422(client, valid_payload, change):
    assert client.post("/v1/predict", json={**valid_payload, **change}).status_code == 422


def test_predict_missing_field_returns_422(client, valid_payload):
    payload = {k: v for k, v in valid_payload.items() if k != "capacity"}
    assert client.post("/v1/predict", json=payload).status_code == 422


def test_predict_nan_returns_422_not_500(client, valid_payload):
    # Le parseur JSON de Python accepte NaN : la réponse d'erreur ne doit pas planter en le recopiant.
    body = json.dumps(valid_payload).replace("14.5", "NaN")
    r = client.post("/v1/predict", content=body, headers={"Content-Type": "application/json"})
    assert r.status_code == 422


def test_unknown_station_does_not_crash(client, valid_payload):
    r = client.post("/v1/predict", json={**valid_payload, "station_id": 999})
    assert r.status_code == 200


def test_health_and_ready_with_model(client):
    assert client.get("/health").json() == {"status": "ok"}
    r = client.get("/ready")
    assert r.status_code == 200
    assert r.json()["model_version"] == "0.0.0-test"


def test_ready_returns_503_without_model(tmp_path, monkeypatch):
    monkeypatch.setenv("MODEL_DIR", str(tmp_path))  # dossier vide : pas de modèle
    monkeypatch.delenv("DATABASE_URL", raising=False)
    from velov.api.main import app

    with TestClient(app) as c:
        assert c.get("/health").status_code == 200
        assert c.get("/ready").status_code == 503
        assert c.post("/v1/predict", json={}).status_code == 422


def test_predict_returns_503_without_model(tmp_path, monkeypatch, valid_payload):
    monkeypatch.setenv("MODEL_DIR", str(tmp_path))
    monkeypatch.delenv("DATABASE_URL", raising=False)
    from velov.api.main import app

    with TestClient(app) as c:
        assert c.post("/v1/predict", json=valid_payload).status_code == 503


def test_tampered_model_is_not_loaded(model_dir, tmp_path, monkeypatch):
    # Un octet ajouté au fichier du modèle : l'empreinte ne correspond plus, /ready doit dire 503.
    for f in model_dir.iterdir():
        (tmp_path / f.name).write_bytes(f.read_bytes())
    with (tmp_path / "model.joblib").open("ab") as fh:
        fh.write(b"\x00")
    monkeypatch.setenv("MODEL_DIR", str(tmp_path))
    monkeypatch.delenv("DATABASE_URL", raising=False)
    from velov.api.main import app

    with TestClient(app) as c:
        assert c.get("/ready").status_code == 503
