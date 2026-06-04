from __future__ import annotations


def test_settings_defaults(monkeypatch):
    for k in ("HARD_CAP", "WINDOW_S", "PAGE_SIZE", "X_AUTH_TOKEN", "X_CT0"):
        monkeypatch.delenv(k, raising=False)

    from tweet_extractor.config import Settings

    s = Settings(_env_file=None)
    assert s.hard_cap == 900_000
    assert s.window_s == 86_400
    assert s.page_size == 20
    assert s.max_accessed_per_page == 40
    assert s.x_auth_token is None


def test_settings_lee_env_vars(monkeypatch):
    monkeypatch.setenv("PAGE_SIZE", "50")
    monkeypatch.setenv("X_AUTH_TOKEN", "abc123")

    from tweet_extractor.config import Settings

    s = Settings(_env_file=None)
    assert s.page_size == 50
    assert s.max_accessed_per_page == 100
    assert s.x_auth_token == "abc123"
