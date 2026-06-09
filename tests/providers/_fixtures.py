from __future__ import annotations

from typing import Any


def tweet_result(
    rest_id: str,
    *,
    typename: str = "Tweet",
    quoted: dict[str, Any] | None = None,
    retweeted: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Un `tweet_results.result` crudo de GraphQL, con quote/RT embebidos opcionales.
    `typename="TweetWithVisibilityResults"` lo envuelve con el `.tweet` interno."""
    legacy: dict[str, Any] = {
        "full_text": f"texto {rest_id}",
        "created_at": "Wed Jan 04 00:00:00 +0000 2023",
    }
    if retweeted is not None:
        legacy["retweeted_status_result"] = {"result": retweeted}
    result: dict[str, Any] = {"__typename": "Tweet", "rest_id": rest_id, "legacy": legacy}
    if quoted is not None:
        result["quoted_status_result"] = {"result": quoted}
    if typename == "TweetWithVisibilityResults":
        return {"__typename": "TweetWithVisibilityResults", "tweet": result}
    return result


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
