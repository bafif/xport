"""El estado de una cuenta según `UserByScreenName` (punto 10 de docs/PLAN.md).

`fixtures/user_by_screen_name.json` son respuestas reales de x.com del 11/11/2026, recortadas a lo
que mira el mapper: una cuenta pública, una protegida, una suspendida y un handle que no
existe. Los bordes que no se pudieron medir (una protegida que se sigue, un bloqueo, la forma
vieja con `legacy`) se arman sobre esas mismas respuestas.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from tweet_extractor.domain.account import AccountCheck, AccountStatus
from tweet_extractor.mappers.account_mapper import map_account
from tweet_extractor.mappers.base import MapperError

REAL: dict[str, Any] = json.loads(
    (Path(__file__).parent / "fixtures" / "user_by_screen_name.json").read_text(encoding="utf-8")
)


def real(case: str) -> dict[str, Any]:
    return copy.deepcopy(REAL[case])


def result_of(raw: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = raw["data"]["user"]["result"]
    return result


@pytest.mark.parametrize(
    ("case", "status"),
    [
        ("active", AccountStatus.ACTIVE),
        ("protected", AccountStatus.PROTECTED),
        ("suspended", AccountStatus.SUSPENDED),
        ("not_found", AccountStatus.NOT_FOUND),
    ],
)
def test_las_respuestas_reales(case: str, status: AccountStatus) -> None:
    assert map_account(real(case)) == AccountCheck(status=status)


def test_una_protegida_que_se_sigue_se_ve() -> None:
    raw = real("protected")
    result_of(raw)["relationship_perspectives"]["following"] = True
    assert map_account(raw).status == AccountStatus.ACTIVE


def test_una_cuenta_que_bloqueo_a_la_de_xport() -> None:
    raw = real("active")
    result_of(raw)["relationship_perspectives"]["blocked_by"] = True
    assert map_account(raw).status == AccountStatus.BLOCKED


def test_la_forma_vieja_con_legacy() -> None:
    raw = real("protected")
    result = result_of(raw)
    del result["privacy"], result["relationship_perspectives"]
    result["legacy"] = {"protected": True, "following": False, "screen_name": "TayandYou"}
    assert map_account(raw).status == AccountStatus.PROTECTED
    result["legacy"]["following"] = True
    assert map_account(raw).status == AccountStatus.ACTIVE
    result["legacy"]["blocked_by"] = True
    assert map_account(raw).status == AccountStatus.BLOCKED


def test_user_sin_result_es_que_no_existe() -> None:
    assert map_account({"data": {"user": {}}}).status == AccountStatus.NOT_FOUND


def test_no_disponible_por_otro_motivo() -> None:
    raw = real("suspended")
    result_of(raw).update(reason="Withheld", message="Withheld in your country")
    assert map_account(raw) == AccountCheck(status=AccountStatus.UNAVAILABLE, reason="Withheld")
    del result_of(raw)["reason"]
    assert map_account(raw).reason == "Withheld in your country"
    del result_of(raw)["message"]
    assert map_account(raw).reason == "UserUnavailable"


def test_el_motivo_se_recorta() -> None:
    raw = real("suspended")
    result_of(raw)["reason"] = "x" * 500
    reason = map_account(raw).reason
    assert reason is not None and len(reason) == 200


@pytest.mark.parametrize(
    "raw",
    [
        None,
        [],
        {},
        {"data": None},
        {"data": {"user": []}},
        {"data": {"user": {"result": "User"}}},
        # Un error de X no dice que la cuenta no exista, aunque `data` venga vacío.
        {"data": {}, "errors": [{"code": 88, "message": "Rate limit exceeded"}]},
        {"data": {"user": {"result": {"__typename": "UserV2"}}}},
        {"data": {"user": {"result": {"__typename": "User", "rest_id": "1"}}}},
        {"data": {"user": {"result": {"__typename": "User", "privacy": {"protected": "no"}}}}},
    ],
)
def test_una_forma_que_no_se_entiende_es_un_error(raw: object) -> None:
    with pytest.raises(MapperError):
        map_account(raw)
