"""`UserByScreenName` → cómo ve X a una cuenta (punto 10 de docs/PLAN.md).

Cuando una captura termina sin un solo tweet en el rango, la extensión abre
`x.com/<cuenta>/following`, la página que pide `UserByScreenName` sin cargar ningún post, y le
pasa al servicio la respuesta cruda. Se interpreta acá, como todo el GraphQL (regla 4 de
docs/PLAN.md).

Las formas, medidas contra x.com el 11/11/2026:

- no existe (o la desactivaron): `{"data": {}}`;
- suspendida: `result.__typename == "UserUnavailable"` con `reason == "Suspended"`;
- protegida: `User` con `privacy.protected == true`, y pública con `false`. Ya no viene
  `legacy`; se mira también ahí, por si vuelve.

Una forma que no se entiende lanza `MapperError`: mejor el aviso genérico que afirmar que una
cuenta no existe porque cambió el formato.
"""

from __future__ import annotations

from typing import Any

from tweet_extractor.domain.account import AccountCheck, AccountStatus
from tweet_extractor.mappers.base import MapperError, clean_str

_REASON_CHARS = 200


def _obj(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _flag(name: str, *sources: dict[str, Any]) -> bool:
    """`True` si alguna de las fuentes trae `name` en `True`."""
    return any(source.get(name) is True for source in sources)


def map_account(raw: object) -> AccountCheck:
    """El estado de la cuenta según la respuesta cruda de `UserByScreenName`."""
    if not isinstance(raw, dict):
        raise MapperError("UserByScreenName: la respuesta no es un objeto")
    if raw.get("errors"):
        # Un error de X (cupo, sesión) no dice nada de la cuenta, aunque `data` venga vacío.
        raise MapperError(f"UserByScreenName con errores: {str(raw['errors'])[:_REASON_CHARS]}")
    data = raw.get("data")
    if not isinstance(data, dict):
        raise MapperError("UserByScreenName sin `data`")
    user = data.get("user")
    if user is None:
        return AccountCheck(status=AccountStatus.NOT_FOUND)
    if not isinstance(user, dict):
        raise MapperError("UserByScreenName: `user` no es un objeto")
    result = user.get("result")
    if result is None:
        return AccountCheck(status=AccountStatus.NOT_FOUND)
    if not isinstance(result, dict):
        raise MapperError("UserByScreenName: `user.result` no es un objeto")

    kind = result.get("__typename")
    if kind == "UserUnavailable":
        reason = clean_str(result.get("reason"))
        if reason == "Suspended":
            return AccountCheck(status=AccountStatus.SUSPENDED)
        why = reason or clean_str(result.get("message")) or "UserUnavailable"
        return AccountCheck(status=AccountStatus.UNAVAILABLE, reason=why[:_REASON_CHARS])
    if kind != "User":
        raise MapperError(f"UserByScreenName: __typename inesperado {kind!r}")

    legacy = _obj(result.get("legacy"))
    relation = _obj(result.get("relationship_perspectives"))
    if _flag("blocked_by", relation, legacy):
        return AccountCheck(status=AccountStatus.BLOCKED)
    protected = _obj(result.get("privacy")).get("protected", legacy.get("protected"))
    if not isinstance(protected, bool):
        raise MapperError("UserByScreenName: no dice si la cuenta es protegida")
    if protected and not _flag("following", relation, legacy):
        return AccountCheck(status=AccountStatus.PROTECTED)
    return AccountCheck(status=AccountStatus.ACTIVE)
