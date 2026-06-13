from __future__ import annotations

from datetime import UTC, datetime

from tweet_extractor.domain.models import Tweet, TweetLink
from tweet_extractor.mappers.twscrape_mapper import MappedTweet


def mapped_tweet(
    tweet_id: str = "1",
    *,
    account: str = "someuser",
    created_at: datetime | None = None,
    content: str | None = None,
    links: list[TweetLink] | None = None,
    quoted_id: str | None = None,
    is_reply: bool = False,
    conversation_id: str | None = None,
) -> MappedTweet:
    """Un `MappedTweet` de dominio listo para storage (sin pasar por el mapper).
    `conversation_id` defaultea al propio id (caso raíz); el quote arma id+url
    juntos (coherencia que exige el modelo)."""
    return MappedTweet(
        tweet=Tweet(
            id=tweet_id,
            account=account,
            created_at=created_at or datetime(2023, 1, 4, tzinfo=UTC),
            content=content if content is not None else f"texto {tweet_id}",
            links=links or [],
            quoted_tweet_id=quoted_id,
            quoted_tweet_url=f"https://x.com/i/web/status/{quoted_id}" if quoted_id else None,
        ),
        is_reply=is_reply,
        conversation_id=conversation_id if conversation_id is not None else tweet_id,
    )
