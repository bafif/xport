# `TwscrapeProvider` — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Construir `TwscrapeProvider`, el primer `TweetProvider` concreto (scraping gratis vía twscrape, sin navegador), que entrega dicts crudos de GraphQL por página conformando el contrato `fetch_page(query, cursor) -> Page` para que el `GatedProvider` lo envuelva sin saltear el Compliance Gate.

**Architecture:** Integración a **nivel de página** (el `GatedProvider` reserva antes de cada `fetch_page`, así que cada `fetch_page` = un request de página con cursor externo). Todo el acoplamiento con internals de twscrape se aísla en `providers/_twscrape_gql.py` (usa `OP_SearchTimeline` con queryId auto-actualizado, `QueueClient` con rate-limit, `encode_params`). El provider hace navegación de *envelope/paginación* con helpers **puros** (`extract_tweet_results`, `extract_bottom_cursor`, `count_accessed`); la interpretación tweet-a-tweet es del mapper (fase siguiente). El fetcher de red se **inyecta** (`page_fetcher`), así los tests corren 100% offline con fixtures de SearchTimeline.

**Tech Stack:** Python 3.12, `uv`, `twscrape` (httpx, sin navegador), pydantic v2 (`Settings`), `pytest` (`asyncio_mode = auto`), `ruff`, `mypy --strict` (solo sobre `src`).

**Spec:** `docs/superpowers/specs/2026-06-09-twscrape-provider-design.md`

---

## File Structure

| Archivo | Responsabilidad |
|---|---|
| `src/tweet_extractor/providers/base.py` | **Modificar**: agregar `ProviderError` |
| `src/tweet_extractor/config.py` | **Modificar**: agregar `accounts_db_path`, `subwindow_days` |
| `src/tweet_extractor/providers/subwindows.py` | **Crear**: `subwindows()` (helper puro de troceado) |
| `src/tweet_extractor/providers/_twscrape_gql.py` | **Crear**: superficie de acoplamiento (`fetch_search_page`, `_build_params`) |
| `src/tweet_extractor/providers/twscrape_provider.py` | **Crear**: `TwscrapeProvider`, `build_query`, `extract_tweet_results`, `extract_bottom_cursor`, `count_accessed`, `build_pool` |
| `pyproject.toml` | **Modificar**: dep `twscrape` + override mypy para `twscrape.*` |
| `.env.example` | **Modificar**: `ACCOUNTS_DB_PATH`, `SUBWINDOW_DAYS` |
| `tests/providers/_fixtures.py` | **Crear**: builders de respuestas SearchTimeline sintéticas |
| `tests/providers/test_subwindows.py` | **Crear**: tests del helper puro |
| `tests/providers/test_twscrape_extract.py` | **Crear**: tests de `extract_*`, `count_accessed`, `build_query` |
| `tests/providers/test_twscrape_provider.py` | **Crear**: `fetch_page` (fetcher fake) + wiring `GatedProvider` + `build_pool` + `_build_params` |
| `tests/test_config.py` | **Modificar**: asserts de los campos nuevos |

**Notas para el ejecutor (convenciones del repo):**
- `asyncio_mode = "auto"`: los tests `async def` corren sin decorador. No agregar `@pytest.mark.asyncio`.
- **Nombres de tests en español SIN tildes** (son identificadores); docstrings y comentarios CON acentuación correcta.
- `from __future__ import annotations` arriba de **cada** archivo (consistente con el repo).
- `mypy` corre **solo sobre `src`** (`files = ["src"]`): los tests no se type-checkean, pero deben quedar **ruff-clean** (reglas `E, F, I, UP, B, ASYNC`, line-length 100).
- `Settings(_env_file=None)` deshabilita la lectura de `.env` (patrón ya usado en `tests/test_config.py`): usalo en tests para que sean deterministas sin importar el `.env` local.
- Los builders de fixtures se importan con `from tests.providers._fixtures import ...`. Funciona porque `tests/` y `tests/providers/` son paquetes (`__init__.py`) y pytest (modo `prepend`) pone la raíz del repo en `sys.path`. `_fixtures.py` no matchea `test_*` → pytest no lo colecta como tests.
- twscrape no trae stubs de tipos: el override de mypy (`ignore_missing_imports`) de Task 1 es **obligatorio** o `mypy --strict` falla al importar twscrape.

**Fuera de alcance** (próximas fases, NO crear ahora): `mappers/`, el loop orquestador de sub-ventanas (fase CLI), `storage/`, `service/`, `cli.py`, `providers/official_api.py`, `providers/factory.py`, troceado adaptivo, pool de N cuentas.

---

## Task 1: Dependencias, config y `ProviderError`

**Files:**
- Modify: `pyproject.toml` (dep + override mypy)
- Modify: `src/tweet_extractor/config.py`
- Modify: `src/tweet_extractor/providers/base.py`
- Modify: `.env.example`
- Test: `tests/test_config.py`

- [ ] **Step 1: Agregar la dependencia twscrape**

Run: `uv add twscrape`
Expected: `pyproject.toml` gana `twscrape>=...` en `dependencies` y `uv.lock` se actualiza (arrastra `httpx` y demás).

- [ ] **Step 2: Agregar el override de mypy para twscrape (sin stubs)**

En `pyproject.toml`, después del bloque `[tool.mypy]`, agregar:

```toml
[[tool.mypy.overrides]]
module = ["twscrape", "twscrape.*"]
ignore_missing_imports = true
```

- [ ] **Step 3: Escribir el test de config (falla primero)**

En `tests/test_config.py`, dentro de `test_settings_defaults`, agregar el `delenv` de las claves nuevas y los asserts. Reemplazar el cuerpo de `test_settings_defaults` por:

