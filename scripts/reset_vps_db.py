"""Dangerous: delete reply-bot / search-bot SQLite files on the VPS.

Requires VPS_IP / VPS_PASS in .env and an explicit --yes flag.
Does not restart containers. After deleting a DB the running bot will
recreate an empty file on the next write — prefer stopping the container first.
"""

from __future__ import annotations

import argparse
import os
import sys

from dotenv import load_dotenv

ROOT = __import__("pathlib").Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

DEFAULT_FILES = (
    "reply_bot_2079647800636428422.db",
    "bot_state.db",
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Delete SQLite files on the VPS")
    parser.add_argument(
        "--files",
        nargs="+",
        default=list(DEFAULT_FILES),
        help="Имена файлов в data/ на VPS",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Подтверждение удаления (без флага скрипт только печатает план)",
    )
    args = parser.parse_args()

    try:
        import paramiko
    except ImportError:
        print("Нужен paramiko: pip install paramiko", file=sys.stderr)
        return 1

    host = os.getenv("VPS_HOST", "").strip() or os.getenv("VPS_IP", "").strip()
    password = os.getenv("VPS_PASS", "").strip()
    user = os.getenv("VPS_USER", "root").strip() or "root"
    data_dir = os.getenv("VPS_REMOTE_ROOT", "/opt/movetorussia/twitter_agent").rstrip("/") + "/data"
    if not host or not password:
        print("Задайте VPS_IP и VPS_PASS в .env", file=sys.stderr)
        return 1

    print(f"Хост: {host}")
    print(f"Каталог: {data_dir}")
    for name in args.files:
        print(f"  удалить: {data_dir}/{name}")
    if not args.yes:
        print("Это был dry-run. Для удаления добавьте --yes")
        return 0

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(host, port=22, username=user, password=password, timeout=30)
    for name in args.files:
        path = f"{data_dir}/{name}"
        _, stdout, stderr = client.exec_command(f"rm -f {path}")
        err = stderr.read().decode().strip()
        stdout.read()
        if err:
            print(f"Error removing {path}: {err}")
        else:
            print(f"Removed {path}")
    client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
