"""Daily video upload cache for X posts."""

from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

from requests_oauthlib import OAuth1Session

from bot.config import Config
from bot.x_client import post_mention

META_MEDIA_ID = "video_media_id"
META_UPLOADED_AT = "video_media_uploaded_at"
CACHE_TTL = timedelta(hours=23)
CHUNK_SIZE = 4 * 1024 * 1024
MEDIA_API_BASES = ("https://api.x.com/2", "https://api.twitter.com/2")
PROCESSING_TIMEOUT_SEC = 300


def _parse_media_id(payload: dict) -> str:
    data = payload.get("data") or payload
    media_id = data.get("id") or data.get("media_id")
    if not media_id:
        raise RuntimeError(f"No media id in response: {payload!r}"[:400])
    return str(media_id)


def _processing_state(payload: dict) -> str | None:
    data = payload.get("data") or payload
    info = data.get("processing_info") or {}
    state = info.get("state")
    return str(state) if state else None


def _wait_for_processing(oauth: OAuth1Session, media_id: str, *, logger: logging.Logger) -> None:
    deadline = time.time() + PROCESSING_TIMEOUT_SEC
    while time.time() < deadline:
        last_error: str | None = None
        for base in MEDIA_API_BASES:
            resp = oauth.get(
                f"{base}/media/upload",
                params={"media_id": media_id},
                timeout=90,
            )
            if resp.status_code == 429:
                reset = int(resp.headers.get("x-rate-limit-reset", time.time() + 15))
                time.sleep(max(reset - int(time.time()), 5))
                resp = oauth.get(
                    f"{base}/media/upload",
                    params={"media_id": media_id},
                    timeout=90,
                )
            if not resp.ok:
                last_error = f"HTTP {resp.status_code}: {resp.text[:300]}"
                continue
            state = _processing_state(resp.json())
            if state in (None, "succeeded"):
                return
            if state == "failed":
                raise RuntimeError(f"Video processing failed: {resp.text[:400]}")
            wait = (resp.json().get("data") or {}).get("processing_info", {}).get(
                "check_after_secs", 5
            )
            logger.info("Video processing %s for media_id=%s, wait %ss", state, media_id, wait)
            time.sleep(max(int(wait), 2))
            break
        else:
            raise RuntimeError(f"Media status failed: {last_error}")
    raise RuntimeError(f"Video processing timeout for media_id={media_id}")


