from __future__ import annotations

from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict, field_validator, model_validator


class TweetLink(BaseModel):
    """Un link embebido normalizado (de `legacy.entities.urls[]`).

    El filtrado de `expanded_url` vacío/autorreferencial (apunta de vuelta a un
    status de x.com) es responsabilidad del mapper; el modelo solo rechaza el
    string vacío como invariante mínima de forma.
    """

    model_config = ConfigDict(frozen=True)

    url: str  # el t.co
    expanded_url: str  # destino real (el que se exporta)
    display_url: str | None = None  # versión legible, opcional

    @field_validator("url", "expanded_url")
    @classmethod
    def _no_vacio(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("url y expanded_url no pueden estar vacíos")
        return v


class Tweet(BaseModel):
    """El tweet normalizado: única fuente de verdad del shape (ver CLAUDE.md).

    Value object inmutable. Valida invariantes de *forma* — no parsing de
    GraphQL, que vive solo en `mappers/`: `created_at` tz-aware en UTC,
    `id`/`account` no vacíos, y coherencia del quote (id y url juntos o ninguno).
    Alcance minimalista (columnas del CSV + `id` como PK de storage); `is_quote`
    es propiedad computada; sin `is_retweet` (los RT se excluyen en el mapper) ni
    `media_urls` (ODQ 4 abierta).
    """

    model_config = ConfigDict(frozen=True)

    id: str  # PK; snowflake como string (no int: precisión)
    account: str  # handle sin @
    created_at: datetime  # tz-aware; normalizado a UTC
    content: str  # texto completo (note_tweet si aplica) — lo resuelve el mapper
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
        if not v.strip():
            raise ValueError("id y account no pueden estar vacíos")
        return v

    @field_validator("created_at")
    @classmethod
    def _aware_utc(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            raise ValueError("created_at debe ser timezone-aware (UTC en almacenamiento)")
        return v.astimezone(UTC)

    @model_validator(mode="after")
    def _quoted_coherente(self) -> Tweet:
        if (self.quoted_tweet_id is None) != (self.quoted_tweet_url is None):
            raise ValueError("quoted_tweet_id y quoted_tweet_url deben venir juntos o ninguno")
        return self
