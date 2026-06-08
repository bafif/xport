from __future__ import annotations

import asyncio

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


async def test_persistencia_entre_reinicios(tmp_path):
    db = tmp_path / "ledger.db"
    gate1 = SlidingWindowGate(db, hard_cap=1000)
    await gate1.setup()
    await gate1.reserve(300)

    gate2 = SlidingWindowGate(db, hard_cap=1000)  # "reinicio": mismo path
    assert await gate2.usage() == 300


async def test_el_ledger_no_deduplica(tmp_path):
    gate = SlidingWindowGate(tmp_path / "ledger.db", hard_cap=1000)
    await gate.setup()

    await gate.reserve(100)
    await gate.reserve(100)  # "mismo acceso" re-pedido: cuenta doble

    assert await gate.usage() == 200


async def test_reserve_poda_filas_fuera_de_la_ventana(tmp_path, fake_clock):
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

    await gate.reserve(100)  # bloque viejo en t0
    fake_clock.advance(86_401)  # cae fuera de la ventana
    await gate.reserve(200)  # éxito → poda el bloque viejo

    db = await gate._db()
    cur = await db.execute("SELECT COUNT(*) FROM access_ledger")
    row = await cur.fetchone()
    assert row is not None
    assert row[0] == 1  # el ledger no crece sin fin: solo queda el bloque vivo
    assert await gate.usage() == 200


async def test_concurrencia_serializa_y_no_cruza_el_cap(tmp_path):
    gate = SlidingWindowGate(tmp_path / "ledger.db", hard_cap=1000)
    await gate.setup()

    rids = await asyncio.gather(*[gate.reserve(100) for _ in range(10)])

    assert len(set(rids)) == 10  # ids únicos (lock + autoincrement)
    assert await gate.usage() == 1000
    assert await gate.remaining() == 0


async def test_regresion_c1_mismo_segundo_ids_distintos(tmp_path, fake_clock):
    gate = SlidingWindowGate(tmp_path / "ledger.db", hard_cap=1000, clock=fake_clock.time)
    await gate.setup()

    rid1 = await gate.reserve(100)
    rid2 = await gate.reserve(100)

    assert rid1 != rid2
    assert await gate.usage() == 200

    await gate.reconcile(rid1, 10)
    assert await gate.usage() == 110  # solo rid1 cambió


async def test_espera_progresiva_multi_bloque(tmp_path, fake_clock):
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

    await gate.reserve(500)  # t0
    fake_clock.advance(100)
    await gate.reserve(450)  # t0+100; usage=950
    assert await gate.usage() == 950

    rid = await gate.reserve(600)  # necesita que AMBOS bloques salgan de la ventana

    assert rid > 0
    assert slept == [86_300.0, 100.0]  # camina al oldest, luego al siguiente
    assert await gate.usage() == 600


async def test_reconcile_despierta_al_que_espera_sin_agotar_el_sleep(tmp_path, fake_clock):
    # En prod el sleep puede ser de 24 h; un reconcile que libera presupuesto debe
    # DESPERTAR al que espera sin aguardar todo el sleep. Con el sleep no-reactivo
    # viejo, reserve(200) quedaría dormido 3600 s reales y el test colgaría.
    sleep_started = asyncio.Event()

    async def blocking_sleep(s: float) -> None:
        sleep_started.set()
        await asyncio.sleep(3600)  # "largo": debe ser cancelado por el reconcile

    gate = SlidingWindowGate(
        tmp_path / "ledger.db",
        hard_cap=1000,
        clock=fake_clock.time,
        sleep=blocking_sleep,
    )
    await gate.setup()
    rid_old = await gate.reserve(900)

    async def free_budget() -> None:
        await sleep_started.wait()
        await gate.reconcile(rid_old, 50)  # libera presupuesto mientras el otro espera

    freer = asyncio.ensure_future(free_budget())
    async with asyncio.timeout(5):  # si regresiona el wake reactivo, falla rápido
        rid = await gate.reserve(200)
    await freer

    assert rid > 0
    assert await gate.usage() == 250  # 50 (reconciliado) + 200


async def test_reconcile_durante_espera_permite_proceder(tmp_path, fake_clock):
    gate = SlidingWindowGate(
        tmp_path / "ledger.db",
        hard_cap=1000,
        window_s=86_400,
        clock=fake_clock.time,
    )
    await gate.setup()
    rid_old = await gate.reserve(900)
    assert await gate.usage() == 900

    calls = {"n": 0}

    async def fake_sleep(s: float) -> None:
        calls["n"] += 1
        if calls["n"] == 1:
            await gate.reconcile(rid_old, 50)  # libera presupuesto durante la espera
        # el clock NO avanza: el waiter procede por el reconcile, no por el tiempo

    gate._sleep = fake_sleep

    rid = await gate.reserve(200)  # 900+200>1000 → espera; tras reconcile 50+200<=1000 → entra

    assert rid > 0
    assert calls["n"] == 1
    assert await gate.usage() == 250
