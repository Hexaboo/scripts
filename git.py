#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
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


def dim(msg: str) -> None:
    print(out.paint(msg, "dim"))


def header(title: str) -> None:
    bar = "=" * (len(title) + 4)
    print("\n" + out.paint(bar, "white"))
    print(out.paint("  " + title, "white"))
    print(out.paint(bar, "white") + "\n")


class CommandError(RuntimeError):
    """Raised when an external command fails."""


def run(
    args: Sequence[str],
    *,
    allow_fail: bool = False,
    capture: bool = True,
) -> subprocess.CompletedProcess[str]:
    """Run an external command.

    Raises CommandError on non-zero exit unless allow_fail=True.
    """
    try:
        proc = subprocess.run(
            [str(a) for a in args],
            check=False,
            capture_output=capture,
            text=True,
        )
    except OSError as exc:
        raise CommandError(f"failed to start {args[0]}: {exc}") from exc

    if proc.returncode != 0 and not allow_fail:
        detail = ""
        if capture:
            detail = (proc.stderr or proc.stdout or "").strip()
        message = (
            f"command failed ({proc.returncode}): "
            f"{' '.join(map(str, args))}"
        )
        if detail:
            message += f"\n{detail}"
        raise CommandError(message)
    return proc


def output(args: Sequence[str]) -> str:
    """Run a command and return its stripped stdout."""
    return run(args).stdout.strip()


def git_config(key: str, value: str, scope: str = "--global") -> None:
    run(["git", "config", scope, key, value])


def git_config_get(key: str, scope: str = "--global", default: str = "") -> str:
    proc = run(["git", "config", scope, key], allow_fail=True)
    return proc.stdout.strip() if proc.returncode == 0 else default

def ask(prompt: str) -> str:
    return input(f"{out.paint(prompt, 'yellow')} ").strip()


def confirm(prompt: str) -> bool:
    return ask(prompt + " (y/n)").lower() in ("y", "yes")


DEPENDENCIES = {
    "git": "Install Git: https://git-scm.com/downloads",
    "gh": "Install GitHub CLI: https://cli.github.com/",
    "jq": "Install jq (Linux): sudo apt install jq",
}


def check_dependencies(deps: dict[str, str] | None = None) -> None:
    missing = []
    for cmd, hint in (deps or DEPENDENCIES).items():
        if not shutil.which(cmd):
            err(f"Required command '{cmd}' is not installed.")
            print(f"      -> {hint}")
            missing.append(cmd)
    if missing:
        raise CommandError("missing required dependencies: " + ", ".join(missing))


def setup_login(username: str = "", email: str = "") -> None:
    header("Git Identity & Credential Setup with GitHub CLI")

    existing_name = git_config_get("user.name")
    existing_email = git_config_get("user.email")
    if existing_name and existing_email and not username and not email:
        if not confirm("Git identity already configured; reconfigure?"):
            info("Keeping existing Git identity.")
            return

    if username and email:
        gh_username, gh_email = username, email
    else:
        step("Logging into GitHub...")
        proc = run(["gh", "auth", "login"], allow_fail=True, capture=False)
        if proc.returncode != 0:
            raise CommandError("GitHub authentication failed.")
        ok("GitHub authentication successful.")

        step("Fetching GitHub user info...")
        user_data = json.loads(output(["gh", "api", "user"]))
        gh_username = username or user_data.get("login", "")
        gh_email = email or user_data.get("email") or ""

        if not gh_email:
            warn("Your GitHub email is not publicly visible.")
            gh_email = ask("Enter the email for Git commits:")

    print("\n  Git identity preview:")
    print(f"  Username: {gh_username}")
    print(f"  Email:    {gh_email}")

    if not confirm("\nContinue with these settings?"):
        raise CommandError("Setup aborted by user.")

    git_config("user.name", gh_username)
    git_config("user.email", gh_email)
    ok("Git user identity has been set.")

    print("\n  Choose Git credential helper:")
    print("  1) GitHub CLI (recommended)")
    print("  2) Git Credential Manager (manager-core)")
    choice = ask("Enter choice [1 or 2]:")

    if choice == "2":
        git_config("credential.helper", "manager-core")
        ok("Git credential helper set to: manager-core")
    else:
        git_config("credential.helper", "!gh auth git-credential")
        ok("Git credential helper set to: GitHub CLI")

    print("\n  Current Git config:")
    print(f"  User Name:         {git_config_get('user.name')}")
    print(f"  User Email:        {git_config_get('user.email')}")
    print(f"  Credential Helper: {git_config_get('credential.helper')}")


ALIASES = {
    "c": "commit -s",
    "cam": "commit --amend",
    "cm": "commit",
    "csm": "commit -s -m",
    "ca": "cherry-pick --abort",
    "cr": "cherry-pick --signoff",
    "p": "push -f",
    "cc": "cherry-pick --continue",
    "cs": "cherry-pick --skip",
    "cp": "cherry-pick",
    "r": "revert",
    "rc": "revert --continue",
    "ro": "remote rm origin",
    "ra": "remote add origin",
    "s": "switch -c",
    "b": "branch",
    "rh": "reset --hard",
    "ch": "checkout",
    "f": "fetch",
    "m": "merge",
}


