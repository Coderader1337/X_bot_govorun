"""Lead discovery via Grok x_search — time-slice archive sweep (старые → новые)."""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any

import requests

from bot.config import Config
from bot.state import Lead, SearchSlice, StateStore

XAI_RESPONSES_URL = "https://api.x.ai/v1/responses"

SEARCH_LENSES = (
    "relocation intent: move/relocate/live in Russia, emigrate from West, repatriation",
    "visa & practical: Russian visa, eVisa, residence permit, citizenship, how to move",
    "dissatisfaction + openness: frustrated with US/EU/UK/CA, Russia as alternative",
    "replies & thread comments: personal agreement to move, visit long-term, live in Russia",
)

LEAD_SIGNALS = (
    "wants to move/relocate/live/work/study in Russia or Russian cities",
    "asking about Russian visa, residence permit, citizenship, eVisa, relocation process",
    "frustrated with US/EU/West and considering Russia as alternative",
    "already moved or planning move; comparing life in Russia vs home country",
    "replies or quote-tweets agreeing to relocate, visit long-term, or praising living in Russia",
    "thread comments with personal relocation intent (not news/media commentary)",
)


def _build_prompt(
    *,
    limit: int,
    slice_info: SearchSlice,
    lens: str,
    excluded_ids: list[str],
) -> str:
    excluded_block = ", ".join(excluded_ids) if excluded_ids else "none"
    signals = "; ".join(LEAD_SIGNALS)

    return f"""x_search: find up to {limit} REAL X posts or replies in this DATE SLICE ONLY.

DATE SLICE (strict): {slice_info.from_date} through {slice_info.to_date} (inclusive).
This is slice {slice_info.index + 1}/{slice_info.total} of a bottom-up ocean sweep (older slices first).

Query lens for this pass: {lens}

Goal: qualified individual leads for relocation assistance to Russia (MoveToRussia audience).
Lead signals (any one is enough): {signals}

Content types — ALL count if intent is personal and genuine:
- original posts, replies, quote-tweets, thread comments, nested replies

Audience geography (individuals from unfriendly countries, NOT Hungary):
EU (except HU), UK, US, CA, AU, JP, KR, NZ, SG, TW, UA, Bahamas, Micronesia, Ukraine.

Include broadly: move to Russia, Russian visa, emigrate, leave the West, Moscow/St Petersburg life,
"would move to Russia", "thinking about Russia", repatriation, diaspora return.

Exclude ONLY: obvious spam/bots, pure sarcasm with no real intent, propaganda accounts,
@MoveToRussiaCom outreach, already-seen tweet IDs: {excluded_block}.

Low engagement and small accounts are welcome. Do NOT require many likes.
Search order within this slice: OLDEST first (earliest date / lowest tweet_id).

Return ONLY JSON array: tweet_id, author_username (no @), tweet_text, is_reply, lead_reason (short)."""


def _extract_output_text(response: dict[str, Any]) -> str:
    parts: list[str] = []
    for block in response.get("output") or []:
        if block.get("type") != "message":
            continue
        for part in block.get("content") or []:
            if part.get("type") in ("output_text", "text"):
                parts.append(part.get("text") or "")
    if parts:
        return "".join(parts)
    choices = response.get("choices") or []
    if choices:
        return choices[0].get("message", {}).get("content") or ""
    return ""


def _parse_json_array(raw: str) -> list[dict[str, Any]]:
    cleaned = re.sub(r"```json\s*", "", raw, flags=re.I)
    cleaned = re.sub(r"```", "", cleaned).strip()
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"\[.*\]", cleaned, re.S)
        if not match:
            raise RuntimeError(f"Grok не вернул JSON-массив:\n{raw[:800]}")
        data = json.loads(match.group(0))
    if not isinstance(data, list):
        raise RuntimeError("Grok вернул JSON, но это не массив")
    return data


