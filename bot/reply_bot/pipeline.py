"""Цикл reply-bot: discovery под parent tweet → очередь → 1 пост."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from bot.config import Config, DEFAULT_POST_BLOCKED_PAUSE_HOURS
from bot.reply_bot.antiblock import is_blocked
from bot.reply_bot.keywords import generate_reply_keywords
from bot.reply_bot.qualify import qualify_reply_leads
from bot.reply_bot.region import filter_by_region
from bot.reply_bot.search import discover_all_replies
from bot.reply_bot.state import ReplyBotStore, ReplyLead
from bot.x_client import fetch_tweet, oauth_session
from bot.video_media import post_mention_with_video

_cycle_counter = 0
POST_BLOCKED_META = "post_blocked_until"


def _is_post_blocked(store: ReplyBotStore) -> bool:
    raw = store.get_meta(POST_BLOCKED_META)
    if not raw:
        return False
    try:
        until = datetime.fromisoformat(raw)
        if until.tzinfo is None:
            until = until.replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc) < until
    except ValueError:
        return False


def _set_post_blocked(
    store: ReplyBotStore,
    hours: int = DEFAULT_POST_BLOCKED_PAUSE_HOURS,
) -> None:
    until = datetime.now(timezone.utc) + timedelta(hours=hours)
    store.set_meta(POST_BLOCKED_META, until.isoformat())


def _is_x_post_forbidden(exc: Exception) -> bool:
    msg = str(exc).lower()
    return "http 403" in msg or "not permitted" in msg


@dataclass(frozen=True)
class ParentTweet:
    tweet_id: str
    conversation_id: str
    text: str
    created_at: str
    author_username: str


def reply_bot_db_path(config: Config, parent_tweet_id: str) -> Path:
    return config.data_dir / f"reply_bot_{parent_tweet_id}.db"


def load_parent(config: Config, tweet_id: str, *, logger: logging.Logger) -> ParentTweet:
    data = fetch_tweet(config, tweet_id)
    if not data:
        raise RuntimeError(f"Твит {tweet_id} не найден")

    conv_id = str(data.get("conversation_id") or tweet_id)
    text = data.get("text") or ""
    created = data.get("created_at") or ""
    author = data.get("author_username") or ""
    if not text or not created:
        raise RuntimeError(f"У твита {tweet_id} нет text/created_at")

    logger.info(
        "Parent @%s %s | conv=%s | %s",
        author,
        tweet_id,
        conv_id,
        created,
    )
    return ParentTweet(
        tweet_id=tweet_id,
        conversation_id=conv_id,
        text=text,
        created_at=created,
        author_username=author,
    )


def init_session(
    config: Config,
    parent: ParentTweet,
    store: ReplyBotStore,
    *,
    logger: logging.Logger,
    force_keywords: bool = False,
) -> str:
    """DeepSeek keywords один раз на сессию. Возвращает keyword query.

    Если БД продолжает работу с прежнего parent-твита (--db-id != --tweet-id),
    закешированные keywords/слайсы принадлежат другому посту — сравниваем
    parent_tweet_id из меты, чтобы не искать ответы на старый твит по чужому
    query и с чужого места в таймлайне.
    """
    cached = store.get_meta("keyword_query")
    cached_parent_id = store.get_meta("parent_tweet_id") or ""
    parent_changed = bool(cached_parent_id) and cached_parent_id != parent.tweet_id
    if cached and not force_keywords and not parent_changed:
        logger.info("Keywords (cache): %s", cached[:160])
        return cached

    if parent_changed:
        logger.info(
            "Parent сменился (%s -> %s) — перегенерация keywords и сброс discovery",
            cached_parent_id,
            parent.tweet_id,
        )

    result = generate_reply_keywords(config, parent.text, logger=logger)
    store.set_meta("keyword_query", result.query)
    store.set_meta("keyword_reason", result.reason)
    store.set_meta("parent_tweet_id", parent.tweet_id)
    store.set_meta("parent_username", parent.author_username)
    store.set_meta("conversation_id", parent.conversation_id)
    store.set_meta("parent_created_at", parent.created_at)
    store.set_meta("parent_text", parent.text)
    if force_keywords or parent_changed:
        store.set_meta("discovery_complete", "")
        store.reset_search_slice()
        logger.info("Discovery сброшен — повторный поиск с новыми keywords")
    return result.query


def _seen_ids(store: ReplyBotStore) -> set[str]:
    return store.get_all_tweet_ids()


def _filter_and_qualify_leads(
    config: Config,
    store: ReplyBotStore,
    leads: list[ReplyLead],
    *,
    logger: logging.Logger,
) -> list[ReplyLead]:
    """Antiblock → geo → DeepSeek. Отклонённые пишем в БД, чтобы не ловить снова."""
    parent_text = store.get_meta("parent_text") or ""

    antiblock_rejected: list[ReplyLead] = []
    after_antiblock: list[ReplyLead] = []
    for lead in leads:
        blocked, reason = is_blocked(lead.tweet_text)
        if blocked:
            antiblock_rejected.append(lead)
            store.mark_blocked_lead(lead, reason)
        else:
            after_antiblock.append(lead)
    if antiblock_rejected:
        logger.info("Antiblock: отсеяно %d до очереди", len(antiblock_rejected))

    geo_ok, geo_rejected = filter_by_region(after_antiblock)
    for lead, reason in geo_rejected:
        store.mark_rejected_lead(lead, reason)
        logger.info(
            "Geo reject @%s %s [%s]: %s — %s",
            lead.author_username,
            lead.tweet_id,
            lead.author_location or "—",
            reason,
            lead.tweet_text[:60].replace("\n", " "),
        )
    if geo_rejected:
        logger.info("Geo: отсеяно %d", len(geo_rejected))

    if not geo_ok:
        return []

    result = qualify_reply_leads(config, geo_ok, parent_text, logger=logger)
    for lead in result.rejected:
        store.mark_rejected_lead(lead, "deepseek reject")
    return result.approved


def run_discovery_page(
    config: Config,
    store: ReplyBotStore,
    *,
    logger: logging.Logger,
) -> tuple[int, bool]:
    """Одна страница discovery внутри текущего слайса. Возвращает (добавлено, discovery_complete)."""
    conv_id = store.get_meta("conversation_id") or ""
    keyword_query = store.get_meta("keyword_query") or ""
    parent_created = store.get_meta("parent_created_at") or ""
    slice_info = store.get_search_slice(parent_created, slice_hours=4)

    if store.get_meta("discovery_complete") == "1":
        # total пересчитывается от текущего времени каждый вызов — если с
        # момента завершения появились новые (ещё не пройденные) слайсы,
        # снимаем флаг и продолжаем оттуда, где остановились.
        if slice_info.index + 1 >= slice_info.total:
            return 0, True
        store.set_meta("discovery_complete", "")
        logger.info(
            "Появились новые слайсы (%d/%d) — возобновляем discovery",
            slice_info.index + 1,
            slice_info.total,
        )

    next_token = store.get_search_next_token()
    seen = _seen_ids(store)

    resume_before = store.get_search_resume_before()
    if not next_token and not resume_before:
        candidate = store.oldest_known_created_at(slice_info.from_dt, slice_info.to_dt)
        if candidate:
            store.set_search_resume_before(candidate)
            resume_before = candidate
            logger.info("Resume: продолжаем с %s (уже собранного ранее)", candidate)

    logger.info(
        "Reply discovery: slice %d/%d (%s…%s) page=%s",
        slice_info.index + 1,
        slice_info.total,
        slice_info.from_date,
        slice_info.to_date,
        "next" if next_token else "1",
    )

    parent_tweet_id = store.get_meta("parent_tweet_id") or ""
    parent_username = store.get_meta("parent_username") or ""
    leads, page_next = discover_all_replies(
        config,
        parent_tweet_id=parent_tweet_id,
        parent_username=parent_username,
        conversation_id=conv_id,
        keyword_query=keyword_query,
        parent_created_at=parent_created,
        slice_info=slice_info,
        logger=logger,
        seen=seen,
        start_next_token=next_token,
        resume_before=resume_before,
    )

    if page_next:
        store.set_search_next_token(page_next)
    else:
        store.set_search_next_token(None)
        if slice_info.index + 1 >= slice_info.total:
            store.set_meta("discovery_complete", "1")
            logger.info("Discovery завершён — все слайсы пройдены")
        else:
            next_slice = store.advance_search_slice(parent_created, slice_hours=4)
            logger.info(
                "Слайс %d/%d исчерпан — переход к %d/%d (%s…%s)",
                slice_info.index + 1,
                slice_info.total,
                next_slice.index + 1,
                next_slice.total,
                next_slice.from_date,
                next_slice.to_date,
            )

    approved = _filter_and_qualify_leads(config, store, leads, logger=logger)
    added = store.enqueue_many(approved)
    if added:
        logger.info("В очередь +%d (всего queued=%d)", added, store.queue_count())
    return added, store.get_meta("discovery_complete") == "1"


def discover_all_blocking(
    config: Config,
    store: ReplyBotStore,
    *,
    logger: logging.Logger,
) -> int:
    """Выгрести все страницы сразу (до постинга)."""
    total = 0
    while store.get_meta("discovery_complete") != "1":
        added, done = run_discovery_page(config, store, logger=logger)
        total += added
        if done:
            break
    return total


def run_cycle(
    config: Config,
    store: ReplyBotStore,
    *,
    dry_run: bool = False,
    logger: logging.Logger | None = None,
) -> dict[str, int]:
    global _cycle_counter
    _cycle_counter += 1

    log = logger or logging.getLogger("movetorussia_reply_bot")
    summary = {"discovered": 0, "posted": 0, "failed": 0}

    posts_24h = store.posts_last_24h()
    queue_size = store.queue_count()
    at_limit = posts_24h >= config.daily_post_limit

    parent_created = store.get_meta("parent_created_at") or ""
    slice_info = (
        store.get_search_slice(parent_created, slice_hours=4)
        if parent_created
        else None
    )
    slice_text = (
        f"{slice_info.index + 1}/{slice_info.total} ({slice_info.from_date}…{slice_info.to_date})"
        if slice_info
        else "—"
    )
    log.info(
        "=== Reply цикл #%d | queue=%d | discovery=%s | slice=%s | posts_24h=%d/%d ===",
        _cycle_counter,
        queue_size,
        "done" if store.get_meta("discovery_complete") == "1" else "in_progress",
        slice_text,
        posts_24h,
        config.daily_post_limit,
    )

    if queue_size == 0 and store.get_meta("discovery_complete") != "1":
        added, _ = run_discovery_page(config, store, logger=log)
        summary["discovered"] = added
        queue_size = store.queue_count()
    elif queue_size > 0 and store.get_meta("discovery_complete") != "1":
        log.info("Discovery отложен — в очереди %d лидов", queue_size)

    lead = store.pop_next_queued()
    if not lead:
        log.info("Нечего постить — очередь пуста")
        log.info("=== Reply цикл #%d: stats=%s ===", _cycle_counter, store.stats())
        return summary

    if at_limit:
        store.requeue(lead.tweet_id)
        log.warning("Дневной лимит %d — лид в очереди", config.daily_post_limit)
        return summary

    if _is_post_blocked(store):
        blocked_until = store.get_meta(POST_BLOCKED_META)
        store.requeue(lead.tweet_id)
        log.warning("X постинг на паузе до %s — лид в очереди", blocked_until)
        return summary

    log.info(
        "Постинг @%s (%s) [%s]: %s",
        lead.author_username,
        lead.tweet_id,
        lead.created_at,
        lead.tweet_text[:100].replace("\n", " "),
    )

    if dry_run:
        log.info("[DRY-RUN] @%s", lead.author_username)
        store.requeue(lead.tweet_id)
        return summary

    oauth = oauth_session(config)
    post_text = config.build_post_text(lead.author_username)
    try:
        posted_id = post_mention_with_video(
            oauth,
            post_text,
            config,
            store.get_meta,
            store.set_meta,
            logger=log,
        )
        store.mark_posted(lead.tweet_id, posted_id)
        summary["posted"] = 1
        log.info(
            "OK posted https://x.com/i/status/%s -> reply %s",
            posted_id,
            lead.x_url,
        )
    except Exception as exc:
        if _is_x_post_forbidden(exc):
            _set_post_blocked(store)
            store.requeue(lead.tweet_id)
            log.error(
                "X 403 — пауза постинга до %s, лид %s в очереди: %s",
                store.get_meta(POST_BLOCKED_META),
                lead.tweet_id,
                exc,
            )
        else:
            store.mark_failed(lead.tweet_id, str(exc))
            summary["failed"] = 1
            log.error("Постинг %s failed: %s", lead.tweet_id, exc)

    log.info(
        "=== Reply цикл #%d: posted=%d queue=%d stats=%s ===",
        _cycle_counter,
        summary["posted"],
        store.queue_count(),
        store.stats(),
    )
    return summary