```python
def test_settings_defaults(monkeypatch):
    from pathlib import Path

    for k in (
        "HARD_CAP", "WINDOW_S", "PAGE_SIZE", "X_AUTH_TOKEN", "X_CT0",
        "ACCOUNTS_DB_PATH", "SUBWINDOW_DAYS",
    ):
        monkeypatch.delenv(k, raising=False)

    from tweet_extractor.config import Settings

    s = Settings(_env_file=None)
    assert s.hard_cap == 900_000
    assert s.window_s == 86_400
    assert s.page_size == 20
    assert s.max_accessed_per_page == 60  # 20 * 3 (citante + 2 niveles de quote)
    assert s.x_auth_token is None
    assert s.accounts_db_path == Path("data/accounts.db")
    assert s.subwindow_days == 7
```

- [ ] **Step 4: Correr el test para verificar que falla**

Run: `uv run pytest tests/test_config.py::test_settings_defaults -v`
Expected: FAIL con `AttributeError: 'Settings' object has no attribute 'accounts_db_path'`.

- [ ] **Step 5: Agregar los campos a `Settings`**

En `src/tweet_extractor/config.py`, reemplazar el bloque de campos:

```python
    audit_db_path: Path = Path("data/audit/ledger.db")
    data_db_path: Path = Path("data/tweets.db")
    page_size: int = 20
    x_auth_token: str | None = None
    x_ct0: str | None = None
```

por:

```python
    audit_db_path: Path = Path("data/audit/ledger.db")
    data_db_path: Path = Path("data/tweets.db")
    accounts_db_path: Path = Path("data/accounts.db")  # cuentas/cookies de twscrape (git-ignored)
    page_size: int = 20
    subwindow_days: int = 7  # paso default (días) del troceado de búsqueda
    x_auth_token: str | None = None
    x_ct0: str | None = None
```

- [ ] **Step 6: Correr el test para verificar que pasa**

Run: `uv run pytest tests/test_config.py -v`
Expected: PASS (los 3 tests de config).

- [ ] **Step 7: Agregar `ProviderError` a `base.py`**

En `src/tweet_extractor/providers/base.py`, justo después del bloque de `RawTweet` (la línea con su docstring, antes de `@dataclass(frozen=True) class SearchQuery`), agregar:

```python
class ProviderError(RuntimeError):
    """Un provider no pudo completar un fetch: cuenta inválida/suspendida,
    rate-limit agotado, o configuración faltante (p.ej. cookies)."""
```

- [ ] **Step 8: Actualizar `.env.example`**

En `.env.example`, reemplazar el bloque de rutas:

```
# Rutas de almacenamiento (el ledger de auditoría va separado del SQLite de datos).
AUDIT_DB_PATH=data/audit/ledger.db
DATA_DB_PATH=data/tweets.db

# Tamaño de página del backend (afecta la cota de accesos por página).
PAGE_SIZE=20
```

por:

```
# Rutas de almacenamiento (el ledger de auditoría va separado del SQLite de datos).
AUDIT_DB_PATH=data/audit/ledger.db
DATA_DB_PATH=data/tweets.db
# Store de cuentas/cookies de twscrape (git-ignored; separado del ledger y de los datos).
ACCOUNTS_DB_PATH=data/accounts.db

# Tamaño de página del backend (afecta la cota de accesos por página).
PAGE_SIZE=20
# Paso default (días) del troceado de búsqueda por sub-ventanas.
SUBWINDOW_DAYS=7
```

- [ ] **Step 9: Verificar lint/types y commitear**

Run: `uv run ruff check . && uv run ruff format . && uv run mypy src && uv run pytest -q`
Expected: todo verde (mypy limpio confirma que el override de twscrape funciona aunque todavía no se importe).

```bash
git add pyproject.toml uv.lock src/tweet_extractor/config.py src/tweet_extractor/providers/base.py .env.example tests/test_config.py
git commit -m "chore(providers): dep twscrape + config (accounts_db, subwindow_days) + ProviderError"
```

---

## Task 2: `subwindows()` — helper puro de troceado

**Files:**
- Create: `src/tweet_extractor/providers/subwindows.py`
- Test: `tests/providers/test_subwindows.py`

- [ ] **Step 1: Escribir los tests (fallan primero)**

Crear `tests/providers/test_subwindows.py`:

```python
from __future__ import annotations

from datetime import UTC, datetime

import pytest

from tweet_extractor.providers.subwindows import subwindows


def test_subwindows_un_solo_tramo_si_rango_menor_que_paso():
    since = datetime(2023, 1, 1, tzinfo=UTC)
    until = datetime(2023, 1, 5, tzinfo=UTC)
    assert list(subwindows(since, until, step_days=7)) == [(since, until)]


def test_subwindows_trocea_sin_solape_ni_hueco():
    since = datetime(2023, 1, 1, tzinfo=UTC)
    until = datetime(2023, 1, 15, tzinfo=UTC)
    assert list(subwindows(since, until, step_days=7)) == [
        (datetime(2023, 1, 1, tzinfo=UTC), datetime(2023, 1, 8, tzinfo=UTC)),
        (datetime(2023, 1, 8, tzinfo=UTC), datetime(2023, 1, 15, tzinfo=UTC)),
    ]


def test_subwindows_ultimo_tramo_recortado_a_until():
    since = datetime(2023, 1, 1, tzinfo=UTC)
    until = datetime(2023, 1, 10, tzinfo=UTC)
    assert list(subwindows(since, until, step_days=7)) == [
        (datetime(2023, 1, 1, tzinfo=UTC), datetime(2023, 1, 8, tzinfo=UTC)),
        (datetime(2023, 1, 8, tzinfo=UTC), datetime(2023, 1, 10, tzinfo=UTC)),
    ]


def test_subwindows_paso_de_un_dia():
    since = datetime(2023, 1, 1, tzinfo=UTC)
    until = datetime(2023, 1, 3, tzinfo=UTC)
    assert list(subwindows(since, until, step_days=1)) == [
        (datetime(2023, 1, 1, tzinfo=UTC), datetime(2023, 1, 2, tzinfo=UTC)),
        (datetime(2023, 1, 2, tzinfo=UTC), datetime(2023, 1, 3, tzinfo=UTC)),
    ]


def test_subwindows_rechaza_naive():
    with pytest.raises(ValueError):
        list(subwindows(datetime(2023, 1, 1), datetime(2023, 1, 8), step_days=7))


def test_subwindows_rechaza_since_posterior_o_igual():
    with pytest.raises(ValueError):
        list(subwindows(datetime(2023, 1, 8, tzinfo=UTC), datetime(2023, 1, 1, tzinfo=UTC), 7))


def test_subwindows_rechaza_step_menor_a_uno():
    with pytest.raises(ValueError):
        list(subwindows(datetime(2023, 1, 1, tzinfo=UTC), datetime(2023, 1, 8, tzinfo=UTC), 0))
```

