# Compliance Gate (núcleo crítico) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Construir el Compliance Gate (tope duro de 900.000 accesos en ventana móvil de 24 h) y el contrato `TweetProvider`, con tests, antes que cualquier camino de fetch real.

**Architecture:** El gate (`SlidingWindowGate`) lleva un ledger persistente en SQLite de auditoría con ventana deslizante real y patrón reserva→fetch→reconcile. El `GatedProvider` envuelve cualquier `TweetProvider` y aplica el gate a cada página; como la paginación (`fetch_tweets`) llama `self.fetch_page` (gateado), ningún acceso lo saltea. Reloj y sleep son inyectables para tests deterministas.

**Tech Stack:** Python 3.12, `uv`, `aiosqlite`, `pydantic` / `pydantic-settings`, `pytest` + `pytest-asyncio` (`asyncio_mode = auto`), `ruff`, `mypy`.

**Spec:** `docs/superpowers/specs/2026-06-04-compliance-gate-core-design.md`

---

## File Structure

| Archivo | Responsabilidad |
|---|---|
| `pyproject.toml` | Proyecto, deps, config de ruff/mypy/pytest |
| `.gitignore`, `.env.example`, `.python-version`, `.node-version`, `README.md` | Scaffolding |
| `src/tweet_extractor/__init__.py` | Marca de paquete + versión |
| `src/tweet_extractor/config.py` | `Settings` (pydantic-settings): rutas DB, `hard_cap`, `page_size`, cookies |
| `src/tweet_extractor/providers/base.py` | `RawTweet`, `SearchQuery`, `Page`, `TweetProvider` (ABC) |
| `src/tweet_extractor/compliance/gate.py` | `ComplianceError`, `SlidingWindowGate` |
| `src/tweet_extractor/compliance/gated_provider.py` | `GatedProvider` |
| `tests/conftest.py` | `FakeClock`, `FakeProvider`, fixtures |
| `tests/compliance/test_gate.py` | Tests del gate |
| `tests/compliance/test_gated_provider.py` | Tests del provider gateado |
| `tests/test_config.py` | Tests de `Settings` |
| `tests/test_smoke.py` | Smoke: el paquete importa |

**Fuera de alcance** (próximas fases, NO crear ahora): `domain/models.py`, `providers/twscrape_provider.py`, `providers/official_api.py`, `providers/factory.py`, `mappers/`, `storage/`, `service/`, `cli.py`, extensión.

---

## Task 1: Scaffolding del proyecto

**Files:**
- Create: `pyproject.toml` (vía `uv init`, luego ajustado), `.python-version`, `.gitignore`, `.env.example`, `.node-version`, `README.md`
- Create: `src/tweet_extractor/__init__.py`, `src/tweet_extractor/providers/__init__.py`, `src/tweet_extractor/compliance/__init__.py`
- Create: `tests/__init__.py`, `tests/compliance/__init__.py`, `tests/test_smoke.py`

- [ ] **Step 1: Inicializar el proyecto con uv (src layout) y fijar Python 3.12**

```bash
cd ~/xport
uv init --package --name tweet-extractor
uv python pin 3.12
```
Expected: crea `pyproject.toml`, `src/tweet_extractor/__init__.py`, `README.md`; `.python-version` con `3.12`.

- [ ] **Step 2: Agregar dependencias del núcleo crítico**

```bash
uv add pydantic pydantic-settings aiosqlite
uv add --dev pytest pytest-asyncio ruff mypy
```
Expected: `uv.lock` generado, `.venv` creado. (Las deps de fases futuras — `twscrape`, `httpx`, `typer`, `fastapi[standard]` — se agregan en su fase: YAGNI.)

- [ ] **Step 3: Sobrescribir `src/tweet_extractor/__init__.py`**

```python
"""Extractor de tweets por rango de fechas → CSV (ver CLAUDE.md)."""

__version__ = "0.1.0"
```

- [ ] **Step 4: Sobrescribir `.gitignore`** (mínimos exigidos por el CLAUDE.md)