def setup_aliases() -> None:
    header("Setting up Git Aliases")
    changed = 0
    for key, value in ALIASES.items():
        if git_config_get(f"alias.{key}") == value:
            continue
        git_config(f"alias.{key}", value)
        changed += 1
        print(
            f"  {out.paint('+', 'green')} "
            f"{out.paint(f'alias.{key:<4}', 'bold')} "
            f"{out.paint('=', 'dim')} "
            f"{out.paint(value, 'white')}"
        )
    print()
    if changed == 0:
        info("All Git aliases are already configured.")
    else:
        ok(f"{changed} Git alias(es) updated.")


GERRIT_HOOK_URL = "https://gerrit-review.googlesource.com/tools/hooks/commit-msg"
DEFAULT_HOOKS_DIR = Path.home() / ".githooks"


def setup_commit_hook() -> None:
    header("Installing Gerrit commit-msg Hook")
    hooks_dir = DEFAULT_HOOKS_DIR
    hook_path = hooks_dir / "commit-msg"

    if hook_path.exists() and git_config_get("core.hooksPath") == str(hooks_dir):
        info(f"Gerrit commit-msg hook already installed at {hook_path}.")
        return

    step("Downloading Gerrit Change-Id commit-msg hook...")
    hooks_dir.mkdir(parents=True, exist_ok=True)
    try:
        urllib.request.urlretrieve(GERRIT_HOOK_URL, hook_path)
    except Exception as exc:
        raise CommandError(f"Failed to download Gerrit commit-msg hook: {exc}") from exc

    hook_path.chmod(0o755)
    git_config("core.hooksPath", str(hooks_dir))
    ok("Hook installed and configured globally.")
    print(f"  Path: {hook_path}")


SSH_DIR = Path.home() / ".ssh"
PUBKEY = SSH_DIR / "id_ed25519.pub"
PRIVKEY = SSH_DIR / "id_ed25519"
ALLOWED_SIGNERS = SSH_DIR / "allowed_signers"
GITHUB_KEYS_URL = "https://github.com/settings/keys"


def _read_pubkey(pubkey_path: Path) -> str:
    if not pubkey_path.exists():
        raise CommandError(f"Public key not found: {pubkey_path}")
    content = pubkey_path.read_text().strip()
    if not content.startswith("ssh-ed25519"):
        raise CommandError(f"{pubkey_path} does not look like an ed25519 public key.")
    return content


def _paste_pubkey() -> None:
    step("Paste your id_ed25519.pub content below (single line, then Enter):")
    content = ask("> ")
    if not content.startswith("ssh-ed25519"):
        raise CommandError("Does not look like a valid ed25519 public key.")

    SSH_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    PUBKEY.write_text(content + "\n")
    PUBKEY.chmod(0o644)
    ok(f"Public key written to {PUBKEY}")

    if PRIVKEY.exists() or not confirm("Paste private key (id_ed25519) as well?"):
        return

    step("Paste your id_ed25519 private key below.")
    info("Multiline input — type END on a new line when done:")
    lines = []
    while True:
        line = input()
        if line.strip() == "END":
            break
        lines.append(line)
    privkey = "\n".join(lines) + "\n"
    if "BEGIN OPENSSH PRIVATE KEY" not in privkey:
        raise CommandError("Does not look like a valid OpenSSH private key.")
    PRIVKEY.write_text(privkey)
    PRIVKEY.chmod(0o600)
    ok(f"Private key written to {PRIVKEY}")


def _restore_from_path() -> None:
    src = Path(ask("Path to .pub file:")).expanduser()
    if not src.exists():
        raise CommandError(f"File not found: {src}")

    SSH_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    shutil.copy2(src, PUBKEY)
    PUBKEY.chmod(0o644)

    src_priv = src.with_suffix("")
    if src_priv.exists() and not PRIVKEY.exists():
        shutil.copy2(src_priv, PRIVKEY)
        PRIVKEY.chmod(0o600)
        ok(f"Private key also restored to {PRIVKEY}")
    ok(f"Public key restored to {PUBKEY}")


def _generate_keypair() -> None:
    email = git_config_get("user.email")
    if not email:
        email = ask("Enter email for key comment:")
    step(f"Generating ed25519 keypair for {email} ...")
    SSH_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    run(
        [
            "ssh-keygen", "-t", "ed25519",
            "-C", email,
            "-f", str(PRIVKEY),
            "-N", "",
        ]
    )
    ok(f"Keypair generated: {PRIVKEY} / {PUBKEY}")


def _ensure_pubkey() -> Path:
    if PUBKEY.exists():
        ok(f"Found existing public key: {PUBKEY}")
        return PUBKEY

    warn("id_ed25519.pub not found in ~/.ssh/")
    print("\n  Options:")
    print("  1) Paste public key content manually")
    print("  2) Provide path to existing .pub file")
    print("  3) Generate a new ed25519 keypair")
    choice = ask("Enter choice [1/2/3]:")

    if choice == "1":
        _paste_pubkey()
    elif choice == "2":
        _restore_from_path()
    elif choice == "3":
        _generate_keypair()
    else:
        raise CommandError("Invalid choice.")

    _read_pubkey(PUBKEY)
    return PUBKEY


