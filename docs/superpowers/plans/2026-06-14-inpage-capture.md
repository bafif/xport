# Captura GraphQL in-page (patrón C) — Implementation Plan

> **For agentic workers:** implementar task-by-task con TDD (escribir test que falla → correr → implementar → correr → lint/types → commit). Steps en checkbox (`- [ ]`).

**Goal:** Ingerir tweets desde la extensión (que captura el GraphQL que x.com ya descarga) en vez de que el backend pegue a x.com. Esquiva el `x-client-transaction-id` (el navegador lo calcula). Reusa `mappers/` + `storage/` + gate; agrega un camino de ingesta paralelo a `providers/`/`orchestrator`.

**Architecture:** content script MAIN-world parchea `fetch`/`XHR` y captura respuestas `SearchTimeline` → bridge ISOLATED → background → `POST /ingest` del FastAPI. El backend extrae el envelope (helpers ya existentes), mapea (`map_tweet`), persiste (`store.save`, dedup por id) y **registra los accesos en el gate con contabilidad record-after** (el acceso ya ocurrió en el browser: no se reserva, se registra; si supera el cap, se rechazan ingestas siguientes). Export a CSV on-demand con `export_account`, ahora filtrando por rango de fechas. La extensión NO parsea GraphQL.

**Tech Stack:** Python 3.12, `uv`, FastAPI, pydantic v2, `pytest` (`asyncio_mode=auto`), `ruff`, `mypy --strict` (solo `src`). Extensión: WXT, TS, `wxt/browser`.

**Spec:** `docs/superpowers/specs/2026-06-14-inpage-capture-design.md`

---

## File Structure

| Archivo | Responsabilidad |
|---|---|
| `src/tweet_extractor/compliance/gate.py` | **Modificar**: `async def record(n)` (record-after) |
| `src/tweet_extractor/providers/search_envelope.py` | **Crear**: mover acá `extract_tweet_results`, `count_accessed` (puros, sin twscrape) |
| `src/tweet_extractor/providers/twscrape_provider.py` | **Modificar**: re-exportar desde `search_envelope` (back-compat) |
| `src/tweet_extractor/storage/sqlite_store.py` | **Modificar**: `iter_account(account, *, since=None, until=None)` con filtro de fecha |
| `src/tweet_extractor/storage/csv_exporter.py` | **Modificar**: `export_account` filtra por `[since, until)` |
| `src/tweet_extractor/service/ingest.py` | **Crear**: router `POST /ingest`, `POST /export`, `GET /exports/{filename}` |
| `src/tweet_extractor/service/schemas.py` | **Modificar**: `IngestPayload`, `IngestResult`, `ExportRequest`, `ExportResult` |
| `src/tweet_extractor/service/app.py` | **Modificar**: incluir el router de ingesta |
| `src/tweet_extractor/config.py` | **Modificar**: `captures_dir` (raíz de CSV de capturas) |
| `extension/lib/capture.ts` | **Crear**: `matchOp(url)` puro + tipos del mensaje |
| `extension/entrypoints/x-capture.main.ts` | **Crear**: MAIN-world patch fetch/XHR |
| `extension/entrypoints/x-capture.bridge.ts` | **Crear**: ISOLATED bridge |
| `extension/entrypoints/background.ts` | **Modificar**: relay + batch + `POST /ingest` |
| `extension/entrypoints/popup/*` | **Modificar**: toggle capturar + contadores + exportar |
| `tests/compliance/test_gate.py` | **Modificar**: tests de `record` |
| `tests/storage/test_csv_exporter.py` | **Modificar**: tests del filtro de fecha |
| `tests/service/test_ingest.py` | **Crear**: `/ingest` + `/export` end-to-end (TestClient) |