```gitignore
.env
data/
*.db
*.sqlite*
.venv/
node_modules/
dist/
__pycache__/
*.py[cod]
.pytest_cache/
.mypy_cache/
.ruff_cache/
.DS_Store
```

- [ ] **Step 5: Crear `.env.example`** (sin valores reales)

```dotenv
# Cookies de sesión de una cuenta de X secundaria/descartable. NUNCA commitear el .env real.
X_AUTH_TOKEN=
X_CT0=

# Rutas de almacenamiento (el ledger de auditoría va separado del SQLite de datos).
AUDIT_DB_PATH=data/audit/ledger.db
DATA_DB_PATH=data/tweets.db

# Tope de cumplimiento (NO subir sin instrucción explícita).
HARD_CAP=900000
WINDOW_S=86400
PAGE_SIZE=20
```

- [ ] **Step 6: Crear `.node-version`** (toolchain de la extensión, Fase 4)

```
22
```

- [ ] **Step 7: Agregar configuración de herramientas al final de `pyproject.toml`**

```toml
[tool.ruff]
line-length = 100
target-version = "py312"

[tool.ruff.lint]
select = ["E", "F", "I", "UP", "B", "ASYNC"]

[tool.mypy]
python_version = "3.12"
strict = true
files = ["src"]

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
```

- [ ] **Step 8: Crear los `__init__.py` de paquetes y el smoke test**

`src/tweet_extractor/providers/__init__.py`, `src/tweet_extractor/compliance/__init__.py`, `tests/__init__.py`, `tests/compliance/__init__.py` → todos vacíos.

`tests/test_smoke.py`:
```python
def test_paquete_importable() -> None:
    import tweet_extractor

    assert tweet_extractor.__version__
```

- [ ] **Step 9: Verificar que todo corre**

```bash
uv run pytest -q
uv run ruff check .
uv run ruff format .
```
Expected: 1 test pasa; ruff sin errores.

- [ ] **Step 10: Commit**

```bash
git add -A -- ':!.planning'
git commit -m "chore: scaffolding del proyecto (uv, estructura, configs)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 2: Contrato `TweetProvider` + conftest

**Files:**
- Create: `src/tweet_extractor/providers/base.py`
- Create: `tests/conftest.py`
- Test: `tests/providers/test_base.py`

- [ ] **Step 1: Crear `tests/conftest.py` con helpers y fixtures**

```python
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from tweet_extractor.providers.base import Page, SearchQuery, TweetProvider


class FakeClock:
    """Reloj controlable para tests deterministas."""

    def __init__(self, start: int = 1_700_000_000) -> None:
        self.now = start

    def time(self) -> float:
        return float(self.now)

    def advance(self, seconds: float) -> None:
        self.now += int(seconds)


class FakeProvider(TweetProvider):
    """Provider en memoria. El cursor 'p{idx}' codifica el índice de página."""

    def __init__(self, pages: list[Page], max_accessed_per_page: int = 40) -> None:
        self._pages = pages
        self.max_accessed_per_page = max_accessed_per_page
        self.cursors_seen: list[str | None] = []

    async def fetch_page(self, query: SearchQuery, cursor: str | None) -> Page:
        self.cursors_seen.append(cursor)
        idx = 0 if cursor is None else int(cursor[1:])
        return self._pages[idx]


@pytest.fixture
def fake_clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def make_provider():
    def _make(pages: list[Page], max_accessed_per_page: int = 40) -> FakeProvider:
        return FakeProvider(pages, max_accessed_per_page)

    return _make


@pytest.fixture
def sample_query() -> SearchQuery:
    return SearchQuery(
        username="someuser",
        since=datetime(2023, 1, 1, tzinfo=timezone.utc),
        until=datetime(2024, 1, 1, tzinfo=timezone.utc),
    )
```

- [ ] **Step 2: Escribir el test de paginación (falla)**

`tests/providers/__init__.py` → vacío.

`tests/providers/test_base.py`:
```python
from __future__ import annotations

from tweet_extractor.providers.base import Page


