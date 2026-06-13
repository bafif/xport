from __future__ import annotations

from typing import Any


def tweet_result(
    rest_id: str,
    *,
    typename: str = "Tweet",
    quoted: dict[str, Any] | None = None,
    retweeted: dict[str, Any] | None = None,
    created_at: str = "Wed Jan 04 00:00:00 +0000 2023",
    full_text: str | None = None,
    urls: list[dict[str, Any]] | None = None,
    conversation_id: str | None = None,
    in_reply_to: str | None = None,
    quoted_permalink: str | None = None,
) -> dict[str, Any]:
    """Un `tweet_results.result` crudo de GraphQL, con quote/RT embebidos opcionales.
    `typename="TweetWithVisibilityResults"` lo envuelve con el `.tweet` interno.
    `conversation_id` defaultea al propio `rest_id` (el caso raíz-de-conversación);
    shapes exóticos (note_tweet, quote en `legacy`) se arman mutando el dict."""
    legacy: dict[str, Any] = {
        "full_text": full_text if full_text is not None else f"texto {rest_id}",
        "created_at": created_at,
        "conversation_id_str": conversation_id if conversation_id is not None else rest_id,
    }
    if in_reply_to is not None:
        legacy["in_reply_to_status_id_str"] = in_reply_to
    if urls is not None:
        legacy["entities"] = {"urls": urls}
    if quoted_permalink is not None:
        legacy["quoted_status_permalink"] = {"expanded": quoted_permalink}
    if retweeted is not None:
        legacy["retweeted_status_result"] = {"result": retweeted}
    result: dict[str, Any] = {"__typename": "Tweet", "rest_id": rest_id, "legacy": legacy}
    if quoted is not None:
        result["quoted_status_result"] = {"result": quoted}
    if typename == "TweetWithVisibilityResults":
        return {"__typename": "TweetWithVisibilityResults", "tweet": result}
    return result


def url_entity(
    expanded_url: str,
    *,
    url: str = "https://t.co/x",
    display_url: str | None = None,
) -> dict[str, Any]:
    """Una entry de `legacy.entities.urls[]`. Para tests de dedupe pasar `url`
    explícito (el default repetido colisionaría en la clave (url, expanded))."""
    entity: dict[str, Any] = {"url": url, "expanded_url": expanded_url}
    if display_url is not None:
        entity["display_url"] = display_url
    return entity


def tweet_entry(rest_id: str, **kwargs: Any) -> dict[str, Any]:
    """Una entry de timeline de tipo tweet (`entryId` = `tweet-<rest_id>`)."""
    return {
        "entryId": f"tweet-{rest_id}",
        "content": {
            "entryType": "TimelineTimelineItem",
            "itemContent": {
                "itemType": "TimelineTweet",
                "tweet_results": {"result": tweet_result(rest_id, **kwargs)},
            },
        },
    }


def cursor_entry(value: str, kind: str = "Bottom") -> dict[str, Any]:
    """Una entry de cursor (`Bottom` para la próxima página, `Top` para la anterior)."""
    return {
        "entryId": f"cursor-{kind.lower()}-{value}",
        "content": {
            "entryType": "TimelineTimelineCursor",
            "cursorType": kind,
            "value": value,
        },
    }


def search_response(entries: list[dict[str, Any]]) -> dict[str, Any]:
    """Una respuesta SearchTimeline con las entries dadas en un TimelineAddEntries."""
    return {
        "data": {
            "search_by_raw_query": {
                "search_timeline": {
                    "timeline": {
                        "instructions": [{"type": "TimelineAddEntries", "entries": entries}]
                    }
                }
            }
        }
    }
