"""Фильтр региона автора ответа — таргет на недружественные России страны."""

from __future__ import annotations

import re

from bot.reply_bot.state import ReplyLead

_US_STATES = frozenset({
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA", "HI", "ID", "IL", "IN",
    "IA", "KS", "KY", "LA", "ME", "MD", "MA", "MI", "MN", "MS", "MO", "MT", "NE", "NV",
    "NH", "NJ", "NM", "NY", "NC", "ND", "OH", "OK", "OR", "PA", "RI", "SC", "SD", "TN",
    "TX", "UT", "VT", "VA", "WA", "WV", "WI", "WY", "DC",
})

# Россия / явно нецелевые страны
_BLOCKED = re.compile(
    r"(?:"
    r"\brussia\b|\brussian\b|\bmoscow\b|\bроссия\b|\bмосква\b|"
    r"\bhungary\b|\bbudapest\b|\bmagyar\b|"
    r"\bbelarus\b|\bminsk\b|"
    r"\biran\b|\btehran\b|"
    r"\bchina\b|\bbeijing\b|\bshanghai\b|"
    r"\bindia\b|\bmumbai\b|\bdelhi\b|"
    r"\bbrazil\b|\bmexico\b|\bargentina\b|"
    r"\bisrael\b|\btel\s*aviv\b|"
    r"\bsaudi\b|\buae\b|\bdubai\b|\bqatar\b|"
    r"\bsouth\s*africa\b|\bnigeria\b|\begypt\b|"
    r"\bthailand\b|\bvietnam\b|\bindonesia\b|\bphilippines\b|"
    r"\bturkey\b|\btürkiye\b|\bistanbul\b|\bankara\b"
    r")",
    re.I,
)

_UNFRIENDLY = re.compile(
    r"(?:"
    r"\b(?:united\s*states|u\.?s\.?a?\.?|america|american)\b|"
    r"\b(?:canada|canadian|toronto|vancouver|montreal|ottawa)\b|"
    r"\b(?:united\s*kingdom|u\.?k\.?|britain|british|england|english|scotland|scottish|"
    r"wales|welsh|london|manchester|birmingham|leeds|liverpool|glasgow|bristol|edinburgh)\b|"
    r"\b(?:australia|australian|sydney|melbourne|brisbane|perth)\b|"
    r"\b(?:new\s*zealand|auckland|wellington)\b|"
    r"\b(?:japan|japanese|tokyo|osaka)\b|"
    r"\b(?:south\s*korea|korean|seoul|busan)\b|"
    r"\b(?:singapore|singaporean)\b|"
    r"\b(?:taiwan|taipei|taiwanese)\b|"
    r"\b(?:ukraine|ukrainian|kyiv|kiev|lviv|odesa)\b|"
    r"\b(?:austria|vienna|belgium|brussels|bulgaria|sofia|germany|german|berlin|munich|"
    r"hamburg|frankfurt|greece|athens|denmark|copenhagen|ireland|dublin|spain|spanish|madrid|"
    r"barcelona|italy|italian|rome|milan|cyprus|latvia|riga|lithuania|vilnius|luxembourg|"
    r"malta|netherlands|dutch|amsterdam|poland|polish|warsaw|krakow|portugal|portuguese|"
    r"lisbon|romania|bucharest|slovakia|bratislava|slovenia|ljubljana|finland|helsinki|"
    r"france|french|paris|lyon|croatia|zagreb|czech|czechia|prague|sweden|swedish|stockholm|"
    r"estonia|tallinn)\b|"
    r"\b(?:albania|andorra|iceland|reykjavik|liechtenstein|monaco|montenegro|norway|oslo|"
    r"macedonia|north\s*macedonia|san\s*marino|switzerland|swiss|zurich|geneva|bern)\b|"
    r"\bbahamas\b|\bmicronesia\b|"
    r"🇵🇹|🇺🇸|🇬🇧|🇩🇪|🇫🇷|🇺🇦|🇵🇱|🇨🇦|🇦🇺|"
    r"\b(?:california|texas|florida|new\s*york|illinois|pennsylvania|ohio|georgia|"
    r"north\s*carolina|michigan|new\s*jersey|virginia|washington|arizona|massachusetts|"
    r"tennessee|indiana|missouri|maryland|wisconsin|colorado|minnesota|south\s*carolina|"
    r"alabama|louisiana|kentucky|oregon|oklahoma|connecticut|utah|iowa|nevada|arkansas|"
    r"mississippi|kansas|new\s*mesico|nebraska|idaho|west\s*virginia|hawaii|new\s*hampshire|"
    r"maine|montana|rhode\s*island|delaware|south\s*dakota|north\s*dakota|alaska|vermont|"
    r"wyoming|district\s*of\s*columbia)\b"
    r")",
    re.I,
)

