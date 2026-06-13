# CLAUDE.md

Contexto del proyecto para Claude Code. Se lee automáticamente al abrir el repo. Mantenelo de alta señal: cada línea es una instrucción que condiciona cómo se escribe el código.

El plan completo y el razonamiento de cada decisión están en `docs/plan-extractor-tweets.md`. Este archivo es el resumen operativo + las reglas que **no se negocian**.

---

## Qué es este proyecto

App que extrae los tweets de **una o varias cuentas** de X/Twitter entre dos fechas y genera **un CSV por cuenta** con solo las columnas relevantes:

- `account` — handle de la cuenta
- `created_at` — fecha/hora del tweet (UTC en almacenamiento, ver convenciones)
- `content` — texto del tweet
- `links` — URLs externas / a documentos / otras páginas embebidas en el tweet (`expanded_url`)
- `quoted_tweet_id` / `quoted_tweet_url` — vínculo del tweet citante al citado

**Se incluyen los quote tweets. Se EXCLUYEN los retweets.**

Arquitectura por capas (estilo OSI) con la fuente de datos detrás de una interfaz abstracta, para poder cambiar el backend de scraping por la X API oficial sin tocar el resto. Después se envuelve como app local (FastAPI + Nginx) y se expone vía extensión de Firefox y Chrome.

---

## ⛔ Reglas innegociables (LEER ANTES DE ESCRIBIR CÓDIGO)

### 1. Compliance Gate — tope ToS de 1M/24h (la invariante crítica del proyecto)

Los ToS de X penalizan acceder a más de 1.000.000 de posts en cualquier ventana de 24 h (daños liquidados $15.000/1M). El repo **debe garantizar por código** que esto es imposible de cruzar. Reglas exactas:

- **`hard_cap = 900_000`, GLOBAL.** Un único ledger para toda la app (el límite es por el cliente, no por cuenta de X). No por-cuenta-de-scraping. 900k, no 1M, deja colchón para conteo difuso.
- **Ventana DESLIZANTE de 24 h**, nunca día calendario. `uso(now) = Σ count WHERE ts > now − 86400`. Un bucket fijo medianoche-a-medianoche NO cumple y es un bug de cumplimiento.
- **Se cuentan ACCESOS, no lo que se guarda.** El contador suma todo objeto-tweet que la respuesta entrega: el citante, el **quote embebido** (aunque no se persista como fila) y los **retweets que se descartan**. La deduplicación es solo del CSV: **el ledger NO deduplica** (cada acceso cuenta; sobre-contar es seguro).
- **Reserva-antes-de-pedir, falla cerrado.** Antes de cada request se reserva una **cota superior real** (≈2× el tamaño de página, por los quotes embebidos); si no entra en el presupuesto, se **espera** a que eventos viejos salgan de la ventana; después del fetch se reconcilia al conteo real.
- **Persistente y atómico.** El ledger vive en un **SQLite de auditoría SEPARADO** del SQLite de datos (así sobrevive a que se borre/regenere la base de tweets). El ciclo leer-decidir-insertar va bajo `asyncio.Lock`.
- **Todo provider pasa por el gate.** El `GatedProvider` envuelve a CUALQUIER `TweetProvider` (scraping u oficial). Ningún camino de fetch puede saltearlo. Si en el futuro la extensión captura GraphQL in-page, también debe reportar sus accesos al gate.
- **Tests obligatorios:** con el ledger pre-cargado, forzar (a) el bloqueo cuando `uso + reserva > cap` y (b) la espera hasta que el evento más viejo sale de la ventana.

**Nunca** debilitar, comentar, "temporalmente desactivar" ni subir el `hard_cap` sin instrucción explícita del usuario en la conversación.

### 2. Secretos y cookies

- Las cookies de sesión (`auth_token`, `ct0`) y cualquier credencial **nunca** se commitean. Van en `.env` (git-ignored) o keyring del SO. Proveer `.env.example` sin valores reales.
- `.gitignore` debe excluir como mínimo: `.env`, `data/`, `*.db`, `*.sqlite*`, `.venv/`, `node_modules/`, `dist/`.

