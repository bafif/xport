from __future__ import annotations

from pathlib import Path


def test_settings_defaults(monkeypatch):
    for k in (
        "HARD_CAP",
        "WINDOW_S",
        "PAGE_SIZE",
        "X_AUTH_TOKEN",
        "X_CT0",
        "ACCOUNTS_DB_PATH",
        "SUBWINDOW_DAYS",
        "XPORT_DATA_DIR",
    ):
        monkeypatch.delenv(k, raising=False)

    from tweet_extractor.config import Settings, default_data_dir

    s = Settings(_env_file=None)
    assert s.hard_cap == 900_000
    assert s.window_s == 86_400
    assert s.page_size == 20
    assert s.max_accessed_per_page == 60  # 20 * 3 (citante + 2 niveles de quote)
    assert s.x_auth_token is None
    assert s.xport_data_dir == default_data_dir()
    assert s.accounts_db_path == default_data_dir() / "accounts.db"
    assert s.subwindow_days == 7


def test_settings_lee_env_vars(monkeypatch):
    monkeypatch.setenv("PAGE_SIZE", "50")
    monkeypatch.setenv("X_AUTH_TOKEN", "abc123")

    from tweet_extractor.config import Settings

    s = Settings(_env_file=None)
    assert s.page_size == 50
    assert s.max_accessed_per_page == 150  # 50 * 3
    assert s.x_auth_token == "abc123"


def test_hard_cap_y_window_no_son_configurables_por_entorno(monkeypatch):
    # Regla #1: el cap y la ventana NO pueden debilitarse vía entorno.
    monkeypatch.setenv("HARD_CAP", "100000000")
    monkeypatch.setenv("WINDOW_S", "1")

    from tweet_extractor.config import Settings

    s = Settings(_env_file=None)
    assert s.hard_cap == 900_000  # ignora el env var
    assert s.window_s == 86_400


def test_carpeta_de_datos_por_sistema(monkeypatch, tmp_path):
    from pathlib import Path

    from tweet_extractor import config

    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(config.sys, "platform", "linux")
    assert config.default_data_dir() == tmp_path / ".local/share/xport"
    monkeypatch.setattr(config.sys, "platform", "darwin")
    assert config.default_data_dir() == tmp_path / "Library/Application Support/xport"
    monkeypatch.setattr(config.sys, "platform", "win32")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "Local"))
    assert config.default_data_dir() == tmp_path / "Local/xport"


def test_rutas_en_la_carpeta_de_datos_sin_importar_desde_donde(monkeypatch, tmp_path):
    # Regla 1 de docs/PLAN.md: desde cualquier carpeta, el mismo registro del tope.
    from tweet_extractor.config import Settings

    data = tmp_path / "xport-data"
    monkeypatch.setenv("XPORT_DATA_DIR", str(data))
    paths = []
    for cwd in (tmp_path / "a", tmp_path / "b"):
        cwd.mkdir()
        monkeypatch.chdir(cwd)
        s = Settings(_env_file=None)
        paths.append((s.audit_db_path, s.data_db_path, s.captures_dir))
    assert paths[0] == paths[1] == (data / "audit/ledger.db", data / "tweets.db", data / "captures")


def test_env_viejo_con_data_apunta_a_lo_mismo(monkeypatch, tmp_path):
    # Un .env copiado del .env.example anterior trae `data/...`: se lee como los defaults.
    from tweet_extractor.config import Settings

    data = tmp_path / "xport-data"
    monkeypatch.setenv("XPORT_DATA_DIR", str(data))
    monkeypatch.setenv("AUDIT_DB_PATH", "data/audit/ledger.db")
    monkeypatch.setenv("CSV_DIR", "data/csv")
    s = Settings(_env_file=None)
    assert s.audit_db_path == data / "audit/ledger.db"
    assert s.csv_dir == data / "csv"
    monkeypatch.setenv("AUDIT_DB_PATH", "/srv/audit/ledger.db")  # absoluta: tal cual
    assert Settings(_env_file=None).audit_db_path == Path("/srv/audit/ledger.db")


def test_carpeta_de_datos_relativa_es_error(monkeypatch):
    import pytest
    from pydantic import ValidationError

    from tweet_extractor.config import Settings

    monkeypatch.setenv("XPORT_DATA_DIR", "datos")
    with pytest.raises(ValidationError, match="absoluta"):
        Settings(_env_file=None)
