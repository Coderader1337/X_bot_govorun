"""Sync bot code to the VPS over SFTP. Does not restart containers.

Credentials come from environment / .env (never hardcode passwords):
  VPS_IP, VPS_USER, VPS_PASS, optional VPS_PORT, VPS_REMOTE_ROOT

By default only code is copied. SQLite overwrite is opt-in:

  python scripts/sync_to_vps.py
  python scripts/sync_to_vps.py --deploy-db path/to/local.db --remote-db-name reply_bot_TWEETID.db

Dockerfile and docker-compose.yml are excluded on purpose: changing the
running tweet-id requires a rebuild on the VPS (see docs/05-vps-deploy.md).
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

EXCLUDED_NAMES = {
    ".git",
    ".gitignore",
    ".env",
    "venv",
    "__pycache__",
    ".cursor",
    ".dockerignore",
    "Dockerfile",
    "docker-compose.yml",
    "logs",
    "data",
    "docs",
    "agent-tools",
    "terminals",
}


def _require_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise SystemExit(f"Задайте {name} в .env")
    return value


def _should_sync(path: Path, local_root: Path) -> bool:
    rel_parts = path.relative_to(local_root).parts
    return not any(part in EXCLUDED_NAMES for part in rel_parts)


def main() -> int:
    parser = argparse.ArgumentParser(description="SFTP sync of bot code to VPS")
    parser.add_argument(
        "--deploy-db",
        type=Path,
        help="Локальный SQLite для заливки (по умолчанию БД на VPS не трогаем)",
    )
    parser.add_argument(
        "--remote-db-name",
        help="Имя файла в data/ на VPS, например reply_bot_2079647800636428422.db",
    )
    args = parser.parse_args()

    try:
        import paramiko
    except ImportError:
        print("Нужен paramiko: pip install paramiko", file=sys.stderr)
        return 1

    host = os.getenv("VPS_HOST", "").strip() or _require_env("VPS_IP")
    user = os.getenv("VPS_USER", "root").strip() or "root"
    password = _require_env("VPS_PASS")
    port = int(os.getenv("VPS_PORT", "22"))
    remote_root = os.getenv("VPS_REMOTE_ROOT", "/opt/movetorussia/twitter_agent").strip()
    local_root = ROOT

    if args.deploy_db and not args.remote_db_name:
        print("Для --deploy-db укажите --remote-db-name", file=sys.stderr)
        return 1

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(host, port=port, username=user, password=password, timeout=30)
    sftp = client.open_sftp()
    client.exec_command(f"mkdir -p {remote_root}")

    files_synced = 0
    dirs_synced = 0
    for local_path in sorted(local_root.rglob("*")):
        if not _should_sync(local_path, local_root):
            continue
        rel = local_path.relative_to(local_root)
        remote_path = f"{remote_root}/{rel.as_posix()}"
        if local_path.is_dir():
            client.exec_command(f"mkdir -p {remote_path}")
            dirs_synced += 1
            continue
        sftp.put(str(local_path), remote_path)
        files_synced += 1

    if args.deploy_db:
        local_db = args.deploy_db.resolve()
        if not local_db.is_file():
            print(f"Локальная БД не найдена: {local_db}", file=sys.stderr)
            sftp.close()
            client.close()
            return 1
        remote_db = f"{remote_root}/data/{args.remote_db_name}"
        client.exec_command(f"mkdir -p {remote_root}/data")
        client.exec_command(f"cp -f {remote_db} {remote_db}.bak 2>/dev/null || true")
        sftp.put(str(local_db), remote_db)
        print(f"Deployed DB: {local_db} -> {remote_db}")

    sftp.close()
    client.close()
    print(f"Synced {dirs_synced} directories and {files_synced} files to {host}:{remote_root}")
    print("Контейнеры не перезапускались.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
