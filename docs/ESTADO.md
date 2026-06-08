# Estado del proyecto — handoff

> **Documento vivo.** Resumen de DÓNDE estamos y CÓMO seguir. Es recuperable con `git pull` desde cualquier máquina — a diferencia de la memoria de claude-mem / context-mode y del historial de chat, que son **locales a cada PC y NO viajan por git**. Si retomás en otra máquina, este archivo + los specs/plans + los mensajes de commit son la fuente de verdad.

**Última actualización:** 2026-06-08.

---

## Qué está hecho

Fase 1 (MVP CLI + scraping), en curso:

1. ✅ **Scaffolding** — `uv`, estructura `src/tweet_extractor/`, configs (`pyproject.toml`, `.gitignore`, `.env.example`, etc.).
2. ✅ **Compliance Gate** — `compliance/gate.py` (`SlidingWindowGate`) + `compliance/gated_provider.py` (`GatedProvider`) + contrato `providers/base.py` (`TweetProvider`, `SearchQuery`, `Page`) + `config.py`. Tests completos.
   - Specs/plan: `docs/superpowers/specs/2026-06-04-compliance-gate-core-design.md`, `docs/superpowers/plans/2026-06-04-compliance-gate-core.md`.
3. ✅ **Modelo de dominio** — `domain/models.py` (`Tweet`, `TweetLink`), pydantic v2, validación estricta de forma. Pasó dos rondas de code-review xhigh recall.
   - Specs/plan: `docs/superpowers/specs/2026-06-08-domain-model-design.md` (incluye §9 y §10 con todas las decisiones y endurecimientos), `docs/superpowers/plans/2026-06-08-domain-model.md`.

**Calidad actual:** 71 tests verdes · `mypy --strict` limpio · `ruff` (lint+format) limpio. Todo en `main`, pusheado a `bafif/xport`.

---

## Próximo paso → `TwscrapeProvider` (paso 4 del orden de CLAUDE.md)

`providers/twscrape_provider.py` — el primer provider concreto (scraping gratis vía `httpx`, sin navegador):

- Búsqueda `from:user since:… until:…` **troceada en sub-ventanas** (esquiva el techo de ~3.200 del timeline). Minimizar el solape (gasta presupuesto del gate).
- Granularidad de página; reportar `accessed_count` = TODO objeto-tweet tocado (citante + quotes embebidos + RT descartados), no `len(tweets)`.
- Se envuelve **siempre** con `GatedProvider` — ningún fetch saltea el gate.
- Requiere: `uv add twscrape httpx`; cookies `X_AUTH_TOKEN`/`X_CT0` en `.env` (cuenta de X descartable).
- Recomendado seguir el mismo flujo que las fases previas: brainstorming → spec → plan → TDD.

Después: `mappers/twscrape_mapper.py` (quotes sí, retweets no, links) → `storage/` (SQLite intermedio + CSV streaming) → `cli.py`.

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
