from __future__ import annotations

import pytest

from tweet_extractor.config import Settings
from tweet_extractor.mappers import api_v2_mapper, twscrape_mapper
from tweet_extractor.providers.base import ProviderError
from tweet_extractor.providers.factory import build_backend
from tweet_extractor.providers.official_api import OfficialApiProvider
from tweet_extractor.providers.twscrape_provider import TwscrapeProvider


async def test_backend_official_aparea_provider_y_mapper():
    settings = Settings(provider_backend="official")
    backend = await build_backend(settings)
    assert isinstance(backend.provider, OfficialApiProvider)
    assert backend.mapper is api_v2_mapper.map_tweet


async def test_backend_twscrape_aparea_provider_y_mapper(monkeypatch: pytest.MonkeyPatch):
    # `build_pool` hace I/O y exige cookies; lo stubeamos para aislar la factory.
    async def fake_build_pool(settings: Settings) -> object:
        return object()

    monkeypatch.setattr("tweet_extractor.providers.factory.build_pool", fake_build_pool)
    settings = Settings(provider_backend="twscrape")
    backend = await build_backend(settings)
    assert isinstance(backend.provider, TwscrapeProvider)
    assert backend.mapper is twscrape_mapper.map_tweet


async def test_backend_twscrape_propaga_falta_de_cookies():
    # Sin cookies, `build_pool` falla cerrado: la factory NO enmascara el error.
    settings = Settings(provider_backend="twscrape", x_auth_token=None, x_ct0=None)
    with pytest.raises(ProviderError):
        await build_backend(settings)


async def test_backend_desconocido_es_error_defensivo():
    settings = Settings()
    settings.provider_backend = "inexistente"  # type: ignore[assignment]
    with pytest.raises(ProviderError):
        await build_backend(settings)


def test_default_backend_es_twscrape():
    assert Settings().provider_backend == "twscrape"
