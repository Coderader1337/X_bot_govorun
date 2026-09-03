# Архитектура

```
run_reply_bot.py          daemon
generate_oauth_tokens.py  разовая выдача User tokens

bot/config.py             .env, текст поста, лимиты
bot/logging_setup.py
bot/x_client.py           Bearer GET, OAuth POST
bot/video_media.py        загрузка mp4, кэш media_id
bot/reply_bot/            поиск / фильтры / очередь / цикл
scripts/sync_to_vps.py    выкладка кода (не трогает compose/БД)
scripts/reset_vps_db.py   удаление sqlite на VPS (нужен --yes)
```

## Точка входа

`run_reply_bot.py --tweet-id …`

| Флаг | Смысл |
|---|---|
| `--db-id` | Имя файла БД, если очередь нужно продолжить при новом parent |
| `--once` | Один цикл |
| `--discover-first` | Сначала выгрести все страницы, потом постинг |
| `--dry-run` | Без постинга |
| `--refresh-keywords` | Перегенерировать keyword-query |

Docker: образ запускает `run_reply_bot.py`, полный command (tweet-id, db-id) задаёт `docker-compose.yml`.

## `bot/reply_bot/`

| Модуль | Роль |
|---|---|
| `pipeline.py` | Цикл, parent, keywords, постинг |
| `search.py` | `search/all` + прямые ответы |
| `keywords.py` | DeepSeek: OR-фрагмент под parent |
| `antiblock.py` | Стоп-лист |
| `region.py` | Гео |
| `qualify.py` | DeepSeek по ответам |
| `state.py` | SQLite `reply_meta` + `reply_queue` |

## Что меняют чаще всего

| Что | Где |
|---|---|
| Текст поста | `bot/config.py` → `DEFAULT_REPLY_BODY` |
| Parent-твит | `docker-compose.yml` → `--tweet-id` / `--db-id` |
| Лимит в сутки | `.env` → `DAILY_POST_LIMIT` |
| Порог DeepSeek | `.env` → `DEEPSEEK_MIN_SCORE` |
| Стоп-слова | `bot/reply_bot/antiblock.py` |
| Гео | `bot/reply_bot/region.py` |
