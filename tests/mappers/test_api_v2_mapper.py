from __future__ import annotations

import pytest

from tweet_extractor.mappers import api_v2_mapper
from tweet_extractor.mappers.base import Mapper, MapperError


def test_map_tweet_es_stub_que_falla_fuerte():
    # Stub de Fase 2: no se implementa mapeo especulativo sin verificar contra
    # respuestas v2 reales. Invocarlo lanza MapperError (no descarta en silencio).
    with pytest.raises(MapperError):
        api_v2_mapper.map_tweet({"id": "1", "text": "hola"}, account="someuser")


def test_conforma_el_protocol_mapper():
    mapper: Mapper = api_v2_mapper.map_tweet  # falla en mypy si la firma no encaja
    assert callable(mapper)