**Convenciones del repo (igual que los plans previos):** `from __future__ import annotations` en cada `.py`; nombres de tests en español SIN tildes; `mypy` solo sobre `src` (tests ruff-clean: `E,F,I,UP,B,ASYNC`, line-length 100); `asyncio_mode=auto` (sin `@pytest.mark.asyncio`); fixtures vía `from tests.providers._fixtures import ...`; TestClient SIEMPRE como context manager (dispara el lifespan). La extensión: `tsc --noEmit` + `wxt build` deben pasar; la lógica DOM/red se verifica manual (no automatizable acá).

---

## Task 1: `gate.record(n)` — contabilidad record-after

**Objetivo:** registrar accesos ya ocurridos sin reservar ni esperar (§5 del spec). Mantiene el ledger global veraz; no deduplica; sobre-contar seguro.

- [ ] **Step 1: tests (fallan primero)** en `tests/compliance/test_gate.py`:
  - `test_record_suma_sin_reservar`: `gate` vacío → `await gate.record(100)` → `usage()==100`.
  - `test_record_acumula_y_no_deduplica`: dos `record(50)` → `usage()==100` (no dedup).
  - `test_record_no_espera_aunque_supere_cap`: gate con `hard_cap=80` → `record(100)` retorna sin bloquear; `usage()==100`; `remaining()==-20` (señal de over-cap para `/ingest`).
  - `test_record_rechaza_n_no_positivo`: `record(0)` → `ValueError`.
- [ ] **Step 2: correr → fallan.**
- [ ] **Step 3: implementar** en `gate.py`:
  ```python
  async def record(self, n: int) -> int:
      """Registra `n` accesos YA ocurridos (captura in-page): inserta en el ledger
      bajo lock, SIN chequeo de presupuesto ni espera (el acceso ya pasó en el
      browser del usuario; no hay nada que reservar). Devuelve el uso resultante.
      El caller decide qué hacer si `usage > hard_cap` (rechazar ingestas)."""
      if n <= 0:
          raise ValueError("n debe ser > 0")
      async with self._lock:
          db = await self._db()
          now = int(self._clock())
          await db.execute("DELETE FROM access_ledger WHERE ts <= ?", (now - self._window_s,))
          await db.execute("INSERT INTO access_ledger(ts, count) VALUES(?, ?)", (now, n))
          await db.commit()
          return await self._usage(db, now)
  ```
  (No toca `reserve`/`reconcile`/`_reconciled`: los caminos activos siguen igual.)
- [ ] **Step 4: correr → pasan. Step 5: ruff/mypy + commit.**

## Task 2: Relocar los walkers de envelope a `providers/search_envelope.py`

**Objetivo:** que la ingesta reuse `extract_tweet_results`/`count_accessed` sin importar `twscrape_provider` (que arrastra twscrape). Refactor puro, sin cambio de comportamiento.

- [ ] **Step 1:** crear `providers/search_envelope.py` y MOVER ahí `extract_tweet_results` y `count_accessed` (puros, ya existen en `twscrape_provider.py`). Sin imports de twscrape.
- [ ] **Step 2:** en `twscrape_provider.py`, importar y **re-exportar** ambos (`from tweet_extractor.providers.search_envelope import extract_tweet_results, count_accessed`) para no romper imports/tests existentes (`test_twscrape_extract` los importa desde `twscrape_provider`).
- [ ] **Step 3:** correr `pytest tests/providers` → verde sin cambios (re-export intacto). ruff/mypy + commit.

## Task 3: Export filtrando por rango de fechas

**Objetivo:** `export_account` ya recibe `since`/`until` (para el nombre); ahora que también **filtre** `created_at ∈ [since, until)`. Lo necesita la captura (el store acumula tweets de varias navegaciones); el flujo de job no cambia (sus tweets ya están en rango).

- [ ] **Step 1: tests (fallan)** en `tests/storage/test_csv_exporter.py`:
  - `test_export_account_filtra_por_rango`: guardar tweets con `created_at` dentro y fuera de `[SINCE, UNTIL)` → el CSV solo trae los de adentro.
