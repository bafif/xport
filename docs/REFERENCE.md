# Referencia

[English](REFERENCE.en.md) · [Volver al README](../README.md)

La línea de comandos, la API oficial de X, la API del servicio y la configuración. Para instalar y usar xport, ver
[Instalar](INSTALL.md) y [Cómo se usa](USAGE.md).

## Levantar el servicio

| Forma | Dirección | Para qué |
|---|---|---|
| `xport` (instalado) | `http://localhost:8000` | el uso de todos los días: arranca el servicio y abre la página |
| `uv run fastapi dev src/tweet_extractor/service/app.py` | `http://localhost:8000` | desarrollar, desde el repo |
| el host de native messaging | `http://localhost:8000` | que el navegador lo arranque solo ([detalle](../deploy/native-host/README.md)) |
| `podman compose -f deploy/compose.yaml up --build` | `http://localhost:8080` | integración continua y despliegue, en contenedores Alpine detrás de nginx |

Todas menos el compose usan la misma carpeta de datos y el mismo registro del tope, se arranquen desde donde se
arranquen. El compose tiene los suyos, en dos volúmenes de Podman separados, y necesita un `.env` (alcanza con
`cp .env.example .env`); para usarlo desde el popup, poné `http://localhost:8080` en el campo del servicio. Si ya hay un
xport respondiendo en `localhost:8000` o en `localhost:8080`, el servicio no arranca y lo dice: dos servicios llevarían
dos registros del tope.

## La línea de comandos

```sh
uv run tweet-extractor capture --account nasa,esa --since 2025-01-01 --until 2026-01-01
uv run tweet-extractor extract --account <cuenta> --since 2025-01-01 --until 2026-01-01
```

- **`capture`** encola capturas para la extensión, una por cuenta. `--account` se repite o lleva varias separadas por
  comas; `--excel` arma el CSV para un Excel en castellano. El avance y los CSV están en la página.
