"""DeepSeek-квалификация ответов под parent tweet."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any

import requests

from bot.config import Config
from bot.reply_bot.state import ReplyLead

DEEPSEEK_URL = "https://api.deepseek.com/chat/completions"
REPLY_QUALIFY_BATCH_SIZE = 15

SYSTEM_PROMPT = """Qualify X/Twitter REPLIES for MoveToRussia outreach under a parent post.

Target audience — citizens/permanent residents of countries eligible for Russia's Shared Values Visa:
- EU member states EXCEPT Hungary (includes Spain, Italy, Germany, France, Poland, etc.)
- UK (incl. Crown Dependencies/Overseas Territories)
- Albania, Andorra, Iceland, Liechtenstein, Monaco, Montenegro, North Macedonia, Norway, San Marino, Switzerland
- US, Canada, Australia, Japan, New Zealand, Singapore, South Korea, Taiwan
- Ukraine, Bahamas, Micronesia

IMPORTANT: Spain is eligible. Do NOT reject replies from Spain or replies in Spanish from Spain.

Approve if: individual, likely from one of the target countries above, personal intent to visit/relocate OR short yes/sure to parent's Russia invitation.
Reject if: already lives in Russia, political rant (Trump/Putin etc), war/spy/sarcasm, spam, hostile, Hungarian, or clearly from a non-target country.

