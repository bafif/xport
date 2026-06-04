from __future__ import annotations

import pytest

from tweet_extractor.compliance.gate import ComplianceError, SlidingWindowGate


async def test_reserve_dentro_del_presupuesto_inserta_y_devuelve_id(tmp_path):
    gate = SlidingWindowGate(tmp_path / "ledger.db", hard_cap=1000)
    await gate.setup()

    rid = await gate.reserve(100)

    assert rid > 0
    assert await gate.usage() == 100
    assert await gate.remaining() == 900


async def test_reserve_n_mayor_que_cap_lanza_compliance_error(tmp_path):
    gate = SlidingWindowGate(tmp_path / "ledger.db", hard_cap=1000)
    await gate.setup()

    with pytest.raises(ComplianceError):
        await gate.reserve(1001)


async def test_reserve_n_no_positivo_lanza_value_error(tmp_path):
    gate = SlidingWindowGate(tmp_path / "ledger.db", hard_cap=1000)
    await gate.setup()

    with pytest.raises(ValueError):
        await gate.reserve(0)


async def test_usage_refleja_la_cota_superior_hasta_reconcile_y_reconcile_libera(tmp_path):
    gate = SlidingWindowGate(tmp_path / "ledger.db", hard_cap=1000)
    await gate.setup()

    rid = await gate.reserve(500)
    assert await gate.usage() == 500  # falla cerrado: cuenta lo reservado

    await gate.reconcile(rid, 120)
    assert await gate.usage() == 120  # reconciliado al conteo real
