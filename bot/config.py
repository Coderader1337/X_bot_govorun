"""Configuration from environment."""

from __future__ import annotations

import os
import random
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT_DIR / "data"
LOGS_DIR = ROOT_DIR / "logs"
DEFAULT_VIDEO_PATH = ROOT_DIR / "bot" / "video" / "V23_1(2).mp4"

# Средний интервал между циклами reply-bot: 86400 / daily_post_limit сек
DEFAULT_INTERVAL_JITTER_FRACTION = 0.55
DEFAULT_MIN_CYCLE_SLEEP_SECONDS = 90
DEFAULT_DAILY_POST_LIMIT = 200
DEFAULT_INTERVAL_MINUTES = 4
DEFAULT_POST_BLOCKED_PAUSE_HOURS = 5
# Пополняем очередь заранее (~5 постов ≈ 2.5 ч буфера при interval=29)
DEFAULT_QUEUE_REFILL_THRESHOLD = 5
DEFAULT_SEARCH_MAX_DAYS = 30
DEFAULT_SEARCH_SLICE_DAYS = 5
DEFAULT_X_SEARCH_PAGE_SIZE = 100
DEFAULT_X_SEARCH_MAX_PAGES = 2
DEFAULT_DEEPSEEK_MODEL = "deepseek-chat"
DEFAULT_DEEPSEEK_QUALIFY_BATCH_SIZE = 15
DEFAULT_DEEPSEEK_MIN_SCORE = 7

DEFAULT_REPLY_BODY = """🇷🇺 Russia is easy to visit!
For US citizens: a 3-year tourist visa ✅
For Europeans: a 30-day e-visa ✈️

Clean parks🌳
Peaceful walks🚶‍♂️
Beautiful cities🏙
Everyday Russia🇷🇺✨

Reach out, and we’ll gladly tell you more.
Link in Bio📩

Visit first, and if you like it—move! Many from the West already have 🌍."""

DEFAULT_EXCLUDED_HANDLES = (
    "MoveToRussiaCom",
    "MoveToRussia",
    "grok",
    "xai",
)


@dataclass(frozen=True)
class Config:
    xai_api_key: str | None
    deepseek_api_key: str
    x_bearer_token: str | None
    x_consumer_key: str
    x_consumer_secret: str
    x_access_token: str
    x_access_token_secret: str
    grok_model: str
    interval_minutes: int
    interval_jitter_fraction: float
    search_limit: int
    search_limit_urgent: int
    daily_post_limit: int
    queue_refill_threshold: int
    sparse_retry_min: int
    excluded_ids_in_prompt: int
    search_max_days: int
    search_slice_days: int
    x_search_page_size: int
    x_search_max_pages: int
    grok_fallback_enabled: bool
    grok_only_when_queue_empty: bool
    deepseek_model: str
    deepseek_timeout: int
    deepseek_qualify_batch_size: int
    deepseek_min_score: int
    excluded_handles: tuple[str, ...]
    state_db_path: Path
    log_file: Path
    xai_timeout: int
    video_path: Path | None

    @property
    def interval_seconds(self) -> int:
        return self.interval_minutes * 60

    @property
    def mean_post_interval_seconds(self) -> float:
        """Средняя пауза между циклами при 1 пост/цикл и лимите daily_post_limit."""
        return 86400 / self.daily_post_limit

    def reply_cycle_sleep_seconds(self) -> int:
        """Случайная пауза reply-bot: uniform вокруг mean, ≈ daily_post_limit постов/сутки."""
        mean = self.mean_post_interval_seconds
        j = self.interval_jitter_fraction
        low = mean * (1.0 - j)
        high = mean * (1.0 + j)
        return max(DEFAULT_MIN_CYCLE_SLEEP_SECONDS, int(random.uniform(low, high)))

    def search_limit_for_queue(self, queue_size: int) -> int:
        """Больше лимит при низкой очереди — выжимаем data lake, пока буфер пуст."""
        if queue_size <= 1:
            return self.search_limit_urgent
        return self.search_limit

    def needs_refill(self, queue_size: int) -> bool:
        if self.grok_only_when_queue_empty:
            return queue_size == 0
        return queue_size <= self.queue_refill_threshold

    def build_post_text(self, username: str) -> str:
        handle = username.lstrip("@")
        return f"@{handle}\n{DEFAULT_REPLY_BODY}"


def _split_handles(raw: str) -> tuple[str, ...]:
    items = [h.strip().lstrip("@") for h in raw.split(",") if h.strip()]
    return tuple(items) if items else DEFAULT_EXCLUDED_HANDLES