- [ ] **Step 2: Correr para verificar que fallan**

Run: `uv run pytest tests/providers/test_subwindows.py -v`
Expected: FAIL con `ModuleNotFoundError: No module named 'tweet_extractor.providers.subwindows'`.

- [ ] **Step 3: Implementar el helper**

Crear `src/tweet_extractor/providers/subwindows.py`:

```python
from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timedelta


def subwindows(
    since: datetime, until: datetime, step_days: int = 7
) -> Iterator[tuple[datetime, datetime]]:
    """Trocea `[since, until)` en tramos de `step_days` días, sin solape ni hueco
    (el solape gastaría presupuesto del Compliance Gate). El último tramo se recorta
    a `until`. Función pura: el loop que la consume (un `SearchQuery` por tramo) es
    de la fase de orquestación, para no saltear el gate.

    Precondiciones (espejo de `SearchQuery`): `since`/`until` timezone-aware,
    `since < until`, `step_days >= 1`.
    """
    if since.utcoffset() is None or until.utcoffset() is None:
        raise ValueError("since y until deben ser timezone-aware (UTC)")
    if since >= until:
        raise ValueError("since debe ser anterior a until")
    if step_days < 1:
        raise ValueError("step_days debe ser >= 1")

    step = timedelta(days=step_days)
    start = since
    while start < until:
        end = min(start + step, until)
        yield (start, end)
        start = end
```

- [ ] **Step 4: Correr para verificar que pasan**

Run: `uv run pytest tests/providers/test_subwindows.py -v`
Expected: PASS (7 tests).

- [ ] **Step 5: Lint/types y commit**

Run: `uv run ruff check . && uv run mypy src`
Expected: limpio.

```bash
git add src/tweet_extractor/providers/subwindows.py tests/providers/test_subwindows.py
git commit -m "feat(providers): subwindows() — troceado puro de rango sin solape"
```

---

## Task 3: Builders de fixtures + helpers de envelope (`extract_*`, `count_accessed`)

**Files:**
- Create: `tests/providers/_fixtures.py`
- Create: `src/tweet_extractor/providers/twscrape_provider.py` (parcial: solo los helpers puros y `_walk`)
- Test: `tests/providers/test_twscrape_extract.py`

- [ ] **Step 1: Crear los builders de fixtures**

Crear `tests/providers/_fixtures.py` (no matchea `test_*` → pytest no lo colecta):

```python
from __future__ import annotations

from typing import Any


def tweet_result(
    rest_id: str,
    *,
    typename: str = "Tweet",
    quoted: dict[str, Any] | None = None,
    retweeted: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Un `tweet_results.result` crudo de GraphQL, con quote/RT embebidos opcionales.
    `typename="TweetWithVisibilityResults"` lo envuelve con el `.tweet` interno."""
    legacy: dict[str, Any] = {
        "full_text": f"texto {rest_id}",
        "created_at": "Wed Jan 04 00:00:00 +0000 2023",
    }
    if retweeted is not None:
        legacy["retweeted_status_result"] = {"result": retweeted}
    result: dict[str, Any] = {"__typename": "Tweet", "rest_id": rest_id, "legacy": legacy}
    if quoted is not None:
        result["quoted_status_result"] = {"result": quoted}
    if typename == "TweetWithVisibilityResults":
        return {"__typename": "TweetWithVisibilityResults", "tweet": result}
    return result


def tweet_entry(rest_id: str, **kwargs: Any) -> dict[str, Any]:
    """Una entry de timeline de tipo tweet (`entryId` = `tweet-<rest_id>`)."""
    return {
        "entryId": f"tweet-{rest_id}",
        "content": {
            "entryType": "TimelineTimelineItem",
            "itemContent": {
                "itemType": "TimelineTweet",
                "tweet_results": {"result": tweet_result(rest_id, **kwargs)},
            },
        },
    }


def cursor_entry(value: str, kind: str = "Bottom") -> dict[str, Any]:
    """Una entry de cursor (`Bottom` para la próxima página, `Top` para la anterior)."""
    return {
        "entryId": f"cursor-{kind.lower()}-{value}",
        "content": {
            "entryType": "TimelineTimelineCursor",
            "cursorType": kind,
            "value": value,
        },
    }


def search_response(entries: list[dict[str, Any]]) -> dict[str, Any]:
    """Una respuesta SearchTimeline con las entries dadas en un TimelineAddEntries."""
    return {
        "data": {
            "search_by_raw_query": {
                "search_timeline": {
                    "timeline": {
                        "instructions": [{"type": "TimelineAddEntries", "entries": entries}]
                    }
                }
            }
        }
    }
```

- [ ] **Step 2: Escribir los tests de los helpers (fallan primero)**

Crear `tests/providers/test_twscrape_extract.py`:

