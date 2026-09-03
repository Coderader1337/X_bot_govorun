"""Keyword query patterns for X API — узкая сеть, меньше чтений, выше precision."""

from __future__ import annotations

# Общие фильтры (лимит query ~512 символов)
COMMON_FILTERS = (
    "-is:retweet -from:MoveToRussiaCom -from:MoveToRussia "
    "-war -Ukraine -NATO -Putin -Zelensky -frontline "
    '-"you should" -"why don\'t you" -"go to Russia if" -"move to Russia then"'
)

# Якорь личного намерения — отсекает «иди сам в Россию»
PERSONAL = (
    '(I OR me OR my OR "want to" OR "would move" OR wanna OR "planning to" OR '
    '"thinking of" OR "how do I" OR "how to move" OR "anyone moved" OR advice)'
)

# (id, query_body) — 10 паттернов вместо 20; ротация по одному за страницу
SEARCH_QUERY_PATTERNS: tuple[tuple[str, str], ...] = (
    (
        "intent_explicit",
        f'("want to move to Russia" OR "would move to Russia" OR "wanna move to Russia" OR '
        f'"planning to move to Russia" OR "thinking of moving to Russia" OR "how do I move to Russia")',
    ),
    (
        "intent_cities",
        f'("want to move to Moscow" OR "moving to Moscow" OR "live in Moscow" OR '
        f'"move to Saint Petersburg" OR "living in Russia" OR "want to live in Russia")',
    ),
    (
        "visa_personal",
        f'{PERSONAL} ("Russian visa" OR "Russia visa" OR "eVisa Russia" OR '
        f'"residence permit Russia" OR "citizenship Russia")',
    ),
    (
        "leave_west",
        f'{PERSONAL} ("leave the US" OR "leaving America" OR "leaving Europe" OR '
        f'"leave the West" OR "escape the West") (Russia OR Moscow OR "Russian visa")',
    ),
    (
        "help_moving",
        f'("how do I move" OR "how to move to Russia" OR "anyone moved to Russia" OR '
        f'"tips for moving to Russia" OR "advice moving to Russia")',
    ),
    (
        "geo_west",
        f'(Canada OR Canadian OR British OR "from the UK" OR American OR Australian OR German OR French) '
        f'("want to move to Russia" OR "Russian visa" OR "move to Russia" OR "living in Russia")',
    ),
    (
        "replies_intent",
        f'("move to Russia" OR "Russian visa" OR "living in Russia" OR emigrat Russia) is:reply '
        f'(I OR want OR would OR planning OR "me too" OR same)',
    ),
    (
        "dream_relocate",
        f'("dream to live" OR "hope to move" OR "want to live" OR "planning to live") '
        f'(Russia OR Moscow OR "Russian visa")',
    ),
    (
        "repatriation_personal",
        f'{PERSONAL} (repatriat OR "return to Russia" OR "back to Russia" OR "moving back to Russia")',
    ),
    (
        "relocating_now",
        f'("moved to Russia" OR "relocated to Russia" OR "now live in Russia" OR "I live in Russia") '
        f'(visa OR help OR question OR advice OR "how")',
    ),
)


def build_query(pattern_index: int) -> tuple[str, str]:
    """Возвращает (pattern_id, полный query для X API)."""
    idx = pattern_index % len(SEARCH_QUERY_PATTERNS)
    pattern_id, body = SEARCH_QUERY_PATTERNS[idx]
    return pattern_id, f"{body} {COMMON_FILTERS}"


def pattern_count() -> int:
    return len(SEARCH_QUERY_PATTERNS)