### 3. Otras reglas duras

- Quotes sí, retweets no (filtrar por presencia de `retweeted_status_result`).
- Nada de lógica de parsing de GraphQL fuera de la capa de `mappers/`.
- No introducir scrapers basados en navegador (Selenium/Playwright/nodriver) en la imagen Docker por defecto: rompen la build Alpine (Chromium sobre musl es un dolor). Ver sección Docker.

---

## Arquitectura (capas estilo OSI)

De abajo hacia arriba. Cada capa solo conoce la de abajo.

1. **`providers/`** — fuente de datos intercambiable. Interfaz abstracta `TweetProvider` (async). Implementaciones: `TwscrapeProvider` (backend inicial, gratis, httpx), `OfficialApiProvider` (stub hoy). Factory `get_provider(config)` decide cuál. **Único punto de intercambio scraping ↔ API oficial.**
2. **`mappers/`** — normalización. Convierte el JSON crudo de cada backend al modelo de dominio. Acá vive TODA la lógica de GraphQL: detectar `quoted_status_result` vs `retweeted_status_result`, manejar `__typename` `Tweet` y `TweetWithVisibilityResults` (en este último el tweet real está bajo `.tweet`), extraer `expanded_url`.
3. **`domain/`** — modelo `Tweet` con pydantic, al que mapean AMBOS backends.
4. **`compliance/`** — `SlidingWindowGate` + `GatedProvider`. (Ver reglas innegociables.)
5. **`storage/`** — SQLite intermedio (dedupe por PK con `INSERT OR IGNORE`, checkpointing) y exportador CSV por cuenta en streaming.
6. **`service/`** — FastAPI: `POST /jobs`, `GET /jobs/{id}`, `GET /jobs/{id}/csv`, y estado del gate (uso/capacidad restante).
7. **`presentation/`** — CLI (Typer) para Fase 1; extensión de navegador para Fase 4.

### Estructura del repo

```
.
├── CLAUDE.md
├── docs/plan-extractor-tweets.md
├── pyproject.toml
├── uv.lock
├── .python-version
├── .node-version
├── .env.example
├── src/tweet_extractor/
│   ├── domain/models.py
│   ├── providers/{base.py,twscrape_provider.py,official_api.py,factory.py}
│   ├── mappers/{twscrape_mapper.py,api_v2_mapper.py}
│   ├── compliance/{gate.py,gated_provider.py}
│   ├── storage/{sqlite_store.py,csv_exporter.py}
│   ├── service/{app.py,routers.py}
│   └── cli.py
├── extension/           # Fase 4 (Chrome MV3 + Firefox, código compartido)
├── deploy/
│   ├── Dockerfile
│   ├── docker-compose.yml
│   └── nginx.conf
└── tests/
```

---

## Stack y herramientas

- **Python → `uv`** (gestor de paquetes/venv). No usar pip/poetry/venv a mano.
- **Node → `fnm`** (versión de Node para el toolchain de la extensión). En tu máquina; en Docker se pinea la imagen (ver abajo).
- **Servidor → Nginx** (reverse-proxy del FastAPI).
- **Build → Docker basado en Alpine** (requisito: el repo debe buildearse en contenedores Alpine).

---

## Comandos

### Python (uv)

```bash
uv init                      # solo la primera vez (repo greenfield)
uv add twscrape pydantic typer httpx aiosqlite
uv add fastapi[standard]
uv add --dev pytest ruff mypy
uv sync                      # crea .venv y resuelve uv.lock
uv sync --frozen             # en CI/Docker: build reproducible, falla si lock desactualizado
uv run python -m tweet_extractor.cli --account X --since 2023-01-01 --until 2026-01-01
uv run fastapi dev src/tweet_extractor/service/app.py
```

### Calidad

```bash
uv run ruff check . && uv run ruff format .
uv run mypy src
uv run pytest                # incluir SIEMPRE los tests del Compliance Gate
```

