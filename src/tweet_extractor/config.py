from __future__ import annotations

from pathlib import Path
from typing import ClassVar

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Configuración de la app. Lee `.env` y variables de entorno."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # --- Constantes de cumplimiento (regla #1 de CLAUDE.md) ---------------------
    # Son ClassVar a propósito: NO son campos pydantic, así que NINGÚN env var ni
    # `.env` puede subir el cap, encoger la ventana ni bajar la cota de accesos.
    # Debilitar el gate exige cambiar el código (y revisión), nunca el entorno.
    HARD_CAP: ClassVar[int] = 900_000  # NO subir sin instrucción explícita del usuario
    WINDOW_S: ClassVar[int] = 86_400
    # Cota SUPERIOR de objetos accedidos por tweet de una página: el citante más
    # quotes embebidos que la respuesta puede hidratar (citante + 2 niveles). Tiene
    # que sobre-estimar: sub-estimarla haría que el gate reserve de menos y deje
    # cruzar el cap; sobre-estimar solo gasta presupuesto de más (fail-closed).
    ACCESS_FACTOR_PER_TWEET: ClassVar[int] = 3

    audit_db_path: Path = Path("data/audit/ledger.db")
    data_db_path: Path = Path("data/tweets.db")
    page_size: int = 20
    x_auth_token: str | None = None
    x_ct0: str | None = None

    @property
    def hard_cap(self) -> int:
        return self.HARD_CAP

    @property
    def window_s(self) -> int:
        return self.WINDOW_S

    @property
    def max_accessed_per_page(self) -> int:
        """Cota superior REAL de accesos por página (fail-closed): la página
        entera más sus quotes embebidos. Sobre-estima a propósito."""
        return self.page_size * self.ACCESS_FACTOR_PER_TWEET
