#!/usr/bin/env python3
"""
MoveToRussia bot — 1 пост за цикл, очередь, лимит 49 постов / rolling 24h.

Daemon (интервал ~29 мин ≈ 49 постов/сутки):
  python run_bot.py

Ручной один цикл:
  python run_bot.py --once

Тест без постинга и без Grok-записи в БД:
  python run_bot.py --once --dry-run
"""

from __future__ import annotations

import argparse
import sys
import time

from bot.config import load_config
from bot.logging_setup import setup_logging
from bot.pipeline import run_cycle
from bot.search_queries import pattern_count


def main() -> None:
    parser = argparse.ArgumentParser(description="MoveToRussia autonomous X bot")
    parser.add_argument(
        "--once",
        action="store_true",
        help="Один цикл и выход",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Без постинга; при пустой очереди Grok всё равно вызывается",
    )
    args = parser.parse_args()

    try:
        config = load_config()
    except RuntimeError as exc:
        print(f"Ошибка конфигурации: {exc}", file=sys.stderr)
        sys.exit(1)

    logger = setup_logging(config.log_file)
    logger.info(
        "Бот | hybrid | interval=%d min | ocean=%dd×%dd | %d patterns | "
        "x_page=%d | grok_fallback=%s | daily=%d",
        config.interval_minutes,
        config.search_max_days,
        config.search_slice_days,
        pattern_count(),
        config.x_search_page_size,
        config.grok_fallback_enabled,
        config.daily_post_limit,
    )

    if args.once:
        run_cycle(config, dry_run=args.dry_run, logger=logger)
        return

    logger.info(
        "Daemon: 1 пост/цикл | X API+DeepSeek | Grok fallback=%s",
        config.grok_fallback_enabled,
    )
    while True:
        try:
            run_cycle(config, dry_run=False, logger=logger)
        except Exception as exc:
            logger.exception("Необработанная ошибка цикла: %s", exc)
        logger.info("Сон %d сек (~%d мин)", config.interval_seconds, config.interval_minutes)
        time.sleep(config.interval_seconds)


if __name__ == "__main__":
    main()
