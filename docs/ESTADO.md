# Estado del proyecto — handoff

> **Documento vivo.** Resumen de DÓNDE estamos y CÓMO seguir. Es recuperable con `git pull` desde cualquier máquina — a diferencia de la memoria de claude-mem / context-mode y del historial de chat, que son **locales a cada PC y NO viajan por git**. Si retomás en otra máquina, este archivo + los specs/plans + los mensajes de commit son la fuente de verdad.

**Última actualización:** 2026-06-14 04:25 UTC.

---

## Qué está hecho

**Fase 1 (MVP CLI + scraping): COMPLETA en código. Fase 2 (modularización + backend oficial stub): COMPLETA en código. Fase 3 (FastAPI + Nginx): COMPLETA en código. Fase 4 (extensiones Chrome/Firefox): COMPLETA en código.**

1. ✅ **Scaffolding** — `uv`, estructura `src/tweet_extractor/`, configs (`pyproject.toml`, `.gitignore`, `.env.example`, etc.).
2. ✅ **Compliance Gate** — `compliance/gate.py` (`SlidingWindowGate`) + `compliance/gated_provider.py` (`GatedProvider`) + contrato `providers/base.py` (`TweetProvider`, `SearchQuery`, `Page`) + `config.py`. Tests completos.
   - Specs/plan: `docs/superpowers/specs/2026-06-04-compliance-gate-core-design.md`, `docs/superpowers/plans/2026-06-04-compliance-gate-core.md`.
3. ✅ **Modelo de dominio** — `domain/models.py` (`Tweet`, `TweetLink`), pydantic v2, validación estricta de forma. Pasó dos rondas de code-review xhigh recall.
   - Specs/plan: `docs/superpowers/specs/2026-06-08-domain-model-design.md` (incluye §9 y §10 con todas las decisiones y endurecimientos), `docs/superpowers/plans/2026-06-08-domain-model.md`.
4. ✅ **`TwscrapeProvider`** (paso 4) — primer provider concreto (scraping gratis vía `twscrape`, sin navegador). `fetch_page(query, cursor) -> Page` devuelve dicts crudos de GraphQL; lo envuelve el `GatedProvider` sin saltear el gate. Piezas: `providers/_twscrape_gql.py` (ÚNICA superficie de acoplamiento con twscrape: `OP_SearchTimeline` con queryId auto-actualizado, `QueueClient`, `encode_params`), `providers/twscrape_provider.py` (helpers puros de envelope `extract_tweet_results`/`extract_bottom_cursor`/`count_accessed`, `build_query` por fecha UTC, `build_pool` de una cuenta desde `.env`, DI del `page_fetcher` → tests offline), `providers/subwindows.py` (troceado puro). Ejecutado con subagentes (implementer + revisión spec/calidad por task) + review final → READY TO MERGE.
   - Spec/plan: `docs/superpowers/specs/2026-06-09-twscrape-provider-design.md`, `docs/superpowers/plans/2026-06-09-twscrape-provider.md`.
