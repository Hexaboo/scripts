#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime
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
    """Wraps text in ANSI codes when the terminal supports color."""

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


def step(msg: str) -> None:
    print(out.paint("[....]", "blue") + " " + msg)


def header(title: str) -> None:
    bar = "=" * (len(title) + 4)
    print("\n" + out.paint(bar, "white"))
    print(out.paint("  " + title, "white"))
    print(out.paint(bar, "white") + "\n")


def section(title: str) -> None:
    print("\n" + out.paint(f"-- {title} {'-' * max(0, 40 - len(title))}", "cyan"))


class CommandError(RuntimeError):
    """Raised when an external command fails."""


def run(
    args: Sequence[str],
    *,
    allow_fail: bool = False,
    capture_output: bool = True,
    show_progress: bool = False,
) -> subprocess.CompletedProcess[str]:
    """Run an external command.

    By default stdout/stderr are captured. When show_progress=True stdout is
    still captured but stderr is left flowing so curl's progress bar shows.
    """
    if capture_output:
        stdout_mode = subprocess.PIPE
        stderr_mode = None if show_progress else subprocess.PIPE
    else:
        stdout_mode = stderr_mode = None

    try:
        proc = subprocess.run(
            [str(a) for a in args],
            check=False,
            stdout=stdout_mode,
            stderr=stderr_mode,
            text=True,
        )
    except OSError as exc:
        raise CommandError(f"failed to start {args[0]}: {exc}") from exc

    if proc.returncode != 0 and not allow_fail:
        detail = ""
        if capture_output:
            detail = (proc.stderr or proc.stdout or "").strip()
        message = (
            f"command failed ({proc.returncode}): "
            f"{' '.join(map(str, args))}"
        )
        if detail:
            message += f"\n{detail}"
        raise CommandError(message)
    return proc


def curl(
    args: Sequence[str],
    *,
    allow_fail: bool = False,
    show_progress: bool = False,
) -> subprocess.CompletedProcess[str]:
    return run(
        ["curl"] + [str(a) for a in args],
        allow_fail=allow_fail,
        show_progress=show_progress,
        capture_output=True,
    )


def parse_json(proc: subprocess.CompletedProcess[str], what: str) -> dict:
    try:
        data = json.loads(proc.stdout)
    except (json.JSONDecodeError, ValueError) as exc:
        raise CommandError(f"{what}: invalid server response") from exc
    if not isinstance(data, dict):
        raise CommandError(f"{what}: unexpected response shape: {data}")
    return data


def load_env(paths: Sequence[Path]) -> None:
    """Load key=value pairs from .env files into the environment.

    Existing environment variables take priority over file contents.
    """
    loaded = False
    for path in paths:
        if not path.is_file():
            continue
        with path.open() as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, val = line.partition("=")
                os.environ.setdefault(key.strip(), val.strip().strip('"').strip("'"))
        loaded = True
    if not loaded:
        warn("No .env file found; using environment variables only.")


def env(key: str, default: str = "") -> str:
    return os.environ.get(key, default)


def check_dependencies() -> None:
    required = {"curl": "Install curl: https://curl.se/download.html",
                "jq": "Install jq (Linux): sudo apt install jq"}
    missing = [cmd for cmd in required if not shutil.which(cmd)]
    if missing:
        raise CommandError("missing required dependencies: " + ", ".join(missing))


def human_size(size_bytes: int) -> str:
    value = float(size_bytes)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024:
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} PB"


def _digest(path: Path, algo) -> str:
    h = algo()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def md5(path: Path) -> str:
    return _digest(path, hashlib.md5)


def sha1(path: Path) -> str:
    return _digest(path, hashlib.sha1)


RETRY_ARGS = ["--retry", "3", "--retry-delay", "2"]


def upload_pixeldrain(file: Path) -> str | None:
    section("PixelDrain")
    step(f"Uploading {file.name} to PixelDrain...")
    api_key = env("PIXELDRAIN_API_KEY")

    proc = curl(
        RETRY_ARGS
        + ["--progress-bar", "--fail-with-body",
            "-u", f":{api_key}",
            "-F", f"file=@{file}",
            "https://pixeldrain.com/api/file"],
        show_progress=True,
        allow_fail=True,
    )
    if proc.returncode != 0:
        err("PixelDrain upload failed.")
        return None

    data = parse_json(proc, "PixelDrain")
    file_id = data.get("id")
    if not file_id:
        err(f"PixelDrain: unexpected response: {data}")
        return None

    link = f"https://pixeldrain.com/u/{file_id}"
    ok(f"PixelDrain -> {out.paint(link, 'white')}")
    return f"PixelDrain: {link}"


