"""Квалификация кандидатов через DeepSeek — дешёвый фильтр шума."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any

import requests

from bot.config import Config
from bot.state import Lead

DEEPSEEK_URL = "https://api.deepseek.com/chat/completions"

SYSTEM_PROMPT = """You qualify X/Twitter posts for MoveToRussia outreach.
Approve ONLY if ALL are true:
1) Individual person (not media/brand/bot/propaganda account tone)
2) Personal intent or openness to relocate/live/visit Russia long-term, OR asking practical questions about moving/visa/life in Russia
3) Author likely from unfriendly countries: EU (NOT Hungary), UK, US, CA, AU, JP, KR, NZ, SG, TW, UA — OR unclear geo but clearly Western expat/relocation context
4) Genuine intent — not pure sarcasm, not debating geopolitics without personal stake
5) NOT telling someone else to move; NOT insults; NOT war/politics commentary

Approve replies/thread comments if the PERSONAL intent is clear.
Reject: news, pundits, spam, hate-only, Russia tourism with no relocation angle, Hungarians, obvious jokes, "you should move to Russia" sarcasm, business ads.

Return ONLY a JSON array. One object per input tweet_id, same order OK.
Format: [{"tweet_id":"...","approve":true,"score":8,"reason":"short"}]
Score 7-10 = approve, 1-6 = reject. Keep reason under 40 characters. No newlines inside JSON."""


@dataclass(frozen=True)
class QualifyResult:
    approved: list[Lead]
    rejected: list[Lead]


def _extract_json_objects(raw: str) -> list[dict[str, Any]]:
    cleaned = re.sub(r"```json\s*", "", raw, flags=re.I)
    cleaned = re.sub(r"```", "", cleaned).strip()
    try:
        data = json.loads(cleaned)
        if isinstance(data, list):
            return data
    except json.JSONDecodeError:
        pass

    objects: list[dict[str, Any]] = []
    for match in re.finditer(
        r'\{\s*"tweet_id"\s*:\s*"(?P<id>\d+)"\s*,\s*"approve"\s*:\s*(?P<approve>true|false)'
        r'[^}]*\}',
        cleaned,
        flags=re.I,
    ):
        try:
            objects.append(json.loads(match.group(0)))
        except json.JSONDecodeError:
            continue
    if objects:
        return objects
    raise RuntimeError(f"DeepSeek не вернул разборный JSON:\n{raw[:600]}")


def _qualify_batch(
    config: Config,
    batch: list[Lead],
    *,
    logger: logging.Logger,
) -> QualifyResult:
    items = [
        {
            "tweet_id": c.tweet_id,
            "username": c.author_username,
            "is_reply": c.is_reply,
            "text": c.tweet_text[:400],
        }
        for c in batch
    ]
    user_msg = f"Qualify these {len(items)} posts:\n{json.dumps(items, ensure_ascii=False)}"
    max_out = max(512, len(batch) * 120)

    resp = requests.post(
        DEEPSEEK_URL,
        headers={
            "Authorization": f"Bearer {config.deepseek_api_key}",
            "Content-Type": "application/json",
        },
        json={
            "model": config.deepseek_model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_msg},
            ],
            "temperature": 0.1,
            "max_tokens": max_out,
        },
        timeout=config.deepseek_timeout,
    )
    if not resp.ok:
        raise RuntimeError(f"DeepSeek HTTP {resp.status_code}: {resp.text[:500]}")

    body = resp.json()
    usage = body.get("usage") or {}
    if usage:
        logger.info(
            "DeepSeek batch(%d): in=%s out=%s",
            len(batch),
            usage.get("prompt_tokens"),
            usage.get("completion_tokens"),
        )

    raw = body["choices"][0]["message"]["content"]
    rows = _extract_json_objects(raw)
    by_id = {str(c.tweet_id): c for c in batch}
    verdicts: dict[str, dict[str, Any]] = {}
    for row in rows:
        tid = str(row.get("tweet_id", ""))
        if tid in by_id:
            verdicts[tid] = row

    approved: list[Lead] = []
    rejected: list[Lead] = []
    for cand in batch:
        v = verdicts.get(cand.tweet_id)
        if not v:
            logger.warning(
                "DeepSeek без вердикта для %s (@%s) — не помечаем rejected",
                cand.tweet_id,
                cand.author_username,
            )
            continue
        if not v.get("approve"):
            rejected.append(cand)
            logger.info(
                "DeepSeek reject @%s %s (score=%s): %s",
                cand.author_username,
                cand.tweet_id,
                v.get("score"),
                (v.get("reason") or "")[:80],
            )
            continue
        score = v.get("score")
        try:
            score_int = int(score) if score is not None else 0
        except (TypeError, ValueError):
            score_int = 0
        if score_int < config.deepseek_min_score:
            rejected.append(cand)
            logger.info(
                "DeepSeek reject @%s %s (score=%s<%d): %s",
                cand.author_username,
                cand.tweet_id,
                score,
                config.deepseek_min_score,
                (v.get("reason") or "low score")[:80],
            )
            continue
        reason = (v.get("reason") or "qualified lead").strip()
        approved.append(
            Lead(
                tweet_id=cand.tweet_id,
                author_username=cand.author_username,
                tweet_text=cand.tweet_text,
                lead_reason=reason,
                is_reply=cand.is_reply,
                x_url=cand.x_url,
            )
        )
        logger.info(
            "DeepSeek approve @%s %s (score=%s): %s",
            cand.author_username,
            cand.tweet_id,
            v.get("score"),
            reason[:80],
        )
    return QualifyResult(approved=approved, rejected=rejected)


def qualify_leads(
    config: Config,
    candidates: list[Lead],
    *,
    logger: logging.Logger,
) -> QualifyResult:
    if not candidates:
        return QualifyResult(approved=[], rejected=[])

    batch_size = config.deepseek_qualify_batch_size
    all_approved: list[Lead] = []
    all_rejected: list[Lead] = []

    for start in range(0, len(candidates), batch_size):
        batch = candidates[start : start + batch_size]
        try:
            result = _qualify_batch(config, batch, logger=logger)
        except Exception as exc:
            logger.warning(
                "DeepSeek batch %d–%d failed: %s — кандидаты не помечаются",
                start + 1,
                start + len(batch),
                exc,
            )
            continue
        all_approved.extend(result.approved)
        all_rejected.extend(result.rejected)

    logger.info(
        "DeepSeek итого: %d одобрено, %d отклонено, %d без вердикта / %d",
        len(all_approved),
        len(all_rejected),
        len(candidates) - len(all_approved) - len(all_rejected),
        len(candidates),
    )
    return QualifyResult(approved=all_approved, rejected=all_rejected)