```python
from __future__ import annotations

from tests.providers._fixtures import (
    cursor_entry,
    search_response,
    tweet_entry,
    tweet_result,
)
from tweet_extractor.providers.twscrape_provider import (
    count_accessed,
    extract_bottom_cursor,
    extract_tweet_results,
)


def test_extract_tweet_results_devuelve_los_de_nivel_tope():
    raw = search_response([tweet_entry("1"), tweet_entry("2"), cursor_entry("CUR")])
    out = extract_tweet_results(raw)
    assert [t["rest_id"] for t in out] == ["1", "2"]


def test_extract_tweet_results_ignora_quotes_y_rt_embebidos():
    # El quote/RT viven DENTRO de result (no son entries) → no son nivel-tope.
    raw = search_response([
        tweet_entry("1", quoted=tweet_result("q1")),
        tweet_entry("2", retweeted=tweet_result("rt2")),
    ])
    out = extract_tweet_results(raw)
    assert [t["rest_id"] for t in out] == ["1", "2"]


def test_extract_tweet_results_no_desenvuelve_tweet_with_visibility_results():
    # El provider entrega el wrapper crudo; desenvolver `.tweet` es del mapper.
    raw = search_response([tweet_entry("1", typename="TweetWithVisibilityResults")])
    out = extract_tweet_results(raw)
    assert len(out) == 1
    assert out[0]["__typename"] == "TweetWithVisibilityResults"
    assert out[0]["tweet"]["rest_id"] == "1"


def test_extract_tweet_results_pagina_vacia():
    assert extract_tweet_results(search_response([])) == []


def test_extract_bottom_cursor_devuelve_value():
    raw = search_response([tweet_entry("1"), cursor_entry("NEXT"), cursor_entry("PREV", kind="Top")])
    assert extract_bottom_cursor(raw) == "NEXT"


def test_extract_bottom_cursor_none_al_final():
    raw = search_response([tweet_entry("1")])
    assert extract_bottom_cursor(raw) is None


def test_count_accessed_cuenta_citante():
    raw = search_response([tweet_entry("1"), tweet_entry("2")])
    assert count_accessed(raw) == 2


def test_count_accessed_sobre_cuenta_quote_embebido():
    # Compliance: el quote embebido cuenta como acceso aunque no se persista.
    raw = search_response([tweet_entry("1", quoted=tweet_result("q1"))])
    assert count_accessed(raw) == 2  # citante + quote


def test_count_accessed_sobre_cuenta_rt_descartado():
    raw = search_response([tweet_entry("1", retweeted=tweet_result("rt1"))])
    assert count_accessed(raw) == 2  # citante + RT (que el mapper descartará)


def test_count_accessed_tweet_with_visibility_results_cuenta_dos():
    # Wrapper (TVR) + .tweet interno (Tweet) → 2: sobre-cuenta, dirección segura del cap.
    raw = search_response([tweet_entry("1", typename="TweetWithVisibilityResults")])
    assert count_accessed(raw) == 2


def test_count_accessed_nunca_menor_que_tweets_de_nivel_tope():
    raw = search_response([tweet_entry("1", quoted=tweet_result("q1")), tweet_entry("2")])
    assert count_accessed(raw) >= len(extract_tweet_results(raw))
```

- [ ] **Step 3: Correr para verificar que fallan**

Run: `uv run pytest tests/providers/test_twscrape_extract.py -v`
Expected: FAIL con `ModuleNotFoundError: No module named 'tweet_extractor.providers.twscrape_provider'`.

- [ ] **Step 4: Implementar los helpers puros (creación parcial del módulo)**

Crear `src/tweet_extractor/providers/twscrape_provider.py` con SOLO los helpers puros (el resto se agrega en Tasks 4–7):

```python
from __future__ import annotations

from collections.abc import Iterator
from typing import Any

_TWEET_TYPENAMES = frozenset({"Tweet", "TweetWithVisibilityResults"})


def _walk(obj: Any) -> Iterator[dict[str, Any]]:
    """Recorre una estructura JSON anidada (depth-first) y hace yield de cada dict.
    La profundidad del envelope de SearchTimeline es acotada (decenas de niveles)."""
    if isinstance(obj, dict):
        yield obj
        for value in obj.values():
            yield from _walk(value)
    elif isinstance(obj, list):
        for value in obj:
            yield from _walk(value)


def extract_tweet_results(raw: dict[str, Any]) -> list[dict[str, Any]]:
    """Los `tweet_results.result` crudos de nivel-tope (uno por entry `tweet-*`).
    NO interpreta el tweet (typename/quote/RT/links) — eso es del mapper. Los quotes/RT
    embebidos viven dentro de `result` (no son entries) → no se confunden con tope.
    Navegación defensiva: tolera claves ausentes (página vacía → `[]`)."""
    out: list[dict[str, Any]] = []
    for d in _walk(raw):
        entry_id = d.get("entryId")
        if not (isinstance(entry_id, str) and entry_id.startswith("tweet-")):
            continue
        content = d.get("content")
        item = content.get("itemContent") if isinstance(content, dict) else None
        tweet_results = item.get("tweet_results") if isinstance(item, dict) else None
        result = tweet_results.get("result") if isinstance(tweet_results, dict) else None
        if isinstance(result, dict):
            out.append(result)
    return out


def extract_bottom_cursor(raw: dict[str, Any]) -> str | None:
    """El cursor `Bottom` para la próxima página (mismo criterio que twscrape: el
    objeto con `cursorType == "Bottom"`). `None` al final → la paginación heredada corta."""
    for d in _walk(raw):
        if d.get("cursorType") == "Bottom":
            value = d.get("value")
            return value if isinstance(value, str) else None
    return None


def count_accessed(raw: dict[str, Any]) -> int:
    """TODO objeto-tweet tocado por la respuesta (citante + quote embebido + RT
    descartado): cantidad de dicts con `__typename` de tweet en cualquier nivel.
    Sobre-cuenta a propósito (un TweetWithVisibilityResults cuenta 2: wrapper + `.tweet`)
    — la dirección SEGURA del cap (el ledger no deduplica; sobre-contar es seguro)."""
    return sum(1 for d in _walk(raw) if d.get("__typename") in _TWEET_TYPENAMES)
```

