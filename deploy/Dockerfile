# Build reproducible en Alpine/musl (requisito del repo). Sin navegador en la
# imagen por defecto: el backend inicial es twscrape (httpx), así la imagen queda
# chica y se evita Chromium-sobre-musl. Ver CLAUDE.md (sección Docker).

# ---- build ----
# Tag pineado (uv 0.11.19) para reproducibilidad; alineado con uv_build del pyproject.
FROM ghcr.io/astral-sh/uv:0.11.19-python3.12-alpine AS build
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never
# Solo si alguna dep compilara desde fuente en musl (normalmente innecesario:
# pydantic-core, httpx, fastapi, aiosqlite, typer traen wheels musllinux):
# RUN apk add --no-cache gcc musl-dev libffi-dev
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project   # capa cacheable de deps
COPY . .
RUN uv sync --frozen --no-dev

# ---- runtime ----
FROM python:3.12-alpine AS runtime
WORKDIR /app
# Usuario sin privilegios + dirs de datos con su ownership: los volúmenes nombrados
# heredan este ownership al crearse, así el proceso (no-root) puede escribirlos.
RUN adduser -D -H app \
    && mkdir -p /data/audit /data/app \
    && chown -R app /data
COPY --from=build /app/.venv /app/.venv
COPY --from=build /app/src   /app/src
ENV PATH="/app/.venv/bin:$PATH"
USER app
EXPOSE 8000
CMD ["uvicorn", "tweet_extractor.service.app:app", "--host", "0.0.0.0", "--port", "8000"]
