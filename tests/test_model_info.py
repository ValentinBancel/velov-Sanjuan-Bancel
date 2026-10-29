"""GET /v1/model : identité du modèle servi (EX-13)."""


def test_model_info_exposes_version_params_and_data(client):
    body = client.get("/v1/model").json()
    assert body["model_version"] == "0.0.0-test"
    assert body["params"]["seed"] == 42
    assert len(body["data_sha256"]) == 64
    assert body["metrics"]["mae_model"] > 0
    assert body["registry"] is None  # modèle de test : pas exporté du registre


def test_model_info_unavailable_without_model(client):
    from velov.api.main import STATE

    saved = STATE["model"]
    STATE["model"] = None
    try:
        assert client.get("/v1/model").status_code == 503
    finally:
        STATE["model"] = saved
