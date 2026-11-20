from __future__ import annotations

import io
import os
import tarfile

import pytest

from app.services.snapshots import _add_tree, _fix_owner, _safe_extract


def _tar(members: list[tuple[str, bytes | None, str | None]]) -> tarfile.TarFile:
    """Build an in-memory tar.

    Each entry is (name, content, linkname). `content=None` means a symlink whose
    target is `linkname`.
    """
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as tar:
        for name, content, link in members:
            info = tarfile.TarInfo(name)
            if content is None:
                info.type = tarfile.SYMTYPE
                info.linkname = link or ""
                tar.addfile(info)
            else:
                info.size = len(content)
                tar.addfile(info, io.BytesIO(content))
    buffer.seek(0)
    return tarfile.open(fileobj=buffer, mode="r")


def test_broken_symlink_does_not_abort_the_restore(tmp_path):
    """A snapshot taken before an image switch can hold links whose target is gone.

    The old code `os.chown`ed every entry, which follows symlinks, so a dangling
    `profiles/node_modules/<plugin>` raised ENOENT and failed the whole restore.
    """
    tar = _tar(
        [
            ("data/settings.yaml", b"ok", None),
            ("data/profiles/node_modules/dsh-minimax-h3", None, "/usr/lib/node_modules/gone"),
        ]
    )
    # The link target is absolute, which _safe_extract refuses by design; the
    # interesting case is a dangling *relative* link, which must be accepted.
    with pytest.raises(RuntimeError):
        _safe_extract(tar, str(tmp_path))

    tar = _tar(
        [
            ("data/settings.yaml", b"ok", None),
            ("data/profiles/node_modules/dsh-minimax-h3", None, "../../../nowhere/dsh-minimax-h3"),
        ]
    )
    root = tmp_path / "root"
    root.mkdir()
    _safe_extract(tar, str(root))
    # Fixing ownership must not raise even though the link target does not exist.
    _fix_owner(str(root))
    assert (root / "data" / "settings.yaml").read_bytes() == b"ok"

    if not _symlinks_supported(tmp_path):
        pytest.skip("this platform cannot create symlinks (Windows without privilege)")
    link = root / "data" / "profiles" / "node_modules" / "dsh-minimax-h3"
    assert link.is_symlink()


def _symlinks_supported(tmp_path) -> bool:
    probe = tmp_path / "probe-link"
    try:
        os.symlink("nowhere", probe)
    except (OSError, NotImplementedError):
        return False
    finally:
        if probe.is_symlink():
            probe.unlink()
    return True


def test_safe_extract_rejects_traversal(tmp_path):
    tar = _tar([("../../escape.txt", b"nope", None)])
    with pytest.raises(RuntimeError):
        _safe_extract(tar, str(tmp_path))


def test_safe_extract_rejects_a_link_that_escapes(tmp_path):
    tar = _tar([("data/evil", None, "../../../../etc/passwd")])
    root = tmp_path / "root"
    root.mkdir()
    with pytest.raises(RuntimeError):
        _safe_extract(tar, str(root))


def test_fix_owner_survives_a_vanished_entry(tmp_path, monkeypatch):
    """Even a weird inode must not fail the restore."""
    target = tmp_path / "a.txt"
    target.write_text("x", encoding="utf-8")

    real = getattr(os, "lchown", None) or getattr(os, "chown", None)
    if real is None:  # pragma: no cover - Windows without chown
        pytest.skip("no chown on this platform")

    def flaky(path, *_args):
        if str(path).endswith("a.txt"):
            raise FileNotFoundError(path)
        return real(path, *_args)

    monkeypatch.setattr(os, "lchown", flaky, raising=False)
    monkeypatch.setattr(os, "chown", flaky, raising=False)
    _fix_owner(str(tmp_path))


def test_add_tree_still_excludes_caches(tmp_path):
    source = tmp_path / "data"
    (source / "node_modules" / ".cache").mkdir(parents=True)
    (source / "node_modules" / ".cache" / "junk.bin").write_bytes(b"x" * 32)
    (source / "node_modules" / "keep.js").write_text("ok", encoding="utf-8")
    (source / ".runtime").mkdir()
    (source / ".runtime" / "bind").write_text("x", encoding="utf-8")

    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as tar:
        _add_tree(tar, str(source), "data")
    buffer.seek(0)
    with tarfile.open(fileobj=buffer) as tar:
        names = tar.getnames()

    assert "data/node_modules/keep.js" in names
    assert not [name for name in names if ".cache" in name]
    assert not [name for name in names if ".runtime" in name]