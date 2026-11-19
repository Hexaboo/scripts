#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Sequence

_ANSI = {
    "reset": "\033[0m",
    "bold": "\033[1m",
    "dim": "\033[2m",
    "red": "\033[31m",
    "green": "\033[32m",
    "yellow": "\033[33m",
    "blue": "\033[34m",
    "cyan": "\033[36m",
    "white": "\033[37m",
}


class Color:
    def __init__(self, enabled: bool | None = None) -> None:
        if enabled is None:
            enabled = not os.environ.get("NO_COLOR") and sys.stdout.isatty()
        self.enabled = enabled

    def paint(self, text: str, color: str) -> str:
        if not self.enabled:
            return text
        return f"{_ANSI[color]}{text}{_ANSI['reset']}"


out = Color()


def ok(msg: str) -> None:
    print(out.paint("[OK]", "green") + "  " + msg)


def err(msg: str) -> None:
    print(out.paint("[ERR]", "red") + " " + msg, file=sys.stderr)


def warn(msg: str) -> None:
    print(out.paint("[WARN]", "yellow") + " " + msg)


def info(msg: str) -> None:
    print(out.paint("[INFO]", "cyan") + " " + msg)


def load_env(paths: Sequence[Path]) -> None:
    for path in paths:
        if not path.is_file():
            continue
        for line in path.read_text().splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            key, _, val = stripped.partition("=")
            os.environ.setdefault(key.strip(), val.strip().strip('"').strip("'"))


API = "https://api.telegram.org/bot"


def _json_post(token: str, method: str, payload: dict) -> bool:
    req = urllib.request.Request(
        f"{API}{token}/{method}",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req) as resp:
            data = json.loads(resp.read())
    except urllib.error.URLError as exc:
        warn(f"Telegram {method} request failed: {exc}")
        return False
    if not data.get("ok"):
        warn(f"Telegram {method} error: {data.get('description')}")
        return False
    return True


def send_message(
    token: str,
    chat_id: str,
    text: str,
    *,
    parse_mode: str | None = "Markdown",
    buttons: Sequence[tuple[str, str]] = (),
    silent: bool = False,
) -> bool:
    payload: dict = {
        "chat_id": chat_id,
        "text": text,
        "disable_notification": silent,
    }
    if parse_mode:
        payload["parse_mode"] = parse_mode
    if buttons:
        keyboard = {"inline_keyboard": [
            [{"text": label, "url": url} for label, url in buttons]
        ]}
        payload["reply_markup"] = keyboard
    return _json_post(token, "sendMessage", payload)


def send_document(
    token: str,
    chat_id: str,
    file_path: Path,
    *,
    caption: str | None = None,
    parse_mode: str | None = "Markdown",
    buttons: Sequence[tuple[str, str]] = (),
    silent: bool = False,
) -> bool:
    cmd = [
        "curl", "-sS", "--fail-with-body",
        "-F", f"chat_id={chat_id}",
        "-F", f"document=@{file_path}",
        "-F", f"disable_notification={'true' if silent else 'false'}",
    ]
    if caption:
        cmd += ["-F", f"caption={caption}"]
    if parse_mode:
        cmd += ["-F", f"parse_mode={parse_mode}"]
    if buttons:
        keyboard = {"inline_keyboard": [
            [{"text": label, "url": url} for label, url in buttons]
        ]}
        cmd += ["-F", f"reply_markup={json.dumps(keyboard)}"]
    cmd.append(f"{API}{token}/sendDocument")

    proc = subprocess.run(cmd, text=True)
    if proc.returncode != 0:
        warn("Telegram sendDocument failed (curl).")
        return False
    return True


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Send Telegram notifications (text and/or documents)",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    parser.add_argument("-m", "--message", default="", help="Message text")
    parser.add_argument(
        "-f", "--file", action="append", metavar="PATH",
        help="File(s) to attach as documents (repeatable)",
    )
    parser.add_argument(
        "--title", default="", help="Optional bold title prepended to the message",
    )
    parser.add_argument("--to", default="", help="Chat ID (overrides .env)")
    parser.add_argument(
        "--parse-mode", choices=("Markdown", "HTML"), default="Markdown",
        help="Parse mode for text (default: Markdown)",
    )
    parser.add_argument("--silent", action="store_true", help="Disable notification sound")
    parser.add_argument(
        "--button", action="append", metavar="LABEL=URL",
        help="Inline button below the message (repeatable)",
    )
    parser.add_argument("--stdin", action="store_true", help="Read message body from stdin")
    parser.add_argument(
        "--env", metavar="PATH", default=None,
        help="Path to a .env file (default: ./ .env, then ~/.env)",
    )
    parser.add_argument(
        "--color", choices=("auto", "always", "never"), default="auto",
        help="Colorize output (default: auto)",
    )
    return parser


def _parse_buttons(raw: Sequence[str] | None) -> list[tuple[str, str]]:
    buttons: list[tuple[str, str]] = []
    for item in raw or []:
        if "=" not in item:
            err(f"Invalid button (expected LABEL=URL): {item}")
            continue
        label, _, url = item.partition("=")
        buttons.append((label.strip(), url.strip()))
    return buttons


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.color == "always":
        out.enabled = True
    elif args.color == "never":
        out.enabled = False

    env_paths = (
        [Path(args.env).expanduser()]
        if args.env
        else [Path.cwd() / ".env", Path.home() / ".env"]
    )
    load_env(env_paths)

    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = args.to or os.environ.get("TELEGRAM_CHAT_ID", "")

    if not token or not chat_id:
        err("TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID are required (env or .env).")
        return 1

    text = args.message
    if args.stdin:
        text = sys.stdin.read().strip()

    if args.title and text:
        text = f"*{args.title}*\n\n{text}"
    elif args.title:
        text = f"*{args.title}*"

    buttons = _parse_buttons(args.button)
    files = [Path(f).expanduser() for f in (args.file or [])]

    if not text and not files:
        err("Nothing to send: provide --message, --stdin, or --file.")
        return 1

    sent = True
    if files:
        for path in files:
            if not path.is_file():
                err(f"File not found: {path}")
                sent = False
                continue
            caption = text if text else None
            if send_document(
                token, chat_id, path,
                caption=caption, parse_mode=args.parse_mode,
                buttons=buttons, silent=args.silent,
            ):
                ok(f"Sent document: {path.name}")
            else:
                sent = False
    elif text:
        if send_message(
            token, chat_id, text,
            parse_mode=args.parse_mode, buttons=buttons, silent=args.silent,
        ):
            ok("Message sent.")
        else:
            sent = False

    return 0 if sent else 1


if __name__ == "__main__":
    sys.exit(main())