async def test_fetch_tweets_pagina_y_para_en_next_cursor_none(make_provider, sample_query):
    pages = [
        Page(tweets=[{"id": "1"}, {"id": "2"}], accessed_count=2, next_cursor="p1"),
        Page(tweets=[{"id": "3"}], accessed_count=1, next_cursor=None),
    ]
    provider = make_provider(pages)

    out = [t async for t in provider.fetch_tweets(sample_query)]

    assert [t["id"] for t in out] == ["1", "2", "3"]
    assert provider.cursors_seen == [None, "p1"]
```

- [ ] **Step 3: Run test → verify FAIL**

Run: `uv run pytest tests/providers/test_base.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'tweet_extractor.providers.base'`.

- [ ] **Step 4: Implementar `src/tweet_extractor/providers/base.py`**

```python
from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import datetime
from typing import Any

RawTweet = dict[str, Any]
"""JSON crudo de un objeto-tweet tal como lo entrega el backend.
El shape de dominio lo fija el mapper en su fase; el gate es agnóstico."""


@dataclass(frozen=True)
class SearchQuery:
    """Parámetros de una búsqueda `from:user since/until`."""

    username: str
    since: datetime
    until: datetime
    include_quotes: bool = True
    include_retweets: bool = False


@dataclass
class Page:
    """Una página de resultados de un provider.

    `accessed_count` cuenta TODO objeto-tweet tocado (citante + quote embebido +
    RT descartado); NO es `len(tweets)`.
    """

    tweets: list[RawTweet]
    accessed_count: int
    next_cursor: str | None = None


class TweetProvider(ABC):
    """Fuente de datos intercambiable. Las subclases implementan `fetch_page`;
    la paginación (`fetch_tweets`) es concreta y se hereda gratis."""

    max_accessed_per_page: int
    """Cota superior de accesos por página (= 2 * page_size, por quote embebido)."""

    @abstractmethod
    async def fetch_page(self, query: SearchQuery, cursor: str | None) -> Page:
        """Trae una página dado un cursor opaco. `None` = primera página."""
        ...

    async def fetch_tweets(self, query: SearchQuery) -> AsyncIterator[RawTweet]:
        cursor: str | None = None
        while True:
            page = await self.fetch_page(query, cursor)
            for tweet in page.tweets:
                yield tweet
            if page.next_cursor is None:
                break
            cursor = page.next_cursor
```

- [ ] **Step 5: Run test → verify PASS**

Run: `uv run pytest tests/providers/test_base.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/tweet_extractor/providers/base.py tests/conftest.py tests/providers/
git commit -m "feat(providers): contrato TweetProvider (fetch_page + fetch_tweets)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 3: `SlidingWindowGate` — reserva básica, usage, reconcile

**Files:**
- Create: `src/tweet_extractor/compliance/gate.py`
- Test: `tests/compliance/test_gate.py`

> Esta tarea implementa el camino "entra" + guards. El camino "espera" se agrega en Task 4 (acá `reserve` lanza `ComplianceError` si no hay presupuesto — **temporal, reemplazado en Task 4**).

- [ ] **Step 1: Escribir los tests de reserva básica (fallan)**

`tests/compliance/test_gate.py`:
```python
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
```

- [ ] **Step 2: Run tests → verify FAIL**

Run: `uv run pytest tests/compliance/test_gate.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'tweet_extractor.compliance.gate'`.

- [ ] **Step 3: Implementar `src/tweet_extractor/compliance/gate.py` (camino "entra")**

