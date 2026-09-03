# База данных

SQLite в `data/` (на VPS: `/opt/movetorussia/twitter_agent/data`). Том Docker, в git не входит.

Файл: `data/reply_bot_<db-id>.db`. По умолчанию `db-id` = `--tweet-id`. С `--db-id` можно сменить parent и оставить ту же очередь (дедуп username, posts_24h).

## Схема

### `reply_meta`

| Ключ | Смысл |
|---|---|
| `parent_tweet_id`, `parent_username`, `conversation_id`, `parent_created_at`, `parent_text` | Кэш parent |
| `keyword_query`, `keyword_reason` | Кэш DeepSeek |
| `search_slice_index` | 4-часовое окно (0 = время parent) |
| `search_next_token` | Курсор X |
| `search_resume_before` | Watermark, если курсор потерян |
| `discovery_complete` | `"1"` — слайсы на тот момент пройдены |
| `post_blocked_until` | Пауза после 403 |
| `video_media_id`, `video_media_uploaded_at` | Кэш видео |

### `reply_queue`

`tweet_id`, автор, текст, `created_at`, `author_location`, `status` (`queued` / `posted` / `failed` / `rejected` / `blocked` / `duplicate_user`), `found_at`, `posted_tweet_id`, `posted_at`, `error`.

Pop: `ORDER BY created_at ASC`. Дневной лимит: `posted` за последние 24 часа.

## Обновление

Каждый цикл открывает файл заново. Уже известный `tweet_id` не обрабатывается. Перед заменой файла — копия и остановка контейнера.

```bash
docker exec movetorussia_reply_bot python - <<'PY'
import sqlite3
c = sqlite3.connect("/app/data/reply_bot_2079647800636428422.db")
print(c.execute("SELECT status, COUNT(*) FROM reply_queue GROUP BY status").fetchall())
for k, v in c.execute("SELECT key, value FROM reply_meta"):
    print(k, "=", (v[:80] + "…") if len(v) > 80 else v)
PY
```

| Задача | Как |
|---|---|
| Искать заново под тем же твитом | Переименовать db-файл (лучше при остановленном контейнере) |
| Новые keywords | `--refresh-keywords` |
| Добрать новые ответы, не теряя posted | Сбросить `discovery_complete` в meta |

`scripts/reset_vps_db.py` без `--yes` только печатает план. На живой истории не использовать.
