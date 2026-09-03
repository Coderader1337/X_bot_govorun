"""Cycle: queue-first → hybrid discovery → 1 post max."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from bot.config import Config
from bot.discovery import search_leads
from bot.search_queries import build_query, pattern_count
from bot.state import Lead, StateStore
from bot.x_client import enrich_lead, oauth_session
from bot.video_media import post_mention_with_video

_cycle_counter = 0


def _verify_and_enqueue(
    config: Config,
    store: StateStore,
    candidates: list[Lead],
    log: logging.Logger,
) -> int:
    """X API verify при пополнении очереди. Возвращает число добавленных."""
    added = 0
    skipped = 0
    for lead in candidates:
        if store.is_known(lead.tweet_id):
            skipped += 1
            continue
        enriched = enrich_lead(config, lead, logger=log)
        if not enriched:
            store.record_lead(lead, status="invalid", error="tweet not found")
            skipped += 1
            continue
        if store.is_username_taken(enriched.author_username):
            store.record_lead(
                enriched,
                status="duplicate_user",
                error="duplicate @username — skip queue",
            )
            skipped += 1
            log.info(
                "Пропуск дубликата @%s (%s) — тот же текст поста",
                enriched.author_username,
                enriched.tweet_id,
            )
            continue
        store.enqueue(enriched)
        added += 1
        log.info(
            "В очередь: @%s %s — %s",
            enriched.author_username,
            enriched.tweet_id,
            enriched.lead_reason[:80],
        )
    if skipped:
        log.info("Verify: добавлено %d, пропущено %d", added, skipped)
    return added


def _refill_queue_if_needed(
    config: Config,
    store: StateStore,
    queue_size: int,
    at_daily_limit: bool,
    *,
    dry_run: bool,
    log: logging.Logger,
) -> tuple[int, int]:
    """Hybrid discovery + verify. Возвращает (found, added)."""
    if at_daily_limit:
        if config.needs_refill(queue_size):
            log.info(
                "Очередь низкая (%d), но лимит постов — discovery пропущен",
                queue_size,
            )
        return 0, 0

    if not config.needs_refill(queue_size):
        if config.grok_only_when_queue_empty:
            log.info(
                "Discovery пропущен — в очереди %d лид(ов), сначала постим буфер",
                queue_size,
            )
        else:
            log.info(
                "Очередь=%d > порог %d — discovery не вызываем",
                queue_size,
                config.queue_refill_threshold,
            )
        return 0, 0

    slice_info = store.get_search_slice(
        config.search_max_days,
        config.search_slice_days,
    )
    q_idx = store.get_query_index()
    pattern_id, _ = build_query(q_idx)
    log.info(
        "Hybrid discovery: slice %d/%d (%s…%s) pattern %d/%d (%s) page=%s",
        slice_info.index + 1,
        slice_info.total,
        slice_info.from_date,
        slice_info.to_date,
        q_idx + 1,
        pattern_count(),
        pattern_id,
        "next" if store.get_search_next_token() else "1",
    )

    try:
        candidates = search_leads(
            config,
            store,
            logger=log,
            queue_size=queue_size,
        )
    except Exception as exc:
        log.warning(
            "Discovery error (%s) — постинг из очереди продолжится",
            exc,
        )
        return 0, 0

    if not candidates:
        log.info("Discovery: квалифицированных лидов нет в этом цикле")
        return 0, 0

    if dry_run:
        for lead in candidates:
            log.info(
                "[DRY-RUN candidate] @%s %s — %s",
                lead.author_username,
                lead.tweet_id,
                lead.lead_reason[:80],
            )
        return len(candidates), 0

    added = _verify_and_enqueue(config, store, candidates, log)
    return len(candidates), added


def run_cycle(
    config: Config,
    *,
    dry_run: bool = False,
    logger: logging.Logger | None = None,
) -> dict[str, int]:
    global _cycle_counter
    _cycle_counter += 1

    log = logger or logging.getLogger("movetorussia_bot")
    store = StateStore(config.state_db_path)
    started = datetime.now(timezone.utc).isoformat()

    posts_24h = store.posts_last_24h()
    queue_size = store.queue_count()
    slice_info = store.get_search_slice(
        config.search_max_days,
        config.search_slice_days,
    )

    log.info(
        "=== Цикл #%d (%s) dry_run=%s | queue=%d | posts_24h=%d/%d | slice=%d/%d (%s…%s) ===",
        _cycle_counter,
        started,
        dry_run,
        queue_size,
        posts_24h,
        config.daily_post_limit,
        slice_info.index + 1,
        slice_info.total,
        slice_info.from_date,
        slice_info.to_date,
    )

    summary = {"found": 0, "queued": 0, "posted": 0, "failed": 0, "skipped": 0}

    at_daily_limit = posts_24h >= config.daily_post_limit
    if at_daily_limit:
        log.warning(
            "Дневной лимит постов (%d/%d) — постинг пауза, очередь сохраняется",
            posts_24h,
            config.daily_post_limit,
        )

    found, added = _refill_queue_if_needed(
        config,
        store,
        queue_size,
        at_daily_limit,
        dry_run=dry_run,
        log=log,
    )
    summary["found"] = found
    summary["queued"] = added

    # --- Постинг: строго 1 за цикл (независимо от discovery) ---
    lead = store.pop_next_queued()
    if not lead:
        log.info("Нечего постить — очередь пуста")
        _log_finish(log, _cycle_counter, summary, store, config)
        return summary

    if at_daily_limit:
        store.enqueue(lead)
        log.info("Лид @%s оставлен в очереди — дневной лимит", lead.author_username)
        _log_finish(log, _cycle_counter, summary, store, config)
        return summary

    log.info(
        "Постинг 1/1 @%s (source %s): %s",
        lead.author_username,
        lead.tweet_id,
        lead.lead_reason,
    )

    if dry_run:
        log.info(
            "[DRY-RUN post] @%s | %s | %s",
            lead.author_username,
            lead.tweet_id,
            lead.tweet_text[:120].replace("\n", " "),
        )
        _log_finish(log, _cycle_counter, summary, store, config)
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
        store.record_lead(lead, status="posted", posted_tweet_id=posted_id)
        summary["posted"] = 1
        log.info(
            "OK posted https://x.com/i/status/%s -> lead %s",
            posted_id,
            lead.x_url,
        )
    except Exception as exc:
        store.record_lead(lead, status="failed", error=str(exc))
        summary["failed"] += 1
        log.error(
            "Постинг %s (@%s) не удался — статус failed, в очередь не возвращаем: %s",
            lead.tweet_id,
            lead.author_username,
            exc,
        )

    _log_finish(log, _cycle_counter, summary, store, config)
    return summary


def _log_finish(
    log: logging.Logger,
    cycle: int,
    summary: dict[str, int],
    store: StateStore,
    config: Config,
) -> None:
    q = store.queue_count()
    runway = q  # 1 пост/цикл ≈ q циклов запаса
    stats = store.stats()
    log.info(
        "=== Цикл #%d: posted=%d queued+%d queue=%d (~%d cycles runway) "
        "failed=%d posts_24h=%d/%d | stats=%s ===",
        cycle,
        summary["posted"],
        summary["queued"],
        q,
        runway,
        summary["failed"],
        store.posts_last_24h(),
        config.daily_post_limit,
        stats,
    )
