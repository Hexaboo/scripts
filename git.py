#!/usr/bin/env python3

import argparse
import json
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path


class C:
    RESET = "\033[0m"
    BOLD = "\033[1m"
    RED = "\033[31m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    BLUE = "\033[34m"
    CYAN = "\033[36m"
    WHITE = "\033[37m"
    DIM = "\033[2m"


def ok(msg):
    print(f"{C.GREEN}[OK]{C.RESET}  {msg}")


def err(msg):
    print(f"{C.RED}[ERR]{C.RESET} {msg}", file=sys.stderr)


def warn(msg):
    print(f"{C.YELLOW}[WARN]{C.RESET} {msg}")


def info(msg):
    print(f"{C.CYAN}[INFO]{C.RESET} {msg}")


def step(msg):
    print(f"{C.BLUE}[....]{C.RESET} {msg}")


def header(title):
    bar = "=" * (len(title) + 4)
    print(f"\n{C.BOLD}{C.WHITE}{bar}{C.RESET}")
    print(f"{C.BOLD}{C.WHITE}  {title}{C.RESET}")
    print(f"{C.BOLD}{C.WHITE}{bar}{C.RESET}\n")


def dim(msg):
    print(f"{C.DIM}{msg}{C.RESET}")


def run(cmd, check=True, capture=False):
    return subprocess.run(cmd, check=check, capture_output=capture, text=True)


def run_output(cmd):
    return run(cmd, capture=True).stdout.strip()


def git_config(key, value, scope="--global"):
    run(["git", "config", scope, key, value])


def confirm(prompt):
    answer = input(f"{C.YELLOW}{prompt} (y/n):{C.RESET} ").strip().lower()
    return answer in ("y", "yes")


def check_dependencies():
    deps = {
        "git": "Install Git: https://git-scm.com/downloads",
        "gh": "Install GitHub CLI: https://cli.github.com/",
        "jq": "Install jq (Linux): sudo apt install jq",
    }
    missing = False
    for cmd, hint in deps.items():
        if not shutil.which(cmd):
            err(f"Required command '{C.BOLD}{cmd}{C.RESET}{C.RED}' is not installed.")
            print(f"      {C.DIM}-> {hint}{C.RESET}")
            missing = True
    if missing:
        sys.exit(1)


def setup_login():
    header("Git Identity & Credential Setup with GitHub CLI")

    step("Logging into GitHub...")
    result = run(["gh", "auth", "login"], check=False)
    if result.returncode != 0:
        err("GitHub authentication failed. Exiting.")
        sys.exit(1)
    ok("GitHub authentication successful.")

    step("Fetching GitHub user info...")
    raw = run_output(["gh", "api", "user"])
    user_data = json.loads(raw)

    gh_username = user_data.get("login", "")
    gh_email = user_data.get("email") or ""

    if not gh_email or gh_email == "null":
        warn("Your GitHub email is not publicly visible.")
        gh_email = input(
            f"  {C.CYAN}Enter the email for Git commits:{C.RESET} "
        ).strip()

    print(f"\n  {C.BOLD}Git identity preview:{C.RESET}")
    print(f"  {C.DIM}Username:{C.RESET} {C.WHITE}{gh_username}{C.RESET}")
    print(f"  {C.DIM}Email:   {C.RESET} {C.WHITE}{gh_email}{C.RESET}")

    if not confirm("\nContinue with these settings?"):
        err("Setup aborted by user.")
        sys.exit(1)

    git_config("user.name", gh_username)
    git_config("user.email", gh_email)
    ok("Git user identity has been set.")

    print(f"\n  {C.BOLD}Choose Git credential helper:{C.RESET}")
    print(f"  {C.DIM}1){C.RESET} GitHub CLI {C.GREEN}(recommended){C.RESET}")
    print(f"  {C.DIM}2){C.RESET} Git Credential Manager (manager-core)")
    choice = input(f"  {C.CYAN}Enter choice [1 or 2]:{C.RESET} ").strip()

    if choice == "2":
        git_config("credential.helper", "manager-core")
        ok("Git credential helper set to: manager-core")
    else:
        git_config("credential.helper", "!gh auth git-credential")
        ok("Git credential helper set to: GitHub CLI")

    print(f"\n  {C.BOLD}Current Git config:{C.RESET}")
    print(
        f"  {C.DIM}User Name:        {C.RESET}{C.WHITE}{run_output(['git', 'config', '--global', 'user.name'])}{C.RESET}"
    )
    print(
        f"  {C.DIM}User Email:       {C.RESET}{C.WHITE}{run_output(['git', 'config', '--global', 'user.email'])}{C.RESET}"
    )
    print(
        f"  {C.DIM}Credential Helper:{C.RESET}{C.WHITE}{run_output(['git', 'config', '--global', 'credential.helper'])}{C.RESET}"
    )


def setup_aliases():
    header("Setting up Git Aliases")

    aliases = {
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

    for key, value in aliases.items():
        git_config(f"alias.{key}", value)
        print(
            f"  {C.GREEN}+{C.RESET} {C.BOLD}alias.{key:<4}{C.RESET} {C.DIM}={C.RESET} {C.WHITE}{value}{C.RESET}"
        )

    print()
    ok("All Git aliases have been configured.")


def setup_commit_hook():
    header("Installing Gerrit commit-msg Hook")

    hooks_dir = Path.home() / ".githooks"
    hook_url = "https://gerrit-review.googlesource.com/tools/hooks/commit-msg"
    hook_path = hooks_dir / "commit-msg"

    step("Downloading Gerrit Change-Id commit-msg hook...")
    hooks_dir.mkdir(parents=True, exist_ok=True)

    try:
        urllib.request.urlretrieve(hook_url, hook_path)
        hook_path.chmod(0o755)
        git_config("core.hooksPath", str(hooks_dir))
        ok("Hook installed and configured globally.")
        print(f"  {C.DIM}Path: {hook_path}{C.RESET}")
    except Exception as e:
        err(f"Failed to download Gerrit commit-msg hook: {e}")
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(
        description="Git setup utility",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    parser.add_argument(
        "-L",
        action="store_true",
        help="Setup Git user identity and credential helper (login)",
    )
    parser.add_argument(
        "-C", action="store_true", help="Install Gerrit Change-Id commit-msg hook"
    )
    parser.add_argument("-A", action="store_true", help="Setup Git aliases")

    args = parser.parse_args()

    if not any([args.L, args.C, args.A]):
        parser.print_help()
        sys.exit(1)

    check_dependencies()

    if args.L:
        setup_login()
    if args.A:
        setup_aliases()
    if args.C:
        setup_commit_hook()

    print(f"\n{C.BOLD}{C.GREEN}All requested setups are done.{C.RESET}\n")


if __name__ == "__main__":
    main()
