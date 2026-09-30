"""Sign the Instagram app session in once and save it for the story publisher.

Run on the server when Instagram asks to confirm the login:

    docker compose exec app python -m publishers.instagram_login

When Instagram sends a confirmation code, type it in. Without a terminal the
code is read from data/instagram_code.txt instead.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

from config.app_config import AppConfig
from publishers.instagram_highlights import DEFAULT_SETTINGS_PATH

CODE_FILE = Path("data/instagram_code.txt")
CODE_WAIT_SECONDS = 15 * 60


def main() -> int:
    config = AppConfig.load()
    login = config.instagram.login
    password = config.instagram.password
    if not (login and password):
        print("Set INSTAGRAM_LOGIN and INSTAGRAM_PASSWORD in .env first.")
        return 1

    from instagrapi import Client
    from instagrapi.exceptions import TwoFactorRequired

    client = Client()
    client.delay_range = [1, 3]
    if DEFAULT_SETTINGS_PATH.is_file():
        client.load_settings(DEFAULT_SETTINGS_PATH)
    client.challenge_code_handler = lambda username, choice=None: _read_code(
        f"Instagram sent a confirmation code ({choice}) for {username}."
    )
    try:
        client.login(login, password)
    except TwoFactorRequired:
        client.login(login, password, verification_code=_read_code("Instagram asks for the two-factor code."))

    DEFAULT_SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    client.dump_settings(DEFAULT_SETTINGS_PATH)
    print(f"Signed in as @{client.username} ({client.user_id}). Session saved to {DEFAULT_SETTINGS_PATH}.")
    return 0


def _read_code(prompt: str) -> str:
    print(prompt, flush=True)
    if sys.stdin.isatty():
        return input("Code: ").strip()
    print(f"Waiting up to {CODE_WAIT_SECONDS // 60} minutes for the code in {CODE_FILE}.", flush=True)
    CODE_FILE.unlink(missing_ok=True)
    deadline = time.monotonic() + CODE_WAIT_SECONDS
    while time.monotonic() < deadline:
        if CODE_FILE.is_file():
            code = CODE_FILE.read_text(encoding="utf-8").strip()
            if code:
                CODE_FILE.unlink(missing_ok=True)
                return code
        time.sleep(3)
    raise SystemExit("No confirmation code arrived.")


if __name__ == "__main__":
    raise SystemExit(main())
