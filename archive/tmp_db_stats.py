import sqlite3
p = "/opt/movetorussia/twitter_agent/data/reply_bot_2074139935297138972.db"
conn = sqlite3.connect(p)
print("status:", conn.execute("SELECT status, COUNT(*) FROM reply_queue GROUP BY status").fetchall())
print("total:", conn.execute("SELECT COUNT(*) FROM reply_queue").fetchone()[0])
for key in ("discovery_complete", "search_next_token", "search_slice_index", "search_slice_from", "search_slice_to", "parent_created_at"):
    row = conn.execute("SELECT value FROM reply_meta WHERE key=?", (key,)).fetchone()
    print(f"{key}:", row[0] if row else None)
oldest = conn.execute("SELECT MIN(created_at), MAX(created_at) FROM reply_queue").fetchone()
print("lead dates:", oldest)
