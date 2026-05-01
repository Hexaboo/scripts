#!/usr/bin/env python3

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path


class C:
    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    RED = "\033[31m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    BLUE = "\033[34m"
    CYAN = "\033[36m"
    WHITE = "\033[37m"


def ok(msg):
    print(f"{C.GREEN}[OK]{C.RESET}  {msg}")


def err(msg):
    print(f"{C.RED}[ERR]{C.RESET} {msg}", file=sys.stderr)


def warn(msg):
    print(f"{C.YELLOW}[WARN]{C.RESET} {msg}")


def step(msg):
    print(f"{C.BLUE}[....]{C.RESET} {msg}")


def header(title):
    bar = "=" * (len(title) + 4)
    print(f"\n{C.BOLD}{C.WHITE}{bar}{C.RESET}")
    print(f"{C.BOLD}{C.WHITE}  {title}{C.RESET}")
    print(f"{C.BOLD}{C.WHITE}{bar}{C.RESET}\n")


def run(cmd, check=True, capture=False):
    return subprocess.run(cmd, check=check, capture_output=capture, text=True)


def run_output(cmd, check=False):
    result = subprocess.run(cmd, check=check, capture_output=True, text=True)
    return result.stdout.strip()


def check_gpg():
    if not shutil.which("gpg"):
        err("GPG is not installed. Please install it first.")
        sys.exit(1)


def list_secret_keys():
    """Parse gpg --list-secret-keys and return list of dicts with key info."""
    output = run_output(
        ["gpg", "--list-secret-keys", "--keyid-format=long", "--with-colons"]
    )
    keys = []
    current = {}

    for line in output.splitlines():
        fields = line.split(":")
        record = fields[0]

        if record == "sec":
            current = {
                "key_id": fields[4] if len(fields) > 4 else "",
                "created": fields[5] if len(fields) > 5 else "",
                "uids": [],
            }
            keys.append(current)
        elif record == "uid" and current:
            uid = fields[9] if len(fields) > 9 else ""
            if uid:
                current["uids"].append(uid)

    return keys


def pick_key(keys):
    """Let user pick from multiple GPG keys. Returns the chosen key_id."""
    print(f"\n  {C.BOLD}Multiple GPG secret keys found:{C.RESET}\n")

    for i, k in enumerate(keys, start=1):
        uid_str = keys[i - 1]["uids"][0] if keys[i - 1]["uids"] else "(no uid)"
        print(
            f"  {C.CYAN}[{i}]{C.RESET} {C.BOLD}{k['key_id']}{C.RESET}  {C.DIM}{uid_str}{C.RESET}"
        )

    print()
    while True:
        choice = input(f"  {C.YELLOW}Select key [1-{len(keys)}]:{C.RESET} ").strip()
        if choice.isdigit() and 1 <= int(choice) <= len(keys):
            return keys[int(choice) - 1]["key_id"]
        warn(f"Invalid choice. Enter a number between 1 and {len(keys)}.")


def resolve_key_id():
    """
    Returns the GPG key ID to use:
    - If 0 keys found: exit with error.
    - If 1 key found: use it automatically.
    - If >1 keys found: prompt user to pick.
    """
    keys = list_secret_keys()

    if not keys:
        err("No secret GPG keys found on this system.")
        sys.exit(1)
    elif len(keys) == 1:
        key_id = keys[0]["key_id"]
        uid_str = keys[0]["uids"][0] if keys[0]["uids"] else "(no uid)"
        ok(f"Using GPG key: {C.BOLD}{key_id}{C.RESET} {C.DIM}({uid_str}){C.RESET}")
        return key_id
    else:
        return pick_key(keys)


def do_backup():
    header("GPG Key Backup")

    key_id = input(f"  {C.CYAN}Enter your GPG Key ID:{C.RESET} ").strip()
    if not key_id:
        err("No key ID provided.")
        sys.exit(1)

    out_dir = Path("gpg-backup")
    out_dir.mkdir(exist_ok=True)

    pub_path = out_dir / "public-key.asc"
    priv_path = out_dir / "private-key.asc"

    step(f"Exporting public key  -> {pub_path}")
    pub_result = run(["gpg", "--export", "--armor", key_id], check=False, capture=True)
    if pub_result.returncode != 0 or not pub_result.stdout.strip():
        err(f"Failed to export public key for ID '{key_id}'. Check the key ID.")
        sys.exit(1)
    pub_path.write_text(pub_result.stdout)
    ok("Public key exported.")

    step(f"Exporting private key -> {priv_path}")
    priv_result = run(
        ["gpg", "--export-secret-keys", "--armor", key_id], check=False, capture=True
    )
    if priv_result.returncode != 0 or not priv_result.stdout.strip():
        err(f"Failed to export private key for ID '{key_id}'.")
        sys.exit(1)
    priv_path.write_text(priv_result.stdout)
    ok("Private key exported.")

    print(f"\n  {C.BOLD}Backup contents:{C.RESET}")
    for f in sorted(out_dir.iterdir()):
        size = f.stat().st_size
        print(f"  {C.DIM}{str(f):<35}{C.RESET} {C.WHITE}{size:>8} bytes{C.RESET}")

    print()
    ok(f"Backup complete. Files saved to: {C.BOLD}{out_dir.resolve()}{C.RESET}")


def do_import():
    header("GPG Key Import")

    key_dir_str = input(
        f"  {C.CYAN}Enter directory path containing .asc key files:{C.RESET} "
    ).strip()
    key_dir = Path(key_dir_str)

    if not key_dir.is_dir():
        err(f"Directory '{key_dir}' does not exist.")
        sys.exit(1)

    asc_files = sorted(key_dir.glob("*.asc"))
    if not asc_files:
        warn(f"No .asc key files found in '{key_dir}'. Nothing imported.")
        sys.exit(1)

    imported = 0
    for keyfile in asc_files:
        step(f"Importing: {keyfile.name}")
        result = run(["gpg", "--import", str(keyfile)], check=False)
        if result.returncode == 0:
            ok(f"Imported: {keyfile.name}")
            imported += 1
        else:
            warn(f"Failed to import: {keyfile.name}")

    if imported == 0:
        err("No keys were successfully imported.")
        sys.exit(1)

    print(f"\n  {C.BOLD}Imported {imported} key file(s).{C.RESET}")

    print(f"\n  {C.BOLD}Detected secret keys:{C.RESET}")
    run(["gpg", "--list-secret-keys", "--keyid-format=long"], check=False)

    new_key_id = resolve_key_id()

    if not new_key_id:
        err("Failed to detect a GPG key ID.")
        sys.exit(1)

    run(["git", "config", "--global", "user.signingkey", new_key_id])
    run(["git", "config", "--global", "commit.gpgsign", "true"])
    ok("Git configured to sign commits with selected GPG key.")

    print(
        f"\n  {C.DIM}user.signingkey{C.RESET} = {C.WHITE}{C.BOLD}{new_key_id}{C.RESET}"
    )
    print(f"  {C.DIM}commit.gpgsign {C.RESET} = {C.WHITE}true{C.RESET}")

    bashrc = Path.home() / ".bashrc"
    gpg_tty_line = "export GPG_TTY=$(tty)"
    try:
        content = bashrc.read_text() if bashrc.exists() else ""
        if "GPG_TTY" not in content:
            with bashrc.open("a") as f:
                f.write(f"\n{gpg_tty_line}\n")
            ok(f"Added '{gpg_tty_line}' to ~/.bashrc")
        else:
            print(f"  {C.DIM}GPG_TTY already set in ~/.bashrc — skipped.{C.RESET}")
    except OSError as e:
        warn(f"Could not update ~/.bashrc: {e}")

    print()
    ok("Import complete.")


def main():
    parser = argparse.ArgumentParser(
        description="GPG key backup and import utility",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    parser.add_argument(
        "-b",
        "--backup",
        action="store_true",
        help="Export/backup GPG keys to ./gpg-backup/",
    )
    parser.add_argument(
        "-i",
        "--import",
        action="store_true",
        dest="import_keys",
        help="Import GPG keys from a directory and configure Git signing",
    )

    args = parser.parse_args()

    if not args.backup and not args.import_keys:
        parser.print_help()
        sys.exit(1)

    check_gpg()

    if args.backup:
        do_backup()

    if args.import_keys:
        do_import()

    print(f"\n{C.BOLD}{C.GREEN}All requested operations are done.{C.RESET}\n")


if __name__ == "__main__":
    main()
