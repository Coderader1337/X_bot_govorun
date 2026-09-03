"""Export clean (DeepSeek-approved) leads from the filtered DB to CSV."""

from __future__ import annotations

import argparse
import csv
import sqlite3
from pathlib import Path


def export_clean_leads(
    source_db: Path,
    output_csv: Path,
    stage: str = "deepseek_approved",
    limit: int | None = None,
) -> int:
    conn = sqlite3.connect(source_db)
    conn.row_factory = sqlite3.Row

    sql = """
        SELECT tweet_id, author_username, tweet_text, author_location,
               created_at, found_at, source_status, deepseek_score,
               deepseek_reason, parent_tweet_id
        FROM filtered_leads
        WHERE filter_stage = ?
        ORDER BY created_at ASC, tweet_id ASC
    """
    params: tuple = (stage,)
    if limit:
        sql += " LIMIT ?"
        params = (stage, limit)
    rows = conn.execute(sql, params).fetchall()

    output_csv.parent.mkdir(parents=True, exist_ok=True)
    with open(output_csv, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f, delimiter=";")
        writer.writerow([
            "tweet_id", "author_username", "tweet_text", "author_location",
            "created_at", "found_at", "source_status", "deepseek_score",
            "deepseek_reason", "parent_tweet_id",
        ])
        for row in rows:
            writer.writerow([
                row["tweet_id"],
                row["author_username"],
                row["tweet_text"],
                row["author_location"] or "",
                row["created_at"],
                row["found_at"],
                row["source_status"],
                row["deepseek_score"] or "",
                row["deepseek_reason"] or "",
                row["parent_tweet_id"] or "",
            ])

    return len(rows)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Export clean (DeepSeek-approved) leads from filtered_leads.db to CSV"
    )
    parser.add_argument(
        "--source-db",
        type=Path,
        default=Path("data/filtered_leads.db"),
        help="Source filtered leads DB",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("clean_leads.csv"),
        help="Output CSV path",
    )
    parser.add_argument(
        "--stage",
        default="deepseek_approved",
        help="Which filter_stage to export (default: deepseek_approved)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Limit number of exported leads",
    )
    args = parser.parse_args()

    count = export_clean_leads(args.source_db, args.output, args.stage, args.limit)
    print(f"Exported {count} leads to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
