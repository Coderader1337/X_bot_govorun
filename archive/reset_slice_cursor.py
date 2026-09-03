#!/usr/bin/env python3
"""Сброс курсора слайсов на первую непокрытую дату (после простоя / 402)."""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta, timezone

from bot.config import load_config
from bot.state import StateStore


def _slice_for_date(
    target: datetime,
    *,
    now: datetime,
    max_days: int,
    slice_days: int,
) -> tuple[int, int, str, str]:
    """Индекс слайса (0-based), total, from_date, to_date для target."""
    total = max(1, (max_days + slice_days - 1) // slice_days)
    for idx in range(total):
        older = max_days - idx * slice_days
        newer = max(max_days - (idx + 1) * slice_days, 0)
        from_dt = now - timedelta(days=older)
        to_dt = now - timedelta(days=newer)
        if from_dt.date() <= target.date() <= to_dt.date():
            return (
                idx,
                total,
                from_dt.strftime("%Y-%m-%d"),
                to_dt.strftime("%Y-%m-%d"),
            )
    return total - 1, total, "", ""


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Сброс search_slice_index на первую непокрытую дату",
    )
    parser.add_argument(
        "--from-date",
        default="2026-06-18",
        help="Первая дата пропуска (YYYY-MM-DD), по умолчанию 2026-06-18",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Только показать, без записи в БД",
    )
    args = parser.parse_args()

    config = load_config()
    store = StateStore(config.state_db_path)
    now = datetime.now(timezone.utc)
    target = datetime.strptime(args.from_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)

    old_idx = store.get_meta("search_slice_index") or "0"
    old_q = store.get_meta("search_query_index") or "0"
    old_token = store.get_meta("search_next_token")

    idx, total, from_d, to_d = _slice_for_date(
        target,
        now=now,
        max_days=config.search_max_days,
        slice_days=config.search_slice_days,
    )

    cur = store.get_search_slice(config.search_max_days, config.search_slice_days)
    print(f"Сейчас: slice {cur.index + 1}/{cur.total} ({cur.from_date}…{cur.to_date}), "
          f"pattern {store.get_query_index() + 1}, next_token={'yes' if old_token else 'no'}")
    print(f"Цель:   slice {idx + 1}/{total} ({from_d}…{to_d}) — покрывает {args.from_date}")
    print(f"Будет:  search_slice_index={idx}, search_query_index=0, next_token=cleared")

    if args.dry_run:
        return

    store.set_meta("search_slice_index", str(idx))
    store.set_meta("search_query_index", "0")
    store.set_search_next_token(None)
    store.set_meta("empty_search_streak", "0")

    new = store.get_search_slice(config.search_max_days, config.search_slice_days)
    print(f"Готово: slice {new.index + 1}/{new.total} ({new.from_date}…{new.to_date}), pattern 1")
    print(f"Было:   slice_index={old_idx}, query_index={old_q}")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"Ошибка: {exc}", file=sys.stderr)
        sys.exit(1)
