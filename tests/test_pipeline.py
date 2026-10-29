"""Tests du pipeline dbt sur un mini-bronze jetable (EX-10, EX-11, EX-12).

On fabrique quelques fichiers GBFS dans un dossier temporaire, on lance `dbt build` dessus avec une base DuckDB
jetable (variable VELOV_DUCKDB), et on lit la table gold. Aucun accès à data/ : les tests ne touchent pas
au vrai pipeline.
"""

from __future__ import annotations

import json
import shutil
import subprocess  # noqa: S404 (lance dbt, binaire du venv)
import sys
from datetime import UTC, datetime
from pathlib import Path

import duckdb
import pytest

DBT = Path(sys.executable).parent / "dbt"
PROJECT = Path(__file__).resolve().parents[1] / "velov_dbt"

pytestmark = pytest.mark.skipif(not DBT.exists(), reason="dbt absent (pip install -r requirements-dev.txt)")


def ts(hhmm: str, day: str = "2026-06-01") -> int:
    return int(datetime.fromisoformat(f"{day}T{hhmm}:00+00:00").timestamp())


def write(bronze: Path, feed: str, last_updated: int, data: dict) -> None:
    day = datetime.fromtimestamp(last_updated, tz=UTC).strftime("%Y-%m-%d")
    path = bronze / feed / f"dt={day}" / f"{last_updated}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"last_updated": last_updated, "data": data}))


def status(bronze: Path, hhmm: str, **bikes_by_station: int) -> None:
    stations = [
        {
            "station_id": sid.removeprefix("s"),
            "num_bikes_available": bikes,
            "num_docks_available": 20 - bikes,
            "is_installed": True,
            "is_renting": True,
            "is_returning": True,
            "last_reported": ts(hhmm),
        }
        for sid, bikes in bikes_by_station.items()
    ]
    write(bronze, "station_status", ts(hhmm), {"stations": stations})


def information(bronze: Path, hhmm: str, *station_ids: int, capacity: int = 20) -> None:
    write(
        bronze,
        "station_information",
        ts(hhmm),
        {"stations": [{"station_id": str(s), "capacity": capacity} for s in station_ids]},
    )


def weather(bronze: Path, hhmm: str, temperature: float = 15.0) -> None:
    write(bronze, "weather", ts(hhmm), {"temperature": temperature, "is_raining": False})


def dbt_build(workdir: Path) -> subprocess.CompletedProcess:
    env = {
        "PATH": str(DBT.parent),
        "VELOV_DUCKDB": str(workdir / "test.duckdb"),  # base jetable, jamais data/velov.duckdb
        "DBT_SEND_ANONYMOUS_USAGE_STATS": "false",
    }
    return subprocess.run(  # noqa: S603
        [
            str(DBT),
            "build",
            "--project-dir",
            str(PROJECT),
            "--profiles-dir",
            str(PROJECT),
            "--target-path",
            str(workdir / "target"),
            "--log-path",
            str(workdir / "logs"),
            "--vars",
            "{bronze_dir: bronze}",
        ],
        cwd=workdir,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def gold_rows(workdir: Path) -> list[tuple]:
    con = duckdb.connect(str(workdir / "test.duckdb"), read_only=True)
    try:
        return con.sql(
            'select station_id, epoch("timestamp")::bigint, capacity, bikes_available '
            'from gold.gold_velov_hourly order by station_id, "timestamp"'
        ).fetchall()
    finally:
        con.close()


@pytest.fixture
def workdir(tmp_path: Path) -> Path:
    yield tmp_path
    shutil.rmtree(tmp_path, ignore_errors=True)


def test_gold_never_uses_a_snapshot_after_the_prediction_time(workdir):
    """EX-11 : pour H = 13:00, l'instantané de 13:05 (99 vélos) ne doit jamais être lu."""
    bronze = workdir / "bronze"
    information(bronze, "12:00", 1, 2)
    status(bronze, "12:40", s1=3, s2=3)  # trop ancien pour H=13:00 (20 min > 15 min)
    status(bronze, "12:50", s1=5)  # station 1 : instantané valide, 10 min avant H
    status(bronze, "13:00", s1=7)  # pile à H : retenu (dernier instantané <= H)
    status(bronze, "13:05", s1=99, s2=99)  # POSTÉRIEUR à H=13:00 : piège
    weather(bronze, "13:00")
    weather(bronze, "14:00")

    result = dbt_build(workdir)
    assert result.returncode == 0, result.stdout[-2000:]

    # station 1 à 13:00 -> 7 (instantané de 13:00 pile). Station 2 : 12:40 trop vieux, 13:05 postérieur -> aucune ligne.
    assert gold_rows(workdir) == [(1, ts("13:00"), 20, 7)]


def test_rerun_leaves_gold_unchanged(workdir):
    """EX-10 : rejouer le pipeline sur la même période ne change pas la table gold."""
    bronze = workdir / "bronze"
    information(bronze, "11:00", 1, 2)
    for hhmm, (a, b) in {"12:55": (4, 9), "13:55": (6, 8)}.items():
        status(bronze, hhmm, s1=a, s2=b)
    weather(bronze, "13:00")
    weather(bronze, "14:00")

    assert dbt_build(workdir).returncode == 0
    first = gold_rows(workdir)
    assert len(first) == 4
    assert dbt_build(workdir).returncode == 0
    assert gold_rows(workdir) == first


def test_build_fails_when_the_join_drops_too_many_rows(workdir):
    """EX-12 : sans météo pour les heures observées, gold se vide : le test de seuil fait échouer le build."""
    bronze = workdir / "bronze"
    information(bronze, "11:00", 1)
    status(bronze, "12:55", s1=4)
    weather(bronze, "20:00")  # météo d'une autre heure : la jointure n'apparie rien

    result = dbt_build(workdir)
    assert result.returncode != 0
    assert "gold_keeps_enough_rows" in result.stdout