```python
from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from pathlib import Path

import aiosqlite


class ComplianceError(RuntimeError):
    """Un pedido no puede cumplirse sin violar el tope de ToS."""


class SlidingWindowGate:
    """Tope duro de ToS: nunca acceder a más de `hard_cap` objetos-tweet en
    cualquier ventana móvil de `window_s` segundos. Cuenta accesos (no lo
    guardado), no deduplica, persiste en SQLite y falla cerrado."""

    def __init__(
        self,
        db_path: str | Path,
        hard_cap: int = 900_000,
        window_s: int = 86_400,
        *,
        clock: Callable[[], float] = time.time,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._db_path = str(db_path)
        self._hard_cap = hard_cap
        self._window_s = window_s
        self._clock = clock
        self._sleep = sleep
        self._lock = asyncio.Lock()

    @property
    def hard_cap(self) -> int:
        return self._hard_cap

    async def setup(self) -> None:
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                """CREATE TABLE IF NOT EXISTS access_ledger (
                    id    INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts    INTEGER NOT NULL,
                    count INTEGER NOT NULL
                )"""
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_ledger_ts ON access_ledger(ts)"
            )
            await db.commit()

    async def _usage(self, db: aiosqlite.Connection, now: int) -> int:
        cur = await db.execute(
            "SELECT COALESCE(SUM(count), 0) FROM access_ledger WHERE ts > ?",
            (now - self._window_s,),
        )
        row = await cur.fetchone()
        return int(row[0]) if row else 0

    async def usage(self, now: int | None = None) -> int:
        async with aiosqlite.connect(self._db_path) as db:
            moment = int(self._clock()) if now is None else now
            return await self._usage(db, moment)

    async def remaining(self, now: int | None = None) -> int:
        return self._hard_cap - await self.usage(now)

    async def reserve(self, n: int) -> int:
        """Reserva capacidad para hasta `n` accesos ANTES del fetch. Devuelve
        el id de reserva (para reconciliar)."""
        if n <= 0:
            raise ValueError("n debe ser > 0")
        if n > self._hard_cap:
            raise ComplianceError(f"pedido de {n} excede el hard_cap {self._hard_cap}")
        async with self._lock:
            async with aiosqlite.connect(self._db_path) as db:
                now = int(self._clock())
                used = await self._usage(db, now)
                if used + n <= self._hard_cap:
                    cur = await db.execute(
                        "INSERT INTO access_ledger(ts, count) VALUES(?, ?)",
                        (now, n),
                    )
                    await db.commit()
                    return int(cur.lastrowid)
        # TEMPORAL: Task 4 reemplaza esto por la espera de ventana deslizante.
        raise ComplianceError("sin presupuesto")

    async def reconcile(self, reservation_id: int, actual: int) -> None:
        """Ajusta la reserva al conteo real de objetos accedidos tras el fetch."""
        async with self._lock:
            async with aiosqlite.connect(self._db_path) as db:
                await db.execute(
                    "UPDATE access_ledger SET count = ? WHERE id = ?",
                    (actual, reservation_id),
                )
                await db.commit()
```

- [ ] **Step 4: Run tests → verify PASS**

Run: `uv run pytest tests/compliance/test_gate.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add src/tweet_extractor/compliance/gate.py tests/compliance/test_gate.py
git commit -m "feat(compliance): SlidingWindowGate reserva/usage/reconcile

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 4: `SlidingWindowGate` — bloqueo y ventana deslizante

**Files:**
- Modify: `src/tweet_extractor/compliance/gate.py` (reemplazar `reserve`, agregar `_wait_seconds`)
- Test: `tests/compliance/test_gate.py` (agregar tests)

- [ ] **Step 1: Agregar los tests de bloqueo/espera/deslizante (fallan)**

Agregar al final de `tests/compliance/test_gate.py`:
```python
async def test_bloquea_y_espera_hasta_que_el_evento_viejo_sale_de_la_ventana(
    tmp_path, fake_clock
):
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
```

- [ ] **Step 2: Run tests → verify FAIL**

Run: `uv run pytest tests/compliance/test_gate.py -k "ventana or bloquea" -v`
Expected: FAIL — `ComplianceError: sin presupuesto` (el `reserve` temporal no espera).

- [ ] **Step 3: Reemplazar `reserve` y agregar `_wait_seconds` en `gate.py`**

Reemplazar el método `reserve` completo (borrando el `raise ComplianceError("sin presupuesto")` temporal) por:
```python
    async def reserve(self, n: int) -> int:
        """Reserva capacidad para hasta `n` accesos ANTES del fetch. Bloquea
        (espera a que eventos viejos salgan de la ventana) si hace falta.
        Devuelve el id de reserva (para reconciliar)."""
        if n <= 0:
            raise ValueError("n debe ser > 0")
        if n > self._hard_cap:
            raise ComplianceError(f"pedido de {n} excede el hard_cap {self._hard_cap}")
        while True:
            async with self._lock:
                async with aiosqlite.connect(self._db_path) as db:
                    now = int(self._clock())
                    used = await self._usage(db, now)
                    if used + n <= self._hard_cap:
                        cur = await db.execute(
                            "INSERT INTO access_ledger(ts, count) VALUES(?, ?)",
                            (now, n),
                        )
                        await db.commit()
                        return int(cur.lastrowid)
                    wait_s = await self._wait_seconds(db, now)
            # sleep FUERA del lock: no bloquea a otras corrutinas ni al reconcile.
            await self._sleep(wait_s)
