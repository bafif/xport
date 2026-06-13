from __future__ import annotations

from dataclasses import dataclass

from tweet_extractor.config import Settings
from tweet_extractor.mappers import api_v2_mapper, twscrape_mapper
from tweet_extractor.mappers.base import Mapper
from tweet_extractor.providers.base import ProviderError, TweetProvider
from tweet_extractor.providers.official_api import OfficialApiProvider
from tweet_extractor.providers.twscrape_provider import TwscrapeProvider, build_pool


@dataclass(frozen=True)
class Backend:
    """Un provider concreto APAREADO con su mapper. La factory garantiza que van
    juntos (cada backend habla un formato crudo distinto y necesita SU mapper);
    el orquestador consume ambos sin saber cuál backend es. El gate se aplica
    aparte, envolviendo `provider` en `GatedProvider` (sigue siendo el único
    camino de fetch, regla #1)."""

    provider: TweetProvider
    mapper: Mapper


async def build_backend(settings: Settings) -> Backend:
    """El ÚNICO punto donde se decide scraping ↔ API oficial (regla de CLAUDE.md).
    Async porque construir el backend de twscrape arma el `AccountsPool` (I/O).
    `provider_backend` ya está validado por pydantic a un literal conocido; el
    `else` es defensivo ante una extensión futura del Literal sin actualizar acá."""
    if settings.provider_backend == "twscrape":
        pool = await build_pool(settings)
        return Backend(
            provider=TwscrapeProvider(settings, pool),
            mapper=twscrape_mapper.map_tweet,
        )
    if settings.provider_backend == "official":
        return Backend(
            provider=OfficialApiProvider(settings),
            mapper=api_v2_mapper.map_tweet,
        )
    raise ProviderError(f"provider_backend desconocido: {settings.provider_backend!r}")
