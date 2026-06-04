from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Configuración de la app. Lee `.env` y variables de entorno."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    audit_db_path: Path = Path("data/audit/ledger.db")
    data_db_path: Path = Path("data/tweets.db")
    hard_cap: int = 900_000  # NO subir sin instrucción explícita del usuario
    window_s: int = 86_400
    page_size: int = 20
    x_auth_token: str | None = None
    x_ct0: str | None = None

    @property
    def max_accessed_per_page(self) -> int:
        """Cota superior de accesos por página (quote embebido de 1 nivel)."""
        return 2 * self.page_size
