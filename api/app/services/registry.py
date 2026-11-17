"""Read the published image catalogue from a container registry (GHCR).

The admin UI needs to answer one question without pulling gigabytes: "is there a
newer dsh-docker build than the ones I have imported?". That is a registry query,
not a Docker daemon query, so it goes through the Distribution HTTP API:

    GET /v2/                                  -> 401 + WWW-Authenticate challenge
    GET <realm>?scope=repository:o/r:pull     -> bearer token (anonymous is fine
                                                 for public packages)
    GET /v2/o/r/tags/list                     -> tag names
    GET /v2/o/r/manifests/<tag>               -> digest + layers (size)
    GET /v2/o/r/blobs/<config digest>         -> created timestamp

Every network call is injectable (`http=`) so the tests never touch the network.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import httpx

from ..config import settings

log = logging.getLogger("dsh.registry")

MANIFEST_ACCEPT = ", ".join(
    [
        "application/vnd.oci.image.index.v1+json",
        "application/vnd.docker.distribution.manifest.list.v2+json",
        "application/vnd.oci.image.manifest.v1+json",
        "application/vnd.docker.distribution.manifest.v2+json",
    ]
)

# dsh-docker publishes `<dsh version>`, `<version>-slim`, `latest`, `sha-<short>`
# and, on a git tag, `v<version>`. Only the readable version tags are candidates.
VERSION_RE = re.compile(r"^v?(\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?)$")
CHANNEL_TAGS = {"latest", "main", "edge", "dev", "nightly"}


class RegistryError(RuntimeError):
    """The registry could not be queried (network, auth, or an unknown repo)."""


@dataclass
class RemoteTag:
    tag: str
    version: str
    variant: str
    digest: str | None = None
    platform_digest: str | None = None
    size_bytes: int = 0
    created_at: datetime | None = None
    platform: str | None = None
    labels: dict[str, str] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "tag": self.tag,
            "version": self.version,
            "variant": self.variant,
            "digest": self.digest,
            "platform_digest": self.platform_digest,
            "size_bytes": self.size_bytes,
            "created_at": self.created_at.isoformat() + "Z" if self.created_at else None,
            "platform": self.platform,
            "labels": self.labels,
        }


def parse_repository(reference: str) -> tuple[str, str]:
    """Split `ghcr.io/owner/repo` into (registry host, repository path)."""
    cleaned = reference.strip().rstrip("/")
    if "://" in cleaned:
        cleaned = cleaned.split("://", 1)[1]
    cleaned = cleaned.split("@", 1)[0]
    # Strip a trailing :tag if someone pasted a full image reference.
    parts = cleaned.split("/")
    if len(parts) < 2:
        raise RegistryError(f"invalid repository reference: {reference!r}")
    if ":" in parts[-1]:
        parts[-1] = parts[-1].rsplit(":", 1)[0]
    host, repo = parts[0], "/".join(parts[1:])
    if not repo or "/" not in repo and "." not in host and ":" not in host:
        # e.g. "owner/repo" without a registry: assume GHCR.
        host, repo = "ghcr.io", cleaned
    return host.lower(), repo


def split_tag(tag: str) -> tuple[str, str] | None:
    """Map a published tag to (version, variant); None when it is not a version tag."""
    variant = "full"
    base = tag
    if tag.endswith("-slim"):
        variant = "slim"
        base = tag[: -len("-slim")]
    match = VERSION_RE.match(base)
    if not match:
        return None
    return match.group(1), variant


def version_key(version: str) -> tuple:
    """Sort key: numeric parts ascending, prereleases below the final release."""
    core, _, pre = version.partition("-")
    numbers = []
    for part in core.split("."):
        try:
            numbers.append(int(part))
        except ValueError:
            numbers.append(0)
    while len(numbers) < 3:
        numbers.append(0)
    # A prerelease ("rc", "beta") sorts before the release with the same numbers.
    return (*numbers, 0 if pre else 1, pre)


def parse_timestamp(raw: str | None) -> datetime | None:
    """Parse a registry timestamp.

    BuildKit writes nanosecond precision ("...28.166507199Z") while
    `datetime.fromisoformat` accepts at most 6 fractional digits, so the fraction
    is truncated rather than dropped (the date stays correct either way).
    """
    if not raw:
        return None
    value = raw.strip().replace("Z", "+00:00")
    # Keep at most 6 fractional digits; fromisoformat rejects the 9 that BuildKit
    # writes ("...T05:59:28.166507199Z").
    value = re.sub(r"(\.\d{6})\d+", r"\1", value)
    try:
        return datetime.fromisoformat(value).replace(tzinfo=None)
    except ValueError:
        return None


class RegistryClient:
    def __init__(
        self,
        reference: str | None = None,
        *,
        username: str | None = None,
        password: str | None = None,
        http: httpx.Client | None = None,
        platform: str | None = None,
    ) -> None:
        self.reference = (reference or settings.dsh_image_repository).strip()
        self.host, self.repository = parse_repository(self.reference)
        self.scheme = "http" if self.host.startswith(("localhost", "127.0.0.1")) else "https"
        self.username = username if username is not None else settings.registry_username
        self.password = password if password is not None else settings.registry_password
        self.platform = platform or settings.dsh_image_platform
        # Do not inherit ambient proxy settings: on Windows a duplicated
        # no_proxy entry (e.g. "[::1]") makes httpx raise InvalidURL at client
        # construction, and a console-side registry probe should never be
        # silently routed through a developer's VPN proxy. Set REGISTRY_PROXY
        # when a proxy is genuinely required.
        self._http = http or httpx.Client(
            timeout=20.0,
            follow_redirects=True,
            trust_env=False,
            proxy=settings.registry_proxy or None,
        )
        self._token: str | None = None

    @property
    def base(self) -> str:
        return f"{self.scheme}://{self.host}"

    def _auth(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._token}"} if self._token else {}

    def _authenticate(self) -> None:
        """Resolve a bearer token from the registry challenge.

        Anonymous access works for public packages, which is the default for
        `ghcr.io/<owner>/dsh-docker`.
        """
        try:
            probe = self._http.get(f"{self.base}/v2/")
        except httpx.ConnectError as exc:
            raise RegistryError(f"无法连接 {self.host}，请检查网络或设置 REGISTRY_PROXY") from exc
        except httpx.HTTPError as exc:
            raise RegistryError(f"cannot reach {self.host}: {exc}") from exc

        header = probe.headers.get("www-authenticate", "")
        realm = re.search(r'realm="([^"]+)"', header)
        if probe.status_code == 200 and not realm:
            return  # registry allows anonymous reads outright
        if not realm:
            if probe.status_code == 401 and self.username:
                # Some registries accept basic auth instead of a token service.
                self._token = None
                return
            raise RegistryError(
                f"{self.host} returned {probe.status_code} without an auth challenge"
            )

        params = {"scope": f"repository:{self.repository}:pull"}
        service = re.search(r'service="([^"]+)"', header)
        if service:
            params["service"] = service.group(1)
        auth = (self.username, self.password) if self.username and self.password else None
        try:
            response = self._http.get(realm.group(1), params=params, auth=auth)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise RegistryError(f"registry authentication failed: {exc}") from exc
        payload = response.json()
        token = payload.get("token") or payload.get("access_token")
        if not token:
            raise RegistryError("registry returned no token")
        self._token = token

    def _get(self, path: str, **kwargs) -> httpx.Response:
        if self._token is None:
            self._authenticate()
        headers = {**self._auth(), **(kwargs.pop("headers", None) or {})}
        basic = (
            httpx.BasicAuth(self.username, self.password)
            if not self._token and self.username and self.password
            else None
        )
        try:
            response = self._http.get(f"{self.base}{path}", headers=headers, auth=basic, **kwargs)
        except httpx.HTTPError as exc:
            raise RegistryError(f"registry request failed: {exc}") from exc
        if response.status_code == 401:
            # Token expired mid-flight: re-authenticate once.
            self._token = None
            self._authenticate()
            headers.update(self._auth())
            response = self._http.get(f"{self.base}{path}", headers=headers, **kwargs)
        if response.status_code == 404:
            raise RegistryError(f"{self.repository} not found on {self.host} (404)")
        if response.status_code >= 400:
            raise RegistryError(f"registry returned {response.status_code} for {path}")
        return response

    def list_tags(self, pages: int = 5) -> list[str]:
        tags: list[str] = []
        path = f"/v2/{self.repository}/tags/list?n=200"
        for _ in range(pages):
            response = self._get(path)
            payload = response.json()
            tags.extend(payload.get("tags") or [])
            link = response.headers.get("link", "")
            match = re.search(r'<([^>]+)>;\s*rel="next"', link)
            if not match:
                break
            path = match.group(1)
            if path.startswith(self.base):
                path = path[len(self.base) :]
        return sorted(set(tags))

    def tag_info(self, tag: str) -> RemoteTag:
        parsed = split_tag(tag)
        version, variant = parsed if parsed else (tag, "other")

        response = self._get(
            f"/v2/{self.repository}/manifests/{tag}", headers={"Accept": MANIFEST_ACCEPT}
        )
        digest = response.headers.get("docker-content-digest")
        manifest = response.json()
        media_type = manifest.get("mediaType") or response.headers.get("content-type", "")

        platform_digest = None
        if "index" in media_type or "manifest.list" in media_type:
            chosen = None
            for entry in manifest.get("manifests") or []:
                platform = entry.get("platform") or {}
                if platform.get("os") == "linux" and platform.get("architecture") == self.platform:
                    chosen = entry
                    break
            if chosen is None and manifest.get("manifests"):
                chosen = manifest["manifests"][0]
            if chosen:
                platform_digest = chosen.get("digest")
                sub = self._get(
                    f"/v2/{self.repository}/manifests/{platform_digest}",
                    headers={"Accept": MANIFEST_ACCEPT},
                )
                manifest = sub.json()
                media_type = manifest.get("mediaType") or sub.headers.get("content-type", "")

        size = sum(int(layer.get("size") or 0) for layer in manifest.get("layers") or [])
        created = None
        labels: dict[str, str] = {}
        config = manifest.get("config") or {}
        if config.get("digest"):
            try:
                blob = self._get(f"/v2/{self.repository}/blobs/{config['digest']}").json()
                created = parse_timestamp(blob.get("created"))
                labels = (blob.get("config") or {}).get("Labels") or {}
            except (RegistryError, ValueError, KeyError):
                log.debug("could not read config blob for %s", tag)

        return RemoteTag(
            tag=tag,
            version=version,
            variant=variant,
            digest=digest,
            platform_digest=platform_digest,
            size_bytes=size,
            created_at=created,
            platform=f"linux/{self.platform}",
            labels=labels,
        )

    def candidates(self, limit: int = 30) -> list[RemoteTag]:
        """Version tags, newest first. Channel tags (`latest`) and `sha-*` are skipped."""
        result: list[RemoteTag] = []
        for tag in self.list_tags():
            if tag in CHANNEL_TAGS:
                continue
            if split_tag(tag) is None:
                continue
            try:
                result.append(self.tag_info(tag))
            except RegistryError as exc:
                log.warning("skipping %s: %s", tag, exc)
        result.sort(key=lambda item: version_key(item.version), reverse=True)
        return result[:limit]

    def latest(self) -> RemoteTag | None:
        for tag in self.candidates():
            if tag.variant == "full":
                return tag
        return None