def _upload_signing_key(pubkey_path: Path) -> None:
    if not shutil.which("gh"):
        warn(f"gh not found. Add your signing key manually at: {GITHUB_KEYS_URL}")
        return

    if not confirm("\nUpload this key to GitHub as a signing key?"):
        return

    title = ask("Key title (e.g. 'ServerHive signing'):") or "SSH signing key"
    proc = run(
        [
            "gh", "ssh-key", "add",
            str(pubkey_path),
            "--type", "signing",
            "--title", title,
        ],
        allow_fail=True,
    )
    if proc.returncode == 0:
        ok(f"Signing key '{title}' uploaded to GitHub.")
    else:
        warn(f"gh ssh-key add failed. You may need to add it manually at: {GITHUB_KEYS_URL}")


def _configure_signing(pubkey_path: Path, pubkey_content: str) -> None:
    step("Configuring Git for SSH commit signing...")
    git_config("gpg.format", "ssh")
    git_config("user.signingKey", str(pubkey_path))
    git_config("commit.gpgSign", "true")
    git_config("tag.gpgSign", "true")

    email = git_config_get("user.email")
    if not email:
        return

    entry = f"{email} {pubkey_content}\n"
    existing = ALLOWED_SIGNERS.read_text() if ALLOWED_SIGNERS.exists() else ""
    if pubkey_content not in existing:
        with ALLOWED_SIGNERS.open("a") as fh:
            fh.write(entry)
        ok(f"Added to allowed_signers: {ALLOWED_SIGNERS}")
    else:
        info("Key already present in allowed_signers.")
    git_config("gpg.ssh.allowedSignersFile", str(ALLOWED_SIGNERS))


def setup_signing() -> None:
    header("SSH Commit Signing Setup")
    pubkey_path = _ensure_pubkey()
    pubkey_content = _read_pubkey(pubkey_path)

    print("\n  Public key:")
    print(f"  {pubkey_content}")

    _upload_signing_key(pubkey_path)
    _configure_signing(pubkey_path, pubkey_content)

    print("\n  Signing config summary:")
    print(f"  gpg.format:              ssh")
    print(f"  user.signingKey:         {pubkey_path}")
    print("  commit.gpgSign:          true")
    print("  tag.gpgSign:             true")
    if git_config_get("user.email"):
        print(f"  gpg.ssh.allowedSigners: {ALLOWED_SIGNERS}")
    ok("SSH commit signing configured.")


def configure_color(mode: str) -> None:
    if mode == "always":
        out.enabled = True
    elif mode == "never":
        out.enabled = False


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Git setup utility",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    parser.add_argument(
        "-L", "--login", action="store_true",
        help="Setup Git user identity and credential helper (login)",
    )
    parser.add_argument(
        "-A", "--aliases", action="store_true",
        help="Setup Git aliases",
    )
    parser.add_argument(
        "-C", "--hook", action="store_true",
        help="Install Gerrit Change-Id commit-msg hook",
    )
    parser.add_argument(
        "-S", "--signing", action="store_true",
        help="Setup SSH commit signing with id_ed25519.pub",
    )
    parser.add_argument(
        "--all", action="store_true",
        help="Run every setup (login, aliases, hook, signing)",
    )
    parser.add_argument(
        "--color", choices=("auto", "always", "never"), default="auto",
        help="Colorize output (default: auto)",
    )
    parser.add_argument(
        "--username", default="",
        help="Git username (skips GitHub lookup during login)",
    )
    parser.add_argument(
        "--email", default="",
        help="Git email (skips GitHub lookup during login)",
    )
    return parser


def print_summary() -> None:
    header("Configuration Summary")
    print(f"  user.name:          {git_config_get('user.name') or '-'}")
    print(f"  user.email:         {git_config_get('user.email') or '-'}")
    print(f"  credential.helper:  {git_config_get('credential.helper') or '-'}")
    print(f"  core.hooksPath:     {git_config_get('core.hooksPath') or '-'}")
    print(f"  gpg.format:         {git_config_get('gpg.format') or '-'}")
    print(f"  user.signingKey:    {git_config_get('user.signingKey') or '-'}")
    print(f"  commit.gpgSign:     {git_config_get('commit.gpgSign') or '-'}")


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    configure_color(args.color)

    if args.all:
        args.login = args.aliases = args.hook = args.signing = True

    if not any([args.login, args.aliases, args.hook, args.signing]):
        parser.print_help()
        return 1

    try:
        check_dependencies()
        if args.login:
            setup_login(username=args.username, email=args.email)
        if args.aliases:
            setup_aliases()
        if args.hook:
            setup_commit_hook()
        if args.signing:
            setup_signing()
        print_summary()
    except CommandError as exc:
        err(str(exc))
        return 1

    print(f"\n{out.paint('All requested setups are done.', 'green')}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