### Extensión (Fase 4)

```bash
fnm use                      # lee .node-version
npm ci
npm run build                # bundler (Vite/WXT) → dist/chrome y dist/firefox
```

`fnm` gestiona la versión de Node solo en la máquina de desarrollo; **en Docker NO se usa fnm**, se fija `node:<ver>-alpine` directamente, alineado con `.node-version`.

### Docker (Alpine)

```bash
docker build -f deploy/Dockerfile -t tweet-extractor .
docker compose -f deploy/docker-compose.yml up --build
```

---

## Convenciones de código

- **Async en todo el pipeline.** `asyncio`, `httpx`, `aiosqlite`. Nada de I/O bloqueante en el event loop.
- **Modelos con pydantic v2.** El dominio es la única fuente de verdad del shape de un tweet.
- **Parsing defensivo del GraphQL** (solo en `mappers/`): los `queryId` rotan con cada deploy de x.com; matchear por nombre de operación, nunca hardcodear el queryId. Manejar `Tweet` y `TweetWithVisibilityResults`. Buscar el quote citado en `quoted_status_result.result` y, defensivamente, también en `legacy.quoted_status_result`. Validar `expanded_url` vacío/autorreferencial.
- **Rango de fechas:** preferir búsqueda `from:user since: until:` **troceada en sub-ventanas** (esquiva el techo de ~3.200 del timeline). Minimizar el solape entre sub-ventanas (gasta presupuesto del gate).
- **Rate-limit aware:** respetar el header `x-rate-limit-reset`; backoff exponencial ante 429. No compartir un token entre workers concurrentes.
- **CSV en streaming**, fila por fila con el módulo `csv` de la stdlib. No cargar todo en memoria con pandas si el volumen es alto. Un archivo por cuenta.
- **Type hints estrictos**, `mypy` debe pasar. `ruff` para lint+format.

### Defaults PROVISIONALES del CSV (confirmar con el usuario antes de fijar)

Estas decisiones siguen abiertas en el plan (ODQ). Usar estos defaults para arrancar, pero **preguntar antes de tratarlas como definitivas**:

- Encoding: UTF-8 (evaluar BOM para Excel-AR).
- Delimitador: `,` (evaluar `;` por config regional de Excel en Argentina).
- Quoting: `csv.QUOTE_ALL` (texto con saltos de línea/comas).
- Zona horaria: **UTC en almacenamiento**; display configurable.
- Replies / threads, descarga de media, profundidad de recursión de quotes: ver ODQ del plan, **no hardcodear sin confirmar**.

---

## Docker / build en Alpine (requisito del repo)

El repo debe buildearse en contenedores Docker basados en Alpine. Claves:

- **musl, no glibc.** La mayoría de las deps Python tienen wheels `musllinux` (pydantic-core trae wheels musl, así que normalmente **no** hace falta toolchain de Rust; `httpx`, `fastapi`, `aiosqlite`, `typer` son puro Python o tienen wheels). Solo instalar `gcc musl-dev libffi-dev` vía `apk` **si** alguna dep no tiene wheel musllinux y compila desde fuente. Mantener la imagen mínima.
- **uv en Alpine:** usar la imagen oficial de astral (familia `ghcr.io/astral-sh/uv:python3.12-alpine`) en el stage de build; **pinear el tag** para reproducibilidad. Forzar que uv use el Python de la imagen (no uno descargado) para que el venv y el runtime usen el mismo intérprete musl.
- **Multi-stage:** stage build (resuelve deps con `uv sync --frozen`), stage runtime mínimo (`python:3.12-alpine`, mismo 3.12 musl) que solo copia el `.venv` y `src/`.
- **Sin navegador en la imagen por defecto.** Por eso el backend inicial es **twscrape (httpx, sin navegador)**: mantiene la imagen chica y evita Chromium-sobre-musl. Si alguna vez se agrega un provider basado en navegador, va en una imagen aparte (p.ej. `debian-slim`), nunca en la Alpine por defecto.
- **Nginx:** usar `nginx:alpine` en compose como reverse-proxy del FastAPI. No duplicar headers CORS entre Nginx y el `CORSMiddleware` de FastAPI: elegir uno solo.
- El **ledger de auditoría del Compliance Gate** debe persistir fuera del contenedor efímero: montarlo en un volumen, separado del SQLite de datos.

