"""Ingestion bronze GBFS : aucun accès réseau (le fetch est remplacé par un faux)."""

from __future__ import annotations

import json

import pytest

from velov.ingest import GBFS_INDEX_URL, IngestError, bronze_path, ingest

STATUS_URL = "https://example.test/station_status.json"
INFO_URL = "https://example.test/station_information.json"
LAST_UPDATED = 1793024022  # 2026-10-26 13:53:42 UTC


def _index(version: str = "2.3") -> bytes:
    feeds = [{"name": "station_status", "url": STATUS_URL}, {"name": "station_information", "url": INFO_URL}]
    return json.dumps({"last_updated": 1, "version": version, "data": {"fr": {"feeds": feeds}}}).encode()


def _payload(last_updated: int = LAST_UPDATED, bikes: int = 3) -> bytes:
    stations = [{"station_id": "1024", "num_bikes_available": bikes}]
    return json.dumps({"last_updated": last_updated, "version": "2.3", "data": {"stations": stations}}).encode()


class FakeHttp:
    def __init__(self, responses: dict[str, bytes | Exception]):
        self.responses = responses
        self.calls: list[str] = []

    def __call__(self, url: str) -> bytes:
        self.calls.append(url)
        out = self.responses[url]
        if isinstance(out, Exception):
            raise out
        return out


@pytest.fixture
def http() -> FakeHttp:
    return FakeHttp({GBFS_INDEX_URL: _index(), STATUS_URL: _payload(), INFO_URL: _payload(bikes=0)})


def _files(bronze):
    return sorted(p for p in bronze.rglob("*") if p.is_file())


def test_deux_passages_le_second_ecrit_zero(tmp_path, http):
    first = ingest(tmp_path, fetch=http)
    second = ingest(tmp_path, fetch=http)
    assert (first.written, first.already_present) == (2, 0)
    assert (second.written, second.already_present) == (0, 2)
    assert len(_files(tmp_path)) == 2


def test_meme_last_updated_contenu_different_pas_d_ecrasement(tmp_path, http):
    ingest(tmp_path, fetch=http)
    path = bronze_path(tmp_path, "station_status", LAST_UPDATED)
    original = path.read_bytes()
    http.responses[STATUS_URL] = _payload(bikes=99)  # même clé, valeur différente
    report = ingest(tmp_path, fetch=http)
    assert report.written == 0
    assert path.read_bytes() == original


def test_aucun_tmp_ne_reste(tmp_path, http):
    ingest(tmp_path, fetch=http)
    assert not list(tmp_path.rglob("*.tmp"))


def test_le_chemin_depend_de_last_updated_pas_de_l_heure_du_run(tmp_path, http):
    old = 1_700_000_000  # 2023-11-14 22:13:20 UTC, très différent d'aujourd'hui
    http.responses[STATUS_URL] = _payload(last_updated=old)
    http.responses[INFO_URL] = _payload(last_updated=old)
    ingest(tmp_path, fetch=http)
    assert (tmp_path / "station_status" / "dt=2023-11-14" / f"{old}.json").is_file()
    assert (tmp_path / "station_information" / "dt=2023-11-14" / f"{old}.json").is_file()
    assert [p.name for p in tmp_path.glob("*/dt=*")] == ["dt=2023-11-14"] * 2  # aucune partition « aujourd'hui »


def test_contenu_ecrit_tel_que_recu(tmp_path, http):
    ingest(tmp_path, fetch=http)
    assert bronze_path(tmp_path, "station_status", LAST_UPDATED).read_bytes() == _payload()


def test_erreur_reseau_exception_propre_aucun_fichier(tmp_path, http):
    http.responses[STATUS_URL] = IngestError("timeout")
    with pytest.raises(IngestError):
        ingest(tmp_path, fetch=http)
    assert not _files(tmp_path)


def test_json_invalide(tmp_path, http):
    http.responses[STATUS_URL] = b"<html>502</html>"
    with pytest.raises(IngestError):
        ingest(tmp_path, fetch=http)
    assert not _files(tmp_path)


def test_version_gbfs_inattendue(tmp_path, http):
    http.responses[GBFS_INDEX_URL] = _index(version="3.0")
    with pytest.raises(IngestError, match="Version GBFS"):
        ingest(tmp_path, fetch=http)
    assert not _files(tmp_path)


def test_last_updated_absent(tmp_path, http):
    http.responses[STATUS_URL] = json.dumps({"data": {"stations": []}}).encode()
    with pytest.raises(IngestError, match="last_updated"):
        ingest(tmp_path, fetch=http)
    assert not _files(tmp_path)