```

Agregar el método `_wait_seconds` (después de `reserve`):
```python
    async def _wait_seconds(self, db: aiosqlite.Connection, now: int) -> float:
        """Segundos hasta que el evento más viejo de la ventana caiga afuera."""
        cur = await db.execute(
            "SELECT MIN(ts) FROM access_ledger WHERE ts > ?",
            (now - self._window_s,),
        )
        row = await cur.fetchone()
        oldest = row[0] if row and row[0] is not None else None
        if oldest is None:
            return 1.0
        return float(max(1, (oldest + self._window_s) - now))
```

- [ ] **Step 4: Run tests → verify PASS**

Run: `uv run pytest tests/compliance/test_gate.py -v`
Expected: PASS (6 tests).

- [ ] **Step 5: Commit**

```bash
git add src/tweet_extractor/compliance/gate.py tests/compliance/test_gate.py
git commit -m "feat(compliance): bloqueo por ventana deslizante (reserva-antes-de-pedir)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 5: `SlidingWindowGate` — invariantes de cumplimiento

**Files:**
- Test: `tests/compliance/test_gate.py` (agregar tests de invariantes; verifican el código ya escrito)

- [ ] **Step 1: Agregar los tests de invariantes**

Primero, agregar `import asyncio` al bloque de imports del tope del archivo (queda así):
```python
from __future__ import annotations

import asyncio

import pytest

from tweet_extractor.compliance.gate import ComplianceError, SlidingWindowGate
```

Luego agregar al final de `tests/compliance/test_gate.py`:
```python
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


async def test_concurrencia_serializa_y_no_cruza_el_cap(tmp_path):
    gate = SlidingWindowGate(tmp_path / "ledger.db", hard_cap=1000)
    await gate.setup()

    rids = await asyncio.gather(*[gate.reserve(100) for _ in range(10)])

    assert len(set(rids)) == 10  # ids únicos (lock + autoincrement)
    assert await gate.usage() == 1000
    assert await gate.remaining() == 0


async def test_regresion_c1_mismo_segundo_ids_distintos(tmp_path, fake_clock):
    # Reloj fijo: ambas reservas comparten ts. Con id=ts (bug del boceto), el
    # reconcile de una afectaría a ambas. Con id autoincrement, no.
    gate = SlidingWindowGate(tmp_path / "ledger.db", hard_cap=1000, clock=fake_clock.time)
    await gate.setup()

    rid1 = await gate.reserve(100)
    rid2 = await gate.reserve(100)

    assert rid1 != rid2
    assert await gate.usage() == 200

    await gate.reconcile(rid1, 10)
    assert await gate.usage() == 110  # solo rid1 cambió
```

- [ ] **Step 2: Run tests → verify PASS**

Run: `uv run pytest tests/compliance/test_gate.py -v`
Expected: PASS (10 tests). Estos verifican invariantes del código de Tasks 3–4; si alguno falla, hay un bug real que corregir antes de seguir.

- [ ] **Step 3: Commit**

```bash
git add tests/compliance/test_gate.py
git commit -m "test(compliance): invariantes (persistencia, no-dedup, concurrencia, regresión C1)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 6: `GatedProvider`

**Files:**
- Create: `src/tweet_extractor/compliance/gated_provider.py`
- Test: `tests/compliance/test_gated_provider.py`

- [ ] **Step 1: Escribir los tests del provider gateado (fallan)**

`tests/compliance/test_gated_provider.py`:
```python
from __future__ import annotations

