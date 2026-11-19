#!/usr/bin/env python3

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
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


DEFAULT_TEMPLATE = {
    "GOFILE_TOKEN": "",
    "PIXELDRAIN_API_KEY": "",
    "TELEGRAM_BOT_TOKEN": "",
    "TELEGRAM_CHAT_ID": "",
    "SOURCEFORGE_USER": "",
    "SOURCEFORGE_PROJECT": "",
    "SOURCEFORGE_PATH": "",
}


def parse_line(line: str) -> tuple[str, str] | None:
    """Parse a raw line into (key, unquoted value) or None if not a KV line."""
    stripped = line.strip()
    if not stripped or stripped.startswith("#") or "=" not in stripped:
        return None
    key, _, val = stripped.partition("=")
    return key.strip(), val.strip().strip('"').strip("'")


def format_value(value: str) -> str:
    if value == "":
        return ""
    if any(ch in value for ch in " \t#'\"\""):
        return '"' + value.replace('"', '\\"') + '"'
    return value


def read_env(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    result: dict[str, str] = {}
    for line in path.read_text().splitlines():
        parsed = parse_line(line)
        if parsed:
            result[parsed[0]] = parsed[1]
    return result


def write_env(path: Path, updates: dict[str, str | None]) -> None:
    """Rewrite path, applying updates.

    updates maps key -> new value, or key -> None to remove the key.
    Comments and ordering are preserved.
    """
    if not path.parent.exists():
        path.parent.mkdir(parents=True, exist_ok=True)

    lines = path.read_text().splitlines() if path.is_file() else []
    remaining = set(updates)

    output: list[str] = []
    for line in lines:
        parsed = parse_line(line)
        if parsed and parsed[0] in updates:
            remaining.discard(parsed[0])
            value = updates[parsed[0]]
            if value is None:
                continue
            output.append(f"{parsed[0]}={format_value(value)}")
        else:
            output.append(line)

    for key, value in updates.items():
        if key in remaining and value is not None:
            output.append(f"{key}={format_value(value)}")

    text = "\n".join(output) + ("\n" if output else "")
    path.write_text(text)


def _resolve_file(args) -> Path:
    return Path(args.file).expanduser() if args.file else Path.cwd() / ".env"


def cmd_generate(args) -> int:
    env_path = _resolve_file(args)
    if env_path.exists() and not args.force:
        err(f"{env_path} already exists. Use --force to overwrite.")
        return 1

    template = Path(args.template).expanduser()
    if template.is_file():
        content = template.read_text()
    else:
        content = "".join(
            f"{key}={format_value(value)}\n"
            for key, value in DEFAULT_TEMPLATE.items()
        )
        if args.template != "example.env":
            warn(f"Template not found: {template} — using built-in defaults.")

    env_path.write_text(content)
    ok(f"Wrote {env_path}")
    return 0


def cmd_list(args) -> int:
    env_path = _resolve_file(args)
    env = read_env(env_path)
    if not env:
        info(f"No keys found in {env_path}.")
        return 0

    keys = sorted(env) if args.sort else list(env)
    for key in keys:
        value = env[key]
        if value and not args.show_secrets:
            display = "*" * min(len(value), 12)
        else:
            display = value or "(empty)"
        print(f"  {out.paint(key, 'bold')} = {out.paint(display, 'dim')}")
    print(out.paint(f"\n{len(keys)} key(s) in {env_path}", "dim"))
    return 0


def cmd_get(args) -> int:
    env_path = _resolve_file(args)
    env = read_env(env_path)
    if args.key not in env:
        err(f"Key not found: {args.key}")
        return 1
    print(env[args.key])
    return 0


def cmd_set(args) -> int:
    if "=" not in args.kv:
        err("Expected KEY=VALUE")
        return 1
    key, _, value = args.kv.partition("=")
    key = key.strip()
    if not key:
        err("Empty key name.")
        return 1
    value = value.strip().strip('"').strip("'")
    write_env(_resolve_file(args), {key: value})
    ok(f"Set {out.paint(key, 'bold')}")
    return 0


def cmd_unset(args) -> int:
    write_env(_resolve_file(args), {args.key: None})
    ok(f"Unset {out.paint(args.key, 'bold')}")
    return 0


def cmd_check(args) -> int:
    env_path = _resolve_file(args)
    env = read_env(env_path)

    template = Path(args.template).expanduser()
    if template.is_file():
        expected = read_env(template)
    else:
        expected = dict(DEFAULT_TEMPLATE)

    missing = [k for k in expected if k not in env]
    empty = [k for k in expected if k in env and not env[k]]
    extra = [k for k in env if k not in expected]

    for key in missing:
        err(f"Missing: {key}")
    for key in empty:
        warn(f"Empty:  {key}")
    for key in extra:
        info(f"Extra:  {key}")

    print(out.paint(f"\ntemplate: {len(expected)} keys, env: {len(env)} keys", "dim"))
    if missing or empty:
        return 1
    ok("All expected keys are present and non-empty.")
    return 0


def _gpg() -> list[str]:
    if not shutil.which("gpg"):
        raise CommandError("gpg is not installed.")
    return ["gpg"]


class CommandError(RuntimeError):
    pass


def _passphrase_args(value: str | None) -> list[str]:
    if not value:
        return []
    return ["--batch", "--pinentry-mode", "loopback", "--passphrase", value]


def cmd_encrypt(args) -> int:
    env_path = _resolve_file(args)
    if not env_path.is_file():
        err(f"File not found: {env_path}")
        return 1
    output_path = Path(args.output).expanduser()

    cmd = _gpg() + _passphrase_args(args.passphrase) + ["--yes"]
    if args.recipient:
        cmd += ["--encrypt", "--recipient", args.recipient]
    else:
        cmd += ["--symmetric", "--cipher-algo", args.cipher]
    cmd += ["--output", str(output_path), str(env_path)]

    proc = subprocess.run(cmd, text=True)
    if proc.returncode != 0:
        err("Encryption failed.")
        return 1
    ok(f"Encrypted → {output_path}")
    return 0


def cmd_decrypt(args) -> int:
    input_path = Path(args.input).expanduser()
    if not input_path.is_file():
        err(f"File not found: {input_path}")
        return 1
    output_path = Path(args.output).expanduser()

    cmd = (
        _gpg()
        + _passphrase_args(args.passphrase)
        + ["--yes", "--decrypt", "--output", str(output_path), str(input_path)]
    )
    proc = subprocess.run(cmd, text=True)
    if proc.returncode != 0:
        err("Decryption failed.")
        return 1
    ok(f"Decrypted → {output_path}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Manage .env files",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    parser.add_argument(
        "--color", choices=("auto", "always", "never"), default="auto",
        help="Colorize output (default: auto)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--file", default=None,
        help="Path to the env file (default: ./.env)",
    )

    p = sub.add_parser("generate", parents=[common], help="Create a .env file")
    p.add_argument("--template", default="example.env", help="Template file to copy")
    p.add_argument("--force", action="store_true", help="Overwrite if it exists")
    p.set_defaults(func=cmd_generate)

    p = sub.add_parser("list", parents=[common], help="List keys (values masked)")
    p.add_argument("--show-secrets", action="store_true", help="Reveal values")
    p.add_argument("--sort", action="store_true", help="Sort keys alphabetically")
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("get", parents=[common], help="Print one value")
    p.add_argument("key")
    p.set_defaults(func=cmd_get)

    p = sub.add_parser("set", parents=[common], help="Create or update a key")
    p.add_argument("kv", metavar="KEY=VALUE")
    p.set_defaults(func=cmd_set)

    p = sub.add_parser("unset", parents=[common], help="Remove a key")
    p.add_argument("key")
    p.set_defaults(func=cmd_unset)

    p = sub.add_parser("check", parents=[common], help="Check against a template")
    p.add_argument("--template", default="example.env", help="Template to compare against")
    p.set_defaults(func=cmd_check)

    p = sub.add_parser("encrypt", parents=[common], help="gpg-encrypt the env file")
    p.add_argument("--output", default=None, help="Output file (default: <env>.gpg)")
    p.add_argument("--recipient", default=None, help="gpg recipient for asymmetric encryption")
    p.add_argument("--cipher", default="AES256", help="Cipher for symmetric encryption")
    p.add_argument("--passphrase", default=None, help="Passphrase (non-interactive)")
    p.set_defaults(func=cmd_encrypt)

    p = sub.add_parser("decrypt", help="gpg-decrypt an env file")
    p.add_argument("--input", required=True, help="Encrypted input file")
    p.add_argument("--output", default=None, help="Output file")
    p.add_argument("--passphrase", default=None, help="Passphrase (non-interactive)")
    p.set_defaults(func=cmd_decrypt)

    return parser


def _default_output(args, suffix: str) -> str:
    if args.output:
        return args.output
    path = _resolve_file(args) if hasattr(args, "file") else Path.cwd() / ".env"
    return str(path) + suffix


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.color == "always":
        out.enabled = True
    elif args.color == "never":
        out.enabled = False

    if args.command == "encrypt":
        args.output = _default_output(args, ".gpg")
    elif args.command == "decrypt" and not args.output:
        args.output = str(Path(args.input).with_suffix(""))

    try:
        return args.func(args)
    except CommandError as exc:
        err(str(exc))
        return 1


if __name__ == "__main__":
    sys.exit(main())
