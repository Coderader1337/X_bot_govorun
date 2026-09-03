# Ручной деплой на VPS

Стейджинга нет. Каталог: `/opt/movetorussia/twitter_agent`. Хост: `207.244.254.188` (`vmi3380733`), `root`. Пароль — `VPS_PASS` в `.env`, не в git.

## Машина общая

Рядом RAG (systemd), Qdrant, почта, мониторинг, чужой `/opt/movetorussia/.env`. Нельзя:

```bash
docker stop $(docker ps -q)
reboot
```

`docker compose down` из каталога бота остановит **этот** контейнер. RAG выживет.

## Новый VPS

```bash
ssh root@<VPS_IP>
mkdir -p /opt/movetorussia/twitter_agent
```

С рабочей машины: `pip install paramiko` и `python scripts/sync_to_vps.py`, либо scp без `.env` / `venv` / `.git`.

На сервере `.env` из `.env.example`. Затем:

```bash
cd /opt/movetorussia/twitter_agent
docker compose build
docker compose up -d
docker compose logs -f
```

В compose один сервис `reply-bot`. `scripts/sync_to_vps.py` **не** копирует `Dockerfile` и `docker-compose.yml` — смена tweet-id = правка yaml на сервере + rebuild.

## Живой сервер

Контейнер `movetorussia_reply_bot`, тома `data/` и `logs/`. Код в образе: правка `.py` на хосте без rebuild контейнер не меняет.

Обновление кода:

```bash
cd /opt/movetorussia/twitter_agent
docker compose build
docker compose up -d
```

Очередь в томе сохранится. Recreate — секунды. Не делать «на всякий случай».

Если на сервере ещё висит старый `movetorussia_twitter_bot` (Exited) — это сирота прежнего compose. Убрать **только его**:

```bash
docker rm movetorussia_twitter_bot
```

Не использовать `--remove-orphans`, пока не уверены, что в этом каталоге больше нет чужих сервисов compose.

## Смена parent

В `docker-compose.yml`: `--tweet-id` и при необходимости `--db-id` (старый id файла очереди). Затем `docker compose up -d --build`.

## Логи

```bash
docker logs -f --tail 100 movetorussia_reply_bot
tail -f /opt/movetorussia/twitter_agent/logs/reply_bot_2079647800636428422.log
```

Снаружи открыт только SSH `:22`. HTTP у бота нет.
