"""Data contract Pandera de la table gold, et refus d'entraîner sur une table qui le viole (EX-12)."""

import pandas as pd
import pytest

from velov.contract import ContractError, validate_gold
from velov.data import simulate
from velov.train import train_from_gold


@pytest.fixture
def gold() -> pd.DataFrame:
    return simulate(days=3, n_stations=2)


def test_simulator_output_respects_the_contract(gold):
    assert len(validate_gold(gold)) == len(gold)


def test_integer_width_is_not_a_violation(gold):
    narrow = gold.astype({"station_id": "int32", "capacity": "int32", "bikes_available": "int32"})
    assert len(validate_gold(narrow)) == len(gold)


@pytest.mark.parametrize(
    ("mutate", "expected"),
    [
        (lambda d: d.assign(bikes_available=d["capacity"] + 1), "bikes_available <= capacity"),
        (lambda d: d.assign(temperature=400.0), "in_range"),
        (lambda d: d.assign(capacity=0), "greater_than"),
        (lambda d: d.assign(capacity=d["capacity"].astype(float)), "int64"),
        (lambda d: d.drop(columns="is_raining"), "is_raining"),
        (lambda d: d.assign(extra=1), "extra"),
        (lambda d: d.assign(timestamp=d["timestamp"] + pd.Timedelta(minutes=7)), "heure ronde"),
        (lambda d: pd.concat([d, d.iloc[[0]]], ignore_index=True), "unique"),
        (lambda d: d.assign(temperature=d["temperature"].where(d.index > 0)), "temperature"),
    ],
)
def test_contract_violations_are_rejected(gold, mutate, expected):
    with pytest.raises(ContractError, match=expected):
        validate_gold(mutate(gold))


def test_train_refuses_a_table_that_violates_the_contract(tmp_path, monkeypatch):
    bad = simulate(days=5, n_stations=2).assign(temperature=400.0)
    monkeypatch.setattr("velov.train.load_gold", lambda _db: bad)
    out = tmp_path / "models"
    with pytest.raises(ContractError):
        train_from_gold(tmp_path / "unused.duckdb", out, test_days=1)
    assert not out.exists()  # aucun artefact écrit