def upload_video(oauth: OAuth1Session, path: Path, *, logger: logging.Logger) -> str:
    """Chunked upload (INIT → APPEND → FINALIZE → poll)."""
    data = path.read_bytes()
    total_bytes = len(data)
    if total_bytes == 0:
        raise RuntimeError(f"Video file is empty: {path}")

    init_body = {
        "media_type": "video/mp4",
        "total_bytes": total_bytes,
        "media_category": "tweet_video",
    }
    last_error: str | None = None
    media_id: str | None = None
    for base in MEDIA_API_BASES:
        resp = oauth.post(f"{base}/media/upload/initialize", json=init_body, timeout=90)
        if resp.status_code == 429:
            reset = int(resp.headers.get("x-rate-limit-reset", time.time() + 30))
            time.sleep(max(reset - int(time.time()), 5))
            resp = oauth.post(f"{base}/media/upload/initialize", json=init_body, timeout=90)
        if resp.ok:
            media_id = _parse_media_id(resp.json())
            break
        last_error = f"INIT {base}: HTTP {resp.status_code} — {resp.text[:400]}"
    if not media_id:
        raise RuntimeError(last_error or "Video INIT failed")

    logger.info("Video INIT ok: media_id=%s (%d bytes)", media_id, total_bytes)

    segment = 0
    for offset in range(0, total_bytes, CHUNK_SIZE):
        chunk = data[offset : offset + CHUNK_SIZE]
        appended = False
        for base in MEDIA_API_BASES:
            resp = oauth.post(
                f"{base}/media/upload/{media_id}/append",
                data={"segment_index": str(segment)},
                files={"media": chunk},
                timeout=180,
            )
            if resp.status_code == 429:
                reset = int(resp.headers.get("x-rate-limit-reset", time.time() + 30))
                time.sleep(max(reset - int(time.time()), 5))
                resp = oauth.post(
                    f"{base}/media/upload/{media_id}/append",
                    data={"segment_index": str(segment)},
                    files={"media": chunk},
                    timeout=180,
                )
            if resp.ok or resp.status_code == 204:
                appended = True
                break
            last_error = f"APPEND {base} seg={segment}: HTTP {resp.status_code} — {resp.text[:300]}"
        if not appended:
            raise RuntimeError(last_error or f"Video APPEND failed at segment {segment}")
        segment += 1

    finalized = False
    finalize_payload: dict | None = None
    for base in MEDIA_API_BASES:
        resp = oauth.post(f"{base}/media/upload/{media_id}/finalize", timeout=90)
        if resp.status_code == 429:
            reset = int(resp.headers.get("x-rate-limit-reset", time.time() + 30))
            time.sleep(max(reset - int(time.time()), 5))
            resp = oauth.post(f"{base}/media/upload/{media_id}/finalize", timeout=90)
        if resp.ok:
            finalize_payload = resp.json()
            finalized = True
            break
        last_error = f"FINALIZE {base}: HTTP {resp.status_code} — {resp.text[:400]}"
    if not finalized:
        raise RuntimeError(last_error or "Video FINALIZE failed")

    state = _processing_state(finalize_payload or {})
    if state and state != "succeeded":
        _wait_for_processing(oauth, media_id, logger=logger)

    logger.info("Video upload complete: media_id=%s", media_id)
    return media_id


def clear_video_cache(set_meta: Callable[[str, str], None]) -> None:
    set_meta(META_MEDIA_ID, "")
    set_meta(META_UPLOADED_AT, "")


def ensure_video_media_id(
    config: Config,
    oauth: OAuth1Session,
    get_meta: Callable[[str], str | None],
    set_meta: Callable[[str, str], None],
    *,
    logger: logging.Logger,
    force: bool = False,
) -> list[str] | None:
    if not config.video_path or not config.video_path.is_file():
        return None

    if not force:
        media_id = get_meta(META_MEDIA_ID)
        uploaded_at = get_meta(META_UPLOADED_AT)
        if media_id and uploaded_at:
            try:
                ts = datetime.fromisoformat(uploaded_at)
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                age = datetime.now(timezone.utc) - ts
                if age < CACHE_TTL:
                    logger.info(
                        "Reuse cached video media_id=%s (uploaded %s ago)",
                        media_id,
                        age,
                    )
                    return [media_id]
            except ValueError:
                pass

    logger.info("Uploading daily video: %s", config.video_path.name)
    media_id = upload_video(oauth, config.video_path, logger=logger)
    set_meta(META_MEDIA_ID, media_id)
    set_meta(META_UPLOADED_AT, datetime.now(timezone.utc).isoformat())
    return [media_id]


def post_mention_with_video(
    oauth: OAuth1Session,
    text: str,
    config: Config,
    get_meta: Callable[[str], str | None],
    set_meta: Callable[[str, str], None],
    *,
    logger: logging.Logger,
) -> str:
    media_ids = ensure_video_media_id(
        config, oauth, get_meta, set_meta, logger=logger
    )
    try:
        return post_mention(oauth, text, media_ids=media_ids)
    except RuntimeError as exc:
        if not media_ids or _is_post_forbidden(exc):
            raise
        logger.warning("Post with video failed (%s), re-uploading video", exc)
        clear_video_cache(set_meta)
        media_ids = ensure_video_media_id(
            config, oauth, get_meta, set_meta, logger=logger, force=True
        )
        return post_mention(oauth, text, media_ids=media_ids)


def _is_post_forbidden(exc: RuntimeError) -> bool:
    msg = str(exc).lower()
    return "http 403" in msg or "not permitted" in msg
