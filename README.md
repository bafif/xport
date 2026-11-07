<img src="docs/img/logo.svg" alt="" width="72">

# xport: tweets de cuentas de X a CSV

[English](README.en.md)

Baja los tweets de una o varias cuentas de X entre dos fechas y arma un CSV por cuenta. Se usa desde una extensión de
Chrome o Firefox que le pasa lo capturado a un servicio que corre en tu computadora.

**No puede pasar el tope de los términos de X.** Los términos multan acceder a más de un millón de posts en cualquier
período de 24 horas, así que xport cuenta cada post que el navegador ve, también los citados y los retweets que después
descarta, y frena en 900.000 dentro de cualquier ventana de 24 horas. Si el servicio no puede contar lo que se captura,
la captura no arranca o se corta.

![Cómo funciona: la extensión copia lo que x.com ya descargó y lo pasa al servicio local, que guarda sin repetir y
arma un CSV por cuenta. El tope de 24 horas cuenta cada post visto y, si no lo puede contar, la captura se
corta.](docs/img/flujo.es.svg)

## Estado

La captura desde el navegador anda contra x.com: abre la búsqueda de la cuenta, la scrollea sola, sigue cuando X la
corta y retoma donde quedó. El CSV tiene estas columnas:

| Columna | Qué tiene |
|---|---|
| `account` | el handle de la cuenta, en minúsculas |
| `created_at` | fecha y hora del tweet, ISO 8601 en UTC |
| `content` | el texto completo, también el de los tweets largos |
| `links` | las URLs externas del tweet (`expanded_url`), separadas por espacio |
| `quoted_tweet_id`, `quoted_tweet_url` | el tweet citado, si lo hay |

Entran los quote tweets y los hilos propios de la cuenta. Quedan afuera los retweets y las respuestas que la cuenta
escribió en conversaciones ajenas; el export dice cuántas respuestas dejó afuera. Si un tweet se editó y lo volvés a
capturar, queda la última versión.

