"""Фиксированные анти-слова для reply-bot — отсекаем враждебный/шпионский контент."""

from __future__ import annotations

import re

# Подстроки (нижний регистр)
ANTI_PHRASES: tuple[str, ...] = (
    "collect intelligence",
    "gather intelligence",
    "spy on",
    "spying on",
    "espionage",
    "undercover",
    "for ukraine",
    "for the ukraine",
    "help ukraine",
    "support ukraine",
    "slava ukraini",
    "glory to ukraine",
    "death to russia",
    "kill putin",
    "assassinate",
    "sabotage",
    "destroy russia",
    "invade russia",
    "war crime",
    "war criminal",
    "nato ",
    "zelensky",
    "frontline",
    "mobilization",
    "draft dodg",
    "cia ",
    "mi6",
    "f sb",
    "five eyes",
    "you should move",
    "go to russia then",
    "enjoy russia",
    "hellhole",
    "shithole",
    "shit hole",
    "trash country",
    "go back to",
    "deport ",
)

ANTI_REGEX = re.compile(
    r"(?:"
    r"\bintelligence\b.*\b(?:ukraine|ukrainian|kyiv|kiev)\b|"
    r"\b(?:ukraine|ukrainian)\b.*\b(?:intelligence|spy|spies|espionage)\b|"
    r"\bonly to (?:collect|gather|get)\b|"
    r"\bfor (?:them|ukraine) to use\b"
    r")",
    re.I,
)


def is_blocked(text: str) -> tuple[bool, str]:
    """True + причина, если твит неприемлем для outreach."""
    lower = text.lower()
    for phrase in ANTI_PHRASES:
        if phrase in lower:
            return True, f"anti-phrase: {phrase}"
    if ANTI_REGEX.search(text):
        return True, "anti-regex: hostile context"
    return False, ""


def filter_leads(leads: list) -> tuple[list, int]:
    """Отфильтровать список ReplyLead. Возвращает (ok, dropped_count)."""
    ok = []
    dropped = 0
    for lead in leads:
        blocked, _ = is_blocked(lead.tweet_text)
        if blocked:
            dropped += 1
            continue
        ok.append(lead)
    return ok, dropped
