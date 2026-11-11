"""Cómo ve X a una cuenta (punto 10 de docs/PLAN.md).

Una captura que termina sin un solo tweet en el rango puede ser una cuenta que no escribió en
esas fechas o una que la búsqueda no puede mostrar. Lo dice `UserByScreenName`, que interpreta
`mappers/account_mapper.py`.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class AccountStatus(StrEnum):
    ACTIVE = "active"  # existe y su búsqueda se ve
    PROTECTED = "protected"  # protegida, y la cuenta de X con que captura xport no la sigue
    SUSPENDED = "suspended"
    NOT_FOUND = "not_found"  # no existe, o la desactivaron
    BLOCKED = "blocked"  # bloqueó a la cuenta de X con que captura xport
    UNAVAILABLE = "unavailable"  # X no la muestra por otro motivo (`reason`)


class AccountCheck(BaseModel):
    """El estado de una cuenta y, si X no la muestra por un motivo que no es la suspensión, ese
    motivo tal como lo da X."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    status: AccountStatus
    reason: str | None = Field(default=None, max_length=200)
