"""Hybrid discovery: X API (primary) → DeepSeek (qualify) → Grok (fallback)."""

from __future__ import annotations

import logging

from bot.config import Config
from bot.deepseek_qualify import qualify_leads
from bot.grok_search import search_leads as grok_search_leads
from bot.state import Lead, StateStore
from bot.x_api_search import fetch_candidates


def search_leads(
    config: Config,
    store: StateStore,
    *,
    logger: logging.Logger,
    queue_size: int = 0,
) -> list[Lead]:
    """Один цикл discovery: страница X API + DeepSeek; Grok если слайс исчерпан."""
    try:
        raw, slice_exhausted = fetch_candidates(config, store, logger=logger)
    except Exception as exc:
        logger.warning("X API search error: %s", exc)
        raw, slice_exhausted = [], False

    if raw:
        result = qualify_leads(config, raw, logger=logger)

        for cand in result.rejected:
            store.record_lead(cand, status="rejected", error="deepseek rejected")

        if result.approved:
            store.record_search_result(len(result.approved))
            return result.approved

        store.record_search_result(0)
        return []

    if slice_exhausted and config.grok_fallback_enabled:
        logger.info("X API слайс исчерпан — fallback Grok (семантика)")
        try:
            grok_leads = grok_search_leads(
                config,
                store,
                logger=logger,
                queue_size=queue_size,
                fallback=True,
            )
            if grok_leads:
                store.record_search_result(len(grok_leads))
                store.finish_slice(
                    config.search_max_days,
                    config.search_slice_days,
                    logger=logger,
                )
                return grok_leads
        except Exception as exc:
            logger.warning("Grok fallback error: %s", exc)
        store.finish_slice(
            config.search_max_days,
            config.search_slice_days,
            logger=logger,
        )

    store.record_search_result(0)
    return []
