# База данных

Всё состояние — **SQLite-файлы** в `data/` (на VPS: `/opt/movetorussia/twitter_agent/data`). Отдельного PostgreSQL/Redis нет. Файлы в `.gitignore`, в образ Docker не копируются: каталог монтируется томом.

## Какие файлы есть

| Файл | Кто пишет | Назначение |
|---|---|---|
| `reply_bot_<tweet_id>.db` | reply-бот | Очередь и мета **одного** parent-твита |
| `bot_state.db` | search-бот | Очередь глобального поиска (`STATE_DB`) |
| `reply_bot_2079647800636428422.db` | прод сейчас | Активная БД |
| `reply_bot_2074139935297138972.db` | старый parent | Слепок июля 2026, бот его не использует |
| `reply_bot_2057874913118290264.db` | ещё более старый parent | То же |
| `discover_filter.db`, `filtered_leads.db` | `scripts/` | Офлайн-фильтрация, не runtime |

Путь reply-бота всегда `data/reply_bot_{tweet_id}.db`. Смена `--tweet-id` = новый файл, старый остаётся нетронутым.

## Reply-бот: схема

Две таблицы, создаются при старте (`CREATE TABLE IF NOT EXISTS` + при необходимости `ALTER` для `author_location`).

### `reply_meta` (ключ-значение)

| Ключ | Смысл |
|---|---|
| `parent_tweet_id`, `parent_username`, `conversation_id`, `parent_created_at`, `parent_text` | Кэш parent |
| `keyword_query`, `keyword_reason` | Кэш DeepSeek-ключей |
| `search_slice_index` | Номер 4-часового окна (0 = время публикации parent) |
| `search_next_token` | Курсор X API |
| `search_resume_before` | Watermark, если next_token потерян |
| `discovery_complete` | `"1"` = все слайсы на момент флага пройдены |
| `post_blocked_until` | ISO-время паузы после 403 |
| `video_media_id`, `video_media_uploaded_at` | Кэш загруженного видео |

### `reply_queue`

| Поле | Смысл |
|---|---|
| `tweet_id` PK | ID ответа в X |
| `author_username`, `tweet_text`, `created_at`, `author_location` | Данные лида |
| `status` | `queued` / `posted` / `failed` / `rejected` / `blocked` / `duplicate_user` |
| `found_at` | Когда положили в БД |
| `posted_tweet_id`, `posted_at` | Наш пост |
| `error` | Причина reject/fail |

Индекс: `(status, created_at)` — pop идёт `ORDER BY created_at ASC` (сначала старые комментарии).

## Search-бот: схема (`bot_state.db`)

- `bot_meta`: `since_id`, `search_slice_index`, `search_query_index`, `search_next_token`, `search_page_num`, `empty_search_streak`, `search_pass_count`, кэш видео.
- `processed_tweets`: те же статусы плюс `invalid`. Постинг — FIFO по `found_at`.

Дневной лимит считается запросом `status='posted' AND posted_at >= now-24h`.

## Как обновляется

- Каждый цикл открывает SQLite заново (нет долгоживущего connection pool).
- Discovery **дописывает** строки; уже известный `tweet_id` не обрабатывается повторно (`is_known`).
- Постинг меняет `status` той же строки.
- Видео-кэш живёт в meta, не в отдельной таблице.
- Бэкапа по расписанию нет. Перед опасными операциями копируйте файл:

```bash
cp data/reply_bot_2079647800636428422.db data/reply_bot_2079647800636428422.db.bak
```

## Полезные запросы (на VPS, только чтение)

Контейнер видит БД как `/app/data/...`:

```bash
docker exec movetorussia_reply_bot python - <<'PY'
import sqlite3
c = sqlite3.connect("/app/data/reply_bot_2079647800636428422.db")
print(c.execute("SELECT status, COUNT(*) FROM reply_queue GROUP BY status").fetchall())
print("queued", c.execute("SELECT COUNT(*) FROM reply_queue WHERE status='queued'").fetchone()[0])
for k, v in c.execute("SELECT key, value FROM reply_meta"):
    print(k, "=", (v[:80] + "…") if len(v) > 80 else v)
PY
```

С хоста тот же файл: `/opt/movetorussia/twitter_agent/data/reply_bot_2079647800636428422.db`. Пока контейнер запущен, не заменяйте файл копированием поверх без остановки — можно словить truncation. Для замены: остановить контейнер → подменить файл → запустить.

## Сброс / пересборка

| Задача | Как |
|---|---|
| Заново искать под **тем же** твитом | Удалить или переименовать `reply_bot_<id>.db` (бот создаст пустую). Лучше остановить контейнер |
| Сменить keywords | `python run_reply_bot.py --tweet-id … --refresh-keywords --once` или сбросить `keyword_query` + `discovery_complete` в meta |
| Не потерять posted, но добрать новые | Не трогать БД; сбросить только `discovery_complete` в `reply_meta` |
| Search-бот: повторить страницу после сбоя DeepSeek | `python reset_discovery.py` |

`scripts/reset_vps_db.py --yes` **удаляет** файлы на сервере. Без `--yes` — dry-run. На живом проде не использовать, пока очередь/история постинга ещё нужны.

## Снимок прода на 31.08.2026

Файл `reply_bot_2079647800636428422.db`, ~3.2 МБ, 9789 строк:

`posted=6327`, `rejected=3279`, `failed=158`, `duplicate_user=21`, `blocked=4`, `queued=0`, `discovery_complete=1`, parent `@Rusia_HD` / `2026-07-21T19:20:48Z`.