from tweet_extractor.compliance.gate import SlidingWindowGate
from tweet_extractor.compliance.gated_provider import GatedProvider
from tweet_extractor.providers.base import Page, SearchQuery, TweetProvider


async def test_cuenta_accessed_count_no_len_tweets(tmp_path, make_provider, sample_query):
    gate = SlidingWindowGate(tmp_path / "ledger.db", hard_cap=1000)
    await gate.setup()
    pages = [
        Page(tweets=[{"id": "1"}], accessed_count=5, next_cursor="p1"),
        Page(tweets=[{"id": "2"}, {"id": "3"}], accessed_count=7, next_cursor=None),
    ]
    gated = GatedProvider(make_provider(pages, max_accessed_per_page=40), gate)

    out = [t async for t in gated.fetch_tweets(sample_query)]

    assert [t["id"] for t in out] == ["1", "2", "3"]
    assert await gate.usage() == 12  # 5+7 accedidos, NO 3 emitidos


async def test_reserva_la_cota_superior_antes_de_pedir_y_reconcilia_despues(
    tmp_path, sample_query
):
    gate = SlidingWindowGate(tmp_path / "ledger.db", hard_cap=1000)
    await gate.setup()
    observed: dict[str, int] = {}

    class SpyProvider(TweetProvider):
        max_accessed_per_page = 40

        async def fetch_page(self, query: SearchQuery, cursor: str | None) -> Page:
            observed["usage_durante_fetch"] = await gate.usage()
            return Page(tweets=[{"id": "1"}], accessed_count=3, next_cursor=None)

    gated = GatedProvider(SpyProvider(), gate)

    _ = [t async for t in gated.fetch_tweets(sample_query)]

    assert observed["usage_durante_fetch"] == 40  # cota superior reservada ANTES
    assert await gate.usage() == 3  # reconciliado al real DESPUÉS


async def test_el_fetch_espera_si_el_gate_esta_lleno(tmp_path, fake_clock, make_provider, sample_query):
    slept: list[float] = []

    async def fake_sleep(s: float) -> None:
        slept.append(s)
        fake_clock.advance(s)

    gate = SlidingWindowGate(
        tmp_path / "ledger.db",
        hard_cap=50,
        window_s=86_400,
        clock=fake_clock.time,
        sleep=fake_sleep,
    )
    await gate.setup()
    await gate.reserve(40)  # quedan 10; la próxima reserva (cota 40) no entra

    pages = [Page(tweets=[{"id": "1"}], accessed_count=5, next_cursor=None)]
    gated = GatedProvider(make_provider(pages, max_accessed_per_page=40), gate)

    out = [t async for t in gated.fetch_tweets(sample_query)]

    assert out == [{"id": "1"}]
    assert slept == [86_400.0]
    assert await gate.usage() == 5
```

- [ ] **Step 2: Run tests → verify FAIL**

Run: `uv run pytest tests/compliance/test_gated_provider.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'tweet_extractor.compliance.gated_provider'`.

- [ ] **Step 3: Implementar `src/tweet_extractor/compliance/gated_provider.py`**

```python
from __future__ import annotations

from tweet_extractor.compliance.gate import SlidingWindowGate
from tweet_extractor.providers.base import Page, SearchQuery, TweetProvider


class GatedProvider(TweetProvider):
    """Aplica el Compliance Gate a CUALQUIER TweetProvider. Reserva la cota
    superior ANTES de cada fetch, hace el request, y reconcilia al conteo real.
    Hereda `fetch_tweets`, que llama `self.fetch_page` (gateado): ningún acceso
    saltea el gate."""

    def __init__(self, inner: TweetProvider, gate: SlidingWindowGate) -> None:
        self._inner = inner
        self._gate = gate
        self.max_accessed_per_page = inner.max_accessed_per_page

    async def fetch_page(self, query: SearchQuery, cursor: str | None) -> Page:
        rid = await self._gate.reserve(self._inner.max_accessed_per_page)
        page = await self._inner.fetch_page(query, cursor)
        await self._gate.reconcile(rid, page.accessed_count)
        return page