- [ ] **Step 2: correr → falla.**
- [ ] **Step 3: implementar**:
  - `store.iter_account(account, *, since: datetime | None = None, until: datetime | None = None)`: si vienen, agregar `AND created_at >= ? AND created_at < ?` con `since.isoformat()`/`until.isoformat()` (ISO UTC ordena lexicográfico).
  - `export_account`: pasar `since`/`until` a `iter_account` (filtro activo).
- [ ] **Step 4: correr** `pytest tests/storage tests/test_orchestrator.py` → verde (las fixtures de orchestrator/csv tienen fechas dentro de rango; si alguna quedara afuera, ajustarla). ruff/mypy + commit.

## Task 4: `POST /ingest` — pipeline de ingesta

**Objetivo:** recibir páginas GraphQL capturadas y correr extract → map → store → `gate.record`.

- [ ] **Step 1: schemas** en `service/schemas.py`:
  - `IngestPayload {account: str, op: str, pages: list[dict[str, Any]]}` (reusar la limpieza de handle de `JobCreate`: extraer `_clean_handle` a un helper compartido y validar `account`).
  - `IngestResult {account, captured: int, saved: int, accessed: int, gate_usage: int, gate_remaining: int, over_cap: bool}`.
- [ ] **Step 2: tests (fallan)** en `tests/service/test_ingest.py` (TestClient + DBs tmp; sin backend builder — la ingesta no usa provider):
  - `test_ingest_persiste_y_registra_en_gate`: `POST /ingest` con `search_response([tweet_entry("1"), tweet_entry("2")])` → `saved==2`, `gate.usage()` subió por `accessed`.
  - `test_ingest_rt_se_descarta_pero_cuenta`: incluir un RT → `saved` lo excluye pero `accessed` lo cuenta (regla #1 por el camino in-page).
  - `test_ingest_dedup_por_id`: re-postear la misma página → `saved==0` la 2da vez (pero `accessed` vuelve a contar — el ledger no deduplica).
  - `test_ingest_over_cap_429`: pre-cargar el gate cerca del cap → ingesta que lo cruza → primera pasa registrando, la siguiente devuelve 429 + `over_cap`.
  - `test_ingest_pagina_malformada_no_crashea`: `pages=[{}]` → 0 tweets, 200.
- [ ] **Step 3: correr → fallan.**
- [ ] **Step 4: implementar** `service/ingest.py` router:
  ```python
  @router.post("/ingest", response_model=IngestResult)
  async def ingest(body: IngestPayload, svc: SvcDep) -> IngestResult:
      if await svc.gate.remaining() <= 0:
          raise HTTPException(429, detail="tope global 24h alcanzado: ingesta pausada")
      mapped, accessed, captured = [], 0, 0
      for page in body.pages:
          accessed += count_accessed(page)
          for raw in extract_tweet_results(page):
              captured += 1
              try:
                  m = map_tweet(raw, account=body.account)
              except MapperError:
                  continue
              if m is not None:
                  mapped.append(m)
      saved = await svc.store.save(mapped)
      usage = await svc.gate.record(accessed) if accessed else await svc.gate.usage()
      remaining = svc.gate.hard_cap - usage
      return IngestResult(account=body.account, captured=captured, saved=saved,
                          accessed=accessed, gate_usage=usage,
                          gate_remaining=remaining, over_cap=remaining < 0)
  ```
  (Registrar en `app.py`: `app.include_router(ingest_router)`.)
- [ ] **Step 5: correr → pasan. Step 6: ruff/mypy + commit.**

## Task 5: `POST /export` + `GET /exports/{filename}`

**Objetivo:** exportar a CSV lo capturado de una cuenta en un rango, on-demand, y servirlo.

- [ ] **Step 1: config** `captures_dir: Path = Path("data/captures")` en `Settings` + `.env.example`.
- [ ] **Step 2: schemas** `ExportRequest {account, since: date, until: date}` (validación since<until + limpieza handle, reusando lo de `JobCreate`); `ExportResult {account, csv, download_url, exported}`.
- [ ] **Step 3: tests (fallan)** en `tests/service/test_ingest.py`:
  - `test_export_tras_ingest`: ingerir → `POST /export {account, since, until}` → `exported` correcto; `GET` del `download_url` → 200 text/csv con el contenido (política de replies aplicada, filtro de rango).
  - `test_export_filename_traversal_404`: `GET /exports/../foo` → 404 (validar filename).
- [ ] **Step 4: implementar** en `service/ingest.py`:
  - `POST /export`: `export_account(svc.store, account, svc.settings.captures_dir, since=since_utc, until=until_utc)` → devuelve `ExportResult` con `download_url=f"/exports/{path.name}"`.
  - `GET /exports/{filename}`: rechazar `/`,`\`,`..` en `filename`; servir `svc.settings.captures_dir / filename` con `FileResponse` (404 si no existe).
- [ ] **Step 5: correr → pasan. Step 6: ruff/mypy + commit.**

## Task 6: Extensión — content scripts + capture lib + popup

**Objetivo:** capturar `SearchTimeline` in-page y mandarlo al backend; UI mínima.

- [ ] **Step 1: `extension/lib/capture.ts`** (puro, testeable):
  ```ts
  export const CAPTURED_OPS = ['SearchTimeline', 'UserTweets', 'UserTweetsAndReplies'] as const;
  const OP_RE = /\/(SearchTimeline|UserTweets|UserTweetsAndReplies)(\?|$)/;
  export function matchOp(url: string): string | null { return url.match(OP_RE)?.[1] ?? null; }
  export interface CaptureMsg { source: 'xport-capture'; op: string; url: string; data: unknown; }
  ```
- [ ] **Step 2: `entrypoints/x-capture.main.ts`** — `defineContentScript({ matches:['*://x.com/*','*://twitter.com/*'], world:'MAIN', runAt:'document_start', main(){...} })`: parchear `window.fetch` (y XHR) → si `matchOp(url)`, `res.clone().json().then(d => window.postMessage({source:'xport-capture',op,url,data:d},'*'))`. No usar `browser.*` (MAIN no tiene).
- [ ] **Step 3: `entrypoints/x-capture.bridge.ts`** — content script ISOLATED (mismos `matches`): `window.addEventListener('message', e => { if (e.source===window && e.data?.source==='xport-capture') browser.runtime.sendMessage(e.data) })`.
- [ ] **Step 4: `background.ts`** — `browser.runtime.onMessage`: batchear por `account` (derivar el handle del `from:user` de la URL de búsqueda, o que el popup fije la cuenta activa) y `POST ${base}/ingest`; buffer + retry si el servicio está caído.
- [ ] **Step 5: popup** — toggle "capturar" (persistido en storage), contador de capturados por cuenta (de las `IngestResult`), aviso si `over_cap`, y botón "Exportar" (llama `POST /export` y abre el `download_url`).
- [ ] **Step 6:** `npm run compile` + `npm run build` (chrome+firefox) verdes. (Unit test de `matchOp` opcional si se agrega vitest; si no, queda cubierto por tsc + verificación manual.) Commit.

## Task 7: Docs + gate final

- [ ] Actualizar `docs/ESTADO.md` (patrón C implementado: ingesta + export; marcar la verificación viva como destrabada por esta vía) y `CLAUDE.md` (mencionar el camino de ingesta y el `record-after` del gate).
- [ ] Gate completo: `uv run ruff check . && uv run ruff format --check . && uv run mypy src && uv run pytest -q`; extensión `npm run compile && npm run build`. Commit.

---

## Qué se difiere (de esta fase)

- Captura de timeline de **perfil** (`UserTweets`/`UserTweetsAndReplies`, otro envelope — agregar variante de `extract_tweet_results`).
- ~~**Auto-scroll dirigido**~~ — IMPLEMENTADO (2026-06-15): `autoscroll.content.ts` + botón en el popup.
- **Dedup de capturas** por fingerprint (ODQ §5 del spec; default: contar todo).
- **Token de auth** del `/ingest` (hardening).
- Verificación cargando la extensión en un navegador real.
