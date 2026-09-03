"""Replay reply-bot filter pipeline on existing leads.

Reads leads from a source reply-bot SQLite DB, applies the same filters the
bot uses (antiblock → geo → DeepSeek), and writes the results to a separate
DB. Does NOT fetch new comments from X.
"""

from __future__ import annotations

import argparse
import json
import logging
import sqlite3
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

# Allow `from bot...` imports when the script is run from any cwd.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from bot.config import Config, DATA_DIR, ROOT_DIR, load_config
from bot.reply_bot.antiblock import is_blocked
from bot.reply_bot.qualify import qualify_reply_leads
from bot.reply_bot.region import filter_by_region
from bot.reply_bot.state import ReplyLead


@dataclass(frozen=True)
class FilterResult:
    lead: ReplyLead
    source_status: str
    stage: str
    reason: str
    deepseek_score: str = ""
    deepseek_reason: str = ""


class FilteredLeadsStore:
    """Separate SQLite store for replayed filter results."""

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
                CREATE TABLE IF NOT EXISTS filter_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS filtered_leads (
                    tweet_id TEXT PRIMARY KEY,
                    author_username TEXT NOT NULL,
                    tweet_text TEXT NOT NULL,
                    author_location TEXT,
                    created_at TEXT NOT NULL,
                    found_at TEXT NOT NULL,
                    source_status TEXT NOT NULL,
                    filter_stage TEXT NOT NULL,
                    filter_reason TEXT,
                    deepseek_score TEXT,
                    deepseek_reason TEXT,
                    processed_at TEXT NOT NULL,
                    parent_tweet_id TEXT,
                    parent_text TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_filtered_stage
                    ON filtered_leads(filter_stage);
                CREATE INDEX IF NOT EXISTS idx_filtered_source
                    ON filtered_leads(source_status);
                """
            )

    def set_meta(self, key: str, value: str) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO filter_meta(key, value) VALUES(?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                (key, value),
            )

    def save_results(
        self,
        results: list[FilterResult],
        parent_tweet_id: str,
        parent_text: str,
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            for r in results:
                conn.execute(
                    """
                    INSERT INTO filtered_leads (
                        tweet_id, author_username, tweet_text, author_location,
                        created_at, found_at, source_status, filter_stage,
                        filter_reason, deepseek_score, deepseek_reason,
                        processed_at, parent_tweet_id, parent_text
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(tweet_id) DO UPDATE SET
                        author_username = excluded.author_username,
                        tweet_text = excluded.tweet_text,
                        author_location = excluded.author_location,
                        created_at = excluded.created_at,
                        found_at = excluded.found_at,
                        source_status = excluded.source_status,
                        filter_stage = excluded.filter_stage,
                        filter_reason = excluded.filter_reason,
                        deepseek_score = excluded.deepseek_score,
                        deepseek_reason = excluded.deepseek_reason,
                        processed_at = excluded.processed_at,
                        parent_tweet_id = excluded.parent_tweet_id,
                        parent_text = excluded.parent_text
                    """,
                    (
                        r.lead.tweet_id,
                        r.lead.author_username,
                        r.lead.tweet_text,
                        r.lead.author_location or "",
                        r.lead.created_at,
                        r.lead.found_at,
                        r.source_status,
                        r.stage,
                        r.reason,
                        r.deepseek_score,
                        r.deepseek_reason,
                        now,
                        parent_tweet_id,
                        parent_text,
                    ),
                )


def load_source_leads(
    db_path: Path,
    status: str | None = None,
) -> list[tuple[ReplyLead, str]]:
    """Return (lead, source_status) tuples from the source reply-bot DB."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    if status and status != "all":
        rows = conn.execute(
            """
            SELECT * FROM reply_queue
            WHERE status = ?
            ORDER BY created_at ASC, tweet_id ASC
            """,
            (status,),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT * FROM reply_queue
            ORDER BY created_at ASC, tweet_id ASC
            """
        ).fetchall()

    leads: list[tuple[ReplyLead, str]] = []
    for row in rows:
        lead = ReplyLead(
            tweet_id=str(row["tweet_id"]),
            author_username=row["author_username"] or "",
            tweet_text=row["tweet_text"] or "",
            created_at=row["created_at"] or "",
            x_url=f"https://x.com/i/status/{row['tweet_id']}",
            author_location=row["author_location"] or "",
            found_at=row["found_at"] or "",
        )
        leads.append((lead, row["status"] or "unknown"))
    return leads


def load_parent_tweet_id_from_source(db_path: Path) -> str | None:
    """Try to read parent_tweet_id from the source reply_meta table."""
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT value FROM reply_meta WHERE key = 'parent_tweet_id'"
        ).fetchone()
        return row["value"] if row else None
    except Exception:
        return None


def load_parent_info(parent_tweet_id: str | None = None) -> tuple[str, str, str]:
    """Return (parent_tweet_id, parent_text, parent_username) from parent_tweet.json."""
    parent_file = ROOT_DIR / "parent_tweet.json"
    if parent_file.exists():
        data = json.loads(parent_file.read_text(encoding="utf-8"))
        tweet_data = data.get("data", {})
        text = tweet_data.get("text", "")
        tid = tweet_data.get("id") or parent_tweet_id or "unknown"
        includes = data.get("includes", {})
        users = includes.get("users", []) if isinstance(includes, dict) else []
        author_id = tweet_data.get("author_id")
        parent_username = ""
        if author_id:
            for user in users:
                if user.get("id") == author_id:
                    parent_username = user.get("username", "")
                    break
        return tid, text, parent_username
    return parent_tweet_id or "unknown", "", ""


def load_parent_text(parent_tweet_id: str | None = None) -> tuple[str, str]:
    """Backwards-compatible helper: return (parent_tweet_id, parent_text)."""
    tid, text, _ = load_parent_info(parent_tweet_id)
    return tid, text


def _mentions_parent(text: str, parent_username: str | None) -> bool:
    if not parent_username:
        return True
    handle = parent_username.lstrip("@").lower()
    return f"@{handle}" in text.lower()


def run_filter_pipeline(
    config: Config,
    leads_with_status: list[tuple[ReplyLead, str]],
    parent_text: str,
    *,
    parent_username: str | None = None,
    skip_deepseek: bool = False,
    logger: logging.Logger,
) -> list[FilterResult]:
    # Apply the same filter chain as the bot: parent-mention -> antiblock -> geo -> DeepSeek.
    results: list[FilterResult] = []

    # 0. Verify the tweet is actually a reply to the parent author.
    after_parent_check: list[tuple[ReplyLead, str]] = []
    for lead, source_status in leads_with_status:
        if not _mentions_parent(lead.tweet_text, parent_username):
            results.append(
                FilterResult(lead, source_status, "not_parent_reply", "missing @parent")
            )
            continue
        after_parent_check.append((lead, source_status))

    logger.info(
        "Parent mention check: %d passed, %d rejected",
        len(after_parent_check),
        len(results),
    )

    # 1. Antiblock
    after_antiblock: list[tuple[ReplyLead, str]] = []
    for lead, source_status in after_parent_check:
        blocked, reason = is_blocked(lead.tweet_text)
        if blocked:
            results.append(
                FilterResult(lead, source_status, "antiblock_rejected", reason)
            )
            continue
        after_antiblock.append((lead, source_status))

    logger.info("Antiblock: %d passed, %d rejected", len(after_antiblock), len(results))

    # 2. Geo
    after_geo: list[tuple[ReplyLead, str]] = []
    geo_leads = [lead for lead, _ in after_antiblock]
    geo_ok, geo_rejected = filter_by_region(geo_leads)
    geo_ok_ids = {lead.tweet_id for lead in geo_ok}
    geo_reasons = {lead.tweet_id: reason for lead, reason in geo_rejected}

    for lead, source_status in after_antiblock:
        if lead.tweet_id in geo_ok_ids:
            after_geo.append((lead, source_status))
        else:
            reason = geo_reasons.get(lead.tweet_id, "geo reject")
            results.append(FilterResult(lead, source_status, "geo_rejected", reason))

    logger.info("Geo: %d passed, %d rejected", len(after_geo), len(geo_rejected))

    # 3. DeepSeek
    if skip_deepseek:
        for lead, source_status in after_geo:
            results.append(
                FilterResult(lead, source_status, "deepseek_skipped", "skipped by user")
            )
        return results

    if not after_geo:
        return results

    batch = [lead for lead, _ in after_geo]
    qualify_result = qualify_reply_leads(config, batch, parent_text, logger=logger)
    approved_ids = {lead.tweet_id for lead in qualify_result.approved}

    for lead, source_status in after_geo:
        if lead.tweet_id in approved_ids:
            results.append(
                FilterResult(lead, source_status, "deepseek_approved", "qualified")
            )
        else:
            results.append(
                FilterResult(lead, source_status, "deepseek_rejected", "deepseek reject")
            )

    return results


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Replay reply-bot filter pipeline on existing leads and save to a separate DB."
    )
    parser.add_argument(
        "--source-db",
        type=Path,
        default=DATA_DIR / "reply_bot_2074139935297138972.db",
        help="Source reply-bot SQLite DB",
    )
    parser.add_argument(
        "--target-db",
        type=Path,
        default=DATA_DIR / "filtered_leads.db",
        help="Target separate DB for filtered leads",
    )
    parser.add_argument(
        "--status",
        choices=["queued", "posted", "rejected", "blocked", "all"],
        default="queued",
        help="Which source status to filter (default: queued)",
    )
    parser.add_argument(
        "--skip-deepseek",
        action="store_true",
        help="Skip DeepSeek qualification (run antiblock + geo only)",
    )
    parser.add_argument(
        "--parent-text",
        type=str,
        default=None,
        help="Parent tweet text (default: read from parent_tweet.json)",
    )
    parser.add_argument(
        "--parent-username",
        type=str,
        default=None,
        help="Parent author username, e.g. Rusia_HD (default: read from parent_tweet.json)",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    logger = logging.getLogger("filter_existing_leads")

    config = load_config()

    logger.info("Loading %s leads from %s", args.status, args.source_db)
    leads_with_status = load_source_leads(args.source_db, args.status)
    logger.info("Loaded %d leads", len(leads_with_status))

    if not leads_with_status:
        logger.warning("No leads found, nothing to do")
        return 0

    parent_tweet_id = load_parent_tweet_id_from_source(args.source_db)
    if args.parent_text:
        parent_text = args.parent_text
        parent_tweet_id = parent_tweet_id or "unknown"
        parent_username = args.parent_username or ""
    else:
        parent_tweet_id, parent_text, parent_username = load_parent_info(parent_tweet_id)

    if args.parent_username:
        parent_username = args.parent_username

    if not parent_text:
        logger.warning(
            "Parent text is empty — DeepSeek qualification may be inaccurate. "
            "Use --parent-text to provide it explicitly."
        )

    if not parent_username:
        logger.warning(
            "Parent username is empty — skipping parent-mention pre-filter. "
            "Use --parent-username to provide it explicitly."
        )

    logger.info("Running filter pipeline on %d leads", len(leads_with_status))
    results = run_filter_pipeline(
        config,
        leads_with_status,
        parent_text,
        parent_username=parent_username,
        skip_deepseek=args.skip_deepseek,
        logger=logger,
    )

    store = FilteredLeadsStore(args.target_db)
    store.set_meta("source_db", str(args.source_db))
    store.set_meta("source_status", args.status)
    store.set_meta("processed_at", datetime.now(timezone.utc).isoformat())
    store.set_meta("parent_tweet_id", parent_tweet_id or "")
    store.set_meta("skip_deepseek", "1" if args.skip_deepseek else "0")
    store.save_results(results, parent_tweet_id or "", parent_text)

    stats: dict[str, int] = {}
    for r in results:
        stats[r.stage] = stats.get(r.stage, 0) + 1

    logger.info("Done. Results saved to %s", args.target_db)
    for stage, count in sorted(stats.items()):
        logger.info("  %s: %d", stage, count)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
