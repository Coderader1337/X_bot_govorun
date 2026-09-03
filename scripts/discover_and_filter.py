"""Discover and filter replies under a parent tweet.

Reads comments under a parent post exactly like the bot does (X API search +
antiblock + geo + DeepSeek), saves results to a separate SQLite DB and CSV.
Stops once the target number of qualified leads is reached.
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from bot.config import Config, DATA_DIR, ROOT_DIR, load_config
from bot.reply_bot.antiblock import is_blocked
from bot.reply_bot.keywords import generate_reply_keywords
from bot.reply_bot.pipeline import ParentTweet, _filter_and_qualify_leads
from bot.reply_bot.region import filter_by_region
from bot.reply_bot.search import discover_all_replies
from bot.reply_bot.state import ReplyBotStore, SearchSlice


def load_parent_from_json(tweet_id: str) -> ParentTweet | None:
    """Load parent tweet from the cached parent_tweet.json file."""
    parent_file = ROOT_DIR / "parent_tweet.json"
    if not parent_file.exists():
        return None
    data = json.loads(parent_file.read_text(encoding="utf-8"))
    tweet_data = data.get("data", {})
    if tweet_data.get("id") != tweet_id:
        return None
    includes = data.get("includes", {})
    users = includes.get("users", []) if isinstance(includes, dict) else []
    author_id = tweet_data.get("author_id")
    author_username = ""
    if author_id:
        for user in users:
            if user.get("id") == author_id:
                author_username = user.get("username", "")
                break
    return ParentTweet(
        tweet_id=tweet_id,
        conversation_id=str(tweet_data.get("conversation_id") or tweet_id),
        text=tweet_data.get("text") or "",
        created_at=tweet_data.get("created_at") or "",
        author_username=author_username,
    )


def _init_keywords(
    config: Config,
    parent: ParentTweet,
    store: ReplyBotStore,
    *,
    logger: logging.Logger,
) -> str:
    """Generate or reuse keyword query for the session."""
    cached = store.get_meta("keyword_query")
    if cached:
        logger.info("Keywords (cache): %s", cached[:160])
        return cached

    result = generate_reply_keywords(config, parent.text, logger=logger)
    store.set_meta("keyword_query", result.query)
    store.set_meta("keyword_reason", result.reason)
    store.set_meta("parent_tweet_id", parent.tweet_id)
    store.set_meta("conversation_id", parent.conversation_id)
    store.set_meta("parent_created_at", parent.created_at)
    store.set_meta("parent_text", parent.text)
    return result.query


def _apply_light_filters(leads: list) -> list:
    """Antiblock + geo only, no DeepSeek."""
    after_antiblock = []
    for lead in leads:
        blocked, _ = is_blocked(lead.tweet_text)
        if not blocked:
            after_antiblock.append(lead)
    geo_ok, _ = filter_by_region(after_antiblock)
    return geo_ok


def export_queued_to_csv(store: ReplyBotStore, csv_path: Path) -> int:
    """Export queued leads from the store to CSV."""
    conn = sqlite3.connect(store.db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """
        SELECT tweet_id, author_username, tweet_text, author_location,
               created_at, found_at
        FROM reply_queue
        WHERE status = 'queued'
        ORDER BY created_at ASC, tweet_id ASC
        """
    ).fetchall()

    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f, delimiter=";")
        writer.writerow([
            "tweet_id", "author_username", "tweet_text", "author_location",
            "created_at", "found_at",
        ])
        for row in rows:
            writer.writerow([
                row["tweet_id"],
                row["author_username"],
                row["tweet_text"],
                row["author_location"] or "",
                row["created_at"],
                row["found_at"],
            ])
    return len(rows)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Discover and filter replies under a parent tweet."
    )
    parser.add_argument(
        "--parent-tweet-id",
        default="2074139935297138972",
        help="Parent tweet ID to search replies under",
    )
    parser.add_argument(
        "--target-count",
        type=int,
        default=90,
        help="Stop once this many qualified leads are queued",
    )
    parser.add_argument(
        "--db-path",
        type=Path,
        default=DATA_DIR / "discover_filter.db",
        help="SQLite DB to store discovered and filtered leads",
    )
    parser.add_argument(
        "--csv-path",
        type=Path,
        default=ROOT_DIR / "discovered_leads.csv",
        help="Output CSV path",
    )
    parser.add_argument(
        "--skip-deepseek",
        action="store_true",
        help="Skip DeepSeek qualification (antiblock + geo only)",
    )
    parser.add_argument(
        "--slice-days",
        type=int,
        default=1,
        help="Days per search slice",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    logger = logging.getLogger("discover_and_filter")

    config = load_config()

    parent = load_parent_from_json(args.parent_tweet_id)
    if not parent:
        raise RuntimeError(
            f"Parent tweet {args.parent_tweet_id} not found in parent_tweet.json"
        )

    store = ReplyBotStore(args.db_path)
    store.set_meta("parent_tweet_id", parent.tweet_id)
    store.set_meta("parent_username", parent.author_username)
    store.set_meta("conversation_id", parent.conversation_id)
    store.set_meta("parent_created_at", parent.created_at)
    store.set_meta("parent_text", parent.text)
    store.reset_search_slice()
    store.set_meta("discovery_complete", "")

    keywords = _init_keywords(config, parent, store, logger=logger)
    logger.info("Parent: %s | conv=%s | keywords: %s", parent.tweet_id, parent.conversation_id, keywords[:160])

    total_added = 0
    while True:
        if store.get_meta("discovery_complete") == "1":
            logger.info("Discovery complete")
            break

        current_queue = store.queue_count()
        if current_queue >= args.target_count:
            logger.info("Reached target count: %d queued", current_queue)
            break

        parent_created = store.get_meta("parent_created_at") or parent.created_at
        slice_info = store.get_search_slice(parent_created, slice_days=args.slice_days)
        next_token = store.get_search_next_token()
        seen = store.get_all_tweet_ids()

        logger.info(
            "Discovery slice %d/%d (%s…%s) page=%s | queued=%d",
            slice_info.index + 1,
            slice_info.total,
            slice_info.from_date,
            slice_info.to_date,
            "next" if next_token else "1",
            current_queue,
        )

        leads, page_next = discover_all_replies(
            config,
        parent_tweet_id=parent.tweet_id,
        parent_username=parent.author_username,
        conversation_id=parent.conversation_id,
        keyword_query=keywords,
        parent_created_at=parent.created_at,
        slice_info=slice_info,
        logger=logger,
        seen=seen,
        start_next_token=next_token,
    )

        if page_next:
            store.set_search_next_token(page_next)
        else:
            store.set_search_next_token(None)
            if slice_info.index + 1 >= slice_info.total:
                store.set_meta("discovery_complete", "1")
                logger.info("Discovery complete — all slices exhausted")
                break
            next_slice = store.advance_search_slice(parent_created, slice_days=args.slice_days)
            logger.info(
                "Advanced to slice %d/%d (%s…%s)",
                next_slice.index + 1,
                next_slice.total,
                next_slice.from_date,
                next_slice.to_date,
            )

        if args.skip_deepseek:
            approved = _apply_light_filters(leads)
        else:
            approved = _filter_and_qualify_leads(config, store, leads, logger=logger)

        added = store.enqueue_many(approved)
        total_added += added
        logger.info(
            "Slice added %d (total added %d, queued %d)",
            added,
            total_added,
            store.queue_count(),
        )

        if not leads and not page_next and slice_info.index + 1 >= slice_info.total:
            store.set_meta("discovery_complete", "1")
            logger.info("No more leads and all slices exhausted")
            break

    count = export_queued_to_csv(store, args.csv_path)
    logger.info("Exported %d leads to %s", count, args.csv_path)
    logger.info(
        "Final stats: %s",
        store.stats(),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
