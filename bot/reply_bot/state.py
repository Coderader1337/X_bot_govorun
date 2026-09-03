"""SQLite для reply-bot: очередь ответов под одним parent tweet."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path


@dataclass(frozen=True)
class ReplyLead:
    tweet_id: str
    author_username: str
    tweet_text: str
    created_at: str
    x_url: str
    author_location: str = ""
    found_at: str = ""


@dataclass(frozen=True)
class SearchSlice:
    index: int
    total: int
    from_date: str
    to_date: str
    from_dt: str = ""
    to_dt: str = ""


def _parse_created_at(raw: str) -> datetime:
    """Парсит created_at твита X API (с миллисекундами или без)."""
    raw = raw.strip()
    for fmt in ("%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ"):
        try:
            return datetime.strptime(raw, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    raise ValueError(f"Не удалось распарсить дату: {raw!r}")


class ReplyBotStore:
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
                CREATE TABLE IF NOT EXISTS reply_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS reply_queue (
                    tweet_id TEXT PRIMARY KEY,
                    author_username TEXT NOT NULL,
                    tweet_text TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    status TEXT NOT NULL,
                    found_at TEXT NOT NULL,
                    posted_tweet_id TEXT,
                    posted_at TEXT,
                    error TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_reply_status_created
                    ON reply_queue(status, created_at);
                """
            )
            cols = {row[1] for row in conn.execute("PRAGMA table_info(reply_queue)")}
            if "author_location" not in cols:
                conn.execute(
                    "ALTER TABLE reply_queue ADD COLUMN author_location TEXT DEFAULT ''"
                )

    def get_meta(self, key: str) -> str | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT value FROM reply_meta WHERE key = ?", (key,)
            ).fetchone()
            return row["value"] if row else None

    def set_meta(self, key: str, value: str) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO reply_meta(key, value) VALUES(?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                (key, value),
            )

    def is_known(self, tweet_id: str) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM reply_queue WHERE tweet_id = ?", (tweet_id,)
            ).fetchone()
            return row is not None

    def is_username_taken(self, username: str, *, exclude_tweet_id: str | None = None) -> bool:
        handle = username.strip().lstrip("@").lower()
        if not handle:
            return True
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT tweet_id, author_username FROM reply_queue
                WHERE status IN ('queued', 'posted', 'failed')
                """
            ).fetchall()
        for row in rows:
            if exclude_tweet_id and str(row["tweet_id"]) == exclude_tweet_id:
                continue
            if (row["author_username"] or "").strip().lstrip("@").lower() == handle:
                return True
        return False

    def get_search_slice(
        self, parent_created_at: str, slice_hours: int = 4
    ) -> SearchSlice:
        """Текущий слайс от времени parent-твита до сейчас.

        Слайсы нарезаются фиксированными окнами по slice_hours часов,
        привязанными к моменту публикации parent-твита (а не к границам
        календарных суток) — это важно, чтобы окно index=0 всегда начиналось
        ровно с parent_created_at, и чтобы более мелкие окна (по умолчанию
        4 часа) не давали X API search/all "захлебнуться" пагинацией в дни
        пиковой активности (see: слайс за 7 июля терял ~70% результатов при
        суточных окнах — курсор next_token X API ненадёжен на плотных окнах).
        """
        parent_dt = _parse_created_at(parent_created_at)
        now = datetime.now(timezone.utc)
        parent_dt = min(parent_dt, now)
        total_seconds = max(1.0, (now - parent_dt).total_seconds())
        slice_seconds = max(1, slice_hours) * 3600
        total = max(1, int((total_seconds + slice_seconds - 1) // slice_seconds))
        idx = int(self.get_meta("search_slice_index") or "0")
        idx = min(max(idx, 0), total - 1)
        from_dt = parent_dt + timedelta(seconds=idx * slice_seconds)
        to_dt = min(from_dt + timedelta(seconds=slice_seconds), now)
        return SearchSlice(
            index=idx,
            total=total,
            from_date=from_dt.strftime("%Y-%m-%d %H:%M"),
            to_date=to_dt.strftime("%Y-%m-%d %H:%M"),
            from_dt=from_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
            to_dt=to_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
        )

    def advance_search_slice(
        self, parent_created_at: str, slice_hours: int = 4
    ) -> SearchSlice:
        """Переход к следующему слайсу. Сбрасывает пагинацию предыдущего."""
        current = self.get_search_slice(parent_created_at, slice_hours)
        next_idx = current.index + 1
        if next_idx >= current.total:
            return current
        self.set_meta("search_slice_index", str(next_idx))
        self.set_search_next_token(None)
        self.set_search_resume_before(None)
        self.set_meta("search_page_num", "1")
        return self.get_search_slice(parent_created_at, slice_hours)

    def reset_search_slice(self) -> None:
        """Сброс слайсов в начало (при перегенерации keywords)."""
        self.set_meta("search_slice_index", "0")
        self.set_search_next_token(None)
        self.set_search_resume_before(None)
        self.set_meta("search_page_num", "1")

    def get_search_next_token(self) -> str | None:
        return self.get_meta("search_next_token")

    def set_search_next_token(self, token: str | None) -> None:
        if token:
            self.set_meta("search_next_token", token)
        else:
            with self._connect() as conn:
                conn.execute("DELETE FROM reply_meta WHERE key = 'search_next_token'")

    def get_search_resume_before(self) -> str | None:
        return self.get_meta("search_resume_before")

    def set_search_resume_before(self, value: str | None) -> None:
        """Watermark самого старого уже найденного твита в текущем слайсе.

        Используется, если next_token потерян (например, слайс был
        ошибочно помечен исчерпанным при сбое API): позволяет продолжить
        поиск с этой временной точки, не перечитывая уже собранные страницы.
        """
        if value:
            self.set_meta("search_resume_before", value)
        else:
            with self._connect() as conn:
                conn.execute(
                    "DELETE FROM reply_meta WHERE key = 'search_resume_before'"
                )

    def oldest_known_created_at(self, from_date: str, to_date: str) -> str | None:
        """Самый старый created_at среди уже известных твитов в диапазоне.

        from_date/to_date могут быть как датой (YYYY-MM-DD), так и полным
        ISO-моментом (YYYY-MM-DDTHH:MM:SS) — сравнение идёт по первым 19
        символам created_at, что покрывает оба случая (миллисекунды/"Z"
        в конце created_at игнорируются).
        """
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT MIN(created_at) AS m FROM reply_queue
                WHERE substr(created_at, 1, length(?)) BETWEEN ? AND ?
                """,
                (from_date, from_date, to_date),
            ).fetchone()
            return row["m"] if row and row["m"] else None

    def get_all_tweet_ids(self) -> set[str]:
        with self._connect() as conn:
            rows = conn.execute("SELECT tweet_id FROM reply_queue").fetchall()
        return {str(r["tweet_id"]) for r in rows}

    def enqueue_many(self, leads: list[ReplyLead]) -> int:
        now = datetime.now(timezone.utc).isoformat()
        added = 0
        known = self.get_all_tweet_ids()
        with self._connect() as conn:
            for lead in leads:
                if lead.tweet_id in known:
                    continue
                conn.execute(
                    """
                    INSERT INTO reply_queue (
                        tweet_id, author_username, tweet_text, created_at,
                        author_location, status, found_at
                    ) VALUES (?, ?, ?, ?, ?, 'queued', ?)
                    """,
                    (
                        lead.tweet_id,
                        lead.author_username,
                        lead.tweet_text,
                        lead.created_at,
                        lead.author_location or "",
                        lead.found_at or now,
                    ),
                )
                known.add(lead.tweet_id)
                added += 1
        return added

    def queue_count(self) -> int:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS cnt FROM reply_queue WHERE status = 'queued'"
            ).fetchone()
            return int(row["cnt"])

    def mark_blocked_lead(self, lead: ReplyLead, reason: str) -> None:
        self._mark_terminal_lead(lead, "blocked", reason)

    def mark_rejected_lead(self, lead: ReplyLead, reason: str) -> None:
        self._mark_terminal_lead(lead, "rejected", reason)

    def _mark_terminal_lead(self, lead: ReplyLead, status: str, reason: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.execute(
                f"""
                INSERT INTO reply_queue (
                    tweet_id, author_username, tweet_text, created_at,
                    author_location, status, found_at, error
                ) VALUES (?, ?, ?, ?, ?, '{status}', ?, ?)
                ON CONFLICT(tweet_id) DO UPDATE SET
                    status = '{status}', error = excluded.error
                """,
                (
                    lead.tweet_id,
                    lead.author_username,
                    lead.tweet_text,
                    lead.created_at,
                    lead.author_location or "",
                    now,
                    reason[:200],
                ),
            )

    def pop_next_queued(self) -> ReplyLead | None:
        with self._connect() as conn:
            while True:
                row = conn.execute(
                    """
                    SELECT * FROM reply_queue
                    WHERE status = 'queued'
                    ORDER BY created_at ASC, tweet_id ASC
                    LIMIT 1
                    """
                ).fetchone()
                if not row:
                    return None
                keys = row.keys()
                lead = ReplyLead(
                    tweet_id=str(row["tweet_id"]),
                    author_username=row["author_username"] or "",
                    tweet_text=row["tweet_text"] or "",
                    created_at=row["created_at"] or "",
                    x_url=f"https://x.com/i/status/{row['tweet_id']}",
                    author_location=(
                        row["author_location"] or ""
                        if "author_location" in keys
                        else ""
                    ),
                )
                if self.is_username_taken(
                    lead.author_username, exclude_tweet_id=lead.tweet_id
                ):
                    conn.execute(
                        """
                        UPDATE reply_queue
                        SET status = 'duplicate_user',
                            error = 'duplicate @username'
                        WHERE tweet_id = ?
                        """,
                        (lead.tweet_id,),
                    )
                    continue
                return lead

    def mark_posted(self, tweet_id: str, posted_tweet_id: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE reply_queue
                SET status = 'posted', posted_tweet_id = ?, posted_at = ?
                WHERE tweet_id = ?
                """,
                (posted_tweet_id, now, tweet_id),
            )

    def mark_failed(self, tweet_id: str, error: str) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE reply_queue SET status = 'failed', error = ?
                WHERE tweet_id = ?
                """,
                (error[:500], tweet_id),
            )

    def requeue(self, tweet_id: str) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE reply_queue SET status = 'queued', error = NULL
                WHERE tweet_id = ?
                """,
                (tweet_id,),
            )

    def posts_last_24h(self) -> int:
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT COUNT(*) AS cnt FROM reply_queue
                WHERE status = 'posted' AND posted_at >= ?
                """,
                (cutoff,),
            ).fetchone()
            return int(row["cnt"])

    def stats(self) -> dict[str, int]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT status, COUNT(*) AS cnt FROM reply_queue GROUP BY status"
            ).fetchall()
            return {str(r["status"]): int(r["cnt"]) for r in rows}