- [ ] **Step 5: Correr para verificar que pasan**

Run: `uv run pytest tests/providers/test_twscrape_extract.py -v`
Expected: PASS (11 tests).

- [ ] **Step 6: Lint/types y commit**

Run: `uv run ruff check . && uv run mypy src`
Expected: limpio.

```bash
git add src/tweet_extractor/providers/twscrape_provider.py tests/providers/_fixtures.py tests/providers/test_twscrape_extract.py
git commit -m "feat(providers): extract_tweet_results/bottom_cursor/count_accessed + fixtures SearchTimeline"
```

---

## Task 4: `build_query()`

**Files:**
- Modify: `src/tweet_extractor/providers/twscrape_provider.py`
- Test: `tests/providers/test_twscrape_extract.py`

- [ ] **Step 1: Agregar los tests (fallan primero)**

Al final de `tests/providers/test_twscrape_extract.py`, agregar (y extender el import de `twscrape_provider`):

```python
from datetime import UTC, datetime

from tweet_extractor.providers.base import SearchQuery
from tweet_extractor.providers.twscrape_provider import build_query


def test_build_query_formato_from_user_y_fechas():
    q = SearchQuery(
        username="someuser",
        since=datetime(2023, 1, 1, tzinfo=UTC),
        until=datetime(2024, 1, 1, tzinfo=UTC),
    )
    assert build_query(q) == "from:someuser since:2023-01-01 until:2024-01-01"


def test_build_query_usa_granularidad_de_fecha_no_segundos():
    q = SearchQuery(
        username="u",
        since=datetime(2023, 3, 5, 14, 30, tzinfo=UTC),
        until=datetime(2023, 3, 12, 9, 0, tzinfo=UTC),
    )
    assert build_query(q) == "from:u since:2023-03-05 until:2023-03-12"
```

(Nota: poné estos `import` arriba junto al resto, no en medio del archivo; ruff `I` ordena imports.)

- [ ] **Step 2: Correr para verificar que falla**

Run: `uv run pytest tests/providers/test_twscrape_extract.py -k build_query -v`
Expected: FAIL con `ImportError: cannot import name 'build_query'`.

- [ ] **Step 3: Implementar `build_query`**

En `src/tweet_extractor/providers/twscrape_provider.py`, agregar el import y la función (debajo de `_TWEET_TYPENAMES`):

```python
from tweet_extractor.providers.base import SearchQuery


def build_query(query: SearchQuery) -> str:
    """La query de búsqueda `from:user since: until:` con operadores POR FECHA
    (granularidad de día), alineada a UTC. Se eligen `since:`/`until:` sobre
    `since_time:`/`until_time:` (epoch): mismo costo por request, los segundos no se
    necesitan, y son los operadores más battle-tested (menor riesgo de no-op del
    filtro). `until:` es exclusivo y `since:` inclusivo → ventanas adyacentes no
    solapan. `query.since/until` son UTC tz-aware (garantía de `SearchQuery`)."""
    return (
        f"from:{query.username} "
        f"since:{query.since:%Y-%m-%d} "
        f"until:{query.until:%Y-%m-%d}"
    )
```

- [ ] **Step 4: Correr para verificar que pasa**

Run: `uv run pytest tests/providers/test_twscrape_extract.py -v`
Expected: PASS (13 tests).

- [ ] **Step 5: Lint/types y commit**

Run: `uv run ruff check . && uv run ruff format . && uv run mypy src`
Expected: limpio.

```bash
git add src/tweet_extractor/providers/twscrape_provider.py tests/providers/test_twscrape_extract.py
git commit -m "feat(providers): build_query — from:user since/until por fecha, UTC"
```

---

## Task 5: `TwscrapeProvider.fetch_page` (con fetcher inyectado)

**Files:**
- Create: `src/tweet_extractor/providers/_twscrape_gql.py` (stub mínimo de `fetch_search_page` para el import del default; cuerpo real en Task 7)
- Modify: `src/tweet_extractor/providers/twscrape_provider.py`
- Test: `tests/providers/test_twscrape_provider.py`

- [ ] **Step 1: Crear el módulo de acoplamiento con `fetch_search_page` (firma + cuerpo real)**

> El cuerpo real va acá desde ya (no es un stub): no se ejercita en los tests de esta task (se inyecta un fake), pero el default `page_fetcher=fetch_search_page` necesita el símbolo importable. Sus tests propios están en Task 7.

Crear `src/tweet_extractor/providers/_twscrape_gql.py`:

```python
from __future__ import annotations

from typing import Any

from twscrape import AccountsPool
from twscrape.api import GQL_FEATURES, GQL_URL, OP_SearchTimeline  # OP_* lleva el queryId auto-actualizado
from twscrape.queue_client import QueueClient  # rotación de cuentas + rate-limit/backoff
from twscrape.utils import encode_params

from tweet_extractor.providers.base import ProviderError


def _build_params(query_str: str, count: int, cursor: str | None) -> dict[str, Any]:
    """Arma los params del request SearchTimeline (variables + features + fieldToggles).
    Pura y testeable; el cursor se incluye solo si hay (la primera página va sin)."""
    variables: dict[str, Any] = {
        "rawQuery": query_str,
        "count": count,
        "product": "Latest",  # reverse-chrono (no relevancia) → mejor cobertura por ventana
        "querySource": "typed_query",
    }
    if cursor is not None:
        variables["cursor"] = cursor
    return {
        "variables": variables,
        "features": GQL_FEATURES,
        "fieldToggles": {"withArticleRichContentState": False},
    }


async def fetch_search_page(
    pool: AccountsPool | None, query_str: str, count: int, cursor: str | None
) -> dict[str, Any]:
    """UN request de página de SearchTimeline (el único seam de red del provider).
    `QueueClient` por-página: el estado de rate-limit vive en el `AccountsPool`
    (persistido), así que recrearlo respeta los locks; el backoff ante 429 lo maneja
    twscrape. `rep is None` → `ProviderError` (no enmascarar como fin de resultados)."""
    if pool is None:
        raise ProviderError("AccountsPool no inicializado (usar build_pool)")
    params = _build_params(query_str, count, cursor)
    async with QueueClient(pool, "SearchTimeline", False) as client:
        rep = await client.get(f"{GQL_URL}/{OP_SearchTimeline}", params=encode_params(params))
    if rep is None:
        raise ProviderError(
            "twscrape no pudo completar el request de búsqueda "
            "(cuenta inválida/suspendida o rate-limit agotado)"
        )
    result: dict[str, Any] = rep.json()
    return result
```

