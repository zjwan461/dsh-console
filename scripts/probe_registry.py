"""Hit the real registry once to prove the catalogue probe works end to end."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "api"))

from app.services.registry import RegistryClient, parse_repository  # noqa: E402

REPO = sys.argv[1] if len(sys.argv) > 1 else "ghcr.io/zjwan461/dsh-docker"

host, repo = parse_repository(REPO)
print(f"repository: {REPO}  -> host={host} path={repo}")

client = RegistryClient(REPO, username="", password="")
tags = client.list_tags()
print(f"tags ({len(tags)}): {tags}")

candidates = client.candidates()
print(f"\nversion candidates ({len(candidates)}):")
for item in candidates:
    mib = item.size_bytes / 1024 / 1024
    print(
        f"  {item.tag:<22} variant={item.variant:<5} "
        f"{mib:8.1f} MiB  created={item.created_at}  digest={str(item.digest)[:19]}"
    )

latest = client.latest()
print(f"\nlatest full build: {latest.tag if latest else None}")