def load_config() -> Config:
    load_dotenv(ROOT_DIR / ".env")

    xai_key = os.getenv("XAI_API_KEY", "").strip()
    deepseek_key = os.getenv("DEEPSEEK_API_KEY", "").strip()
    if not deepseek_key:
        raise RuntimeError("DEEPSEEK_API_KEY не задан в .env")

    bearer = os.getenv("X_BEARER_TOKEN", "").strip() or None
    if not bearer:
        raise RuntimeError("X_BEARER_TOKEN нужен для hybrid discovery (X API search)")

    consumer_key = os.getenv("X_CONSUMER_KEY", "").strip()
    consumer_secret = os.getenv("X_SECRET_KEY", "").strip()
    access_token = os.getenv("X_ACCESS_TOKEN", "").strip()
    access_secret = os.getenv("X_ACCESS_TOKEN_SECRET", "").strip()
    missing = [
        name
        for name, val in (
            ("X_CONSUMER_KEY", consumer_key),
            ("X_SECRET_KEY", consumer_secret),
            ("X_ACCESS_TOKEN", access_token),
            ("X_ACCESS_TOKEN_SECRET", access_secret),
        )
        if not val
    ]
    if missing:
        raise RuntimeError(f"Для постинга нужны в .env: {', '.join(missing)}")

    video_raw = os.getenv("BOT_VIDEO_PATH", "").strip()
    if video_raw:
        video_path = Path(video_raw)
    elif DEFAULT_VIDEO_PATH.is_file():
        video_path = DEFAULT_VIDEO_PATH
    else:
        video_path = None

    return Config(
        xai_api_key=xai_key or None,
        deepseek_api_key=deepseek_key,
        x_bearer_token=bearer,
        x_consumer_key=consumer_key,
        x_consumer_secret=consumer_secret,
        x_access_token=access_token,
        x_access_token_secret=access_secret,
        grok_model=os.getenv("GROK_MODEL", "grok-4.3").strip(),
        interval_minutes=int(os.getenv("BOT_INTERVAL_MINUTES", str(DEFAULT_INTERVAL_MINUTES))),
        interval_jitter_fraction=float(
            os.getenv("BOT_INTERVAL_JITTER", str(DEFAULT_INTERVAL_JITTER_FRACTION))
        ),
        search_limit=int(os.getenv("SEARCH_LIMIT", "10")),
        search_limit_urgent=int(os.getenv("SEARCH_LIMIT_URGENT", "15")),
        daily_post_limit=int(os.getenv("DAILY_POST_LIMIT", str(DEFAULT_DAILY_POST_LIMIT))),
        queue_refill_threshold=int(
            os.getenv("QUEUE_REFILL_THRESHOLD", str(DEFAULT_QUEUE_REFILL_THRESHOLD))
        ),
        sparse_retry_min=int(os.getenv("SPARSE_RETRY_MIN", "3")),
        excluded_ids_in_prompt=int(os.getenv("EXCLUDED_IDS_IN_PROMPT", "50")),
        search_max_days=int(os.getenv("SEARCH_MAX_DAYS", str(DEFAULT_SEARCH_MAX_DAYS))),
        search_slice_days=int(
            os.getenv("SEARCH_SLICE_DAYS", str(DEFAULT_SEARCH_SLICE_DAYS))
        ),
        x_search_page_size=int(
            os.getenv("X_SEARCH_PAGE_SIZE", str(DEFAULT_X_SEARCH_PAGE_SIZE))
        ),
        x_search_max_pages=int(
            os.getenv("X_SEARCH_MAX_PAGES", str(DEFAULT_X_SEARCH_MAX_PAGES))
        ),
        grok_fallback_enabled=os.getenv("GROK_FALLBACK_ENABLED", "1").strip()
        not in ("0", "false", "False", "no")
        and bool(xai_key),
        grok_only_when_queue_empty=os.getenv("GROK_ONLY_WHEN_QUEUE_EMPTY", "1").strip()
        not in ("0", "false", "False", "no"),
        deepseek_model=os.getenv("DEEPSEEK_MODEL", DEFAULT_DEEPSEEK_MODEL).strip(),
        deepseek_timeout=int(os.getenv("DEEPSEEK_TIMEOUT", "90")),
        deepseek_qualify_batch_size=int(
            os.getenv("DEEPSEEK_QUALIFY_BATCH_SIZE", str(DEFAULT_DEEPSEEK_QUALIFY_BATCH_SIZE))
        ),
        deepseek_min_score=int(os.getenv("DEEPSEEK_MIN_SCORE", str(DEFAULT_DEEPSEEK_MIN_SCORE))),
        excluded_handles=_split_handles(os.getenv("EXCLUDED_HANDLES", "")),
        state_db_path=Path(os.getenv("STATE_DB", str(DATA_DIR / "bot_state.db"))),
        log_file=Path(os.getenv("LOG_FILE", str(LOGS_DIR / "bot.log"))),
        xai_timeout=int(os.getenv("XAI_TIMEOUT", "120")),
        video_path=video_path,
    )
