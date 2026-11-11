from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import ClassVar, Literal, Self

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from tweet_extractor.i18n import t

ProviderBackend = Literal["twscrape", "official"]

# La raíz del repo, si se corre desde el repo (uv run, fastapi dev, el native host). Instalado
# como paquete no hay repo: `parents[2]` cae en otra carpeta, sin `pyproject.toml`.
_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT: Path | None = _ROOT if (_ROOT / "pyproject.toml").is_file() else None

# Instalado con el instalador (punto 14 de docs/PLAN.md, `deploy/bundle/`): el Python propio
# vive en `<raíz>/python`, la raíz tiene la marca `xport-bundle.json` y la extensión armada va
# en `<raíz>/extension`.
BUNDLE_MARK = "xport-bundle.json"


def find_bundle_root(prefix: Path) -> Path | None:
    root = prefix.resolve().parent
    return root if (root / BUNDLE_MARK).is_file() else None


BUNDLE_ROOT: Path | None = find_bundle_root(Path(sys.prefix))

# La extensión armada para Chrome que el servicio le carga a su navegador sin ventana (punto 13
# de docs/PLAN.md): la de `bash deploy/extension-build.sh` en el repo, o la del instalador.
DEFAULT_EXTENSION_DIR: Path | None = (
    REPO_ROOT / "extension" / "dist" / "chrome-mv3"
    if REPO_ROOT
    else BUNDLE_ROOT / "extension"
    if BUNDLE_ROOT
    else None
)


def default_data_dir() -> Path:
    """La carpeta fija de datos de este usuario (regla 1 de docs/PLAN.md: un solo registro
    del tope). Es la misma para el servicio, la CLI y el native host, sin importar desde
    qué carpeta se arranquen. No se lee `XDG_DATA_HOME`: el navegador lanza el native host
    con otro entorno que la terminal, y una variable que uno ve y el otro no partiría el
    registro en dos."""
    if sys.platform == "win32":
        local = os.environ.get("LOCALAPPDATA")
        return (Path(local) if local else Path.home() / "AppData" / "Local") / "xport"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "xport"
    return Path.home() / ".local" / "share" / "xport"


# El `.env` se lee siempre del mismo lugar, no de la carpeta desde donde se arranca: si no, la
# CLI corrida desde otra carpeta no veía la misma configuración que el servicio. En el repo, el
# del repo; instalado, el de la carpeta de datos (un acceso directo arranca en cualquier lado).
ENV_FILE: Path = REPO_ROOT / ".env" if REPO_ROOT else default_data_dir() / ".env"


def _legacy_data_dirs() -> list[Path]:
    """Dónde quedaban los datos hasta noviembre de 2026: `data/`, relativo a la carpeta
    desde donde se arrancaba (la raíz del repo, para el servicio y el native host)."""
    dirs = [Path.cwd() / "data"]
    if REPO_ROOT is not None:
        dirs.insert(0, REPO_ROOT / "data")
    return list(dict.fromkeys(d.absolute() for d in dirs))


def _in_data_dir(data_dir: Path, path: Path) -> Path:
    """Las rutas relativas son relativas a la carpeta de datos. Las del `.env.example`
    viejo empezaban con `data/` (todo vivía en `./data/`): ese primer tramo se saca, así un
    `.env` copiado antes apunta a lo mismo que los valores por defecto."""
    path = path.expanduser()
    if path.is_absolute():
        return path
    if path.parts and path.parts[0] == "data":
        path = Path(*path.parts[1:])
    return data_dir / path


# Los campos de Settings que son rutas dentro de la carpeta de datos.
_DATA_PATHS = (
    "audit_db_path",
    "data_db_path",
    "accounts_db_path",
    "csv_dir",
    "captures_dir",
    "queue_db_path",
    "browser_profile_dir",
)


class Settings(BaseSettings):
    """Configuración de la app. Lee el `.env` del repo y variables de entorno."""

    model_config = SettingsConfigDict(env_file=ENV_FILE, extra="ignore")

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

    # Carpeta de datos (`XPORT_DATA_DIR`). Las rutas de abajo, si son relativas, cuelgan
    # de acá; absolutas (el compose) se usan tal cual.
    xport_data_dir: Path = Field(default_factory=default_data_dir)
    audit_db_path: Path = Path("audit/ledger.db")
    data_db_path: Path = Path("tweets.db")
    accounts_db_path: Path = Path("accounts.db")  # cuentas/cookies de twscrape
    csv_dir: Path = Path("csv")  # raíz de salida de los CSV (el service usa un subdir por job)
    captures_dir: Path = Path("captures")  # CSV exportados de la captura in-page (patrón C)
    queue_db_path: Path = Path("queue.db")  # la cola de capturas (sobrevive a un reinicio)
    browser_profile_dir: Path = Path("browser")  # el perfil del navegador de xport (sesión de X)
    # El navegador sin ventana que maneja el servicio (`service/browser.py`): con la cola no
    # vacía, levanta Chrome, Chromium o Edge con la extensión y captura sin ninguna ventana.
    # Apagado (`XPORT_BROWSER=false`), la cola la toma la extensión del navegador de siempre.
    xport_browser: bool = True
    xport_browser_bin: Path | None = None  # si no, el primero que encuentre en el PATH
    xport_browser_cage: Path | None = None  # la pantalla virtual (cage); si no, la del PATH
    xport_extension_dir: Path | None = DEFAULT_EXTENSION_DIR
    # Notificación del sistema al terminar o cortarse una captura de la cola.
    xport_notify: bool = True
    # De dónde mudar los datos viejos (`data/`) la primera vez (ver `datadir.py`).
    xport_legacy_dirs: list[Path] = Field(default_factory=_legacy_data_dirs)
    # Puertos donde el servicio busca otro xport antes de arrancar: el de `fastapi dev` y
    # el native host (8000) y el del compose (8080). Dos servicios serían dos registros.
    xport_peer_ports: list[int] = [8000, 8080]
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

    # twscrape: cookies de una cuenta de X aparte de la tuya (.env).
    x_auth_token: str | None = None
    x_ct0: str | None = None
    # X API v2 oficial (backend `official`, paga; sin probar contra la API real): Bearer token.
    x_api_bearer_token: str | None = None

    @field_validator("xport_data_dir")
    @classmethod
    def _data_dir_absoluta(cls, value: Path) -> Path:
        # Una carpeta relativa dependería otra vez de desde dónde se arranca.
        value = value.expanduser()
        if not value.is_absolute():
            raise ValueError(t("config.data_dir_absolute", path=value))
        return value

    @model_validator(mode="after")
    def _rutas_en_la_carpeta_de_datos(self) -> Self:
        for name in _DATA_PATHS:
            setattr(self, name, _in_data_dir(self.xport_data_dir, getattr(self, name)))
        return self

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