- **`extract`** baja los tweets desde el servidor, sin navegador: con la [API oficial](#con-la-api-oficial) o con las
  cookies de una cuenta de X, que hoy no anda (x.com cambió cómo firma los pedidos) y falla con un error que lo
  explica. `--since` entra y `--until` no (días UTC). Opciones: `--out-dir` (por defecto, `csv/` en la carpeta de
  datos), `--subwindow-days`, `--encoding` (`utf-8-sig` le agrega el BOM que espera Excel) y `--delimiter` (`;` para un
  Excel en castellano). Si se corta, correrlo de nuevo saltea los tramos que ya terminó.
- **`--lang es`** o **`--lang en`** cambia el idioma de los mensajes.

## Con la API oficial

Con una app de X con acceso pago a la API, poné en el `.env` `PROVIDER_BACKEND=official` y el Bearer token en
`X_API_BEARER_TOKEN`, y usá `tweet-extractor extract` o `POST /jobs`.

- Usa la búsqueda de archivo completo, que no tiene el techo de unos 3.200 tweets del timeline de una cuenta.
- No pide retweets ni expansions, para no pagar lo que después se descarta: el id del citado ya viene en la respuesta.
- Hace como mucho un pedido por segundo y, si X dice que se pasó del límite, espera lo que X indica.
- Si tu plan no incluye la búsqueda de archivo completo, X responde 403 y xport lo dice.

Cada post leído se paga (X publicaba 0,005 dólares por post y un máximo de 3 millones por mes). **No se probó contra
la API real:** si algo falla, el error trae lo que respondió X.

## La API del servicio

| Pedido | Qué hace |
|---|---|
| `GET /` | la página de xport |
| `GET /i18n/{idioma}` | los textos de la página en `es` o `en` |
| `GET /healthz` | dice si el servicio está vivo, que es xport y el número de su proceso |
| `GET /gate` | uso del tope en las últimas 24 horas, cuánto queda, el tope y la ventana |
| `GET /gate/days` | lo visto por día en los últimos 30 días con actividad (no cuenta para el tope) |
| `POST /captures`, `GET /captures` | encola una captura por cuenta; lista las últimas, con su avance |
| `POST /captures/next` | la captura que tiene que hacer la extensión; 204 si no hay ninguna o el tope está lleno |
| `POST /captures/{id}/state` | lo que avisa la extensión (en curso, en pausa, terminada, cortada) o una cancelación |
| `GET /captures/{id}/csv` | el CSV de una captura terminada |
| `GET /browser`, `POST /browser/login` | el estado del Chrome de xport y su sesión de X; abre la ventana para iniciarla |
| `POST /ingest` | guarda las páginas que capturó la extensión y registra sus accesos en el tope |
| `POST /progress` | el tweet más viejo guardado de una cuenta en un rango: desde dónde se retoma |
| `POST /export`, `GET /exports/{archivo}` | arma y baja el CSV de un rango capturado (`encoding` y `delimiter`, opcionales) |
| `POST /reset` | borra lo capturado de una cuenta (el registro del tope no se toca) |
| `POST /jobs`, `GET /jobs`, `GET /jobs/{id}`, `GET /jobs/{id}/csv/{cuenta}` | bajadas desde el servidor, sin navegador |

El servicio responde solo a pedidos dirigidos a `localhost` o `127.0.0.1`. Los que cambian algo (encolar, cancelar,
borrar) tienen que venir de la propia página, de la extensión o de un programa sin navegador, como la CLI: los que
traen el `Origin` de otro sitio reciben 403.

## Configuración

Va en un `.env`: el del repo cuando xport corre desde el código, o el de la carpeta de datos cuando está instalado.
[`.env.example`](../.env.example) tiene las variables comentadas. La captura desde el navegador no necesita ninguna.
Las rutas relativas cuelgan de la carpeta de datos.

| Variable | Para qué | Por defecto |
|---|---|---|
| `XPORT_DATA_DIR` | la carpeta de datos (ruta absoluta) | la de tu sistema, ver [Cómo se usa](USAGE.md#privacidad) |
| `XPORT_BROWSER` | que el servicio capture con su propio Chrome | `true` |
| `XPORT_BROWSER_BIN`, `XPORT_BROWSER_CAGE` | el navegador y cage que usa | los busca solo: en el `PATH` y, el navegador, en las carpetas de macOS y Windows |
| `XPORT_EXTENSION_DIR` | la extensión armada que carga en ese Chrome | la del paquete o `extension/dist/chrome-mv3` |
| `BROWSER_PROFILE_DIR` | el perfil de ese Chrome, con la sesión de X | `browser` |
| `XPORT_NOTIFY` | notificación del sistema al terminar o cortarse una captura | `true` |
| `XPORT_LANG` | idioma del servicio y de sus notificaciones: `es` o `en` | el del sistema |
| `AUDIT_DB_PATH` | el registro del tope, aparte de los datos | `audit/ledger.db` |
| `DATA_DB_PATH`, `QUEUE_DB_PATH` | los tweets; la cola de capturas | `tweets.db`, `queue.db` |
| `CSV_DIR`, `CAPTURES_DIR` | los CSV de las bajadas desde el servidor y los de las capturas | `csv`, `captures` |
| `PROVIDER_BACKEND` | de dónde baja el servidor: `twscrape` u `official` | `twscrape` |
| `X_API_BEARER_TOKEN` | Bearer token de la API oficial (paga) | vacío |
| `X_AUTH_TOKEN`, `X_CT0`, `ACCOUNTS_DB_PATH` | cookies y cuentas para twscrape | vacías, `accounts.db` |
| `PAGE_SIZE`, `SUBWINDOW_DAYS` | tamaño de página y días por tramo de búsqueda (servidor) | `20`, `7` |
| `ALLOWED_HOSTS` | hosts a los que responde el servicio (contra el DNS rebinding) | `["localhost","127.0.0.1"]` |
| `CORS_ALLOW_ORIGINS` | lista JSON de orígenes permitidos (la extensión no lo necesita) | `[]` |

El tope (900.000) y la ventana (24 horas) no están en esta lista a propósito: son constantes del código y ninguna
variable los cambia. **Nunca subas el `.env` a git:** puede tener cookies de sesión.

## La búsqueda de X

xport busca `from:<cuenta> since_time:<desde> until_time:<hasta>`, en segundos UTC: el día de inicio entra entero y el
de fin no (`since:` y `until:` tomarían días en la hora de quien busca). De las respuestas, el CSV se queda con las que
cuelgan de una conversación que empezó la misma cuenta. Si ese primer tweet cae fuera del rango y nunca se capturó, sus
respuestas dentro del rango también quedan afuera.
