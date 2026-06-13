from __future__ import annotations

from pathlib import Path
from typing import ClassVar, Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

ProviderBackend = Literal["twscrape", "official"]


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
    accounts_db_path: Path = Path("data/accounts.db")  # cuentas/cookies de twscrape (git-ignored)
    csv_dir: Path = Path("data/csv")  # raíz de salida de los CSV (el service usa un subdir por job)
    page_size: int = 20
    subwindow_days: int = 7  # paso default (días) del troceado de búsqueda

    # CORS del FastAPI: orígenes permitidos para la extensión de navegador (Fase 4).
    # Vacío por default (sin middleware CORS) = seguro. El CORS lo maneja FastAPI,
    # NUNCA Nginx (regla de CLAUDE.md: no duplicar headers CORS entre ambos).
    cors_allow_origins: list[str] = []

    # Backend de datos: el ÚNICO punto de intercambio scraping ↔ API oficial (la
    # factory lo lee). Un valor inválido falla la validación de pydantic.
    provider_backend: ProviderBackend = "twscrape"

    # twscrape (scraping gratis): cookies de una cuenta descartable (.env).
    x_auth_token: str | None = None
    x_ct0: str | None = None
    # X API v2 oficial (backend `official`, stub de Fase 2): Bearer token.
    x_api_bearer_token: str | None = None

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
