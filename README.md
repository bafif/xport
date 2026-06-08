# xport — extractor de tweets de X/Twitter por rango de fechas

Extrae los tweets de **una o varias cuentas** de X/Twitter entre dos fechas y genera
**un CSV por cuenta** con solo las columnas relevantes:

| columna | qué es |
|---|---|
| `account` | handle de la cuenta |
| `created_at` | fecha/hora del tweet (UTC en almacenamiento) |
| `content` | texto del tweet |
| `links` | URLs externas / a documentos embebidas (`expanded_url`) |
| `quoted_tweet_id` / `quoted_tweet_url` | vínculo del citante al citado |

Se **incluyen** los quote tweets. Se **excluyen** los retweets.

El diferencial del proyecto es el **Compliance Gate**: una garantía *por código* de que
nunca se accede a más de 900 000 objetos-tweet en cualquier ventana móvil de 24 h (colchón
bajo el tope de ToS de X de 1 000 000/24 h). Ver [Compliance Gate](#compliance-gate).

> ⚠️ **Estado: en construcción (greenfield).** El núcleo crítico ya está implementado y
> testeado; el scraping real, el CSV y las interfaces de usuario todavía no. Ver
> [Roadmap](#roadmap).

---

## Requisitos

- **Python 3.12** (fijado en `.python-version`).
- **[uv](https://docs.astral.sh/uv/)** como gestor de paquetes/venv (no usar pip/poetry a mano).
- *(Solo para la futura extensión, Fase 4)* Node 22 vía [fnm](https://github.com/Schniz/fnm).

Instalar uv (una vez):

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

---

## Setup

```bash
git clone git@github.com:bafif/xport.git
cd xport
uv sync                       # crea .venv y resuelve uv.lock (build reproducible)
cp .env.example .env          # completar las cookies (ver abajo)
```

> Mové el proyecto entre máquinas **con git**, no con un `.zip`: el `.zip` empaqueta el
> `.venv` (binarios de un SO) y rompe en otro. Con git, `.venv/` está ignorado y cada
> máquina arma el suyo con `uv sync`.

### Configuración (`.env`)

Todo lo sensible va al `.env` (git-ignored); nunca se commitea.

| variable | descripción | default |
|---|---|---|
| `X_AUTH_TOKEN` | cookie de sesión de una cuenta de X descartable | *(vacío)* |
| `X_CT0` | cookie CSRF de la misma sesión | *(vacío)* |
| `AUDIT_DB_PATH` | SQLite del ledger de auditoría (separado de los datos) | `data/audit/ledger.db` |
| `DATA_DB_PATH` | SQLite intermedio de tweets | `data/tweets.db` |
| `PAGE_SIZE` | tamaño de página del backend | `20` |

> El tope (`hard_cap = 900000`) y la ventana (`window_s = 86400`) **no** son configurables
> por entorno a propósito: son constantes de código (regla de cumplimiento). Setearlas en
> `.env` no tiene efecto.

---

## Uso

### Calidad (lint, tipos, tests)

```bash
uv run ruff check . && uv run ruff format .   # lint + formato
uv run mypy src                               # tipado estricto
uv run pytest                                 # incluye los tests del Compliance Gate
```

### El Compliance Gate como librería

Hoy lo usable es el gate. Envuelve *cualquier* fuente de datos: se reserva una cota
superior **antes** de pedir, se hace el fetch, y se reconcilia al conteo real.

```python
import asyncio
from tweet_extractor.compliance.gate import SlidingWindowGate
from tweet_extractor.config import Settings


async def main() -> None:
    cfg = Settings()
    # `async with` hace el setup del ledger y cierra la conexión al salir.
    async with SlidingWindowGate(
        cfg.audit_db_path, hard_cap=cfg.hard_cap, window_s=cfg.window_s
    ) as gate:
        print("restante:", await gate.remaining())

        # Antes de cada request: reservar la cota superior de accesos de la página.
        rid = await gate.reserve(cfg.max_accessed_per_page)  # bloquea si no entra
        # ... acá iría el fetch real al backend ...
        accesos_reales = 17
        await gate.reconcile(rid, accesos_reales)            # ajusta al conteo real

        print("uso:", await gate.usage())


asyncio.run(main())
```

Cuando exista un `TweetProvider` concreto, se lo envuelve con `GatedProvider` y **ningún
acceso puede saltear el gate**:

```python
from tweet_extractor.compliance.gated_provider import GatedProvider

gated = GatedProvider(inner_provider, gate)        # inner = scraping u API oficial
async for raw_tweet in gated.fetch_tweets(query):  # paginado y gateado automáticamente
    ...
```

---

## Compliance Gate

La invariante crítica del repo. Reglas que se garantizan por código:

- **`hard_cap = 900_000`, global** (un único ledger para toda la app).
- **Ventana deslizante de 24 h**, nunca día calendario: `uso(now) = Σ count WHERE ts > now − 86400`.
- **Se cuentan accesos, no lo guardado**: el citante, el quote embebido y los retweets
  descartados. El ledger **no deduplica** (sobre-contar es seguro).
- **Reserva-antes-de-pedir, falla cerrado**: si una página no entra en el presupuesto, se
  **espera** a que eventos viejos salgan de la ventana; tras el fetch se reconcilia al real.
- **Persistente y atómico**: SQLite de auditoría **separado** del de datos, bajo `asyncio.Lock`.

---

## Arquitectura (capas, estilo OSI)

Cada capa solo conoce la de abajo. La fuente de datos está detrás de una interfaz abstracta
para poder cambiar el backend (scraping ↔ API oficial de X) sin tocar el resto.

```
providers/   contrato TweetProvider (intercambiable)     ✅ contrato listo
mappers/     normalización GraphQL → dominio             ⬜ pendiente
domain/      modelo Tweet (pydantic)                     ⬜ pendiente
compliance/  SlidingWindowGate + GatedProvider           ✅ implementado y testeado
storage/     SQLite intermedio + exportador CSV          ⬜ pendiente
service/     FastAPI (jobs + estado del gate)            ⬜ pendiente
presentation CLI (Typer) y extensión de navegador        ⬜ pendiente
```

---

## Roadmap

1. **Compliance Gate** + tests, envuelto en `GatedProvider`. ✅
2. `domain/models.py` (el `Tweet`) y `TwscrapeProvider` (scraping gratis vía httpx, sin navegador).
3. `mappers/` (quotes sí, retweets no, links) + `storage/` (SQLite + CSV en streaming) + `cli.py`.
4. FastAPI + Nginx (app local) y extensiones de Firefox/Chrome.

El plan completo y el razonamiento de cada decisión están en
[`docs/plan-extractor-tweets.md`](docs/plan-extractor-tweets.md).

---

## Licencia

[MIT](LICENSE) © 2026 Bautista D. Fiori.
