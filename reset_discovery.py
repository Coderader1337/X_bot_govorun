#!/usr/bin/env python3
"""Сброс ошибочных rejected и пагинации X API (после сбоя DeepSeek)."""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

from bot.config import load_config


def main() -> None:
    config = load_config()
    db = config.state_db_path
    if not db.exists():
        print(f"БД не найдена: {db}", file=sys.stderr)
        sys.exit(1)

    conn = sqlite3.connect(db)
    cur = conn.execute(
        "DELETE FROM processed_tweets WHERE status = ? AND error = ?",
        ("rejected", "deepseek rejected"),
    )
    deleted = cur.rowcount
    conn.execute("DELETE FROM bot_meta WHERE key = ?", ("search_next_token",))
    conn.commit()
    conn.close()

    print(f"Удалено rejected: {deleted}")
    print("search_next_token сброшен — следующий цикл повторит страницу X API")


if __name__ == "__main__":
    main()