def _normalize_row(row: dict[str, Any]) -> Lead | None:
    tweet_id = row.get("tweet_id")
    if not tweet_id:
        return None
    tweet_id = str(tweet_id).strip()
    if not tweet_id.isdigit():
        return None
    username = (row.get("author_username") or row.get("username") or "").strip().lstrip("@")
    if not username:
        return None
    text = (row.get("tweet_text") or row.get("text") or "").strip()
    if not text:
        return None
    reason = (row.get("lead_reason") or row.get("reason") or "relocation lead").strip()
    return Lead(
        tweet_id=tweet_id,
        author_username=username,
        tweet_text=text,
        lead_reason=reason,
        is_reply=bool(row.get("is_reply")),
        x_url=f"https://x.com/i/status/{tweet_id}",
    )


def _filter_leads(rows: list[dict[str, Any]], store: StateStore) -> list[Lead]:
    leads: list[Lead] = []
    seen: set[str] = set()
    for row in rows:
        lead = _normalize_row(row)
        if not lead or lead.tweet_id in seen:
            continue
        seen.add(lead.tweet_id)
        if store.is_known(lead.tweet_id):
            continue
        leads.append(lead)
    leads.sort(key=lambda item: item.tweet_id)
    return leads


def _call_grok(
    config: Config,
    store: StateStore,
    *,
    limit: int,
    slice_info: SearchSlice,
    lens: str,
    logger: logging.Logger,
) -> list[Lead]:
    excluded_ids = store.get_recent_excluded_ids(config.excluded_ids_in_prompt)

    tool: dict[str, Any] = {
        "type": "x_search",
        "excluded_x_handles": list(config.excluded_handles[:20]),
        "from_date": slice_info.from_date,
        "to_date": slice_info.to_date,
    }

    logger.info(
        "Grok x_search [slice %d/%d]: %s → %s | lens=%s | limit=%d",
        slice_info.index + 1,
        slice_info.total,
        slice_info.from_date,
        slice_info.to_date,
        lens[:48],
        limit,
    )

    payload = {
        "model": config.grok_model,
        "input": [
            {
                "role": "user",
                "content": _build_prompt(
                    limit=limit,
                    slice_info=slice_info,
                    lens=lens,
                    excluded_ids=excluded_ids,
                ),
            }
        ],
        "tools": [tool],
        "store": False,
    }

    resp = requests.post(
        XAI_RESPONSES_URL,
        headers={
            "Authorization": f"Bearer {config.xai_api_key}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=config.xai_timeout,
    )
    if not resp.ok:
        raise RuntimeError(
            f"xAI Responses API HTTP {resp.status_code}: {resp.text[:800]}"
        )

    data = resp.json()
    usage = data.get("usage") or {}
    if usage:
        logger.info(
            "Grok usage [slice %d/%d]: in=%s out=%s",
            slice_info.index + 1,
            slice_info.total,
            usage.get("input_tokens"),
            usage.get("output_tokens"),
        )

    raw_text = _extract_output_text(data)
    if not raw_text.strip():
        raise RuntimeError("Grok вернул пустой ответ")

    rows = _parse_json_array(raw_text)
    leads = _filter_leads(rows, store)
    logger.info(
        "Grok [slice %d/%d]: %d кандидатов (из %d JSON)",
        slice_info.index + 1,
        slice_info.total,
        len(leads),
        len(rows),
    )
    return leads


def search_leads(
    config: Config,
    store: StateStore,
    *,
    logger: logging.Logger,
    queue_size: int = 0,
    fallback: bool = False,
) -> list[Lead]:
    """
    Один вызов Grok на текущий 5-дневный слайс (30 дней = 6 слайсов, с дна вверх).
    Пустой слайс → следующий слайс в следующем цикле.
    """
    limit = config.search_limit_for_queue(queue_size)
    slice_info = store.get_search_slice(
        config.search_max_days,
        config.search_slice_days,
    )
    lens = SEARCH_LENSES[slice_info.index % len(SEARCH_LENSES)]

    leads = _call_grok(
        config,
        store,
        limit=limit,
        slice_info=slice_info,
        lens=lens,
        logger=logger,
    )
    if leads:
        return leads

    if fallback:
        return []

    store.advance_search_slice(
        config.search_max_days,
        config.search_slice_days,
        logger=logger,
    )
    return []