- [ ] **Step 1b: Verificar que los símbolos de twscrape resuelven (superficie de acoplamiento)**

Run: `uv run python -c "from tweet_extractor.providers._twscrape_gql import fetch_search_page; print('ok')"`
Expected: imprime `ok`. Si falla con `ImportError` sobre `OP_SearchTimeline`/`GQL_URL`/`GQL_FEATURES`/`QueueClient`/`encode_params`, la versión instalada de twscrape renombró ese símbolo: ajustar SOLO los imports de `_twscrape_gql.py` para matchear (`uv run python -c "import twscrape.api as a; print([n for n in dir(a) if 'Search' in n or n.startswith('GQL')])"` ayuda a encontrar el nombre nuevo). NO hardcodear queryId.

- [ ] **Step 2: Escribir los tests de `fetch_page` (fallan primero)**

Crear `tests/providers/test_twscrape_provider.py`:

```python
from __future__ import annotations

from typing import Any

from tests.providers._fixtures import cursor_entry, search_response, tweet_entry
from tweet_extractor.config import Settings
from tweet_extractor.providers.base import SearchQuery
from tweet_extractor.providers.twscrape_provider import TwscrapeProvider


def _settings() -> Settings:
    return Settings(_env_file=None)


async def test_fetch_page_arma_page_desde_la_respuesta(sample_query):
    resp = search_response([tweet_entry("1"), tweet_entry("2"), cursor_entry("CUR")])

    async def fake_fetch(pool: Any, query_str: str, count: int, cursor: str | None) -> dict[str, Any]:
        return resp

    provider = TwscrapeProvider(_settings(), pool=None, page_fetcher=fake_fetch)
    page = await provider.fetch_page(sample_query, None)

    assert [t["rest_id"] for t in page.tweets] == ["1", "2"]
    assert page.next_cursor == "CUR"
    assert page.accessed_count == 2


async def test_fetch_page_pasa_query_count_y_cursor_al_fetcher(sample_query):
    seen: dict[str, Any] = {}

    async def fake_fetch(pool: Any, query_str: str, count: int, cursor: str | None) -> dict[str, Any]:
        seen["query_str"] = query_str
        seen["count"] = count
        seen["cursor"] = cursor
        return search_response([])

    provider = TwscrapeProvider(_settings(), pool=None, page_fetcher=fake_fetch)
    await provider.fetch_page(sample_query, "CUR")

    assert seen["query_str"] == "from:someuser since:2023-01-01 until:2024-01-01"
    assert seen["count"] == 20
    assert seen["cursor"] == "CUR"


async def test_fetch_page_expone_la_cota_de_reserva():
    provider = TwscrapeProvider(_settings(), pool=None, page_fetcher=_unused_fetcher)
    assert provider.max_accessed_per_page == 60  # page_size(20) * ACCESS_FACTOR(3)


async def _unused_fetcher(pool: Any, query_str: str, count: int, cursor: str | None) -> dict[str, Any]:
    return search_response([])
```

- [ ] **Step 3: Correr para verificar que fallan**

Run: `uv run pytest tests/providers/test_twscrape_provider.py -v`
Expected: FAIL con `ImportError: cannot import name 'TwscrapeProvider'`.

- [ ] **Step 4: Implementar `TwscrapeProvider`**

En `src/tweet_extractor/providers/twscrape_provider.py`, agregar los imports nuevos y la clase (al final del archivo). El bloque de imports superior queda:

```python
from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterator
from typing import Any

from twscrape import AccountsPool

from tweet_extractor.config import Settings
from tweet_extractor.providers._twscrape_gql import fetch_search_page
from tweet_extractor.providers.base import Page, SearchQuery, TweetProvider
```

(El import previo de `SearchQuery` de Task 4 queda subsumido en esta línea — `SearchQuery` se importa una sola vez.)

Y al final del módulo:

```python
PageFetcher = Callable[[AccountsPool | None, str, int, "str | None"], Awaitable[dict[str, Any]]]


class TwscrapeProvider(TweetProvider):
    """Provider de scraping gratis vía twscrape (httpx, sin navegador). Entrega dicts
    crudos de GraphQL por página; el mapper los interpreta. Conforma `fetch_page`
    (un request por página, cursor externo) para que el `GatedProvider` lo gatee. El
    fetcher de red se inyecta (`page_fetcher`) → tests offline con fixtures."""

    def __init__(
        self,
        settings: Settings,
        pool: AccountsPool | None,
        *,
        page_fetcher: PageFetcher = fetch_search_page,
    ) -> None:
        self.max_accessed_per_page = settings.max_accessed_per_page  # cota de reserva del gate
        self._count = settings.page_size
        self._pool = pool
        self._fetch = page_fetcher

    async def fetch_page(self, query: SearchQuery, cursor: str | None) -> Page:
        raw = await self._fetch(self._pool, build_query(query), self._count, cursor)
        return Page(
            tweets=extract_tweet_results(raw),
            accessed_count=count_accessed(raw),
            next_cursor=extract_bottom_cursor(raw),
        )
```

