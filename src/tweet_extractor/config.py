from __future__ import annotations

from pathlib import Path
from typing import ClassVar, Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

ProviderBackend = Literal["twscrape", "official"]


class Settings(BaseSettings):
    """Configuración de la app. Lee `.env` y variables de entorno."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # --- Constantes de cumplimiento (regla 1 de docs/PLAN.md) ------------------------
    # Son ClassVar a propósito: NO son campos pydantic, así que NINGÚN env var ni
    # `.env` puede subir el cap, encoger la ventana ni bajar la cota de accesos.
    # Debilitar el gate exige cambiar el código (y revisión), nunca el entorno.
    HARD_CAP: ClassVar[int] = 900_000  # margen bajo el millón de los términos de X; no se sube
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
    captures_dir: Path = Path("data/captures")  # CSV exportados de la captura in-page (patrón C)
    page_size: int = 20
    subwindow_days: int = 7  # paso default (días) del troceado de búsqueda

    # CORS del FastAPI: orígenes permitidos para la extensión de navegador.
    # Vacío por default (sin middleware CORS) = seguro. El CORS lo maneja FastAPI,
    # NUNCA Nginx (regla 6 de docs/PLAN.md: no duplicar headers CORS entre ambos).
    cors_allow_origins: list[str] = []

    # Hosts que el servicio acepta en el encabezado `Host`. Escucha solo en la propia
    # computadora, pero una página web con DNS rebinding podría hablarle con su propio
    # dominio apuntando a 127.0.0.1: con esto, esos pedidos reciben 400.
    allowed_hosts: list[str] = ["localhost", "127.0.0.1"]

    # Backend de datos: el ÚNICO punto de intercambio scraping ↔ API oficial (la
    # factory lo lee). Un valor inválido falla la validación de pydantic.
    provider_backend: ProviderBackend = "twscrape"

    # twscrape (sin API paga): cookies de una cuenta aparte (.env).
    x_auth_token: str | None = None
    x_ct0: str | None = None
    # X API v2 oficial (backend `official`, todavía un stub): Bearer token.
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
