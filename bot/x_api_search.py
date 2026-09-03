"""Детерминированный поиск лидов через X API (recent / full-archive)."""

from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Any

import requests

from bot.config import Config
from bot.search_queries import build_query, pattern_count
from bot.state import Lead, SearchSlice, StateStore
from bot.x_client import API_BASES

RECENT_MAX_DAYS = 7


def _slice_datetimes(slice_info: SearchSlice) -> tuple[datetime, datetime]:
    start = datetime.strptime(slice_info.from_date, "%Y-%m-%d").replace(
        hour=0, minute=0, second=0, tzinfo=timezone.utc
    )
    end = datetime.strptime(slice_info.to_date, "%Y-%m-%d").replace(
        hour=23, minute=59, second=59, tzinfo=timezone.utc
    )
    # X API: end_time must be ≥10s before request time (слайс 6/6 = «сегодня» → 23:59 в будущем)
    max_end = datetime.now(timezone.utc) - timedelta(seconds=15)
    if end > max_end:
        end = max_end
    if start >= end:
        start = end - timedelta(hours=1)
    return start, end


def _pick_endpoint(slice_info: SearchSlice) -> str:
    """Recent — последние 7 дней; иначе full-archive."""
    start, _ = _slice_datetimes(slice_info)
    cutoff = datetime.now(timezone.utc) - timedelta(days=RECENT_MAX_DAYS)
    if start >= cutoff:
        return "/tweets/search/recent"
    return "/tweets/search/all"


def _bearer_search(
    path: str,
    bearer_token: str,
    params: dict[str, Any],
) -> dict[str, Any]:
    last_error: str | None = None
    for base in API_BASES:
        url = f"{base}{path}"
        try:
            resp = requests.get(
                url,
                headers={"Authorization": f"Bearer {bearer_token}"},
                params=params,
                timeout=(15, 90),
            )
        except requests.RequestException as exc:
            last_error = str(exc)
            continue
        if resp.status_code == 429:
            reset = int(resp.headers.get("x-rate-limit-reset", time.time() + 60))
            time.sleep(max(reset - int(time.time()), 15))
            resp = requests.get(
                url,
                headers={"Authorization": f"Bearer {bearer_token}"},
                params=params,
                timeout=(15, 90),
            )
        if not resp.ok:
            last_error = f"HTTP {resp.status_code}: {resp.text[:400]}"
            continue
        return resp.json()
    raise RuntimeError(f"X search {path} failed: {last_error}")


def _rows_to_leads(
    data: dict[str, Any],
    store: StateStore,
) -> tuple[list[Lead], str | None]:
    tweets = data.get("data") or []
    users = {u["id"]: u for u in data.get("includes", {}).get("users", [])}
    meta = data.get("meta") or {}
    next_token = meta.get("next_token")

    leads: list[Lead] = []
    for tweet in tweets:
        tweet_id = str(tweet.get("id", ""))
        if not tweet_id or store.is_known(tweet_id):
            continue
        username = users.get(tweet.get("author_id"), {}).get("username", "")
        text = tweet.get("text") or ""
        if not username or not text:
            continue
        ref = tweet.get("referenced_tweets") or []
        is_reply = any(r.get("type") == "replied_to" for r in ref)
        leads.append(
            Lead(
                tweet_id=tweet_id,
                author_username=username,
                tweet_text=text,
                lead_reason="x_api_candidate",
                is_reply=is_reply,
                x_url=f"https://x.com/i/status/{tweet_id}",
            )
        )
    leads.sort(key=lambda item: item.tweet_id)
    return leads, next_token


def fetch_candidates(
    config: Config,
    store: StateStore,
    *,
    logger: logging.Logger,
) -> tuple[list[Lead], bool]:
    """
    Одна страница X API. Возвращает (кандидаты, slice_exhausted).
    slice_exhausted=True когда все паттерны и страницы слайса пройдены.
    """
    if not config.x_bearer_token:
        raise RuntimeError("X_BEARER_TOKEN нужен для hybrid discovery")

    slice_info = store.get_search_slice(
        config.search_max_days,
        config.search_slice_days,
    )
    query_idx = store.get_query_index()
    pattern_id, query = build_query(query_idx)
    start_dt, end_dt = _slice_datetimes(slice_info)
    endpoint = _pick_endpoint(slice_info)

    params: dict[str, Any] = {
        "query": query,
        "max_results": min(config.x_search_page_size, 100),
        "start_time": start_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "end_time": end_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "tweet.fields": "author_id,created_at,text,referenced_tweets",
        "expansions": "author_id",
        "user.fields": "username",
        "sort_order": "recency",
    }
    next_token = store.get_search_next_token()
    if next_token:
        params["next_token"] = next_token

    logger.info(
        "X API %s [%s]: slice %d/%d (%s…%s) pattern %d/%d (%s)",
        endpoint.rsplit("/", 1)[-1],
        "recent" if "recent" in endpoint else "all",
        slice_info.index + 1,
        slice_info.total,
        slice_info.from_date,
        slice_info.to_date,
        query_idx + 1,
        pattern_count(),
        pattern_id,
    )

    try:
        data = _bearer_search(endpoint, config.x_bearer_token, params)
    except RuntimeError as exc:
        if "all" in endpoint and "403" in str(exc):
            logger.warning("search/all недоступен — fallback на recent для слайса")
            endpoint = "/tweets/search/recent"
            recent_start = datetime.now(timezone.utc) - timedelta(days=RECENT_MAX_DAYS - 1)
            if end_dt < recent_start:
                store.finish_slice(
                    config.search_max_days,
                    config.search_slice_days,
                    logger=logger,
                )
                return [], True
            params["start_time"] = max(start_dt, recent_start).strftime("%Y-%m-%dT%H:%M:%SZ")
            data = _bearer_search(endpoint, config.x_bearer_token, params)
        else:
            raise

    leads, page_next = _rows_to_leads(data, store)
    raw_count = len(data.get("data") or [])

    logger.info(
        "X API: %d сырых, %d новых кандидатов, next_token=%s",
        raw_count,
        len(leads),
        "yes" if page_next else "no",
    )

    page_num = int(store.get_meta("search_page_num") or "1")
    if page_next and page_num >= config.x_search_max_pages:
        logger.info(
            "Лимит %d стр./паттерн — переход к следующему паттерну",
            config.x_search_max_pages,
        )
        store.set_search_next_token(None)
        store.set_meta("search_page_num", "1")
        slice_done = store.advance_query_or_finish_slice(
            config.search_max_days,
            config.search_slice_days,
            pattern_count(),
            logger=logger,
        )
        return leads, slice_done

    if page_next:
        store.set_search_next_token(page_next)
        store.set_meta("search_page_num", str(page_num + 1))
        return leads, False

    store.set_search_next_token(None)
    store.set_meta("search_page_num", "1")
    slice_done = store.advance_query_or_finish_slice(
        config.search_max_days,
        config.search_slice_days,
        pattern_count(),
        logger=logger,
    )
    return leads, slice_done