5. ✅ **`mappers/twscrape_mapper.py`** (paso 5) — interpreta los dicts crudos del provider y los mapea al `Tweet` de dominio. Decisiones de implementación:
   - **Quotes sí, retweets no** (`legacy.retweeted_status_result` ⇒ descarte). Desenvuelve `TweetWithVisibilityResults` (incluso anidado). Quote: `quoted_status_result.result` → fallback `legacy.quoted_status_result` → fallback `legacy.quoted_status_id_str` (quote tombstoneado); URL del permalink de X o forma canónica `https://x.com/i/web/status/<id>`.
   - **Dos canales de descarte**: `None` para descartes ESPERADOS (RT, tombstone/unavailable); `MapperError` fuerte para shape malformado — una rotación del GraphQL se nota como error, no como CSV vacío. El quote embebido malformado se degrada (no tumba al citante).
   - **Links**: `legacy.entities.urls[]` + `entity_set` del note tweet; descarta sin `expanded_url` y autorreferenciales (permalink del propio tweet o de su quote); links a TERCEROS tweets se conservan; dedupe (url, expanded_url) preservando orden. **Note tweets**: el texto completo reemplaza al `full_text` truncado.
   - **Política de replies implementada**: `map_tweet` devuelve `MappedTweet` (= `Tweet` + `is_reply` + `conversation_id`); `apply_reply_policy` (por-colección) conserva no-replies y replies cuya raíz (`conversation_id`) sea tweet propio — self-threads sí, replies a conversaciones ajenas no, incluido el self-reply que cuelga de conversación ajena. Aplicarla sobre la colección COMPLETA del job por cuenta (no por página). Limitación conocida: raíz fuera del rango de fechas ⇒ sus replies en-rango se descartan. Reply sin `conversation_id` ⇒ descarte (cerrado por defecto).
   - `account` lo estampa el caller (el handle consultado), no se extrae del payload. Fixtures extendidas en `tests/providers/_fixtures.py` (`url_entity`, params de legacy).
6. ✅ **`storage/`** (paso 6) — `sqlite_store.py` + `csv_exporter.py`, con los defaults del CSV (arrancaron provisionales; **ODQ resueltas el 2026-06-13**, ver arriba):
   - **`SqliteStore`**: SQLite de DATOS (separado del ledger del gate). Persiste `MappedTweet` (el `Tweet` + `is_reply`/`conversation_id`: la política de replies se aplica AL EXPORTAR, así un job se puede reanudar sin perderla). Dedupe por PK `id` con `INSERT OR IGNORE` (sub-ventanas solapadas); `created_at` como ISO 8601 UTC (orden lexicográfico = cronológico); `links` como JSON. **Checkpointing por sub-ventana** (`mark_window_done`/`is_window_done`, clave normalizada a UTC): un tramo completado no se re-fetchea en un re-run (cada re-fetch gasta presupuesto del gate); marcar SOLO al cerrar el tramo. Mismo patrón de conexión perezosa + context manager async que el gate.
   - **`csv_exporter`**: `write_csv` en streaming (chunks de 500 filas, escrituras vía `asyncio.to_thread`, nada de I/O bloqueante en el loop), `QUOTE_ALL`, **escritura atómica** (`.tmp` + `os.replace`; un fallo a mitad limpia el tmp y preserva el CSV previo). `export_account(store, account, out_dir, *, since, until)` → `<account>_<since>_<until>.csv` en orden cronológico aplicando `passes_reply_policy` en streaming (predicado extraído de `apply_reply_policy`, única fuente de la regla) con los `own_ids` persistidos. Cuenta vacía → CSV solo-header. Links múltiples en una celda separados por espacio (no ambiguo en URLs).
   - **Defaults del CSV (decididos 2026-06-13)**: UTF-8 sin BOM, delimitador `,`, `QUOTE_ALL`, `created_at` ISO UTC, nombre `<account>_<since>_<until>.csv`. Encoding/delimitador configurables por parámetro (Excel-AR); columnas: `account, created_at, content, links, quoted_tweet_id, quoted_tweet_url` (sin `id`, según CLAUDE.md).