def upload_gofile(file: Path) -> str | None:
    section("GoFile")
    step(f"Uploading {file.name} to GoFile...")
    token = env("GOFILE_TOKEN")

    auth = ["-F", f"token={token}"] if token else []
    proc = curl(
        RETRY_ARGS
        + ["--progress-bar", "--fail-with-body"]
        + auth
        + ["-F", f"file=@{file}", "https://upload.gofile.io/uploadFile"],
        show_progress=True,
        allow_fail=True,
    )
    if proc.returncode != 0:
        err("GoFile upload failed.")
        return None

    data = parse_json(proc, "GoFile")
    try:
        link = data["data"]["downloadPage"]
    except (KeyError, TypeError) as exc:
        err(f"GoFile: unexpected response: {data}")
        return None
    if not link.startswith("http"):
        err(f"GoFile: unexpected response: {data}")
        return None

    ok(f"GoFile -> {out.paint(link, 'white')}")
    return f"GoFile: {link}"


def upload_ranoz(file: Path) -> str | None:
    section("Ranoz.gg")
    size_b = file.stat().st_size

    step(f"Requesting upload URL for {file.name}...")
    init = curl(
        ["-sS", "--fail-with-body", "-X", "POST",
         "https://ranoz.gg/api/v1/files/upload_url",
         "-H", "Content-Type: application/json",
         "-d", json.dumps({"filename": file.name, "size": size_b})],
        allow_fail=True,
    )
    if init.returncode != 0:
        err(f"Ranoz init failed: {(init.stderr or init.stdout).strip()}")
        return None

    meta = parse_json(init, "Ranoz init")
    try:
        upload_url = meta["data"]["upload_url"]
        link = meta["data"]["url"]
    except (KeyError, TypeError):
        err(f"Ranoz init: unexpected response: {meta}")
        return None
    if upload_url == "null" or not link:
        err(f"Ranoz init: unexpected response: {meta}")
        return None

    step("Uploading to Ranoz.gg...")
    put = curl(
        RETRY_ARGS
        + ["--progress-bar", "--fail-with-body", "-X", "PUT",
            upload_url, "--upload-file", str(file),
            "-H", f"Content-Length: {size_b}",
            "-o", "/dev/null"],
        show_progress=True,
        allow_fail=True,
    )
    if put.returncode != 0:
        err("Ranoz upload failed.")
        return None

    ok(f"Ranoz.gg -> {out.paint(link, 'white')}")
    return f"Ranoz.gg: {link}"


def upload_sourceforge(file: Path) -> str | None:
    section("SourceForge")
    sf_user = env("SOURCEFORGE_USER")
    sf_path = env("SOURCEFORGE_PATH")
    sf_project = env("SOURCEFORGE_PROJECT")

    if not sf_user or not sf_path or not sf_project:
        err("SOURCEFORGE_USER / SOURCEFORGE_PATH / SOURCEFORGE_PROJECT not set in .env")
        return None

    dest = f"{sf_user}@frs.sourceforge.net:{sf_path}/{file.name}"
    step(f"Uploading {file.name} to SourceForge -> {dest}")

    if shutil.which("pv"):
        pv = subprocess.Popen(["pv", str(file)], stdout=subprocess.PIPE)
        ssh = subprocess.Popen(["ssh", dest, "true"], stdin=pv.stdout)
        if pv.stdout is not None:
            pv.stdout.close()
        pv.wait()
        ret = ssh.wait()
    else:
        warn("'pv' not installed — no progress bar. Using scp.")
        ret = run(["scp", str(file), dest], allow_fail=True).returncode

    if ret != 0:
        err("SourceForge upload failed.")
        return None

    link = f"https://sourceforge.net/projects/{sf_project}/files/{file.name}/download"
    ok(f"SourceForge -> {out.paint(link, 'white')}")
    return f"SourceForge: {link}"


ALL_SERVICES = ["PixelDrain", "GoFile", "Ranoz", "SourceForge"]

UPLOADERS = {
    "PixelDrain": upload_pixeldrain,
    "GoFile": upload_gofile,
    "Ranoz": upload_ranoz,
    "SourceForge": upload_sourceforge,
}


TELEGRAM_API = "https://api.telegram.org/bot"


