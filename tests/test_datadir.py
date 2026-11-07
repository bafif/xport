from __future__ import annotations

import sqlite3
import time
from contextlib import closing
from pathlib import Path

from fastapi.testclient import TestClient

from tests.service.conftest import make_settings
from tweet_extractor.compliance.gate import LEDGER_SCHEMA
from tweet_extractor.config import Settings
from tweet_extractor.datadir import migrate_legacy_data
from tweet_extractor.service.app import create_app


def _ledger(path: Path, *counts: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    now = int(time.time())
    with closing(sqlite3.connect(path)) as db:
        db.executescript(LEDGER_SCHEMA)
        db.executemany(
            "INSERT INTO access_ledger(ts, count) VALUES(?, ?)", [(now, c) for c in counts]
        )
        db.commit()


def _accesses(path: Path) -> int:
    with closing(sqlite3.connect(path)) as db:
        return int(db.execute("SELECT COALESCE(SUM(count), 0) FROM access_ledger").fetchone()[0])


def _settings(tmp_path: Path, *legacy: Path) -> Settings:
    return Settings(
        _env_file=None, xport_data_dir=tmp_path / "nueva", xport_legacy_dirs=list(legacy)
    )


def test_mueve_el_data_viejo_y_no_deja_copia(tmp_path: Path) -> None:
    old = tmp_path / "repo" / "data"
    _ledger(old / "audit" / "ledger.db", 5, 7)
    (old / "tweets.db").write_bytes(b"tweets")
    (old / "captures").mkdir()
    (old / "captures" / "nasa_2025-01-01_2025-02-01.csv").write_text("csv")
    s = _settings(tmp_path, old)

    notes = migrate_legacy_data(s)

    assert _accesses(s.audit_db_path) == 12
    assert s.data_db_path.read_bytes() == b"tweets"
    assert (s.captures_dir / "nasa_2025-01-01_2025-02-01.csv").read_text() == "csv"
    assert not old.exists()  # se movió todo: no quedan dos registros
    assert len(notes) == 3
    assert migrate_legacy_data(s) == []  # la segunda vez no hay nada que mover


def test_dos_registros_se_suman_en_uno(tmp_path: Path) -> None:
    # La CLI corrida desde otra carpeta había creado otro registro: sus accesos no se
    # pierden ni se dejan aparte, se suman al de la carpeta de datos.
    old = tmp_path / "otra" / "data"
    _ledger(old / "audit" / "ledger.db", 30)
    (old / "tweets.db").write_bytes(b"viejo")
    s = _settings(tmp_path, old)
    _ledger(s.audit_db_path, 100)
    s.data_db_path.write_bytes(b"nuevo")

    notes = migrate_legacy_data(s)

    assert _accesses(s.audit_db_path) == 130
    assert not (old / "audit" / "ledger.db").exists()
    assert s.data_db_path.read_bytes() == b"nuevo"  # las bases de tweets no se mezclan
    assert (old / "tweets.db").exists()
    assert any("dejé" in n for n in notes)


def test_no_toca_un_data_ajeno(tmp_path: Path) -> None:
    other = tmp_path / "proyecto" / "data"
    other.mkdir(parents=True)
    (other / "tweets.db").write_bytes(b"de otro programa")
    s = _settings(tmp_path, other)

    assert migrate_legacy_data(s) == []
    assert (other / "tweets.db").exists()
    assert not s.data_db_path.exists()


def test_el_servicio_muda_el_registro_viejo_al_arrancar(tmp_path: Path) -> None:
    old = tmp_path / "repo" / "data"
    _ledger(old / "audit" / "ledger.db", 42)
    settings = make_settings(tmp_path / "svc", xport_legacy_dirs=[old])
    with TestClient(create_app(settings)) as client:
        assert client.get("/gate").json()["usage"] == 42
    assert not (old / "audit" / "ledger.db").exists()
