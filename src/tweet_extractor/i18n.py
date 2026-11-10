"""Los textos que ve la persona, en castellano y en inglés.

Los catálogos son `locales/es.json` y `locales/en.json`, con las mismas claves y los mismos
`{marcadores}` (lo revisa `tests/test_i18n.py`). Cada texto se pide por su clave con `t()`.

El idioma, como en todas las apps de xport: castellano si alguno de los idiomas preferidos es
castellano, inglés en cualquier otro caso, y lo que la persona elige manda.

- **CLI y servicio:** `--lang` (solo la CLI), `XPORT_LANG`, o el locale del sistema
  (`LANGUAGE`, `LC_ALL`, `LC_MESSAGES`, `LANG`; en Windows, el idioma de la interfaz).
- **Respuestas HTTP:** el `Accept-Language` del pedido (la página y la extensión mandan el que
  eligió la persona); sin ese header, el del servicio.
- **Avisos guardados** (el motivo de un corte, el estado del navegador de xport): se guardan
  como `Note`, una clave con sus valores, y cada uno los muestra en su idioma al leerlos.
"""

from __future__ import annotations

import json
import os
import re
import sys
from collections.abc import Iterable, Mapping
from functools import cache
from pathlib import Path
from typing import Literal, cast

from pydantic import BaseModel, Field, ValidationError

Lang = Literal["es", "en"]
LANGS: tuple[Lang, ...] = ("es", "en")
LOCALES_DIR = Path(__file__).parent / "locales"

_SPANISH = re.compile(r"es(?:[-_.@]|$)", re.IGNORECASE)
_ENGLISH = re.compile(r"en(?:[-_.@]|$)", re.IGNORECASE)


@cache
def catalog(lang: Lang) -> dict[str, str]:
    """Todos los textos de un idioma, por clave."""
    raw = json.loads((LOCALES_DIR / f"{lang}.json").read_text(encoding="utf-8"))
    return cast(dict[str, str], raw)


def is_spanish(tag: str) -> bool:
    """`es`, `es-AR`, `es_AR.UTF-8`, `es-419`…"""
    return _SPANISH.match(tag.strip()) is not None


def pick(tags: Iterable[str]) -> Lang:
    """Castellano si alguno de los idiomas preferidos lo es; si no, inglés."""
    return "es" if any(is_spanish(t) for t in tags) else "en"


def parse_lang(value: str | None) -> Lang | None:
    """Un idioma elegido a mano (`es`, `en`, `es-AR`…), o `None` si no es ninguno de los dos."""
    value = (value or "").strip()
    if is_spanish(value):
        return "es"
    if _ENGLISH.match(value):
        return "en"
    return None


if sys.platform == "win32":

    def _windows_ui_lang() -> str:
        import ctypes

        try:
            langid = int(ctypes.windll.kernel32.GetUserDefaultUILanguage())
        except (AttributeError, OSError):
            return ""
        return "es" if langid & 0x3FF == 0x0A else "en"  # 0x0A: LANG_SPANISH

else:

    def _windows_ui_lang() -> str:
        return ""


def system_lang(env: Mapping[str, str] | None = None) -> Lang:
    """El idioma del servicio y de la CLI: `XPORT_LANG` si está, o el locale del sistema."""
    env = os.environ if env is None else env
    forced = parse_lang(env.get("XPORT_LANG"))
    if forced is not None:
        return forced
    tags = [t for t in env.get("LANGUAGE", "").split(":") if t]
    for var in ("LC_ALL", "LC_MESSAGES", "LANG"):
        if env.get(var):
            tags.append(env[var])
            break
    if not tags:
        tags.append(_windows_ui_lang())
    return pick(tags)


def accept_lang(header: str | None) -> Lang | None:
    """El idioma de un `Accept-Language`, o `None` si no trae ninguno."""
    tags = []
    for part in (header or "").split(","):
        tag, _, rest = part.strip().partition(";")
        q = re.search(r"q=([0-9.]+)", rest)
        if tag and not (q and float(q.group(1) or 0) == 0):
            tags.append(tag)
    return pick(tags) if tags else None


_current: Lang | None = None


def set_lang(lang: Lang) -> None:
    global _current
    _current = lang


def current() -> Lang:
    """El idioma de este proceso (el de la CLI o el del servicio)."""
    global _current
    if _current is None:
        _current = system_lang()
    return _current


def t(key: str, lang: Lang | None = None, /, **params: object) -> str:
    """El texto de `key` en `lang` (por defecto, el del proceso) con sus valores."""
    text = catalog(lang or current()).get(key) or catalog("en").get(key) or key
    return text.format(**params)


def number(n: int, lang: Lang | None = None) -> str:
    """Un entero con separador de miles: «1.234» en castellano, «1,234» en inglés."""
    s = f"{n:,}"
    return s.replace(",", ".") if (lang or current()) == "es" else s


class Note(BaseModel):
    """Un aviso que se guarda y se muestra después, en el idioma de quien lo lee: la clave
    del catálogo y sus valores (un valor puede ser otro aviso). Lo que se guardó como texto
    suelto antes de los dos idiomas vuelve como `note.text`."""

    key: str = Field(max_length=64, pattern=r"^[a-z][a-z0-9_.]*$")
    params: dict[str, str | int | Note] = Field(default_factory=dict)

    def known(self) -> bool:
        """Si la clave, y la de cada aviso de adentro, están en el catálogo."""
        return self.key in catalog("en") and all(
            v.known() for v in self.params.values() if isinstance(v, Note)
        )

    def render(self, lang: Lang | None = None) -> str:
        values = {
            k: v.render(lang)
            if isinstance(v, Note)
            else number(v, lang)
            if isinstance(v, int)
            else v
            for k, v in self.params.items()
        }
        return t(self.key, lang, **values)

    def dump(self) -> str:
        return self.model_dump_json(exclude_defaults=True)

    @classmethod
    def load(cls, raw: str | None) -> Note | None:
        if raw is None:
            return None
        try:
            return cls.model_validate_json(raw)
        except ValidationError:
            return cls.text(raw)

    @classmethod
    def text(cls, raw: str) -> Note:
        return cls(key="note.text", params={"text": raw})