- [ ] **Step 5: Correr para verificar que pasan**

Run: `uv run pytest tests/providers/test_twscrape_provider.py -v`
Expected: PASS (3 tests).

- [ ] **Step 6: Lint/types y commit**

Run: `uv run ruff check . && uv run ruff format . && uv run mypy src && uv run pytest -q`
Expected: todo verde.

```bash
git add src/tweet_extractor/providers/_twscrape_gql.py src/tweet_extractor/providers/twscrape_provider.py tests/providers/test_twscrape_provider.py
git commit -m "feat(providers): TwscrapeProvider.fetch_page + fetch_search_page (seam de red inyectable)"
```

---

## Task 6: Wiring `GatedProvider(TwscrapeProvider)`

**Files:**
- Modify: `tests/providers/test_twscrape_provider.py`

> Este test verifica la **integración** de unidades ya construidas (Task 5): no hay implementación nueva, así que pasa apenas se escribe bien. Confirma que `accessed_count` del provider fluye correcto por el gate y que la paginación heredada corta con `next_cursor=None`.

- [ ] **Step 1: Extender los imports del archivo de tests**

Dejar la cabecera de `tests/providers/test_twscrape_provider.py` así (agregar `tweet_result` y `GatedProvider`; ruff `I` ordena):

```python
from __future__ import annotations

from typing import Any

from tests.providers._fixtures import cursor_entry, search_response, tweet_entry, tweet_result
from tweet_extractor.compliance.gated_provider import GatedProvider
from tweet_extractor.config import Settings
from tweet_extractor.providers.base import SearchQuery
from tweet_extractor.providers.twscrape_provider import TwscrapeProvider
```

- [ ] **Step 2: Escribir el test de wiring**

Agregar a `tests/providers/test_twscrape_provider.py`:

```python
async def test_gated_twscrape_reserva_reconcilia_y_pagina(tmp_path, make_gate, sample_query):
    page1 = search_response([
        tweet_entry("1", quoted=tweet_result("q1")),  # citante(1) + quote(1) = 2 accedidos
        cursor_entry("C1"),
    ])
    page2 = search_response([])  # sin entries ni cursor → fin de la paginación
    responses: dict[str | None, dict[str, Any]] = {None: page1, "C1": page2}

    async def fake_fetch(pool: Any, query_str: str, count: int, cursor: str | None) -> dict[str, Any]:
        return responses[cursor]

    provider = TwscrapeProvider(_settings(), pool=None, page_fetcher=fake_fetch)
    gate = make_gate(tmp_path / "ledger.db", hard_cap=1000)
    await gate.setup()
    gated = GatedProvider(provider, gate)

    out = [t async for t in gated.fetch_tweets(sample_query)]

    assert [t["rest_id"] for t in out] == ["1"]
    # page1 reconcilia a 2; page2 a 0. La cota reservada (60) NO queda en el ledger.
    assert await gate.usage() == 2
```

- [ ] **Step 3: Correr para verificar que pasa**

Run: `uv run pytest tests/providers/test_twscrape_provider.py -v`
Expected: PASS (4 tests). Si falla por `ModuleNotFoundError: tests.providers._fixtures`, ver la nota de `sys.path` en "Notas para el ejecutor".

- [ ] **Step 4: Lint/types y commit**

Run: `uv run ruff check . && uv run mypy src`
Expected: limpio.

```bash
git add tests/providers/test_twscrape_provider.py
git commit -m "test(providers): wiring GatedProvider(TwscrapeProvider) reserva/reconcilia/pagina"
```

---

## Task 7: `_build_params` + `build_pool`

**Files:**
- Modify: `src/tweet_extractor/providers/twscrape_provider.py` (agregar `build_pool`)
- Test: `tests/providers/test_twscrape_provider.py`

- [ ] **Step 1: Escribir los tests (fallan primero)**

Agregar a `tests/providers/test_twscrape_provider.py`:

```python
from pathlib import Path

from tweet_extractor.providers._twscrape_gql import _build_params
from tweet_extractor.providers.base import ProviderError
from tweet_extractor.providers.twscrape_provider import build_pool


def test_build_params_incluye_rawquery_count_product():
    params = _build_params("from:u since:2023-01-01 until:2023-01-08", 20, None)
    variables = params["variables"]
    assert variables["rawQuery"] == "from:u since:2023-01-01 until:2023-01-08"
    assert variables["count"] == 20
    assert variables["product"] == "Latest"
    assert "cursor" not in variables  # primera página sin cursor


def test_build_params_agrega_cursor_si_hay():
    params = _build_params("from:u since:2023-01-01 until:2023-01-08", 20, "CUR")
    assert params["variables"]["cursor"] == "CUR"


async def test_build_pool_carga_cuenta_activa_con_cookies(tmp_path):
    settings = Settings(
        _env_file=None,
        x_auth_token="AUTHTOKEN",
        x_ct0="CT0TOKEN",
        accounts_db_path=tmp_path / "accounts.db",
    )
    pool = await build_pool(settings)
    acc = await pool.get_account("xport-session")
    assert acc is not None
    assert acc.active is True


async def test_build_pool_idempotente(tmp_path):
    settings = Settings(
        _env_file=None,
        x_auth_token="AUTHTOKEN",
        x_ct0="CT0TOKEN",
        accounts_db_path=tmp_path / "accounts.db",
    )
    await build_pool(settings)
    pool = await build_pool(settings)  # segunda vez: no debe crashear ni duplicar
    acc = await pool.get_account("xport-session")
    assert acc is not None


async def test_build_pool_sin_cookies_falla(tmp_path):
    settings = Settings(_env_file=None, accounts_db_path=tmp_path / "accounts.db")
    with pytest.raises(ProviderError):
        await build_pool(settings)
```

