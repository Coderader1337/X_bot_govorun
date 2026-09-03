#!/usr/bin/env python3
"""
Одноразовый бэкафилл пропущенного окна 7 июля для reply-bot по твиту
2074139935297138972.

Почему: суточное окно (7 июля, 6601 совпадений по keyword-запросу) было
слишком плотным для курсора next_token X API search/all — пагинация
оборвалась после ~20 страниц (~1956 твитов из 6601), собрав только ~30%.
Разбираем день на 2-часовые окна (макс. ~1140 твитов/окно по факту
почасовой статистики) — с большим запасом ниже точки обрыва курсора.

Дедупликация: твиты, уже есть в БД (is_known), не отправляются повторно
на DeepSeek/antiblock/geo — только новые проходят полный пайплайн
качественной фильтрации. X API всё равно должен вернуть страницу целиком
(это неизбежные "прочитанные" твиты), но повторной траты DeepSeek-бюджета
не будет.

Запуск (внутри контейнера, чтобы был доступ к .env и сети):
    docker compose run --rm reply-bot python backfill_july7.py
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from bot.config import load_config
from bot.reply_bot.pipeline import _filter_and_qualify_leads, reply_bot_db_path
from bot.reply_bot.search import discover_all_replies
from bot.reply_bot.state import ReplyBotStore, SearchSlice

TWEET_ID = "2074139935297138972"
DAY_START = datetime(2026, 7, 7, 0, 0, 0, tzinfo=timezone.utc)
DAY_END = datetime(2026, 7, 8, 0, 0, 0, tzinfo=timezone.utc)
WINDOW_HOURS = 2


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    logger = logging.getLogger("backfill_july7")

    config = load_config()
    db_path = reply_bot_db_path(config, TWEET_ID)
    store = ReplyBotStore(db_path)

    conv_id = store.get_meta("conversation_id") or ""
    keyword_query = store.get_meta("keyword_query") or ""
    parent_created = store.get_meta("parent_created_at") or ""
    parent_tweet_id = store.get_meta("parent_tweet_id") or ""
    parent_username = store.get_meta("parent_username") or ""

    if not (conv_id and keyword_query and parent_created):
        raise RuntimeError("В БД нет meta parent-сессии — сначала запусти обычный run_reply_bot.py")

    before_stats = store.stats()
    logger.info("До бэкафилла: %s", before_stats)

    total_added = 0
    total_raw = 0
    cursor = DAY_START
    window_num = 0
    total_windows = int((DAY_END - DAY_START).total_seconds() // (WINDOW_HOURS * 3600))

    while cursor < DAY_END:
        window_num += 1
        window_end = min(cursor + timedelta(hours=WINDOW_HOURS), DAY_END)
        logger.info(
            "=== Окно %d/%d: %s … %s ===",
            window_num,
            total_windows,
            cursor.isoformat(),
            window_end.isoformat(),
        )

        # Кастомный "слайс" ровно на это 2-часовое окно. index=0/total=1
        # безопасны здесь: они лишь означают "последний слайс" для
        # _slice_datetimes (доп. капа на now-15s, не влияет — окно в прошлом).
        fake_slice = SearchSlice(
            index=0,
            total=1,
            from_date=cursor.strftime("%Y-%m-%d %H:%M"),
            to_date=window_end.strftime("%Y-%m-%d %H:%M"),
            from_dt=cursor.strftime("%Y-%m-%dT%H:%M:%SZ"),
            to_dt=window_end.strftime("%Y-%m-%dT%H:%M:%SZ"),
        )

        next_token: str | None = None
        page_num = 0
        while True:
            page_num += 1
            seen = store.get_all_tweet_ids()
            leads, next_token = discover_all_replies(
                config,
                parent_tweet_id=parent_tweet_id,
                parent_username=parent_username,
                conversation_id=conv_id,
                keyword_query=keyword_query,
                parent_created_at=parent_created,
                slice_info=fake_slice,
                logger=logger,
                seen=seen,
                start_next_token=next_token,
                resume_before=None,
            )
            total_raw += len(leads)

            # Пропускаем уже известные твиты до DeepSeek/antiblock/geo —
            # экономим DeepSeek-бюджет на дубликатах.
            new_leads = [l for l in leads if not store.is_known(l.tweet_id)]
            if new_leads:
                approved = _filter_and_qualify_leads(config, store, new_leads, logger=logger)
                added = store.enqueue_many(approved)
                total_added += added
                if added:
                    logger.info("  страница %d: +%d новых в очередь", page_num, added)

            if not next_token:
                logger.info("  окно исчерпано (%d страниц)", page_num)
                break

        cursor = window_end

    after_stats = store.stats()
    logger.info("Готово. Всего прочитано твитов (сырых, с дублями): %d", total_raw)
    logger.info("Добавлено новых лидов в очередь: %d", total_added)
    logger.info("Stats до: %s", before_stats)
    logger.info("Stats после: %s", after_stats)


if __name__ == "__main__":
    main()
