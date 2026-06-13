# Estado del proyecto — handoff

> **Documento vivo.** Resumen de DÓNDE estamos y CÓMO seguir. Es recuperable con `git pull` desde cualquier máquina — a diferencia de la memoria de claude-mem / context-mode y del historial de chat, que son **locales a cada PC y NO viajan por git**. Si retomás en otra máquina, este archivo + los specs/plans + los mensajes de commit son la fuente de verdad.

**Última actualización:** 2026-06-13.

---

## Qué está hecho

Fase 1 (MVP CLI + scraping), en curso:

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
6. ✅ **`storage/`** (paso 6) — `sqlite_store.py` + `csv_exporter.py`, con los **defaults provisionales** del CSV (instrucción del usuario; las ODQ siguen abiertas, ver abajo):
   - **`SqliteStore`**: SQLite de DATOS (separado del ledger del gate). Persiste `MappedTweet` (el `Tweet` + `is_reply`/`conversation_id`: la política de replies se aplica AL EXPORTAR, así un job se puede reanudar sin perderla). Dedupe por PK `id` con `INSERT OR IGNORE` (sub-ventanas solapadas); `created_at` como ISO 8601 UTC (orden lexicográfico = cronológico); `links` como JSON. **Checkpointing por sub-ventana** (`mark_window_done`/`is_window_done`, clave normalizada a UTC): un tramo completado no se re-fetchea en un re-run (cada re-fetch gasta presupuesto del gate); marcar SOLO al cerrar el tramo. Mismo patrón de conexión perezosa + context manager async que el gate.
   - **`csv_exporter`**: `write_csv` en streaming (chunks de 500 filas, escrituras vía `asyncio.to_thread`, nada de I/O bloqueante en el loop), `QUOTE_ALL`, **escritura atómica** (`.tmp` + `os.replace`; un fallo a mitad limpia el tmp y preserva el CSV previo). `export_account(store, account, out_dir)` → `<account>.csv` en orden cronológico aplicando `passes_reply_policy` en streaming (predicado extraído de `apply_reply_policy`, única fuente de la regla) con los `own_ids` persistidos. Cuenta vacía → CSV solo-header. Links múltiples en una celda separados por espacio (no ambiguo en URLs).
   - **Defaults provisionales aplicados**: UTF-8 sin BOM, delimitador `,`, `QUOTE_ALL`, `created_at` ISO UTC. Todos configurables por parámetro (`encoding`/`delimiter`); columnas: `account, created_at, content, links, quoted_tweet_id, quoted_tweet_url` (sin `id`, según CLAUDE.md).

7. ✅ **`orchestrator.py` + `cli.py`** (paso 7 — **cierra la Fase 1 en código**):
   - **`orchestrator.run_job`**: por cuenta, un `SearchQuery` por tramo de `subwindows()` (salteando los `is_window_done` SIN tocar al provider: cero presupuesto del gate) → `provider.fetch_tweets` → `map_tweet` (None = RT/tombstone) → `store.save` en tandas de 200 → `mark_window_done` al CERRAR el tramo → `export_account` al final de la cuenta (ahí se aplica la política de replies). Falla rápido: un error a mitad de tramo propaga sin checkpoint ni CSV de esa cuenta; lo persistido sobrevive y el re-run repite solo ese tramo (dedupe absorbe). `provider` DEBE ser el `GatedProvider`. Módulo separado de la CLI a propósito: el FastAPI de Fase 3 consume el mismo `run_job`.
   - **`cli.py`** (Typer, `tweet-extractor` como script + `python -m tweet_extractor.cli`): `--account/-a` repetible (tolera `@`), `--since/--until` YYYY-MM-DD UTC (validación inclusiva/exclusiva), `--out-dir`, `--subwindow-days`, `--encoding`/`--delimiter` (defaults provisionales). Wiring real en `cli._run`: `build_pool` + `SlidingWindowGate` + `SqliteStore` + `GatedProvider`. Resumen final por cuenta + uso/restante del gate. Errores de dominio (`ProviderError`/`ComplianceError`/`MapperError`) → exit 1 con mensaje; tests mockean `cli._run`.
   - Se agregó **typer** a las deps y `[project.scripts]` en `pyproject.toml`.

**Calidad actual:** 171 tests verdes · `mypy --strict` limpio · `ruff` (lint+format) limpio. En `main`.

---

## Próximo paso → verificaciones contra datos vivos, después Fase 2

La Fase 1 está completa en código pero **NO verificada contra x.com real**. Con cookies de una cuenta descartable en `.env` (sección de abajo, spec §11): correr `uv run tweet-extractor -a <cuenta> --since ... --until ...` acotado y validar los 4 puntos de la sección "Verificaciones contra datos vivos". Después: Fase 2 (factory `get_provider` + stub `OfficialApiProvider` + mapper API v2) y Fase 3 (FastAPI + Nginx), ver `docs/plan-extractor-tweets.md`.

### Verificaciones contra datos vivos (pendientes hasta tener cookies — NO bloquean lo hecho)

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
uv run pytest -q                              # 158 verdes confirma que el entorno quedó OK
```

Comandos de calidad: `uv run ruff check . && uv run ruff format .` · `uv run mypy src` · `uv run pytest`.

### Qué NO viaja por git (regenerar en cada máquina)

- **`.env`** (cookies `auth_token`/`ct0`): git-ignored. Copiar de `.env.example` y completar con una cuenta descartable.
- **`.venv/`**: regenerar con `uv sync`.
- **Memoria de claude-mem y knowledge base de context-mode**: locales a cada PC; no viajan. El contexto importante vive en este doc + specs/plans + commits.
- **`.planning/` y `.claude/`**: locales (GSD / settings de Claude Code).

### Nota de acceso git (importante)

El `origin` de la Mac actual usa un **alias SSH local**: `git@github-bafif:bafif/xport.git`, que resuelve a la cuenta personal `bafif` y convive con la cuenta de trabajo `bautista-obrok` (config en `~/.ssh/config`, clave `~/.ssh/id_ed25519_bafif`). **Ese alias es solo de esa máquina.** En otra PC, cloná con la URL estándar `git@github.com:bafif/xport.git` (o `https://github.com/bafif/xport.git`), asegurándote de que la máquina tenga acceso a la cuenta `bafif` (su propia clave SSH registrada en GitHub, o login HTTPS).
