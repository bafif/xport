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


async def test_bloquea_y_espera_hasta_que_el_evento_viejo_sale_de_la_ventana(tmp_path, fake_clock):
    slept: list[float] = []

    async def fake_sleep(s: float) -> None:
        slept.append(s)
        fake_clock.advance(s)

    gate = SlidingWindowGate(
        tmp_path / "ledger.db",
        hard_cap=1000,
        window_s=86_400,
        clock=fake_clock.time,
        sleep=fake_sleep,
    )
    await gate.setup()

    await gate.reserve(900)  # ocupa 900 en t0; quedan 100
    assert await gate.usage() == 900

    rid = await gate.reserve(200)  # 900+200>1000 → espera a que salgan los 900

    assert slept == [86_400.0]
    assert rid > 0
    assert await gate.usage() == 200  # el bloque viejo ya salió de la ventana


async def test_ventana_deslizante_no_es_bucket_de_dia_calendario(tmp_path, fake_clock):
    async def fake_sleep(s: float) -> None:
        fake_clock.advance(s)

    gate = SlidingWindowGate(
        tmp_path / "ledger.db",
        hard_cap=1000,
        window_s=86_400,
        clock=fake_clock.time,
        sleep=fake_sleep,
    )
    await gate.setup()

    await gate.reserve(600)  # t0
    fake_clock.advance(3600)  # +1h: misma ventana móvil
    assert await gate.usage() == 600  # sigue contando (deslizante, no medianoche)

    await gate.reserve(600)  # 600+600>1000 → espera al primer bloque

    assert await gate.usage() == 600  # solo el segundo bloque queda en la ventana