### Dockerfile de referencia (Python service)

```dockerfile
# ---- build ----
FROM ghcr.io/astral-sh/uv:python3.12-alpine AS build
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never
# Solo si alguna dep compila desde fuente en musl (normalmente innecesario):
# RUN apk add --no-cache gcc musl-dev libffi-dev
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project   # capa cacheable de deps
COPY . .
RUN uv sync --frozen --no-dev

# ---- runtime ----
FROM python:3.12-alpine AS runtime
WORKDIR /app
RUN adduser -D -H app
COPY --from=build /app/.venv /app/.venv
COPY --from=build /app/src   /app/src
ENV PATH="/app/.venv/bin:$PATH"
USER app
EXPOSE 8000
CMD ["uvicorn", "tweet_extractor.service.app:app", "--host", "0.0.0.0", "--port", "8000"]
```

### docker-compose de referencia (service + Nginx)

```yaml
services:
  app:
    build: { context: .., dockerfile: deploy/Dockerfile }
    expose: ["8000"]
    volumes:
      - audit-ledger:/data/audit      # ledger del Compliance Gate (persistente)
    env_file: ../.env
  nginx:
    image: nginx:alpine
    ports: ["8080:80"]
    volumes:
      - ./nginx.conf:/etc/nginx/conf.d/default.conf:ro
    depends_on: [app]
volumes:
  audit-ledger:
```

---

## Estado del repo / por dónde empezar

> **Estado actual y handoff** (qué está hecho, qué sigue, cómo arrancar en otra máquina): ver **`docs/ESTADO.md`**. Mantenerlo al día al cerrar cada fase. La memoria de claude-mem/context-mode es local; el handoff vive en git.

Orden de construcción (Fase 1 MVP, CLI + scraping gratis):

1. ✅ `uv init` + dependencias (ver comandos).
2. ✅ **Compliance Gate** (`compliance/`) + tests, envuelto en `GatedProvider`.
3. ✅ `domain/models.py` (el `Tweet` + `TweetLink`, pydantic, validado).
4. ✅ `TwscrapeProvider.fetch_tweets()` con búsqueda `from:user since/until` troceada, granularidad de página, reporte de `accessed_count`.
5. ✅ `mappers/twscrape_mapper.py` (quotes incluidos, retweets excluidos, links, política de replies por raíz de conversación).
6. ✅ `storage/csv_exporter.py` (streaming, un CSV por cuenta, defaults provisionales del CSV) + SQLite intermedio (dedupe por PK, checkpointing por sub-ventana).
7. ✅ `cli.py` (Typer) + `orchestrator.py` (loop de sub-ventanas; lo comparten CLI hoy y FastAPI en Fase 3).

**Fase 1 (MVP CLI) completa en código.** Antes de confiar el pipeline: verificaciones contra datos vivos con cookies reales (ver `docs/ESTADO.md`).

Fases 2-4 (modularización + stub API oficial, FastAPI + Nginx, extensiones Firefox/Chrome): ver `docs/plan-extractor-tweets.md`.

---

## Qué NO hacer (resumen)

- No saltear ni debilitar el Compliance Gate. No subir el `hard_cap`. No usar día calendario en vez de ventana deslizante. No deduplicar el ledger.
- No commitear cookies/credenciales ni `*.db`.
- No meter lógica de GraphQL fuera de `mappers/`. No hardcodear `queryId`.
- No meter Selenium/Playwright/Chromium en la imagen Alpine por defecto.
- No hardcodear encoding/delimitador/timezone/manejo de replies del CSV sin confirmar (siguen abiertos).
- No usar pip/poetry directo (es `uv`) ni fnm dentro de Docker (se pinea la imagen Node).

