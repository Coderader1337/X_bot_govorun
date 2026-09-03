"""DeepSeek: ключевые слова для поиска ответов под конкретным твитом."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass

import requests

from bot.config import Config

DEEPSEEK_URL = "https://api.deepseek.com/chat/completions"

KEYWORD_PROMPT = """You help find X/Twitter REPLIES under a specific post for MoveToRussia outreach.

Given the parent post text, generate an X API search query fragment (OR-joined terms) to find replies where people:
- express personal intent to move/relocate/live in Russia
- ask about Russian visa, residence, citizenship, practical relocation
- agree personally with moving to Russia (not sarcasm toward others)
- give SHORT affirmative answers to the post's question/offer

Parent post:
{tweet_text}

Return ONLY JSON:
{{"query": "(term1 OR term2 OR \\"phrase three\\")", "reason": "brief"}}

Rules:
- 12-24 terms/phrases, OR-joined, valid for X search
- If the post asks a question or offers something ("would you visit", "who wants", "free trip") — MUST include short affirmative replies
- Include short one-word YES replies in multiple languages of countries unfriendly to Russia: English (yes/yeah/sure), Spanish (si/claro), French (oui/bien sur), German (ja/sicher), Portuguese (sim/claro), Italian (si/certo), etc.
- Include personal-intent phrases in those languages where natural (e.g., "me mudo", "je voudrais", "ich wuerde")
- Use ASCII-only terms without diacritics (accents): e.g., "si" not "sí", "sur" not "sûr", "wuerde" not "würde"
- Short one-word replies matter — do NOT only use long relocation phrases
- Relevant to THIS post's topic and reply context
- Multilingual ASCII terms; no hashtags unless essential
- No conversation_id, no is:reply — only keyword part"""

# Дополнение для вопросов/опросов — ловит «yes», «si», «oui», «ja» и т.п. на языках недружественных стран.
AFFIRMATIVE_SUPPLEMENT = (
    '"yes" OR yeah OR sure OR si OR oui OR ja OR sim OR certo'
)

_QUESTION_MARKERS = (
    "?",
    "would you",
    "do you",
    "who would",
    "who wants",
    "anyone",
    "raise your hand",
    "interested",
    "want to",
    "free ",
    "visit russia",
    "visit ",
    "poll",
    "vote",
)


def _looks_like_question_or_offer(text: str) -> bool:
    t = text.lower()
    return any(m in t for m in _QUESTION_MARKERS)


def _merge_queries(primary: str, extra: str) -> str:
    p = primary.strip()
    if p.startswith("(") and p.endswith(")"):
        p = p[1:-1].strip()
    e = extra.strip()
    if e.startswith("(") and e.endswith(")"):
        e = e[1:-1].strip()
    return f"({p} OR {e})"


def finalize_keyword_query(deepseek_query: str, parent_text: str) -> str:
    """Добавляет yes/yeah и т.д. если пост — вопрос или оффер."""
    if not _looks_like_question_or_offer(parent_text):
        return deepseek_query
    return _merge_queries(deepseek_query, AFFIRMATIVE_SUPPLEMENT)


@dataclass(frozen=True)
class KeywordResult:
    query: str
    reason: str


def _extract_json(raw: str) -> dict:
    cleaned = re.sub(r"```json\s*", "", raw, flags=re.I)
    cleaned = re.sub(r"```", "", cleaned).strip()
    return json.loads(cleaned)


def generate_reply_keywords(
    config: Config,
    parent_text: str,
    *,
    logger: logging.Logger,
) -> KeywordResult:
    user_msg = KEYWORD_PROMPT.format(tweet_text=parent_text[:2000])
    resp = requests.post(
        DEEPSEEK_URL,
        headers={
            "Authorization": f"Bearer {config.deepseek_api_key}",
            "Content-Type": "application/json",
        },
        json={
            "model": config.deepseek_model,
            "messages": [
                {"role": "user", "content": user_msg},
            ],
            "temperature": 0.2,
            "max_tokens": 400,
        },
        timeout=config.deepseek_timeout,
    )
    if not resp.ok:
        raise RuntimeError(f"DeepSeek HTTP {resp.status_code}: {resp.text[:500]}")

    body = resp.json()
    usage = body.get("usage") or {}
    if usage:
        logger.info(
            "DeepSeek keywords: in=%s out=%s",
            usage.get("prompt_tokens"),
            usage.get("completion_tokens"),
        )

    raw = body["choices"][0]["message"]["content"]
    data = _extract_json(raw)
    query = str(data.get("query", "")).strip()
    reason = str(data.get("reason", "")).strip()
    if not query:
        raise RuntimeError(f"DeepSeek вернул пустой query: {raw[:300]}")
    query = finalize_keyword_query(query, parent_text)
    logger.info("Keywords: %s — %s", query[:160], reason[:80])
    return KeywordResult(query=query, reason=reason)