You MUST return exactly one JSON object per input tweet_id, same count as inputs.
[{"tweet_id":"123","approve":true,"score":8,"reason":"short"}]
Score 7-10=approve, 1-6=reject. JSON array only, no markdown."""


@dataclass(frozen=True)
class ReplyQualifyResult:
    approved: list[ReplyLead]
    rejected: list[ReplyLead]


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


def _build_user_message(parent_text: str, batch: list[ReplyLead]) -> str:
    items = [
        {
            "tweet_id": lead.tweet_id,
            "username": lead.author_username,
            "author_location": (lead.author_location or "")[:80],
            "text": lead.tweet_text[:250],
        }
        for lead in batch
    ]
    parent = parent_text[:300]
    return (
        f"Parent:\n{parent}\n\n"
        f"Replies ({len(items)}):\n"
        f"{json.dumps(items, ensure_ascii=False)}"
    )


def _call_deepseek(
    config: Config,
    parent_text: str,
    batch: list[ReplyLead],
    *,
    logger: logging.Logger,
) -> tuple[list[dict[str, Any]], str]:
    user_msg = _build_user_message(parent_text, batch)
    max_out = max(256, len(batch) * 100)

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
            "temperature": 0.0,
            "max_tokens": max_out,
        },
        timeout=config.deepseek_timeout,
    )
    if not resp.ok:
        raise RuntimeError(f"DeepSeek HTTP {resp.status_code}: {resp.text[:500]}")

    body = resp.json()
    choice = (body.get("choices") or [{}])[0]
    usage = body.get("usage") or {}
    finish = choice.get("finish_reason") or "?"
    raw = (choice.get("message") or {}).get("content") or ""

    if usage:
        logger.info(
            "DeepSeek reply batch(%d): in=%s out=%s finish=%s",
            len(batch),
            usage.get("prompt_tokens"),
            usage.get("completion_tokens"),
            finish,
        )

    if not raw.strip():
        raise RuntimeError(f"DeepSeek пустой ответ (finish={finish})")

    rows = _extract_json_objects(raw)
    return rows, raw


def _apply_verdicts(
    batch: list[ReplyLead],
    rows: list[dict[str, Any]],
    *,
    config: Config,
    logger: logging.Logger,
) -> tuple[ReplyQualifyResult, list[ReplyLead]]:
    by_id = {str(lead.tweet_id): lead for lead in batch}
    verdicts: dict[str, dict[str, Any]] = {}
    for row in rows:
        tid = str(row.get("tweet_id", ""))
        if tid in by_id:
            verdicts[tid] = row

    approved: list[ReplyLead] = []
    rejected: list[ReplyLead] = []
    missing: list[ReplyLead] = []

    for lead in batch:
        v = verdicts.get(lead.tweet_id)
        if not v:
            missing.append(lead)
            continue
        if not v.get("approve"):
            rejected.append(lead)
            logger.info(
                "DeepSeek reject @%s %s (score=%s): %s",
                lead.author_username,
                lead.tweet_id,
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
            rejected.append(lead)
            logger.info(
                "DeepSeek reject @%s %s (score=%s<%d): %s",
                lead.author_username,
                lead.tweet_id,
                score,
                config.deepseek_min_score,
                (v.get("reason") or "low score")[:80],
            )
            continue
        approved.append(lead)
        logger.info(
            "DeepSeek approve @%s %s (score=%s): %s",
            lead.author_username,
            lead.tweet_id,
            v.get("score"),
            (v.get("reason") or "ok")[:80],
        )
    return ReplyQualifyResult(approved=approved, rejected=rejected), missing


def _qualify_batch(
    config: Config,
    batch: list[ReplyLead],
    parent_text: str,
    *,
    logger: logging.Logger,
    allow_retry: bool = True,
) -> ReplyQualifyResult:
    rows, raw = _call_deepseek(config, parent_text, batch, logger=logger)
    result, missing = _apply_verdicts(batch, rows, config=config, logger=logger)

    if missing and allow_retry:
        logger.warning(
            "DeepSeek неполный батч: %d/%d без вердикта, retry по одному",
            len(missing),
            len(batch),
        )
        if len(rows) <= 1 and batch:
            logger.debug("DeepSeek raw: %s", raw[:300])
        for lead in missing:
            try:
                single = _qualify_batch(
                    config, [lead], parent_text, logger=logger, allow_retry=False
                )
            except Exception as exc:
                logger.warning(
                    "DeepSeek retry @%s failed: %s — пропуск",
                    lead.author_username,
                    exc,
                )
                continue
            result.approved.extend(single.approved)
            result.rejected.extend(single.rejected)
    elif missing:
        for lead in missing:
            logger.warning(
                "DeepSeek без вердикта для %s (@%s) — пропуск",
                lead.tweet_id,
                lead.author_username,
            )

    return result


def qualify_reply_leads(
    config: Config,
    candidates: list[ReplyLead],
    parent_text: str,
    *,
    logger: logging.Logger,
) -> ReplyQualifyResult:
    if not candidates:
        return ReplyQualifyResult(approved=[], rejected=[])

    batch_size = min(REPLY_QUALIFY_BATCH_SIZE, config.deepseek_qualify_batch_size)
    all_approved: list[ReplyLead] = []
    all_rejected: list[ReplyLead] = []

    for start in range(0, len(candidates), batch_size):
        batch = candidates[start : start + batch_size]
        try:
            result = _qualify_batch(config, batch, parent_text, logger=logger)
        except Exception as exc:
            logger.warning(
                "DeepSeek reply batch %d–%d failed: %s — retry по одному",
                start + 1,
                start + len(batch),
                exc,
            )
            for lead in batch:
                try:
                    single = _qualify_batch(config, [lead], parent_text, logger=logger)
                except Exception as one_exc:
                    logger.warning(
                        "DeepSeek @%s failed: %s — пропуск",
                        lead.author_username,
                        one_exc,
                    )
                    continue
                all_approved.extend(single.approved)
                all_rejected.extend(single.rejected)
            continue
        all_approved.extend(result.approved)
        all_rejected.extend(result.rejected)

    logger.info(
        "DeepSeek reply итого: %d одобрено, %d отклонено, %d без вердикта / %d",
        len(all_approved),
        len(all_rejected),
        len(candidates) - len(all_approved) - len(all_rejected),
        len(candidates),
    )
    return ReplyQualifyResult(approved=all_approved, rejected=all_rejected)


def qualify_one_reply(
    config: Config,
    lead: ReplyLead,
    parent_text: str,
    *,
    logger: logging.Logger,
) -> bool:
    """True если DeepSeek одобрил один лид (для проверки перед постингом)."""
    try:
        result = _qualify_batch(config, [lead], parent_text, logger=logger)
    except Exception as exc:
        logger.warning(
            "DeepSeek pre-post @%s failed: %s — не постим",
            lead.author_username,
            exc,
        )
        return False
    return bool(result.approved)