_USERNAME_HINTS = re.compile(
    r"(?:"
    r"usa|america|american|british|uk|london|german|deutsch|france|french|"
    r"canada|canadian|aussie|australia|europe|european|"
    r"ukrainian|ukraine|polish|poland|swedish|sweden|finnish|finland|"
    r"dutch|netherlands|belgian|spain|spanish|italian|italy|portugal|portuguese|"
    r"japan|japanese|korean|korea|singapore|taiwan"
    r")",
    re.I,
)

# Явно нецелевые, если указаны в location (не «неизвестный город»)
_CLEAR_NON_TARGET = re.compile(
    r"(?:"
    r"\bnigeria\b|\bnigerian\b|\bafrica\b|\bindia\b|\bindian\b|\bchina\b|\bchinese\b|"
    r"\bbrazil\b|\bbrazilian\b|\bmexico\b|\bmexican\b|\begypt\b|\begyptian\b|"
    r"\bturkey\b|\bturkish\b|\biran\b|\biranian\b|\bisrael\b|\bisraeli\b|"
    r"\bsaudi\b|\buae\b|\bthailand\b|\bvietnam\b|\bindonesia\b|\bphilippines\b|"
    r"\bhungary\b|\bhungarian\b|\bbelarus\b|\brussia\b|\brussian\b|\bmoscow\b"
    r")",
    re.I,
)

_US_STATE_SUFFIX = re.compile(r",\s*([A-Z]{2})\s*$")


def _is_russian_st_petersburg(text: str) -> bool:
    if not re.search(r"\b(?:saint\s*petersburg|st\.?\s*petersburg)\b", text, re.I):
        return False
    if re.search(r"\b(?:florida|,\s*fl\b)\b", text, re.I):
        return False
    return True


def _has_us_state(location: str) -> bool:
    m = _US_STATE_SUFFIX.search((location or "").strip())
    return bool(m and m.group(1).upper() in _US_STATES)


def _geo_text(location: str, username: str) -> str:
    loc = (location or "").strip()
    user = (username or "").strip().lstrip("@")
    if loc and user:
        return f"{loc} | @{user}"
    if loc:
        return loc
    return f"@{user}" if user else ""


def region_verdict(location: str, username: str) -> tuple[str, str]:
    """
    pass — явно недружественный регион.
    reject — явно Россия/Венгрия/нецелевая страна.
    unknown — гео неясно, решит DeepSeek.
    """
    loc = (location or "").strip()
    text = _geo_text(location, username).lower()
    if not text or text == "@":
        return "unknown", "geo empty"

    if _is_russian_st_petersburg(text) or _BLOCKED.search(text):
        return "reject", "geo blocked region"

    if _UNFRIENDLY.search(text) or _has_us_state(loc):
        return "pass", "geo unfriendly match"

    if not loc:
        if _USERNAME_HINTS.search(username or ""):
            return "pass", "geo username hint"
        return "unknown", "geo unknown (no location)"

    if _CLEAR_NON_TARGET.search(loc):
        return "reject", "geo non-target country"

    # Непонятный город/шутка в bio — не режем, пусть DeepSeek решит
    return "unknown", "geo ambiguous"


def filter_by_region(leads: list[ReplyLead]) -> tuple[list[ReplyLead], list[tuple[ReplyLead, str]]]:
    """Возвращает (прошли geo, отклонённые с причиной). unknown тоже проходит — дальше DeepSeek."""
    ok: list[ReplyLead] = []
    rejected: list[tuple[ReplyLead, str]] = []
    for lead in leads:
        verdict, reason = region_verdict(lead.author_location, lead.author_username)
        if verdict == "reject":
            rejected.append((lead, reason))
        else:
            ok.append(lead)
    return ok, rejected
