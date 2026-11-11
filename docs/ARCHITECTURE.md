# Cómo está hecho xport

[English](ARCHITECTURE.en.md) · [Volver al README](../README.md)

## Las piezas

![Cómo funciona: la extensión copia lo que x.com ya descargó y lo pasa al servicio local, que guarda sin repetir y
arma un CSV por cuenta. El tope de 24 horas cuenta cada post visto y, si no lo puede contar, la captura se
corta.](img/flujo.es.svg)

- **El servicio** (Python, FastAPI): guarda lo capturado, lleva el registro del tope, maneja la cola, arma los CSV y
  sirve la página. Escucha solo en `localhost:8000`.
- **La extensión** (TypeScript, [WXT](https://wxt.dev)): un solo código para Chrome (MV3) y Firefox (MV2). Toma
  capturas de la cola, recorre la búsqueda de X y le pasa al servicio las respuestas que x.com ya descargó. No las
  interpreta.
- **El Chrome de xport** (solo Linux): el servicio lo abre adentro de [cage](https://github.com/cage-kiosk/cage), una
  pantalla que nadie ve, con su propio perfil y la extensión cargada. Si no está, la captura la hace la extensión en el
  navegador de la persona.
- **El lanzador y el instalador** (`launcher.py`, `installer.py`): el comando `xport` y lo que lo deja en el sistema.
  El paquete trae su propio Python.

## Las capas

Cada capa conoce solo la de abajo:

```
providers/     de dónde salen los datos, detrás de TweetProvider (twscrape, API oficial)
mappers/       respuestas de X → modelo de dominio (el único lugar que las interpreta)
domain/        Tweet y TweetLink (pydantic v2)
compliance/    el registro del tope (SlidingWindowGate) y GatedProvider
storage/       SQLite de datos (deduplicado, tramos terminados), la cola y el export CSV en streaming
service/       FastAPI: capturas, export, estado del tope, el Chrome de xport, la página
cli.py         la línea de comandos (Typer)
launcher.py    el comando xport: arranca el servicio y abre la página
installer.py   el instalador del paquete
extension/     la extensión para Chrome y Firefox (WXT)
```

## Decisiones de diseño

**El tope de 24 horas**

- Es 900.000 y no un millón, para dejar margen ante cualquier duda sobre qué cuenta como acceso.
- La ventana es deslizante: un contador por día calendario dejaría pasar casi dos millones entre las 23 y la 1.
- Se cuentan accesos y no tweets guardados, sin deduplicar: contar de más es seguro y contar de menos no.
- Lo que se baja desde el servidor reserva antes de cada pedido una cota de lo que puede traer (tres veces el tamaño de
  página, por los citados) y espera si no entra. Lo que captura el navegador ya ocurrió, así que se registra después;
  por eso la extensión corta la captura cuando el servicio no puede registrarla.
- El registro está en un SQLite aparte, así sobrevive a que se borren los tweets. Cada chequeo y su escritura van en
  una transacción `BEGIN IMMEDIATE`, así dos procesos con el mismo registro no pasan juntos.
- Todo camino de acceso a X pasa por el mismo registro, y el tope y la ventana son constantes del código.

**La captura**

- Se captura en el navegador y no desde el servidor: x.com firma cada pedido con un valor que cambia seguido, y el
  navegador lo calcula solo. La forma de las respuestas es la misma, así que el resto del código sirve para los dos
  caminos.
- X no atiende a Chrome en modo `--headless`, así que el Chrome de xport corre con ventana, pero en una pantalla
  invisible. No se disfraza el navegador.
- La cola va de a una cuenta, porque X frena la búsqueda por cuenta de X y en paralelo no rinde. Vive en SQLite, así
  sobrevive a un reinicio.
- Solo `mappers/` interpreta las respuestas de X. Cuando x.com cambia la forma, se arregla en un solo lugar, y un cambio
  que el mapper no entiende da un error y no un CSV vacío.
- La API oficial usa la búsqueda de archivo completo y no el timeline de la cuenta, que solo llega a los ~3.200 tweets
  más recientes y dejaría afuera un rango viejo.

**La seguridad del servicio**

- Responde solo a pedidos dirigidos a `localhost` o `127.0.0.1`: escucha solo en la computadora, pero una página con
  DNS rebinding podría llegar a él con su propio dominio.
- Cualquier sitio abierto puede mandarle pedidos a `localhost` desde el navegador. Por eso los que cambian algo tienen
  que venir de la propia página, de la extensión o de un programa sin navegador, y los que traen el `Origin` de otro
  sitio reciben 403.
- El CORS lo maneja solo FastAPI; nginx no agrega encabezados, para no duplicarlos.

**El CSV**

- UTF-8 sin BOM, coma, todo entre comillas, fechas en UTC y `<cuenta>_<desde>_<hasta>.csv`. El encoding y el
  delimitador se pueden cambiar para Excel.
- Se escribe fila por fila, sin cargar todo en memoria. El mismo rango reescribe su archivo y rangos distintos
  conviven.

**El paquete**

- Trae el CPython portable de python-build-standalone, con las dependencias exactas de `uv.lock` instaladas adentro:
  quien lo usa no instala más que Chrome (y cage, en Linux). Se arma en cada sistema porque algunas dependencias vienen
  compiladas (pydantic-core, uvloop).
- El instalador lo corre ese mismo Python, así sus mensajes salen en los dos idiomas y se prueba con pytest.
- La imagen de contenedor es Alpine, en dos etapas y sin navegador.

## Desarrollar

```sh
uv sync                                   # el entorno, desde uv.lock
uv run fastapi dev src/tweet_extractor/service/app.py
bash deploy/extension-build.sh            # la extensión, con Podman (o: cd extension && npm ci && npm run build)
```

Más formas de levantar el servicio y la configuración, en la [referencia](REFERENCE.md). La extensión tiene su propio
[README](../extension/README.md).

## Pruebas

```sh
uv run pytest
uv run ruff check . && uv run ruff format --check .
uv run mypy src
```

Corren sin red, con respuestas de X fabricadas. Las del tope fuerzan las dos cosas que importan: que bloquee cuando no
hay lugar y que espere hasta que el acceso más viejo salga de la ventana. Las de la extensión (vitest) corren con
`npm test` en `extension/` y dentro de `bash deploy/extension-build.sh`; cubren, entre otras cosas, cuándo se corta la
captura.

La integración continua (`.github/workflows/ci.yml`) corre todo eso, arma la imagen del servicio y las dos versiones de
la extensión, y arma el paquete instalable en Linux, Windows y macOS, con una prueba de humo en cada uno. Al publicar un release,
`.github/workflows/release.yml` arma los instaladores y los zip de la extensión y los adjunta al release.

## Idiomas

Todo lo que ve la persona está en castellano y en inglés: el servicio y la CLI en `src/tweet_extractor/locales/`, la
extensión en `extension/locales/`. Los tests fallan si un texto falta en uno de los dos catálogos. Cada documento tiene
su versión en el otro idioma al lado (`X.md` y `X.en.md`). El CSV no se traduce.