```

- [ ] **Step 4: Run tests → verify PASS**

Run: `uv run pytest tests/compliance/test_gated_provider.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add src/tweet_extractor/compliance/gated_provider.py tests/compliance/test_gated_provider.py
git commit -m "feat(compliance): GatedProvider (reserva→fetch→reconcile por página)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 7: `config.py`

**Files:**
- Create: `src/tweet_extractor/config.py`
- Test: `tests/test_config.py`

- [ ] **Step 1: Escribir el test de Settings (falla)**

`tests/test_config.py`:
```python
from __future__ import annotations

import pytest


def test_settings_defaults(monkeypatch):
    for k in ("HARD_CAP", "WINDOW_S", "PAGE_SIZE", "X_AUTH_TOKEN", "X_CT0"):
        monkeypatch.delenv(k, raising=False)

    from tweet_extractor.config import Settings

    s = Settings(_env_file=None)
    assert s.hard_cap == 900_000
    assert s.window_s == 86_400
    assert s.page_size == 20
    assert s.max_accessed_per_page == 40
    assert s.x_auth_token is None


def test_settings_lee_env_vars(monkeypatch):
    monkeypatch.setenv("PAGE_SIZE", "50")
    monkeypatch.setenv("X_AUTH_TOKEN", "abc123")

    from tweet_extractor.config import Settings

    s = Settings(_env_file=None)
    assert s.page_size == 50
    assert s.max_accessed_per_page == 100
    assert s.x_auth_token == "abc123"
```

- [ ] **Step 2: Run tests → verify FAIL**

Run: `uv run pytest tests/test_config.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'tweet_extractor.config'`.

- [ ] **Step 3: Implementar `src/tweet_extractor/config.py`**

```python
from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Configuración de la app. Lee `.env` y variables de entorno."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    audit_db_path: Path = Path("data/audit/ledger.db")
    data_db_path: Path = Path("data/tweets.db")
    hard_cap: int = 900_000  # NO subir sin instrucción explícita del usuario
    window_s: int = 86_400
    page_size: int = 20
    x_auth_token: str | None = None
    x_ct0: str | None = None

    @property
    def max_accessed_per_page(self) -> int:
        """Cota superior de accesos por página (quote embebido de 1 nivel)."""
        return 2 * self.page_size
```

- [ ] **Step 4: Run tests → verify PASS**

Run: `uv run pytest tests/test_config.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add src/tweet_extractor/config.py tests/test_config.py
git commit -m "feat(config): Settings (pydantic-settings) con hard_cap y rutas DB

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 8: Verificación final (suite + lint + types)

**Files:** ninguno (verificación)

- [ ] **Step 1: Correr toda la suite**

Run: `uv run pytest -v`
Expected: PASS — 1 (smoke) + 1 (base) + 10 (gate) + 3 (gated_provider) + 2 (config) = **17 tests verdes**.

- [ ] **Step 2: Lint y formato**

Run: `uv run ruff check . && uv run ruff format --check .`
Expected: sin errores. (Si `format --check` falla, correr `uv run ruff format .` y re-commitear.)

- [ ] **Step 3: Type-check estricto del código fuente**

Run: `uv run mypy src`
Expected: `Success: no issues found`.

- [ ] **Step 4: Commit de cierre (si lint/format hizo cambios)**

```bash
git add -A -- ':!.planning'
git commit -m "chore: verificación final del núcleo crítico (tests, ruff, mypy)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>" || echo "nada que commitear"
```

---

## Notas para el ejecutor

- **No tocar el `hard_cap`** (900.000) ni debilitar el gate. Las 3 correcciones al boceto del plan (id autoincrement, reserva-antes-de-pedir, reloj inyectable) ya están incorporadas; ver spec §2.
- **`.planning/`** queda sin trackear a propósito (los `git add` lo excluyen con `':!.planning'`).
- Si `uv init` ya creó archivos (`README.md`, `.python-version`, `src/.../__init__.py`), respetarlos y solo aplicar las sobrescrituras indicadas.
- Nombres de tests en español sin tildes (identificadores válidos); docstrings y comentarios con acentuación correcta.
