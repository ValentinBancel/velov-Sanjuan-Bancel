"""Règle de promotion (EX-14) : trois conditions dans l'ordre, puis un cycle complet sur un registre jetable."""

import pytest

from velov.data import simulate
from velov.promote import MIN_GAIN, decide
from velov.promote import run as promote
from velov.registry import RegistryError, version_of
from velov.tracking import MODEL_NAME, log_run
from velov.train import train

PERSISTENCE = 2.0


def scores(model: float, baseline: float = PERSISTENCE) -> dict:
    return {"mae_model": model, "mae_baseline_persistence": baseline}


def test_a_model_that_does_not_beat_persistence_is_never_promoted():
    assert not decide(scores(2.1), None).promote
    assert not decide(scores(2.0), None).promote  # l'égalité ne justifie pas la complexité
    assert not decide(scores(2.1), scores(3.0)).promote  # même s'il bat le champion


def test_first_champion_only_needs_to_beat_persistence():
    decision = decide(scores(1.9), None)
    assert decision.promote
    assert decision.reason == "premier champion"


def test_challenger_must_beat_the_champion_by_two_percent():
    champion = scores(1.5)
    assert decide(scores(1.5 * (1 - MIN_GAIN)), champion).promote  # pile au seuil : promu
    assert not decide(scores(1.5 * (1 - MIN_GAIN) + 0.001), champion).promote
    assert not decide(scores(1.5), champion).promote  # à égalité : on garde le champion
    assert not decide(scores(1.6), champion).promote


def test_persistence_is_checked_before_the_champion():
    # champion très mauvais : le challenger le bat largement mais ne bat pas la persistance
    assert "persistance" in decide(scores(2.5), scores(9.0)).reason


@pytest.fixture
def registry(tmp_path, monkeypatch):
    """Registre MLflow SQLite jetable + un historique simulé ; chdir pour que les artefacts restent dans tmp_path."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MLFLOW_TRACKING_URI", f"sqlite:///{tmp_path / 'mlflow.db'}")
    monkeypatch.setenv("MLFLOW_DISABLE_AGENT_HINT", "1")
    csv = tmp_path / "history.csv"
    simulate(days=20, n_stations=3, seed=0).to_csv(csv, index=False)
    return tmp_path, csv


def holder(alias: str) -> str | None:
    """Numéro (texte) de la version qui porte `alias`."""
    mv = version_of(alias)
    return None if mv is None else str(mv.version)


def register(tmp_path, csv, name, seed=42):
    out = tmp_path / name
    metadata = train(csv, out, version=name, test_days=3, seed=seed)
    return log_run(metadata, out)


def test_full_promotion_cycle(registry, monkeypatch):
    tmp_path, csv = registry

    # un champion volontairement faible (20 arbres : bat la persistance, pas le modèle normal à 200)
    monkeypatch.setattr("velov.train.MAX_ITER", 20)
    weak = register(tmp_path, csv, "weak")
    monkeypatch.setattr("velov.train.MAX_ITER", 200)

    # 1. premier champion : sans --apply, rien ne bouge
    assert promote(csv, tmp_path / "unused.duckdb", 3, apply=False).promote
    assert holder("champion") is None
    assert holder("challenger") == weak.version

    # 2. avec --apply : champion posé, challenger consommé, tag de décision
    promote(csv, tmp_path / "unused.duckdb", 3, apply=True, validated_by="test")
    assert holder("champion") == weak.version
    assert holder("challenger") is None
    tags = version_of("champion").tags
    assert tags["promotion"] == "promoted"
    assert tags["promotion_validated_by"] == "test"

    # 3. le bon modèle bat nettement le champion faible : promu, l'ancien devient previous-champion
    good = register(tmp_path, csv, "good")
    assert promote(csv, tmp_path / "unused.duckdb", 3, apply=True, validated_by="test").promote
    assert holder("champion") == good.version
    assert holder("previous-champion") == weak.version

    # 4. un clone du champion (même seed) ne le bat pas de 2 % : refusé, champion inchangé, trace laissée
    clone = register(tmp_path, csv, "clone")
    refused = promote(csv, tmp_path / "unused.duckdb", 3, apply=True, validated_by="test")
    assert not refused.promote
    assert holder("champion") == good.version
    assert holder("challenger") == clone.version
    assert version_of("challenger").tags["promotion"] == "rejected"


def test_promotion_without_challenger_is_an_error(registry):
    tmp_path, csv = registry
    with pytest.raises(RegistryError, match="challenger"):
        promote(csv, tmp_path / "unused.duckdb", 3)
    assert MODEL_NAME  # registre vierge : rien n'a été créé par la tentative


def test_exported_champion_is_servable_and_traceable(registry, tmp_path_factory):
    """EX-13 : le modèle exporté se charge dans l'API (empreinte vérifiée) et pointe sur son run."""
    from velov.api.main import load_model
    from velov.export import export

    tmp_path, csv = registry
    good = register(tmp_path, csv, "good")
    promote(csv, tmp_path / "unused.duckdb", 3, apply=True, validated_by="test")

    out = tmp_path / "exported"
    info = export("champion", out)
    assert info["version"] == good.version
    assert info["run_id"] == good.run_id

    _, metadata = load_model(out)  # lève si l'empreinte du paquet ne correspond pas
    assert metadata["registry"]["run_id"] == good.run_id
    assert metadata["params"]["seed"] == 42
