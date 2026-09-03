"""Поиск всех ответов под parent tweet через X API search."""

from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Any

from bot.config import Config
from bot.reply_bot.state import ReplyLead, SearchSlice
from bot.x_client import API_BASES

SEARCH_ENDPOINT = "/tweets/search/all"
COMMON_FILTERS = (
    "-is:retweet -from:MoveToRussiaCom -from:MoveToRussia "
    "-Ukraine -spy -espionage -NATO -Zelensky -frontline -sabotage -kill -war"
)


def _parse_twitter_time(raw: str) -> datetime:
    return datetime.strptime(raw, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc)


def _parse_twitter_time_z(raw: str) -> datetime:
    """Как _parse_twitter_time, но без миллисекунд (наш собственный формат)."""
    return datetime.strptime(raw, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def _slice_datetimes(
    parent_created_at: str,
    slice_info: SearchSlice,
    resume_before: str | None = None,
) -> tuple[datetime, datetime]:
    """start/end ISO-моменты для текущего слайса.

    Слайс несёт свои точные границы (from_dt/to_dt), нарезанные фиксированными
    окнами (по умолчанию 4 часа) от времени parent-твита — это не позволяет
    X API search/all "захлебнуться" пагинацией в периоды пиковой активности
    (курсор next_token у X ненадёжен на очень плотных окнах, см. историю
    со слайсом за 7 июля, где суточное окно теряло ~70% результатов).

    Последний слайс дополнительно ограничивается now-15s.
    resume_before — если задан, окно сужается до самого старого уже
    найденного твита в этом слайсе, чтобы не перечитывать уже собранные
    страницы после потери next_token (например, из-за сбоя API).
    """
    now = datetime.now(timezone.utc)
    max_end = now - timedelta(seconds=15)

    if slice_info.from_dt and slice_info.to_dt:
        start = _parse_twitter_time_z(slice_info.from_dt)
        end = _parse_twitter_time_z(slice_info.to_dt)
    else:
        # Обратная совместимость со старыми (календарными) слайсами.
        parent_dt = _parse_twitter_time(parent_created_at)
        start = datetime.strptime(slice_info.from_date, "%Y-%m-%d").replace(
            hour=0, minute=0, second=0, tzinfo=timezone.utc
        )
        if slice_info.index == 0:
            start = max(start, parent_dt)
        end = datetime.strptime(slice_info.to_date, "%Y-%m-%d").replace(
            hour=23, minute=59, second=59, tzinfo=timezone.utc
        )

    if slice_info.index + 1 >= slice_info.total:
        end = min(end, max_end)

    if resume_before:
        try:
            resume_dt = _parse_twitter_time(resume_before) - timedelta(seconds=1)
            end = min(end, resume_dt)
        except ValueError:
            pass

    if start >= end:
        start = end - timedelta(hours=1)
    return start, end


def _pick_endpoint(start_dt: datetime) -> str:
    """Всегда full-archive — recent не используем (лимит query 512)."""
    return SEARCH_ENDPOINT


_SEARCH_LOGGER = logging.getLogger("movetorussia_reply_search")

# Короткие таймауты + видимые логи на каждой попытке — раньше здесь стоял
# read-timeout 120s без единой строки лога на 429/сетевые проблемы, из-за
# чего зависание запроса выглядело как "бот молча стоит колом" без единой
# зацепки в логах.
_CONNECT_TIMEOUT = 10
_READ_TIMEOUT = 30
_MAX_ATTEMPTS_PER_BASE = 2


def _bearer_search(
    path: str,
    bearer_token: str,
    params: dict[str, Any],
) -> dict[str, Any]:
    import requests

    last_error: str | None = None
    for base in API_BASES:
        url = f"{base}{path}"
        for attempt in range(1, _MAX_ATTEMPTS_PER_BASE + 1):
            try:
                resp = requests.get(
                    url,
                    headers={"Authorization": f"Bearer {bearer_token}"},
                    params=params,
                    timeout=(_CONNECT_TIMEOUT, _READ_TIMEOUT),
                )
            except requests.RequestException as exc:
                last_error = str(exc)
                _SEARCH_LOGGER.warning(
                    "X search %s: сетевая ошибка (base=%s, попытка %d/%d): %s",
                    path, base, attempt, _MAX_ATTEMPTS_PER_BASE, exc,
                )
                continue
            if resp.status_code == 429:
                reset = int(resp.headers.get("x-rate-limit-reset", time.time() + 60))
                wait_s = max(reset - int(time.time()), 15)
                _SEARCH_LOGGER.warning(
                    "X search %s: 429 rate limit, жду %ds (base=%s)", path, wait_s, base
                )
                time.sleep(wait_s)
                continue
            if not resp.ok:
                last_error = f"HTTP {resp.status_code}: {resp.text[:400]}"
                _SEARCH_LOGGER.warning(
                    "X search %s: %s (base=%s, попытка %d/%d)",
                    path, last_error, base, attempt, _MAX_ATTEMPTS_PER_BASE,
                )
                continue
            return resp.json()
    raise RuntimeError(f"X search {path} failed: {last_error}")


# search/all: max 1024
X_SEARCH_QUERY_MAX_LEN = 1024


def _fit_keyword_query(prefix: str, keyword_query: str, suffix: str, max_len: int) -> str:
    """Укорачивает OR-фрагмент keywords, чтобы полный query уложился в лимит X API."""
    full = f"{prefix}{keyword_query}{suffix}"
    if len(full) <= max_len:
        return full

    budget = max_len - len(prefix) - len(suffix)
    if budget < 20:
        raise RuntimeError(
            f"X search query too long even without keywords "
            f"(prefix+suffix={len(prefix) + len(suffix)}, max={max_len})"
        )

    q = keyword_query.strip()
    if q.startswith("(") and q.endswith(")"):
        q = q[1:-1].strip()
    # Режем с конца по OR, пока не влезем.
    while True:
        candidate = f"({q})" if q else ""
        if len(prefix) + len(candidate) + len(suffix) <= max_len:
            return f"{prefix}{candidate}{suffix}"
        # Убрать последний OR-терм (с учётом кавычек).
        parts: list[str] = []
        buf = ""
        in_quotes = False
        i = 0
        while i < len(q):
            ch = q[i]
            if ch == '"':
                in_quotes = not in_quotes
                buf += ch
                i += 1
                continue
            if not in_quotes and q[i : i + 4].upper() == " OR ":
                parts.append(buf.strip())
                buf = ""
                i += 4
                continue
            buf += ch
            i += 1
        if buf.strip():
            parts.append(buf.strip())
        if len(parts) <= 1:
            # Последний шанс — жёсткая обрезка.
            trimmed = q[: max(0, budget - 2)].rstrip(" OR(")
            return f"{prefix}({trimmed}){suffix}"
        q = " OR ".join(parts[:-1])


def build_reply_search_query(
    conversation_id: str,
    keyword_query: str,
    parent_username: str | None = None,
    *,
    max_len: int = X_SEARCH_QUERY_MAX_LEN,
) -> str:
    """Build X search query for replies under the parent tweet.

    conversation_id narrows to the thread; to:parent_username further
    restricts to replies addressed to the parent account. Both together
    minimize junk from nested replies in the conversation.

    Полный query обрезается до max_len (для /search/all лимит X API = 1024).
    """
    to_clause = f"to:{parent_username.lstrip('@')} " if parent_username else ""
    prefix = f"conversation_id:{conversation_id} is:reply {to_clause}"
    suffix = f" {COMMON_FILTERS}"
    return _fit_keyword_query(prefix, keyword_query, suffix, max_len)


def _is_direct_reply_to_parent(tweet: dict[str, Any], parent_tweet_id: str) -> bool:
    """True if this tweet is a direct reply to the parent tweet."""
    if not parent_tweet_id:
        return True
    for ref in tweet.get("referenced_tweets") or []:
        if ref.get("type") == "replied_to" and str(ref.get("id")) == parent_tweet_id:
            return True
    return False


def _page_to_leads(
    data: dict[str, Any],
    seen: set[str],
    parent_tweet_id: str | None = None,
) -> list[ReplyLead]:
    tweets = data.get("data") or []
    users = {u["id"]: u for u in data.get("includes", {}).get("users", [])}
    leads: list[ReplyLead] = []
    skipped = 0
    for tweet in tweets:
        tweet_id = str(tweet.get("id", ""))
        if not tweet_id or tweet_id in seen:
            continue
        if not _is_direct_reply_to_parent(tweet, parent_tweet_id or ""):
            skipped += 1
            continue
        user = users.get(tweet.get("author_id"), {})
        username = user.get("username", "")
        location = (user.get("location") or "").strip()
        text = tweet.get("text") or ""
        created = tweet.get("created_at") or ""
        if not username or not text or not created:
            continue
        seen.add(tweet_id)
        leads.append(
            ReplyLead(
                tweet_id=tweet_id,
                author_username=username,
                tweet_text=text,
                created_at=created,
                x_url=f"https://x.com/i/status/{tweet_id}",
                author_location=location,
            )
        )
    if skipped:
        logging.getLogger("movetorussia_reply_search").info(
            "Skipped %d non-parent replies in page", skipped
        )
    return leads


def discover_all_replies(
    config: Config,
    *,
    parent_tweet_id: str,
    parent_username: str | None = None,
    conversation_id: str,
    keyword_query: str,
    parent_created_at: str,
    slice_info: SearchSlice,
    logger: logging.Logger,
    seen: set[str] | None = None,
    start_next_token: str | None = None,
    resume_before: str | None = None,
) -> tuple[list[ReplyLead], str | None]:
    """
    Одна страница ответов внутри одного временного слайса.
    Возвращает (новые лиды, next_token или None).
    """
    if not config.x_bearer_token:
        raise RuntimeError("X_BEARER_TOKEN нужен для поиска ответов")

    seen = seen or set()
    start_dt, end_dt = _slice_datetimes(parent_created_at, slice_info, resume_before)
    endpoint = _pick_endpoint(start_dt)
    query = build_reply_search_query(conversation_id, keyword_query, parent_username)

    params: dict[str, Any] = {
        "query": query,
        "max_results": min(config.x_search_page_size, 100),
        "start_time": start_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "end_time": end_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "tweet.fields": "author_id,created_at,text,referenced_tweets",
        "expansions": "author_id",
        "user.fields": "username,location",
        "sort_order": "recency",
    }
    if start_next_token:
        params["next_token"] = start_next_token

    logger.info(
        "Reply search %s: slice %d/%d (%s…%s) conv=%s | page=%s",
        endpoint.rsplit("/", 1)[-1],
        slice_info.index + 1,
        slice_info.total,
        slice_info.from_date,
        slice_info.to_date,
        conversation_id,
        "next" if start_next_token else "1",
    )

    data = _bearer_search(endpoint, config.x_bearer_token, params)

    leads = _page_to_leads(data, seen, parent_tweet_id=parent_tweet_id)
    next_token = (data.get("meta") or {}).get("next_token")
    raw_count = len(data.get("data") or [])

    logger.info(
        "Reply search: %d сырых, %d новых, next_token=%s",
        raw_count,
        len(leads),
        "yes" if next_token else "no",
    )
    return leads, next_token
