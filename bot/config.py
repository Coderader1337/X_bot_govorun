"""Configuration from environment — reply-bot only."""

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

DEFAULT_INTERVAL_JITTER_FRACTION = 0.55
DEFAULT_MIN_CYCLE_SLEEP_SECONDS = 90
DEFAULT_DAILY_POST_LIMIT = 200
DEFAULT_POST_BLOCKED_PAUSE_HOURS = 5
DEFAULT_X_SEARCH_PAGE_SIZE = 100
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


@dataclass(frozen=True)
class Config:
    deepseek_api_key: str
    x_bearer_token: str
    x_consumer_key: str
    x_consumer_secret: str
    x_access_token: str
    x_access_token_secret: str
    interval_jitter_fraction: float
    daily_post_limit: int
    x_search_page_size: int
    deepseek_model: str
    deepseek_timeout: int
    deepseek_qualify_batch_size: int
    deepseek_min_score: int
    data_dir: Path
    logs_dir: Path
    video_path: Path | None

    @property
    def mean_post_interval_seconds(self) -> float:
        return 86400 / self.daily_post_limit

    def reply_cycle_sleep_seconds(self) -> int:
        mean = self.mean_post_interval_seconds
        j = self.interval_jitter_fraction
        low = mean * (1.0 - j)
        high = mean * (1.0 + j)
        return max(DEFAULT_MIN_CYCLE_SLEEP_SECONDS, int(random.uniform(low, high)))

    def build_post_text(self, username: str) -> str:
        handle = username.lstrip("@")
        return f"@{handle}\n{DEFAULT_REPLY_BODY}"


def load_config() -> Config:
    load_dotenv(ROOT_DIR / ".env")

    deepseek_key = os.getenv("DEEPSEEK_API_KEY", "").strip()
    if not deepseek_key:
        raise RuntimeError("DEEPSEEK_API_KEY не задан в .env")

    bearer = os.getenv("X_BEARER_TOKEN", "").strip()
    if not bearer:
        raise RuntimeError("X_BEARER_TOKEN нужен для поиска ответов")

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

    data_dir = Path(os.getenv("DATA_DIR", str(DATA_DIR)))
    logs_dir = Path(os.getenv("LOG_DIR", str(LOGS_DIR)))

    return Config(
        deepseek_api_key=deepseek_key,
        x_bearer_token=bearer,
        x_consumer_key=consumer_key,
        x_consumer_secret=consumer_secret,
        x_access_token=access_token,
        x_access_token_secret=access_secret,
        interval_jitter_fraction=float(
            os.getenv("BOT_INTERVAL_JITTER", str(DEFAULT_INTERVAL_JITTER_FRACTION))
        ),
        daily_post_limit=int(os.getenv("DAILY_POST_LIMIT", str(DEFAULT_DAILY_POST_LIMIT))),
        x_search_page_size=int(
            os.getenv("X_SEARCH_PAGE_SIZE", str(DEFAULT_X_SEARCH_PAGE_SIZE))
        ),
        deepseek_model=os.getenv("DEEPSEEK_MODEL", DEFAULT_DEEPSEEK_MODEL).strip(),
        deepseek_timeout=int(os.getenv("DEEPSEEK_TIMEOUT", "90")),
        deepseek_qualify_batch_size=int(
            os.getenv("DEEPSEEK_QUALIFY_BATCH_SIZE", str(DEFAULT_DEEPSEEK_QUALIFY_BATCH_SIZE))
        ),
        deepseek_min_score=int(os.getenv("DEEPSEEK_MIN_SCORE", str(DEFAULT_DEEPSEEK_MIN_SCORE))),
        data_dir=data_dir,
        logs_dir=logs_dir,
        video_path=video_path,
    )
