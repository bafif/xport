# Estado del proyecto — handoff

> **Documento vivo.** Resumen de DÓNDE estamos y CÓMO seguir. Es recuperable con `git pull` desde cualquier máquina — a diferencia de la memoria de claude-mem / context-mode y del historial de chat, que son **locales a cada PC y NO viajan por git**. Si retomás en otra máquina, este archivo + los specs/plans + los mensajes de commit son la fuente de verdad.

**Última actualización:** 2026-06-09.

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

**Calidad actual:** 100 tests verdes · `mypy --strict` limpio · `ruff` (lint+format) limpio. En `main` (mergeado desde `feat/twscrape-provider`).

---

## Próximo paso → `mappers/twscrape_mapper.py` (paso 5 del orden de CLAUDE.md)

Interpreta los dicts crudos de GraphQL que entrega `TwscrapeProvider` y los mapea al `Tweet` de dominio:

- **Quotes sí, retweets no**: descartar los que tengan `retweeted_status_result`; el quote citado sale de `quoted_status_result.result` (y, defensivamente, `legacy.quoted_status_result`).
- Normalizar `__typename` `Tweet` y `TweetWithVisibilityResults` (en este último el tweet real está bajo `.tweet` — el provider entrega el wrapper crudo, el mapper lo desenvuelve).
- Extraer `links` de `legacy.entities.urls[]` (`expanded_url`), validando vacíos/autorreferenciales.
- **Política de replies** (decidida acá, no en el provider): definición *por raíz de conversación* usando `conversationId` — self-threads sí, replies a terceros no; resuelve el caso del self-reply que cuelga de una conversación ajena.
- Las fixtures de `tests/providers/_fixtures.py` siembran esta fase (ya modelan Tweet/TVR/quote/RT anidados).

Después: `storage/` (SQLite intermedio + CSV streaming) + el **loop orquestador de sub-ventanas** (un `SearchQuery` por tramo de `subwindows()` → `gated.fetch_tweets`) → `cli.py`.

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
uv run pytest -q                              # 71 verdes confirma que el entorno quedó OK
```

Comandos de calidad: `uv run ruff check . && uv run ruff format .` · `uv run mypy src` · `uv run pytest`.

### Qué NO viaja por git (regenerar en cada máquina)

- **`.env`** (cookies `auth_token`/`ct0`): git-ignored. Copiar de `.env.example` y completar con una cuenta descartable.
- **`.venv/`**: regenerar con `uv sync`.
- **Memoria de claude-mem y knowledge base de context-mode**: locales a cada PC; no viajan. El contexto importante vive en este doc + specs/plans + commits.
- **`.planning/` y `.claude/`**: locales (GSD / settings de Claude Code).

### Nota de acceso git (importante)

El `origin` de la Mac actual usa un **alias SSH local**: `git@github-bafif:bafif/xport.git`, que resuelve a la cuenta personal `bafif` y convive con la cuenta de trabajo `bautista-obrok` (config en `~/.ssh/config`, clave `~/.ssh/id_ed25519_bafif`). **Ese alias es solo de esa máquina.** En otra PC, cloná con la URL estándar `git@github.com:bafif/xport.git` (o `https://github.com/bafif/xport.git`), asegurándote de que la máquina tenga acceso a la cuenta `bafif` (su propia clave SSH registrada en GitHub, o login HTTPS).