def send_telegram(
    file: Path,
    size_b: int,
    file_md5: str,
    file_sha1: str,
    now: str,
    results: list[str],
) -> None:
    token = env("TELEGRAM_BOT_TOKEN")
    chat_id = env("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        warn("TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID not set. Skipping Telegram.")
        return

    buttons = []
    for entry in results:
        if ": " in entry:
            label, url = entry.split(": ", 1)
            buttons.append({"text": f"Download via {label}", "url": url.strip()})
    keyboard = {"inline_keyboard": [[btn] for btn in buttons]}

    msg = (
        f"*Upload Complete*\n\n"
        f"*File:* `{file.name}`\n"
        f"*Size:* {human_size(size_b)}\n"
        f"*MD5:* `{file_md5}`\n"
        f"*SHA1:* `{file_sha1}`\n"
        f"*Time:* {now}"
    )

    payload = json.dumps(
        {
            "chat_id": chat_id,
            "text": msg,
            "parse_mode": "Markdown",
            "reply_markup": keyboard,
        }
    ).encode()

    req = urllib.request.Request(
        f"{TELEGRAM_API}{token}/sendMessage",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(req) as resp:
            data = json.loads(resp.read())
            if data.get("ok"):
                ok("Telegram notification sent.")
            else:
                warn(f"Telegram API error: {data.get('description')}")
    except urllib.error.URLError as exc:
        warn(f"Telegram request failed: {exc}")


def configure_color(mode: str) -> None:
    if mode == "always":
        out.enabled = True
    elif mode == "never":
        out.enabled = False


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Multi-service file uploader",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    svc = parser.add_mutually_exclusive_group(required=True)
    svc.add_argument(
        "-A", "--all", dest="services", action="store_const",
        const=ALL_SERVICES, help="Upload to ALL services",
    )
    svc.add_argument(
        "-P", "--pixeldrain", dest="services", action="store_const",
        const=["PixelDrain"], help="Upload to PixelDrain only",
    )
    svc.add_argument(
        "-G", "--gofile", dest="services", action="store_const",
        const=["GoFile"], help="Upload to GoFile only",
    )
    svc.add_argument(
        "-R", "--ranoz", dest="services", action="store_const",
        const=["Ranoz"], help="Upload to Ranoz.gg only",
    )
    svc.add_argument(
        "-S", "--sourceforge", dest="services", action="store_const",
        const=["SourceForge"], help="Upload to SourceForge only",
    )
    parser.add_argument(
        "-F", "--file", required=True, metavar="FILE",
        help="Path to the file to upload",
    )
    parser.add_argument(
        "--env", metavar="PATH", default=None,
        help="Path to a .env file (default: ./ .env, then ~/.env)",
    )
    parser.add_argument(
        "--color", choices=("auto", "always", "never"), default="auto",
        help="Colorize output (default: auto)",
    )
    parser.add_argument(
        "--no-telegram", action="store_true",
        help="Skip the Telegram notification",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    configure_color(args.color)

    file = Path(args.file).expanduser()
    if not file.is_file():
        err(f"File not found: {file}")
        return 1

    env_paths = (
        [Path(args.env).expanduser()]
        if args.env
        else [Path.cwd() / ".env", Path.home() / ".env"]
    )
    load_env(env_paths)

    try:
        check_dependencies()
    except CommandError as exc:
        err(str(exc))
        return 1

    size_b = file.stat().st_size
    file_md5 = md5(file)
    file_sha1 = sha1(file)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    header(f"File Uploader  ->  {file.name}")
    print(f"  {out.paint('Size  :', 'dim')} {out.paint(human_size(size_b), 'white')}")
    print(f"  {out.paint('MD5   :', 'dim')} {out.paint(file_md5, 'white')}")
    print(f"  {out.paint('SHA1  :', 'dim')} {out.paint(file_sha1, 'white')}")
    print(f"  {out.paint('Time  :', 'dim')} {out.paint(now, 'white')}")
    print(f"  {out.paint('Target:', 'dim')} {out.paint(', '.join(args.services), 'white')}")

    results: list[str] = []
    for svc in args.services:
        try:
            link = UPLOADERS[svc](file)
        except CommandError as exc:
            err(f"{svc}: {exc}")
            link = None
        if link:
            results.append(link)

    header("Summary")
    if results:
        for r in results:
            print(f"  {out.paint('+', 'green')} {out.paint(r, 'white')}")
    else:
        warn("No successful uploads.")

    if not args.no_telegram:
        send_telegram(file, size_b, file_md5, file_sha1, now, results)

    print(f"\n{out.paint('Done.', 'green')}\n")
    return 0 if results else 1


if __name__ == "__main__":
    sys.exit(main())
