# Архитектура кода

Два независимых входа, общая инфраструктура X/DeepSeek/видео.

```
run_reply_bot.py          прод: ответы под одним твитом
run_bot.py                запасной: поиск лидов по всей ленте
generate_oauth_tokens.py  разовая выдача User tokens
reset_discovery.py        сброс rejected + next_token у search-бота

bot/                      общее ядро
bot/reply_bot/            только reply-режим
scripts/                  выгрузки, фильтры, выкладка на VPS
archive/                  одноразовые артефакты, не для прода
```

## Точки входа

| Файл | Роль |
|---|---|
| `run_reply_bot.py` | Daemon reply-бота. Обязателен `--tweet-id`. Флаги: `--once`, `--discover-first`, `--dry-run`, `--refresh-keywords` |
| `run_bot.py` | Daemon search-бота. `--once`, `--dry-run` |
| `generate_oauth_tokens.py` | PIN-flow OAuth 1.0a |
| `reset_discovery.py` | Удаляет `rejected` после сбоя DeepSeek у search-бота |

Docker: `CMD` образа = `run_bot.py`. Reply-бот переопределяет command в `docker-compose.yml`.

## `bot/` — общее ядро

| Модуль | Ответственность |
|---|---|
| `config.py` | Чтение `.env`, дефолты, `DEFAULT_REPLY_BODY`, excluded handles, интервалы |
| `logging_setup.py` | Файл + stdout, логгер `movetorussia_bot` |
| `x_client.py` | OAuth1-сессия, Bearer GET твита, `POST /2/tweets` |
| `video_media.py` | Chunked upload видео, кэш `video_media_id` на 23 ч |
| `x_api_search.py` | Keyword-search для search-бота (recent/all, слайсы, паттерны) |
| `search_queries.py` | 10 XQL-паттернов + общие фильтры `-is:retweet -war …` |
| `deepseek_qualify.py` | Квалификация кандидатов search-бота |
| `grok_search.py` | Fallback xAI Responses API + `x_search` |
| `discovery.py` | Склейка: X API → DeepSeek → Grok |
| `pipeline.py` | Цикл search-бота: refill очереди + 1 пост |
| `state.py` | SQLite search-бота (`bot_meta`, `processed_tweets`) |

На VPS в каталоге `bot/` лежит ещё `prefilter.py` — в текущем git его **нет**. Это дрифт образа от 14–30 июля. На поведение reply-бота не влияет (он его не импортирует).

## `bot/reply_bot/` — прод-контур

| Модуль | Ответственность |
|---|---|
| `pipeline.py` | Цикл: discovery page / pop queue / post; загрузка parent; keywords |
| `search.py` | `search/all` по conversation_id + keywords, фильтр прямых ответов |
| `keywords.py` | DeepSeek: OR-фрагмент под текст parent (+ yes/si/oui/ja) |
| `antiblock.py` | Жёсткий стоп-лист фраз |
| `region.py` | Гео по `user.location` и username |
| `qualify.py` | DeepSeek по ответам (батч 15, score ≥ 7) |
| `state.py` | SQLite `reply_meta` + `reply_queue` |

Очередь reply-бота **не связана** с очередью search-бота: разные файлы БД.

## `scripts/`

| Скрипт | Когда нужен |
|---|---|
| `sync_to_vps.py` | Залить код на VPS по SFTP. Ключи из `.env`. БД **не** трогает, пока не передан `--deploy-db` |
| `reset_vps_db.py` | Удалить sqlite на VPS. Без `--yes` только печатает план |
| `discover_and_filter.py` | Локально набрать лиды под твит в отдельную БД/CSV, без постинга |
| `filter_existing_leads.py` | Прогнать уже скачанные лиды через antiblock/geo/DeepSeek |
| `export_clean_leads.py` | CSV из `filtered_leads.db` |

`paramiko` в `requirements.txt` нет — ставится отдельно, только для выкладки с рабочей машины.

## Что сознательно не код

- Текст outreach — константа, не LLM.
- Нет веб-UI, нет CI, нет стейджинга, нет n8n.
- Нет автосбора нового вирусного поста: `--tweet-id` задаёт человек.
- Видео — один mp4, перезаливается раз в ~23 часа.

## Конфигурация, которая чаще всего меняется

| Что | Где |
|---|---|
| Текст поста | `bot/config.py` → `DEFAULT_REPLY_BODY` |
| Parent-твит | `docker-compose.yml` → `--tweet-id` |
| Лимит постов/сутки | `.env` → `DAILY_POST_LIMIT` |
| Порог DeepSeek | `.env` → `DEEPSEEK_MIN_SCORE` |
| Стоп-слова | `bot/reply_bot/antiblock.py` |
| Гео | `bot/reply_bot/region.py` |
| Keyword-паттерны search-бота | `bot/search_queries.py` |
