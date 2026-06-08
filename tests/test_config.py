from __future__ import annotations


def test_settings_defaults(monkeypatch):
    for k in ("HARD_CAP", "WINDOW_S", "PAGE_SIZE", "X_AUTH_TOKEN", "X_CT0"):
        monkeypatch.delenv(k, raising=False)

    from tweet_extractor.config import Settings

    s = Settings(_env_file=None)
    assert s.hard_cap == 900_000
    assert s.window_s == 86_400
    assert s.page_size == 20
    assert s.max_accessed_per_page == 60  # 20 * 3 (citante + 2 niveles de quote)
    assert s.x_auth_token is None


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