Y agregar `import pytest` al tope si no está (lo necesita el último test). Mantené el orden de imports ruff-clean.

- [ ] **Step 2: Correr para verificar que fallan**

Run: `uv run pytest tests/providers/test_twscrape_provider.py -k "build_pool or build_params" -v`
Expected: FAIL con `ImportError: cannot import name 'build_pool'` (`_build_params` ya existe de Task 5, sus 2 tests pasarían; `build_pool` falla).

- [ ] **Step 3: Implementar `build_pool`**

En `src/tweet_extractor/providers/twscrape_provider.py`, agregar al final (usa `AccountsPool` y `ProviderError`; extender el import de `base` con `ProviderError`):

El import de base queda:

```python
from tweet_extractor.providers.base import Page, ProviderError, SearchQuery, TweetProvider
```

Y la función:

```python
async def build_pool(settings: Settings) -> AccountsPool:
    """Arma el `AccountsPool` de twscrape con UNA cuenta desde las cookies del `.env`
    (estructura pool-friendly para crecer a N cuentas después). Con `ct0` presente la
    cuenta queda activa sin login. Idempotente (twscrape persiste en `accounts.db`)."""
    if not settings.x_auth_token or not settings.x_ct0:
        raise ProviderError(
            "Faltan cookies X_AUTH_TOKEN/X_CT0 en el entorno (.env). "
            "Cargá una cuenta de X descartable."
        )
    settings.accounts_db_path.parent.mkdir(parents=True, exist_ok=True)
    pool = AccountsPool(db_file=str(settings.accounts_db_path))
    if await pool.get_account("xport-session") is None:
        await pool.add_account(
            username="xport-session",
            password="",
            email="",
            email_password="",
            cookies=f"auth_token={settings.x_auth_token}; ct0={settings.x_ct0}",
        )
    return pool
```

- [ ] **Step 4: Correr para verificar que pasan**

Run: `uv run pytest tests/providers/test_twscrape_provider.py -v`
Expected: PASS (todos: fetch_page, wiring, _build_params, build_pool).

- [ ] **Step 5: Lint/types y commit**

Run: `uv run ruff check . && uv run ruff format . && uv run mypy src`
Expected: limpio.

```bash
git add src/tweet_extractor/providers/twscrape_provider.py tests/providers/test_twscrape_provider.py
git commit -m "feat(providers): build_pool (una cuenta desde .env) + tests de _build_params"
```

---

## Task 8: Verificación final (suite + lint + types) y handoff

**Files:**
- Modify: `docs/ESTADO.md`

- [ ] **Step 1: Suite completa + calidad**

Run: `uv run ruff check . && uv run ruff format . && uv run mypy src && uv run pytest -q`
Expected: TODO verde. Contar los tests nuevos: subwindows (7) + extract (13) + provider (≥9) + config (3) = se suman a los 71 previos.

- [ ] **Step 2: Actualizar el handoff `docs/ESTADO.md`**

Marcar el paso 4 (`TwscrapeProvider`) como hecho en "Qué está hecho", actualizar "Próximo paso" → `mappers/twscrape_mapper.py` (paso 5), y dejar registradas las 4 verificaciones contra comportamiento vivo (spec §11) como pendientes hasta tener cookies. Actualizar el conteo de tests y la fecha (2026-06-09).

- [ ] **Step 3: Commit del handoff**

```bash
git add docs/ESTADO.md
git commit -m "docs: handoff — TwscrapeProvider hecho, próximo el mapper"
```

- [ ] **Step 4: Nota de verificación viva (NO bloquea esta fase)**

Recordatorio (spec §11): los tests son offline. Antes de confiar el pipeline, con una cuenta descartable real correr una query chica y verificar: (1) el filtro temporal acota; (2) el cursor Bottom pagina y termina; (3) el shape real matchea las fixtures (`tweet_results.result`, `quoted_status_result`, `retweeted_status_result`, `__typename`); (4) `accessed_count` real ≤ la cota de reserva (60). No agregar tests que peguen a la red a la suite.

---

## Self-Review (completado por el autor del plan)

**Cobertura del spec:**
- §2 (integración a nivel de página) → Tasks 5–6 (`fetch_page` + wiring gate). ✅
- §3 D1 (acoplamiento aislado) → Task 5 (`_twscrape_gql.py`). ✅
- §3 D2/§5 (envelope vs mapper, `accessed_count` sobre-cuenta) → Task 3. ✅
- §3 D3 (dicts crudos) → Task 3 (`extract_tweet_results` no desenvuelve TVR). ✅
- §3 D4 / §4.4 (una cuenta) → Task 7 (`build_pool`). ✅
- §3 D5 / §4.3 (sub-ventanas) → Task 2. ✅
- §3 D7 / §4.2 (`build_query` por fecha) → Task 4. ✅
- §3 D8 (DI del fetcher, tests offline) → Task 5. ✅
- §6 (errores: `rep is None` → `ProviderError`; cuenta-objetivo vacía → fin) → Task 1 (`ProviderError`), Task 5 (`fetch_search_page` lanza), Task 6 (page vacía corta). ✅
- §7 (config + deps + `.env.example`) → Task 1. ✅
- §8 (tests offline con fixtures) → Tasks 2–7. ✅

**Consistencia de tipos/nombres:** `fetch_page`, `extract_tweet_results`, `extract_bottom_cursor`, `count_accessed`, `build_query`, `build_pool`, `fetch_search_page`, `_build_params`, `PageFetcher`, `ProviderError` — usados con la misma firma en todas las tasks. `max_accessed_per_page` = 60. `Settings(_env_file=None)` en todos los tests.

**Placeholders:** ninguno — todo paso de código muestra el código completo.
