from __future__ import annotations

from pydantic import BaseModel, ConfigDict, field_validator


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
