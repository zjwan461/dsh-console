"""Fail if a tracked file or commit message looks like it carries deployment data.

This repository is public, so three things must never appear: credentials,
database DSNs with passwords, and the address of a real host. The check runs in
CI (see .github/workflows/security.yml) and can be run locally:

    python scripts/check_no_secrets.py
    python scripts/check_no_secrets.py --history   # also scan every commit

Private/loopback addresses (10/8, 172.16/12, 192.168/16, 127/8) and the
placeholder domain example.com are fine; documentation needs them.
"""

from __future__ import annotations

import argparse
import ipaddress
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

CREDENTIAL_PATTERNS = [
    (re.compile(r"ghp_[A-Za-z0-9]{20,}"), "GitHub personal access token"),
    (re.compile(r"github_pat_[A-Za-z0-9_]{20,}"), "GitHub fine-grained token"),
    (re.compile(r"AKIA[0-9A-Z]{16}"), "AWS access key id"),
    (re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"), "private key"),
]

# A connection string only leaks something when its host is a real endpoint.
# `mysql:3306` / `redis:6379` are compose service aliases (no dot), which is how
# every example in this repository is written.
DSN = re.compile(
    r"(?P<scheme>[a-z+]+)://(?P<user>[^\s:/@]+):(?P<password>[^\s:/@]+)@"
    r"(?P<host>[^\s/]+)",
    re.IGNORECASE,
)

IPV4 = re.compile(r"(?<![\w.])(\d{1,3}(?:\.\d{1,3}){3})(?![\w.])")

# Files that legitimately contain addresses or fake secrets.
ALLOWLIST = {
    "scripts/check_no_secrets.py",
    ".github/workflows/security.yml",
}

TEXT_SUFFIXES = {
    ".py", ".ts", ".vue", ".js", ".json", ".md", ".yml", ".yaml", ".toml",
    ".ini", ".sh", ".example", ".txt", ".css", ".html", ".mako", "", ".env",
}


def git(*args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=True,
    ).stdout


def is_public(address: str) -> bool | None:
    try:
        parsed = ipaddress.ip_address(address)
    except ValueError:
        return None
    if parsed.version != 4:
        return None
    if parsed.is_private or parsed.is_loopback or parsed.is_link_local:
        return False
    if parsed.is_multicast or parsed.is_reserved or parsed.is_unspecified:
        return False
    return True


def check_text(where: str, text: str) -> list[str]:
    problems: list[str] = []
    for pattern, label in CREDENTIAL_PATTERNS:
        for match in pattern.finditer(text):
            problems.append(f"{where}: possible {label}: {match.group(0)[:60]}")
    for match in DSN.finditer(text):
        host = match.group("host").split(":")[0]
        # Compose aliases (mysql, redis, host) and documentation hosts are fine.
        if "." not in host:
            continue
        if host.endswith(".example") or "example.com" in host or host == "localhost":
            continue
        parsed = is_public(host)
        if parsed is False:
            continue  # private range: an example, not a real deployment
        line = text[: match.start()].count("\n") + 1
        problems.append(
            f"{where}:{line}: connection string with credentials to {host}"
        )
    for match in IPV4.finditer(text):
        address = match.group(1)
        if is_public(address):
            line = text[: match.start()].count("\n") + 1
            problems.append(f"{where}:{line}: public address {address}")
    return problems


def scan_tracked() -> list[str]:
    problems: list[str] = []
    for name in git("ls-files").splitlines():
        if name in ALLOWLIST:
            continue
        path = ROOT / name
        if path.suffix not in TEXT_SUFFIXES:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        problems.extend(check_text(name, text))
    return problems


def scan_history() -> list[str]:
    problems: list[str] = []
    commits = git("rev-list", "--all").split()
    for sha in commits:
        message = git("log", "-1", "--format=%B", sha)
        problems.extend(check_text(f"commit {sha[:8]} message", message))
        diff = subprocess.run(
            ["git", "show", "--format=", sha],
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        ).stdout
        problems.extend(check_text(f"commit {sha[:8]} diff", diff or ""))
    return problems


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--history", action="store_true", help="also scan all commits")
    args = parser.parse_args()

    problems = scan_tracked()
    if args.history:
        problems.extend(scan_history())

    if problems:
        print("found deployment data that must not be committed:\n", file=sys.stderr)
        for item in problems:
            print(f"  {item}", file=sys.stderr)
        return 1
    print("clean: no credentials, no public addresses")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())