7. ✅ **`orchestrator.py` + `cli.py`** (paso 7 — **cierra la Fase 1 en código**):
   - **`orchestrator.run_job`**: por cuenta, un `SearchQuery` por tramo de `subwindows()` (salteando los `is_window_done` SIN tocar al provider: cero presupuesto del gate) → `provider.fetch_tweets` → `map_tweet` (None = RT/tombstone) → `store.save` en tandas de 200 → `mark_window_done` al CERRAR el tramo → `export_account` al final de la cuenta (ahí se aplica la política de replies). Falla rápido: un error a mitad de tramo propaga sin checkpoint ni CSV de esa cuenta; lo persistido sobrevive y el re-run repite solo ese tramo (dedupe absorbe). `provider` DEBE ser el `GatedProvider`. Módulo separado de la CLI a propósito: el FastAPI de Fase 3 consume el mismo `run_job`.
   - **`cli.py`** (Typer, `tweet-extractor` como script + `python -m tweet_extractor.cli`): `--account/-a` repetible (tolera `@`), `--since/--until` YYYY-MM-DD UTC (validación inclusiva/exclusiva), `--out-dir`, `--subwindow-days`, `--encoding`/`--delimiter` (defaults decididos: utf-8, `,`). Wiring real en `cli._run`: `build_pool` + `SlidingWindowGate` + `SqliteStore` + `GatedProvider`. Resumen final por cuenta + uso/restante del gate. Errores de dominio (`ProviderError`/`ComplianceError`/`MapperError`) → exit 1 con mensaje; tests mockean `cli._run`.
   - Se agregó **typer** a las deps y `[project.scripts]` en `pyproject.toml`.

8. ✅ **Fase 2 — modularización + backend oficial stub**:
   - **`mappers/base.py`** (NUEVO): lo agnóstico de backend se movió acá — `MapperError`, `MappedTweet`, `apply_reply_policy`, `passes_reply_policy` y el **Protocol `Mapper`** (`(raw, *, account) -> MappedTweet | None`). `twscrape_mapper` los re-exporta vía `__all__` (back-compat: storage/cli/tests no cambiaron sus imports).
   - **`providers/official_api.py`** (NUEVO): `OfficialApiProvider`, **stub honesto de la X API v2**. Conforma `TweetProvider` (lo envuelve el `GatedProvider` igual que al de scraping — testeado), helpers de envelope v2 puros y testeados (`extract_tweets_v2` = `data[]`, `extract_next_token` = `meta.next_token`, `count_accessed_v2` = data + `includes.tweets` sobre-contando), `build_params` documenta el request a `/2/users/:id/tweets` (start_time/end_time/max_results/pagination_token, tweet.fields+expansions). El seam de red (`page_fetcher`, inyectable) por default **falla cerrado** (`ProviderError`: no hay credenciales/acceso de pago para verificar). `max_accessed_per_page` = 100 × factor (sobre-estima, fail-closed).
   - **`mappers/api_v2_mapper.py`** (NUEVO): `map_tweet` **stub que lanza `MapperError`** — NO se implementó mapeo especulativo sin poder verificar contra respuestas v2 reales. La docstring tiene el mapeo v2→dominio completo (referenced_tweets type quoted/retweeted/replied_to, entities.urls, conversation_id) para cuando se implemente.
   - **`providers/factory.py`** (NUEVO): `build_backend(settings) -> Backend(provider, mapper)` — **único punto de intercambio** scraping ↔ oficial, leyendo `settings.provider_backend` (Literal validado por pydantic). Aparea cada provider con SU mapper. Async (construir twscrape arma el pool).
   - **Desacople del orquestador**: `run_job` ahora recibe `mapper: Mapper` por parámetro (ya no importa `map_tweet` hardcodeado); `cli._run` usa `build_backend` y pasa `backend.mapper`. El gate se sigue aplicando aparte (GatedProvider en `cli._run`).
   - **Config**: `provider_backend: Literal["twscrape","official"] = "twscrape"` + `x_api_bearer_token`. `.env.example` actualizado.
   - Fase 2 ítems 3-4 del plan (storage dedupe/checkpoint; async backoff) ya estaban: storage se hizo en paso 6; el backoff/rate-limit lo maneja twscrape (`QueueClient`).

