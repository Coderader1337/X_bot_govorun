#!/usr/bin/env python3
"""
Reply-bot — ищет ответы под конкретным твитом и постит на них.

Запуск:
  python run_reply_bot.py --tweet-id 2072326012344414403

Сначала выгрести все ответы, потом постить по 1/цикл:
  python run_reply_bot.py --tweet-id 123 --discover-first

Один цикл (1 страница discovery + 1 пост):
  python run_reply_bot.py --tweet-id 123 --once

Без постинга:
  python run_reply_bot.py --tweet-id 123 --discover-first --dry-run
"""

from __future__ import annotations

import argparse
import sys
import time

from bot.config import load_config
from bot.logging_setup import setup_logging
from bot.reply_bot.pipeline import (
    discover_all_blocking,
    init_session,
    load_parent,
    reply_bot_db_path,
    run_cycle,
)
from bot.reply_bot.state import ReplyBotStore


def main() -> None:
    parser = argparse.ArgumentParser(
        description="MoveToRussia reply-bot — ответы под одним твитом",
    )
    parser.add_argument(
        "--tweet-id",
        required=True,
        help="ID parent-твита, под которым ищем ответы",
    )
    parser.add_argument(
        "--db-id",
        default=None,
        help=(
            "ID для имени файла БД (data/reply_bot_<db-id>.db). "
            "По умолчанию = --tweet-id. Задайте старый tweet-id здесь, "
            "чтобы продолжить работу с прежней БД (история, дедуп @username, "
            "posts_24h) при переключении на новый parent-твит."
        ),
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Один цикл и выход",
    )
    parser.add_argument(
        "--discover-first",
        action="store_true",
        help="Сначала выгрести все страницы ответов, потом daemon-постинг",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Без постинга",
    )
    parser.add_argument(
        "--refresh-keywords",
        action="store_true",
        help="Перегенерировать ключевые слова DeepSeek",
    )
    args = parser.parse_args()

    tweet_id = args.tweet_id.strip()
    if not tweet_id.isdigit():
        print("tweet-id должен быть числом", file=sys.stderr)
        sys.exit(1)

    try:
        config = load_config()
    except RuntimeError as exc:
        print(f"Ошибка конфигурации: {exc}", file=sys.stderr)
        sys.exit(1)

    log_path = config.log_file.parent / f"reply_bot_{tweet_id}.log"
    logger = setup_logging(log_path)

    parent = load_parent(config, tweet_id, logger=logger)
    db_id = (args.db_id or tweet_id).strip()
    db_path = reply_bot_db_path(config, db_id)
    store = ReplyBotStore(db_path)
    if db_id != tweet_id:
        logger.info("БД продолжается от id=%s (файл %s)", db_id, db_path.name)

    keyword_query = init_session(
        config,
        parent,
        store,
        logger=logger,
        force_keywords=args.refresh_keywords,
    )
    logger.info(
        "Reply-bot | parent=%s | conv=%s | keywords=%s | limit=%d/day | mean pause %.1f min | jitter ±%.0f%%",
        tweet_id,
        parent.conversation_id,
        keyword_query[:100],
        config.daily_post_limit,
        config.mean_post_interval_seconds / 60,
        config.interval_jitter_fraction * 100,
    )

    if args.discover_first and store.get_meta("discovery_complete") != "1":
        if store.queue_count() > 0:
            logger.info(
                "Discovery-first пропущен — в очереди %d лидов, сначала постинг",
                store.queue_count(),
            )
        else:
            logger.info("Полный discovery всех страниц…")
            total = discover_all_blocking(config, store, logger=logger)
            logger.info("Discovery: добавлено %d, queued=%d", total, store.queue_count())

    if args.once:
        run_cycle(config, store, dry_run=args.dry_run, logger=logger)
        return

    if args.discover_first:
        logger.info("Daemon: постинг 1/цикл, oldest→newest")
    else:
        logger.info("Daemon: постинг 1/цикл; discovery только когда очередь пуста")

    while True:
        try:
            run_cycle(config, store, dry_run=args.dry_run, logger=logger)
        except Exception as exc:
            logger.exception("Необработанная ошибка: %s", exc)
        sleep_s = config.reply_cycle_sleep_seconds()
        logger.info(
            "Сон %d сек (~%.1f min, mean %.1f min)",
            sleep_s,
            sleep_s / 60,
            config.mean_post_interval_seconds / 60,
        )
        time.sleep(sleep_s)


if __name__ == "__main__":
    main()
