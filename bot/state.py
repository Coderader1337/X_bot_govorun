"""SQLite storage: since_id, queue, daily post counter."""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from bot.search_queries import build_query


@dataclass(frozen=True)
class SearchSlice:
    index: int
    total: int
    from_date: str
    to_date: str


@dataclass(frozen=True)
class Lead:
    tweet_id: str
    author_username: str
    tweet_text: str
    lead_reason: str
    is_reply: bool
    x_url: str


def _row_to_lead(row: sqlite3.Row) -> Lead:
    return Lead(
        tweet_id=str(row["tweet_id"]),
        author_username=row["author_username"] or "",
        tweet_text=row["tweet_text"] or "",
        lead_reason=row["lead_reason"] or "",
        is_reply=bool(row["is_reply"]),
        x_url=f"https://x.com/i/status/{row['tweet_id']}",
    )


class StateStore:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS bot_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS processed_tweets (
                    tweet_id TEXT PRIMARY KEY,
                    author_username TEXT,
                    tweet_text TEXT,
                    lead_reason TEXT,
                    is_reply INTEGER DEFAULT 0,
                    status TEXT NOT NULL,
                    found_at TEXT NOT NULL,
                    posted_tweet_id TEXT,
                    posted_at TEXT,
                    error TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_processed_status
                    ON processed_tweets(status);
                CREATE INDEX IF NOT EXISTS idx_processed_posted_at
                    ON processed_tweets(posted_at);
                """
            )

    def get_since_id(self) -> str | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT value FROM bot_meta WHERE key = 'since_id'"
            ).fetchone()
            return row["value"] if row else None

    def get_meta(self, key: str) -> str | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT value FROM bot_meta WHERE key = ?", (key,)
            ).fetchone()
            return row["value"] if row else None

    def set_meta(self, key: str, value: str) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO bot_meta(key, value) VALUES(?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                (key, value),
            )

    def set_since_id(self, tweet_id: str) -> None:
        current = self.get_since_id()
        if current and tweet_id <= current:
            return
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO bot_meta(key, value) VALUES('since_id', ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                (tweet_id,),
            )

    def is_known(self, tweet_id: str) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM processed_tweets WHERE tweet_id = ?",
                (tweet_id,),
            ).fetchone()
            return row is not None

    @staticmethod
    def _norm_user(username: str) -> str:
        return username.strip().lstrip("@").lower()

    def is_username_taken(self, username: str, *, exclude_tweet_id: str | None = None) -> bool:
        """
        Один @username — один outreach-текст. Повтор блокирует X (duplicate content).
        """
        handle = self._norm_user(username)
        if not handle:
            return True
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT tweet_id, author_username FROM processed_tweets
                WHERE status IN ('queued', 'posted', 'failed', 'duplicate_user')
                """
            ).fetchall()
        for row in rows:
            if exclude_tweet_id and str(row["tweet_id"]) == exclude_tweet_id:
                continue
            if self._norm_user(row["author_username"] or "") == handle:
                return True
        return False

    def get_recent_excluded_ids(self, limit: int) -> list[str]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT tweet_id FROM processed_tweets
                ORDER BY tweet_id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
            return [str(r["tweet_id"]) for r in rows]

    def get_archive_cursor(self) -> str | None:
        """Legacy — для логов совместимости."""
        return self.get_meta("archive_cursor")

    def get_search_slice(self, max_days: int, slice_days: int) -> SearchSlice:
        """Текущий временной слайс: с дна (старые дни) к поверхности."""
        total = max(1, (max_days + slice_days - 1) // slice_days)
        idx = int(self.get_meta("search_slice_index") or "0") % total
        now = datetime.now(timezone.utc)
        older_days_ago = max_days - idx * slice_days
        newer_days_ago = max(max_days - (idx + 1) * slice_days, 0)
        from_dt = now - timedelta(days=older_days_ago)
        to_dt = now - timedelta(days=newer_days_ago)
        return SearchSlice(
            index=idx,
            total=total,
            from_date=from_dt.strftime("%Y-%m-%d"),
            to_date=to_dt.strftime("%Y-%m-%d"),
        )

    def get_query_index(self) -> int:
        raw = self.get_meta("search_query_index")
        return int(raw) if raw and raw.isdigit() else 0

    def get_search_next_token(self) -> str | None:
        return self.get_meta("search_next_token")

    def set_search_next_token(self, token: str | None) -> None:
        if token:
            self.set_meta("search_next_token", token)
        else:
            with self._connect() as conn:
                conn.execute("DELETE FROM bot_meta WHERE key = 'search_next_token'")

    def advance_query_or_finish_slice(
        self,
        max_days: int,
        slice_days: int,
        total_patterns: int,
        *,
        logger: logging.Logger | None = None,
    ) -> bool:
        """
        После пустой страницы: следующий паттерн ИЛИ слайс исчерпан (без смены слайса).
        Возвращает True если все паттерны слайса пройдены — нужен Grok, потом finish_slice().
        """
        q_idx = self.get_query_index()
        next_q = q_idx + 1
        if next_q < total_patterns:
            self.set_meta("search_query_index", str(next_q))
            self.set_meta("search_page_num", "1")
            if logger:
                pid, _ = build_query(next_q)
                logger.info(
                    "Паттерн %d/%d пуст — следующий: %s",
                    q_idx + 1,
                    total_patterns,
                    pid,
                )
            return False
        if logger:
            logger.info(
                "Все %d паттернов слайса пройдены — semantic fallback",
                total_patterns,
            )
        return True

    def finish_slice(self, max_days: int, slice_days: int, *, logger: logging.Logger | None = None) -> None:
        """После Grok fallback: сброс паттернов и переход к следующему слайсу."""
        self.set_meta("search_query_index", "0")
        self.set_meta("search_page_num", "1")
        self.set_search_next_token(None)
        self.advance_search_slice(max_days, slice_days, logger=logger)

    def advance_search_slice(
        self,
        max_days: int,
        slice_days: int,
        *,
        logger: logging.Logger | None = None,
    ) -> int:
        """Следующий временной слайс. Возвращает новый index."""
        total = max(1, (max_days + slice_days - 1) // slice_days)
        idx = int(self.get_meta("search_slice_index") or "0") % total
        next_idx = (idx + 1) % total
        self.set_meta("search_slice_index", str(next_idx))
        if next_idx == 0:
            passes = int(self.get_meta("search_pass_count") or "0") + 1
            self.set_meta("search_pass_count", str(passes))
            if logger:
                logger.info("Завершён проход #%d по всем %d слайсам", passes, total)
        elif logger:
            nxt = self.get_search_slice(max_days, slice_days)
            logger.info(
                "Слайс пуст — переход %d/%d → %d/%d (%s … %s)",
                idx + 1,
                total,
                next_idx + 1,
                total,
                nxt.from_date,
                nxt.to_date,
            )
        return next_idx

    def get_empty_search_streak(self) -> int:
        raw = self.get_meta("empty_search_streak")
        return int(raw) if raw and raw.isdigit() else 0

    def record_search_result(self, found: int) -> None:
        if found > 0:
            self.set_meta("empty_search_streak", "0")
            return
        streak = self.get_empty_search_streak() + 1
        self.set_meta("empty_search_streak", str(streak))

    def queue_count(self) -> int:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS cnt FROM processed_tweets WHERE status = 'queued'"
            ).fetchone()
            return int(row["cnt"])

    def pop_next_queued(self) -> Lead | None:
        """FIFO; пропускает дубликаты @username (помечает duplicate_user)."""
        with self._connect() as conn:
            while True:
                row = conn.execute(
                    """
                    SELECT * FROM processed_tweets
                    WHERE status = 'queued'
                    ORDER BY found_at ASC, tweet_id ASC
                    LIMIT 1
                    """
                ).fetchone()
                if not row:
                    return None
                lead = _row_to_lead(row)
                if self.is_username_taken(
                    lead.author_username, exclude_tweet_id=lead.tweet_id
                ):
                    conn.execute(
                        """
                        UPDATE processed_tweets
                        SET status = 'duplicate_user',
                            error = 'duplicate @username — identical post text'
                        WHERE tweet_id = ?
                        """,
                        (lead.tweet_id,),
                    )
                    continue
                return lead

    def posts_last_24h(self) -> int:
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT COUNT(*) AS cnt FROM processed_tweets
                WHERE status = 'posted' AND posted_at IS NOT NULL AND posted_at >= ?
                """,
                (cutoff,),
            ).fetchone()
            return int(row["cnt"])

    def record_lead(
        self,
        lead: Lead,
        *,
        status: str,
        posted_tweet_id: str | None = None,
        error: str | None = None,
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO processed_tweets (
                    tweet_id, author_username, tweet_text, lead_reason,
                    is_reply, status, found_at, posted_tweet_id, posted_at, error
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(tweet_id) DO UPDATE SET
                    author_username = excluded.author_username,
                    tweet_text = excluded.tweet_text,
                    lead_reason = excluded.lead_reason,
                    status = excluded.status,
                    posted_tweet_id = COALESCE(excluded.posted_tweet_id, posted_tweet_id),
                    posted_at = COALESCE(excluded.posted_at, posted_at),
                    error = excluded.error
                """,
                (
                    lead.tweet_id,
                    lead.author_username,
                    lead.tweet_text,
                    lead.lead_reason,
                    1 if lead.is_reply else 0,
                    status,
                    now,
                    posted_tweet_id,
                    now if posted_tweet_id else None,
                    error,
                ),
            )
        if status in ("queued", "posted"):
            self.set_since_id(lead.tweet_id)

    def enqueue(self, lead: Lead) -> None:
        self.record_lead(lead, status="queued")

    def stats(self) -> dict[str, int]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT status, COUNT(*) AS cnt FROM processed_tweets GROUP BY status"
            ).fetchall()
            return {str(r["status"]): int(r["cnt"]) for r in rows}