Sin navegador hay dos caminos. Con una cuenta de X común está bloqueado desde junio de 2026: x.com cambió cómo firma los
pedidos y twscrape dejó de poder hacerlo. Con la [API oficial de X](#con-la-api-oficial), que es paga, está implementado
pero **no se probó contra la API real**: se armó con la documentación de X y respuestas fabricadas. Los cortes de la
captura que se sumaron el 6 de noviembre de 2026 todavía no se probaron en un navegador.

## Uso diario: el popup

Una vez instalado (ver [Instalar](#instalar)), todo se hace desde el popup de la extensión:

<img align="right" width="260" src="docs/img/popup.png" alt="El popup de xport con una cuenta de ejemplo, el rango de
fechas, el uso del tope y el enlace al CSV exportado.">

1. **Abrir el popup:** hacé clic en el ícono de xport. Arriba muestra cuántos accesos lleva el tope en las últimas
   24 horas y cuántos quedan.
2. **Completar:** la cuenta sin `@`, «Desde» y «Hasta» en formato `AAAA-MM-DD`. «Desde» entra; «Hasta» no.
3. **Capturar:** «Capturar ▶» abre la búsqueda de X en una pestaña nueva y la scrollea sola. Podés cerrar el popup.
   Si X deja de dar resultados antes de la fecha de inicio, la extensión achica el rango y busca de nuevo, hasta
   cubrirlo entero. «Detener ⏹» la corta. **Esa pestaña tiene que quedar a la vista:** si pasás a otra pestaña o
   minimizás la ventana, la captura se pone en pausa (el popup lo avisa) y sigue sola cuando volvés.
4. **Exportar:** volvé a abrir el popup y apretá «Exportar CSV». Aparece el enlace al archivo, con cuántos tweets tiene.
   Si lo vas a abrir en un Excel configurado en castellano, marcá antes «Para Excel en castellano»: el CSV sale separado
   con `;` y con el BOM que Excel necesita para leer los acentos. El popup se acuerda de la casilla.
5. **Seguir otro día:** si X frenó la búsqueda, volvé más tarde y apretá «Capturar ▶» con las mismas fechas. Sigue
   desde el tweet más viejo que ya tenías, sin volver a pedir lo bajado.

**Es privado.** La extensión usa la sesión de X que ya tenés abierta en el navegador: tus cookies no salen de él. Lo
capturado va al servicio local, que escucha solo en tu computadora, y queda en tu carpeta de datos de xport:
`~/.local/share/xport` en Linux, `%LOCALAPPDATA%\xport` en Windows y `~/Library/Application Support/xport` en macOS.
No se manda a ningún otro lado. «Borrar capturado 🗑» borra lo
de una cuenta; el registro del tope no se borra nunca. Conviene capturar con una cuenta de X que puedas perder: X puede
limitar las cuentas que bajan muchos tweets.

Por ahora captura solo desde la búsqueda de X, que entrega como mucho unos mil resultados por búsqueda. Si un solo día
tiene más de 800 tweets, la extensión se queda con lo que X le dio de ese día y sigue con los anteriores: el resto de
ese día no entra.

Si el popup dice que no arranca la captura porque el servicio no responde, levantalo (ver [Uso](#uso)) o instalá el
arranque automático (ver [Instalar](#instalar)). Si dice que el tope está lleno, esperá: el popup muestra cuánto queda.
Si dice que x.com cambió el formato de los datos, el servicio no entendió la mayoría de los tweets que llegaron y cortó la
captura para no dejarte un CSV vacío: hace falta una versión nueva de xport, y el log del servicio tiene un ejemplo.

## Instalar

Hace falta [uv](https://docs.astral.sh/uv/), que trae el Python correcto (3.12, fijado en `.python-version`), y
[Podman](https://podman.io) sin root para armar la extensión sin instalar Node. Con Node 22 también se puede armar sin
Podman.

1. **Bajar el código:** en GitHub, «Code» → «Download ZIP», o `git clone` de este repo.
2. **Preparar el servicio:** en la carpeta del repo, `uv sync`.
3. **Armar la extensión:** `bash deploy/extension-build.sh`. Queda en `extension/dist/`.
4. **Cargarla en el navegador:** ver abajo, según el tuyo.
5. **Arranque automático (opcional):** con el host de native messaging, el navegador levanta el servicio al abrirse y lo
   baja al cerrarse. Se instala una vez con `bash deploy/native-host/install-linux.sh` (Firefox) o
   `bash deploy/native-host/install-linux.sh --chrome` (Chrome, Chrome Beta o Chromium). Para un navegador de Windows con el
   servicio en WSL, y para diagnosticar problemas, ver [deploy/native-host/README.md](deploy/native-host/README.md).

### En Chrome

1. Abrí `chrome://extensions` y prendé «Modo de desarrollador», arriba a la derecha.
2. Hacé clic en «Cargar descomprimida» y elegí la carpeta `extension/dist/chrome-mv3`.

### En Firefox

1. Abrí `about:debugging` y elegí «Este Firefox».
2. Hacé clic en «Cargar complemento temporal…» y elegí el archivo `extension/dist/firefox-mv2/manifest.json`.

Firefox borra los complementos temporales al cerrarse: cada vez que lo abras, hay que cargarlo de nuevo.

## Uso

El servicio se levanta de una de estas formas. `fastapi dev`, el arranque automático y `tweet-extractor` usan la misma
carpeta de datos y el mismo registro del tope, se corran desde la carpeta que sea. La primera vez mudan ahí lo que haya
quedado en la carpeta `data/` del repo de una versión anterior (lo mueven, no lo copian, para no dejar dos registros).
El compose es para CI y para desplegar: tiene su propio registro, en un volumen de Podman. Si ya hay un xport
respondiendo en `localhost:8000` o en `localhost:8080`, el servicio no arranca y lo dice, porque dos servicios llevarían
dos registros.

```sh
uv sync
uv run fastapi dev src/tweet_extractor/service/app.py
cp .env.example .env && podman compose -f deploy/compose.yaml up --build
uv run tweet-extractor --account <cuenta> --since 2025-01-01 --until 2026-01-01
```

- `fastapi dev` levanta el servicio en `http://localhost:8000`, la dirección que la extensión usa si no le decís otra.
  Guarda todo en la carpeta de datos. Es lo mismo que levanta el arranque automático.
- `podman compose`, para CI y para desplegar, lo levanta en contenedores Alpine, detrás de nginx, en `http://localhost:8080`, que escucha solo en
  tu computadora. En el popup, poné esa dirección en el campo del servicio. Necesita el `.env`, aunque sea la copia
  vacía de `.env.example`. Los datos y el registro del tope van en dos volúmenes separados. Después de reiniciar la
  computadora se levanta solo únicamente si está prendido `podman-restart.service`
  (`systemctl --user enable podman-restart.service`).
- `tweet-extractor` baja tweets desde el servidor, sin navegador: con la API oficial (ver abajo) o con las cookies de
  una cuenta de X, que está bloqueado desde junio de 2026 y falla con un error que lo explica. `--since` entra y
  `--until` no (días UTC). Además: `--out-dir` (por defecto, `csv/` en la carpeta de datos), `--subwindow-days`, `--encoding` (`utf-8-sig` le agrega el BOM que Excel
  espera) y `--delimiter` (`;` para un Excel configurado en castellano). Si se corta, correrlo de nuevo saltea los
  tramos que ya terminó.

### Con la API oficial

Si tenés una app de X con acceso pago a la API, poné en el `.env` `PROVIDER_BACKEND=official` y el Bearer token en
`X_API_BEARER_TOKEN`, y usá `tweet-extractor` o `POST /jobs`. Usa la búsqueda de archivo completo, que no tiene el techo
de unos 3.200 tweets del timeline de una cuenta, y no pide retweets ni expansions, para no pagar lo que después se
descarta. Pide como mucho un pedido por segundo y, si X dice que se pasó del límite, espera lo que X indica.

Cada tweet leído se paga: en noviembre de 2026, X publicaba 0,005 dólares por post (5 dólares cada mil) y un máximo de
3 millones por mes. Si tu plan no incluye la búsqueda de archivo completo, X responde 403 y xport lo dice. **No se
probó contra la API real**: si algo falla, el error trae lo que respondió X.

El servicio responde a estos pedidos:

| Pedido | Qué hace |
|---|---|
| `GET /healthz` | dice si el servicio está vivo y que es xport |
| `GET /gate` | uso del tope en las últimas 24 horas, cuánto queda, el tope y la ventana |
| `POST /ingest` | guarda las páginas que capturó la extensión y registra sus accesos |
| `POST /progress` | el tweet más viejo guardado de una cuenta en un rango: desde dónde se retoma |
| `POST /export`, `GET /exports/{archivo}` | arma y baja el CSV de un rango capturado (`encoding` y `delimiter`, opcionales) |
| `POST /reset` | borra lo capturado de una cuenta (el registro del tope no se toca) |
| `POST /jobs`, `GET /jobs`, `GET /jobs/{id}`, `GET /jobs/{id}/csv/{cuenta}` | bajadas desde el servidor (bloqueadas) |

La configuración va en el `.env` del repo, que git ignora; [`.env.example`](.env.example) tiene todas las variables
comentadas. Se lee siempre el del repo, aunque la CLI se corra desde otra carpeta. La captura desde el navegador no
necesita ninguna: usa la sesión del navegador. Las rutas relativas cuelgan de la carpeta de datos.

| Variable | Para qué | Por defecto |
|---|---|---|
| `PROVIDER_BACKEND` | de dónde baja el servidor: `twscrape` o `official` | `twscrape` |
| `X_AUTH_TOKEN`, `X_CT0` | cookies de una cuenta de X aparte (twscrape) | vacías |
| `X_API_BEARER_TOKEN` | Bearer token de la API oficial (paga) | vacío |
| `XPORT_DATA_DIR` | carpeta de datos (ruta absoluta) | la de tu sistema, ver arriba |
| `AUDIT_DB_PATH` | registro del tope, aparte de los datos | `audit/ledger.db` |
| `DATA_DB_PATH`, `ACCOUNTS_DB_PATH` | tweets; cuentas de twscrape | `tweets.db`, `accounts.db` |
| `CSV_DIR`, `CAPTURES_DIR` | CSV de las bajadas del servidor y de las capturas | `csv`, `captures` |
| `CORS_ALLOW_ORIGINS` | lista JSON de orígenes permitidos (la extensión no lo necesita) | `[]` |
| `ALLOWED_HOSTS` | hosts a los que el servicio responde (protege contra DNS rebinding) | `["localhost","127.0.0.1"]` |
| `PAGE_SIZE`, `SUBWINDOW_DAYS` | tamaño de página y días por tramo de búsqueda (servidor) | `20`, `7` |

El tope (900.000) y la ventana (24 horas) no están en esta lista a propósito: son constantes del código y ninguna
variable los cambia.

La búsqueda de X es `from:<cuenta> since:<desde> until:<hasta>`, con días en UTC: `since` entra y `until` no. De las
respuestas, el CSV se queda con las que cuelgan de una conversación que empezó la misma cuenta. Si ese primer tweet cae
fuera del rango y nunca se capturó, sus respuestas dentro del rango también quedan afuera.

## Pruebas

```sh
uv run pytest
uv run ruff check . && uv run ruff format --check .
uv run mypy src
```

Corren sin red, con respuestas de X fabricadas. Las del tope fuerzan las dos cosas que importan: que bloquee cuando no
hay lugar y que espere hasta que el acceso más viejo salga de la ventana. Los de la extensión (vitest) corren dentro de
`bash deploy/extension-build.sh`, entre el chequeo de tipos y el armado; cubren, entre otras cosas, cuándo se corta el
crawl. **Nunca subas el `.env`:** tiene cookies de sesión. Lo capturado no vive en el repo sino en la carpeta de datos.

## Cómo se arma

La integración continua (`.github/workflows/ci.yml`) corre ruff, mypy y los tests, y arma con Podman la imagen del
servicio y las dos versiones de la extensión, con sus tests. A mano:

```sh
podman build -f deploy/Containerfile -t xport .
bash deploy/extension-build.sh
```

La imagen sale de `deploy/Containerfile`; la extensión queda en `extension/dist/chrome-mv3` y
`extension/dist/firefox-mv2`. Ningún contenedor monta el repo: la extensión se copia afuera con `podman cp`. Algunas
decisiones:

- La captura es en el navegador y no desde el servidor. Desde que x.com cambió cómo firma los pedidos, en junio de
  2026, bajar desde afuera exige reconstruir esa firma cada pocas semanas. El navegador la calcula solo, y la forma de
  las respuestas no cambió, así que el resto del código sirve igual.
- El tope es 900.000 y no un millón, para dejar margen ante cualquier duda sobre qué cuenta como acceso. La ventana es
  deslizante, porque un contador por día calendario dejaría pasar casi dos millones entre las 23 y la 1. Se cuentan
  accesos y no tweets guardados, sin deduplicar, porque contar de más es seguro y contar de menos no.
- Lo que se baja desde el servidor reserva antes de cada pedido una cota de lo que puede traer (tres veces el tamaño
  de página, por los citados) y espera si no entra. Lo que captura el navegador ya pasó, así que se registra después, y
  por eso la extensión corta el crawl cuando el servicio no puede registrarlo.
- El registro del tope está en un SQLite aparte, así sobrevive a que se borren los tweets. Cada chequeo y su escritura
  van en una transacción `BEGIN IMMEDIATE`, así dos procesos con el mismo registro no pasan juntos.
- La API oficial usa la búsqueda de archivo completo y no el timeline de la cuenta, que solo llega a los ~3.200 tweets
  más recientes y dejaría afuera un rango viejo. No pide expansions: el id del citado ya viene en la respuesta, y X las
  cobra desde el 22 de noviembre de 2026.
- El servicio responde solo a pedidos dirigidos a `localhost` o `127.0.0.1`. Escucha solo en tu computadora, pero una
  página web con DNS rebinding podría llegar a él con su propio dominio.
- Solo `mappers/` interpreta las respuestas de X; la extensión no las lee. Cuando x.com cambie la forma, se arregla en
  un solo lugar, y un cambio que el mapper no entiende da un error y no un CSV vacío.
- Los defaults del CSV (UTF-8 sin BOM, coma, todo entre comillas, UTC, `<cuenta>_<desde>_<hasta>.csv`) están cerrados
  desde junio de 2026. El mismo rango reescribe su archivo y rangos distintos conviven.
- La imagen es Alpine, en dos etapas, y sin navegador: Chromium sobre musl no vale la pena. Si alguna vez hace falta
  uno, va en otra imagen.
- El CORS lo maneja solo FastAPI; nginx no agrega encabezados, para no duplicarlos.
- La extensión está hecha con [WXT](https://wxt.dev): un solo código para Chrome (MV3) y Firefox (MV2).
- Las imágenes salen de `docs/img/generar.py` (`python3 docs/img/generar.py`): el logo en vector, los diagramas en
  castellano y en inglés y la tarjeta para redes, así se corrigen en el código y no en un programa de dibujo. Los
  íconos de la extensión salen del logo. `docs/img/popup.png` es el popup real con datos de ejemplo.
- La estética es la de X en negro («Lights out»), con un toque propio que sale del logo: la esquina-flecha azul
  marca lo que sale y la letra monoespaciada va en los datos.
- La página del proyecto (`docs/index.html` y `docs/en/`) la publica GitHub Pages desde la carpeta `docs/` de `main`,
  sin ningún paso de armado.

![El tope: una línea de tiempo de 48 horas donde solo cuentan los accesos de las últimas 24; su suma se compara con el
tope de 900.000 de xport, debajo del millón de los términos de X.](docs/img/tope.es.svg)

El código va en capas, y cada una conoce solo la de abajo:

```
providers/     de dónde salen los datos, detrás de TweetProvider (twscrape, API oficial)
mappers/       respuestas de X → modelo de dominio (el único lugar que las interpreta)
domain/        Tweet y TweetLink (pydantic v2)
compliance/    el registro del tope (SlidingWindowGate) y GatedProvider
storage/       SQLite de datos (deduplicado, tramos terminados) y export CSV en streaming
service/       FastAPI: capturas, export, estado del tope, bajadas desde el servidor
cli.py         la línea de comandos (Typer)
extension/     extensión para Chrome y Firefox (WXT)
```

Las decisiones con su historia y lo que falta están en [docs/PLAN.md](docs/PLAN.md).

## Licencia

[MIT](LICENSE).
