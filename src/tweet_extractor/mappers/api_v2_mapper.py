from __future__ import annotations

from tweet_extractor.mappers.base import MappedTweet, MapperError
from tweet_extractor.providers.base import RawTweet

# Stub de Fase 2: el mapper de la X API v2 oficial. El `OfficialApiProvider` es
# también un stub (no hay credenciales ni acceso de pago para verificar contra
# respuestas reales), así que este mapper NO se implementa con lógica especulativa
# sin testear: lanza fuerte si se lo invoca. Cuando se implemente el backend
# oficial, este es el mapeo a hacer (forma v2, PLANA, distinta del GraphQL anidado
# de twscrape):
#
#   - Tweet object (de `data[]` o de `includes.tweets[]`):
#       id            -> Tweet.id
#       text          -> Tweet.content   (v2 no trunca como el full_text legacy)
#       created_at    -> Tweet.created_at (ISO 8601 / RFC 3339, ya tz-aware)
#       author_id     -> resolver el handle vía `includes.users[]` o usar `account`
#   - referenced_tweets[]: { type: "retweeted" | "quoted" | "replied_to", id }
#       type == "retweeted" -> DESCARTE (None), regla dura del proyecto
#       type == "quoted"    -> quoted_tweet_id = id; url canónica /i/web/status/<id>
#       type == "replied_to"-> is_reply = True
#   - conversation_id -> MappedTweet.conversation_id (pedir tweet.fields=conversation_id)
#   - entities.urls[]: { url (t.co), expanded_url, display_url } -> TweetLink
#       (misma validación de vacíos/autorreferenciales que el mapper de twscrape)
#
# Campos a pedir explícitamente (la v2 por defecto solo trae id+text):
#   tweet.fields=created_at,entities,referenced_tweets,conversation_id,author_id
#   expansions=referenced_tweets.id,author_id


def map_tweet(raw: RawTweet, *, account: str) -> MappedTweet | None:
    """Conforma el Protocol `Mapper` (ver `mappers/base`). Stub: lanza
    `MapperError` porque el backend oficial todavía no está implementado y mapear
    sin poder verificar contra respuestas v2 reales sería código especulativo."""
    raise MapperError(
        "api_v2_mapper es un stub de Fase 2: el backend oficial no está implementado. "
        "Usá el backend twscrape (PROVIDER_BACKEND=twscrape)."
    )
