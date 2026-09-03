import sqlite3

c = sqlite3.connect("/opt/movetorussia/twitter_agent/data/reply_bot_2074139935297138972.db")
rows = c.execute(
    """
    SELECT tweet_id, author_username, created_at, tweet_text, status
    FROM reply_queue
    ORDER BY created_at ASC, tweet_id ASC
    LIMIT 3
    """
).fetchall()
print("oldest_leads_by_date:")
for r in rows:
    print(r)
print("stats:", c.execute("SELECT status, COUNT(*) FROM reply_queue GROUP BY status").fetchall())