9. ✅ **Fase 3 — FastAPI + Nginx (`service/` + `deploy/`)**: API HTTP sobre el MISMO `orchestrator.run_job` que la CLI.
   - **`service/app.py`**: `create_app(settings, *, backend_builder)` (factory inyectable para tests: DBs temporales + backend fake sin twscrape/cookies). El **lifespan abre el gate y el store como SINGLETONS** de la app (un único ledger global, regla #1; una sola conexión de datos compartida por todos los jobs) y los cierra al apagar (después de cancelar los jobs en vuelo). `app = create_app()` a nivel módulo = entry-point de uvicorn (`tweet_extractor.service.app:app`). **CORS lo maneja FastAPI** (CORSMiddleware, solo si `cors_allow_origins` no está vacío), NUNCA Nginx (regla: no duplicar headers).
   - **`service/jobs.py`**: `JobStatus` (pending/running/done/error), `JobRecord` (estado mutable en memoria + rastro de log por sub-ventana), `JobRegistry` (índice + tracking de tasks para cancelarlas en el shutdown), `ServiceState` (lo compartido por Depends), `run_extraction` (corutina de background: `build_backend` → `GatedProvider` sobre el gate **compartido** → `run_job`; captura TODO error para reportarlo vía la API en vez de morir en silencio; `CancelledError` propaga). **Los DATOS del job son durables** (tweets/CSV/checkpoints en disco): re-enviar un job reanuda desde los checkpoints; el estado *runtime* del job (status/log) es en memoria.
   - **`service/routers.py`**: `POST /jobs` (encola y corre en background, 201 + id), `GET /jobs` (lista), `GET /jobs/{id}` (estado + log + resultados con URL de descarga), `GET /jobs/{id}/csv/{account}` (FileResponse; **por cuenta**, ya que un job son varios CSV — un archivo por cuenta, según el spec; 409 si el job no terminó, 404 si no existe/cuenta ajena), `GET /gate` (uso/restante/cap/ventana — la invariante crítica, observable), `GET /healthz`.
   - **`service/schemas.py`**: DTOs pydantic. `JobCreate` valida fechas `YYYY-MM-DD` (since<until), limpia handles (`@`, vacíos) y **rechaza separadores de path** en el handle (defensa en profundidad: el handle es nombre de archivo + segmento de URL).
   - **`deploy/`**: `Dockerfile` multi-stage Alpine (uv 0.11.19 pineado en build → runtime `python:3.12-alpine` no-root; `/data` chowneado para que los volúmenes nombrados hereden ownership escribible), `docker-compose.yml` (app + `nginx:alpine`; **ledger del gate en su propio volumen** `audit-ledger`, separado de `app-data`; paths absolutos del contenedor por `environment`, cookies por `env_file`), `nginx.conf` (reverse-proxy, sin CORS). **Build Alpine verificado** (`docker build` OK) y **contenedor verificado sirviendo** healthz/gate/openapi.
   - **Config**: `csv_dir` (raíz de CSV; el service usa un subdir por job para no pisar CSVs entre jobs) + `cors_allow_origins`. `.env.example` actualizado.
   - **14 tests del service** (`tests/service/`, TestClient sobre app con backend fake + DBs tmp): happy path POST→poll→descarga CSV, el gate singleton **cuenta los accesos** del job, **el RT se descarta pero igual cuenta en el gate** (regla #1 vía HTTP), 404/409/422, lista de jobs, job con backend que falla → `error`.

10. ✅ **Fase 4 — extensiones Chrome/Firefox (`extension/`)**: extensión cliente, **patrón (a)** del plan (la extensión NO scrapea; el popup pega contra el FastAPI local).
   - **WXT** (Chrome MV3 + Firefox, código compartido) → `npm run build` genera `dist/chrome-mv3` y `dist/firefox-mv2`. WXT resuelve las diferencias cross-browser: Chrome `manifest_version:3` + `background.service_worker` + `action`; Firefox `manifest_version:2` + `background.scripts` + `browser_action` (host perms en `permissions`, otorgados al instalar). El `browser` cross-browser sale de `wxt/browser` (capa polyfill).
   - **`lib/api.ts`**: `XportClient` tipado, **espejo de los schemas de `service/schemas.py`** (`JobCreate`/`JobResponse`/`AccountResultDTO`/`GateResponse`): `createJob`/`getJob`/`gate`/`csvUrl` + `XportApiError` (extrae el `detail` de FastAPI). Mantener en sync si cambian los schemas.
   - **`entrypoints/popup/`**: UI vanilla TS (form cuentas+fechas → `POST /jobs` → polling de `GET /jobs/{id}` cada 1 s → muestra estado/log/gate y links de descarga por cuenta). **`entrypoints/background.ts`**: service worker mínimo (listo para Native Messaging futuro). Recuerda la URL base en `browser.storage.local`.
   - **Sin CORS del servidor**: el popup es una *extension page*; con `host_permissions` (`http://localhost/*`, `http://127.0.0.1/*`) sus fetch cross-origin no quedan sujetos a CORS. `CORS_ALLOW_ORIGINS` del FastAPI solo haría falta para un content script / página web (no este patrón).
   - **Verificado**: `npm install` + `wxt prepare` + `npm run compile` (tsc estricto, limpio) + `npm run build` (ambos targets OK, manifests inspeccionados). **NO verificado**: cargar la extensión en un navegador real (no automatizable acá); faltan iconos (`public/icon/*.png`, hoy usa el default).
   - **Toolchain**: Node 22 (`.node-version`), `fnm` en dev. `package-lock.json` commiteado (para `npm ci`). `node_modules/`, `dist/`, `.wxt/` git-ignored.

**Calidad actual:** 207 tests Python verdes · `mypy --strict` limpio (28 archivos) · `ruff` (lint+format) limpio · build Docker Alpine OK · extensión: `tsc` + `wxt build` (chrome+firefox) OK. En `main`.

---

## Próximo paso → verificaciones de runtime (las 4 fases están en código)

**Las 4 fases del roadmap están completas en código.** Lo que queda es pasar de "compila y testea offline" a "verificado contra el mundo real", más decisiones abiertas. Nada de esto bloquea lo hecho:

1. **Pipeline de scraping vs x.com real** — ⚠️ **probado el 2026-06-14, BLOQUEADO por twscrape upstream** (ver "Verificación con datos vivos" abajo: x.com cambió el formato que twscrape parsea para el `x-client-transaction-id`; 0.18.1 es la última versión). No es un bug de xport. Decidir entre esperar fix / parche local / API oficial / patrón (c) de la extensión.
2. **Extensión en un navegador real**: cargar `extension/dist/chrome-mv3` (Chrome: `chrome://extensions` → descomprimida) y `extension/dist/firefox-mv2` (Firefox: `about:debugging`) con el servicio corriendo; confirmar el flujo popup → job → descarga y el estado del gate. Agregar iconos (`public/icon/*.png`).
3. **Backend oficial real** (`official_api.py` + `api_v2_mapper`): llenar los seams cuando haya API key v2 de pago con qué verificar.
4. **Persistencia del estado runtime de los jobs** (hoy `JobRegistry` en memoria; los DATOS sí son durables): evaluar si se quiere sobrevivir reinicios sin re-POST.

**ODQ del CSV: RESUELTAS (2026-06-13).** UTF-8 sin BOM, delimitador `,`, display UTC, nombre `<account>_<since>_<until>.csv`. Encoding/delimitador quedan configurables por parámetro para Excel-AR. Ver CLAUDE.md "Defaults del CSV".

Mejoras posibles de la extensión: patrón (b) Native Messaging ("abrir la app" sin server a mano) y patrón (c) captura GraphQL in-page (de menor fricción; **debe** reportar accesos al Compliance Gate). Ver `docs/plan-extractor-tweets.md`.

### ⚠️ Verificación con datos vivos (2026-06-14): BLOQUEADA por upstream (twscrape)

Se corrió la verificación con cookies reales (cuenta descartable). Resultado: el backend de scraping **no puede hablar con x.com hoy**, por un problema **de twscrape, no de xport**:

- twscrape **0.18.1 es la última versión publicada** (no hay upgrade). Su generador del header `x-client-transaction-id` (`twscrape/xclid.py`, `get_scripts_list`) parsea el HTML de x.com buscando el mapa de chunks `{id}:"{7 hex}"`. **x.com cambió ese formato después del código "as of 2026-05" de twscrape**: el regex matchea 0 entradas y `ondemand.s.*.js` ya no aparece → `Exception("Failed to parse scripts")` → sin transaction-id → x.com rechaza el GraphQL → `QueueClient.get` devuelve `None` → `fetch_search_page` lanza `ProviderError`.
- **No es baneo ni rate-limit ni cookies malas**: la página pública de x.com se obtiene OK (687 KB, perfil real, con `twitter-site-verification` y `loading-x-anim`); el account se agrega `active=True`. Solo falla el cálculo del transaction-id.
- **xport se comportó bien**: `build_pool` OK, el provider/gate **fallaron cerrado** con un error claro (no enmascararon como "sin resultados"). Los 4 puntos de abajo quedan sin poder verificarse hasta destrabar el transporte.

**Opciones evaluadas:** (a) esperar fix de twscrape upstream; (b) parche local del transaction-id; (c) migrar al `OfficialApiProvider` (API key v2 de pago) — `PROVIDER_BACKEND=official`; (d) **patrón (c)** de la extensión (captura GraphQL in-page): el navegador calcula el transaction-id → esquiva el problema de raíz. Script de verificación usado: `/tmp/verify_live.py` (reutilizable: `uv run python /tmp/verify_live.py <cuenta> <since> <until>`).

**Investigación A (parche) vs C (in-page) — 2026-06-14:**
- **(A) resultó GRANDE, no un regex.** x.com migró **todo el web client de webpack a Vite/ESM**: de `abs.twimg.com/responsive-web/client-web/*.js` (manifest `{id:"7hex"}` + chunk `ondemand.s.*.js`) a `abs.twimg.com/x-web/x-web/assets/*.js` (174 chunks con content-hash, `<script type="module">`). El `ondemand.s` que alimenta el algoritmo del transaction-id **ya no existe**. Las libs dedicadas no lo resolvieron: `xclienttransaction` última release 2026-03-18 (pre-migración), `twscrape` 0.18.1 del 2026-05-23 ("as of 2026-05"). Parchear = reverse-engineering del bundle Vite + ciclo de rotura de 2-4 semanas. Alquiler caro y recurrente.
- **(C) es inmune a esto.** Interceptar el GraphQL in-page sigue siendo el método recomendado en 2026; el navegador calcula el transaction-id nativo; la migración Vite no afecta la API (`/i/api/graphql/<id>/<Op>`) ni el JSON → **los mappers actuales se reusan**. Inmune también a la rotación de doc_id que rompe a (A)/(B).

**DECISIÓN (2026-06-14): se va con (C), captura GraphQL in-page.** Diseño en `docs/superpowers/specs/2026-06-14-inpage-capture-design.md`. (A) descartado por costo/fragilidad; (E) API oficial queda como plan B pago.

### Verificaciones contra datos vivos (los 4 puntos, pendientes hasta destrabar el transporte)

Los tests del provider son 100% offline. Antes de confiar el pipeline, con una cuenta descartable real (spec `2026-06-09-twscrape-provider-design.md` §11):

1. El filtro temporal `since:/until:` realmente acota (descartar no-op del operador en el endpoint GraphQL → drenaría el presupuesto).
2. El cursor `Bottom` pagina y termina (el guard de cursor-repetido de `base.py` corta).
3. El shape real matchea las fixtures (`tweet_results.result`, `quoted_status_result`, `retweeted_status_result`, `__typename`); ajustar la navegación defensiva si x.com cambió algo.
4. `accessed_count` real ≤ la cota de reserva (60) por página.

**Notas de robustez del review final (menores, validar en el punto 3):** (a) `extract_tweet_results`/`count_accessed` usan un `_walk` global en vez de anclar a `instructions[].entries[]` — elegido por robustez ante rotación del envelope; un `entryId:"tweet-*"` anidado dentro de un quote se tomaría como nivel-tope (imposible con datos reales; la dirección del cap sigue segura). (b) Ningún test fija estructuralmente la garantía offline. Reconsiderar (a) si el shape vivo sorprende.

---

## Decisiones clave (para no re-litigar)

- **`Tweet` minimalista**: columnas del CSV + `id` (PK de storage). Sin `is_retweet` (los RT se excluyen en el mapper) ni `media_urls` (ODQ 4 abierta). `is_quote` es propiedad computada.
- **`links` es `list[TweetLink]`** (no `tuple`): la dedupe es por `id` en SQLite, no `set[Tweet]` en memoria. Evaluado y confirmado dos veces.
- **Validación estricta de *forma* en el modelo** (el parsing de GraphQL va SOLO en `mappers/`): `created_at` debe ser `datetime` tz-aware (rechaza epoch/string), normalizado a UTC; `id`/`account`/URLs trimeados, no vacíos, sin caracteres de control; `quoted_*` coherentes (id+url juntos o ninguno); `extra="forbid"`. **`content` queda libre** (puede ser vacío; se preserva tal cual, con saltos de línea, para reconstruir el tweet — el CSV usará `QUOTE_ALL`).
- **ODQ del plan AÚN ABIERTAS** — confirmar con el usuario antes de hardcodear en el CSV exporter: encoding/BOM, delimitador (`,` vs `;` para Excel-AR), timezone de display, replies/threads, política de dedup, naming de archivos. Ver "Open Design Questions" en `docs/plan-extractor-tweets.md`.
- **Reglas innegociables**: ver CLAUDE.md (Compliance Gate: no debilitar, no subir `hard_cap`, ventana deslizante real, no deduplicar el ledger; secretos fuera del repo; GraphQL solo en `mappers/`).

---

## Cómo arrancar en otra máquina (solo git)

```bash
git clone git@github.com:bafif/xport.git    # URL estándar — ver "Nota de acceso git" abajo
cd xport
uv sync                                       # crea .venv desde uv.lock (build reproducible)
cp .env.example .env                          # completar cookies X (NUNCA se commitean)
uv run pytest -q                              # 207 verdes confirma que el entorno quedó OK
```

Comandos de calidad: `uv run ruff check . && uv run ruff format .` · `uv run mypy src` · `uv run pytest`.

### Qué NO viaja por git (regenerar en cada máquina)

- **`.env`** (cookies `auth_token`/`ct0`): git-ignored. Copiar de `.env.example` y completar con una cuenta descartable.
- **`.venv/`**: regenerar con `uv sync`.
- **`extension/node_modules/`, `extension/dist/`, `extension/.wxt/`**: regenerar con `cd extension && npm ci && npm run build` (el `package-lock.json` SÍ viaja).
- **Memoria de claude-mem y knowledge base de context-mode**: locales a cada PC; no viajan. El contexto importante vive en este doc + specs/plans + commits.
- **`.planning/` y `.claude/`**: locales (GSD / settings de Claude Code).

### Nota de acceso git (importante)

El `origin` de la Mac actual usa un **alias SSH local**: `git@github-bafif:bafif/xport.git`, que resuelve a la cuenta personal `bafif` y convive con la cuenta de trabajo `bautista-obrok` (config en `~/.ssh/config`, clave `~/.ssh/id_ed25519_bafif`). **Ese alias es solo de esa máquina.** En otra PC, cloná con la URL estándar `git@github.com:bafif/xport.git` (o `https://github.com/bafif/xport.git`), asegurándote de que la máquina tenga acceso a la cuenta `bafif` (su propia clave SSH registrada en GitHub, o login HTTPS).
