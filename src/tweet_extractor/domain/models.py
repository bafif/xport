from __future__ import annotations

import re
from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")


def _limpiar(v: str) -> str:
    """Trimea whitespace de borde y rechaza caracteres de control (C0/DEL): un
    salto/CR/TAB embebido corrompería la PK o los identificadores y partiría una
    fila del CSV. NO se aplica a `content` (texto libre del tweet, que se preserva)."""
    s = v.strip()
    if _CONTROL_CHARS.search(s):
        raise ValueError("no puede contener caracteres de control")
    return s


class TweetLink(BaseModel):
    """Un link embebido normalizado (de `legacy.entities.urls[]`).

    Normaliza whitespace de borde de `url`/`expanded_url` y rechaza vacíos. El
    filtrado de `expanded_url` autorreferencial (apunta de vuelta a un status de
    x.com) es del mapper; el modelo solo garantiza forma (no vacío, sin bordes).
    `extra="forbid"`: un campo desconocido (typo del mapper) falla, no se descarta.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    url: str  # el t.co
    expanded_url: str  # destino real (el que se exporta)
    display_url: str | None = None  # versión legible, opcional

    @field_validator("url", "expanded_url")
    @classmethod
    def _no_vacio(cls, v: str) -> str:
        s = _limpiar(v)
        if not s:
            raise ValueError("url y expanded_url no pueden estar vacíos")
        return s

    @field_validator("display_url")
    @classmethod
    def _display_normalizado(cls, v: str | None) -> str | None:
        # Opcional/cosmético: normaliza whitespace de borde; whitespace-only -> None
        # (ausencia), coherente con la higiene de forma del resto de los campos.
        if v is None:
            return None
        return _limpiar(v) or None


class Tweet(BaseModel):
    """El tweet normalizado: única fuente de verdad del shape (ver CLAUDE.md).

    Value object inmutable. Valida invariantes de *forma* — no parsing de
    GraphQL, que vive solo en `mappers/`: `created_at` es un `datetime`
    timezone-aware (no epoch ni string) normalizado a UTC, `id`/`account`
    trimeados y no vacíos, `quoted_*` no vacíos si están presentes, y coherencia
    del quote (id y url juntos o ninguno). `content` es texto libre y PUEDE ser
    vacío (tweets de solo-media/link/quote); no se trimea (es contenido, no un
    identificador). `extra="forbid"`: un campo desconocido falla, no se descarta.
    Alcance minimalista (columnas del CSV + `id` como PK de storage); `is_quote`
    es propiedad computada; sin `is_retweet` ni `media_urls`. No es hasheable (el
    campo `links: list` lo impide); la dedupe es por `id` en storage, no en memoria.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str  # PK; snowflake como string (no int: precisión)
    account: str  # handle sin @
    created_at: datetime  # tz-aware; normalizado a UTC
    content: str  # texto libre; puede ser vacío; NO se trimea (es contenido)
    links: list[TweetLink] = []
    quoted_tweet_id: str | None = None
    quoted_tweet_url: str | None = None

    @property
    def is_quote(self) -> bool:
        """Deriva del id citado; nunca se desincroniza de un campo almacenado."""
        return self.quoted_tweet_id is not None

    @field_validator("id", "account")
    @classmethod
    def _no_vacio(cls, v: str) -> str:
        s = _limpiar(v)
        if not s:
            raise ValueError("id y account no pueden estar vacíos")
        return s

    @field_validator("quoted_tweet_id", "quoted_tweet_url")
    @classmethod
    def _quote_no_vacio(cls, v: str | None) -> str | None:
        # None = no hay quote. Si está presente, no puede ser vacío (quote degenerado):
        # se rechaza (fail-fast, D2) en vez de silenciarlo dejando is_quote en True.
        if v is None:
            return None
        s = _limpiar(v)
        if not s:
            raise ValueError("quoted_tweet_id/url, si presentes, no pueden ser vacíos")
        return s

    @field_validator("created_at", mode="before")
    @classmethod
    def _es_datetime(cls, v: object) -> object:
        # fail-fast (D2): el mapper debe pasar un datetime ya parseado. Un epoch crudo
        # (int seg o ms) o un string se coercionarían en silencio a una fecha plausible
        # —un mixup seg/ms daría un timestamp errado sin señal—; se rechazan.
        if not isinstance(v, datetime):
            raise ValueError("created_at debe ser datetime (parsealo en el mapper, no epoch/str)")
        return v

    @field_validator("created_at")
    @classmethod
    def _aware_utc(cls, v: datetime) -> datetime:
        # `utcoffset() is None` es el test canónico de "naive" (más fuerte que
        # `tzinfo is None`: atrapa también un tzinfo cuyo utcoffset() devuelve None).
        if v.utcoffset() is None:
            raise ValueError("created_at debe ser timezone-aware (UTC en almacenamiento)")
        try:
            return v.astimezone(UTC)
        except (OverflowError, TypeError) as e:
            # astimezone desborda cerca de año 1/9999 con offset, o falla con un tzinfo
            # roto: lo re-lanzamos como ValueError para honrar el contrato §5 (toda
            # violación se manifiesta como ValidationError, no como excepción cruda).
            raise ValueError("created_at fuera de rango representable en UTC") from e

    @model_validator(mode="after")
    def _quoted_coherente(self) -> Tweet:
        if (self.quoted_tweet_id is None) != (self.quoted_tweet_url is None):
            raise ValueError("quoted_tweet_id y quoted_tweet_url deben venir juntos o ninguno")
        return self
