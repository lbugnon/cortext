"""Low-level, crash-safe storage helpers for Cortex files."""

from __future__ import annotations

import hashlib
import os
import tempfile
from pathlib import Path

import frontmatter


def atomic_write_bytes(path: str | Path, data: bytes) -> None:
    """Replace *path* atomically after flushing the new contents to disk."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    mode = target.stat().st_mode if target.exists() else None
    fd, temporary_name = tempfile.mkstemp(
        dir=target.parent,
        prefix=".cor-write-",
        suffix=".tmp",
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        if mode is not None:
            os.chmod(temporary, mode)
        os.replace(temporary, target)
        directory_fd = os.open(target.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def atomic_write_text(path: str | Path, text: str) -> None:
    """UTF-8 variant of :func:`atomic_write_bytes`."""
    atomic_write_bytes(path, text.encode("utf-8"))


def atomic_write_post(path: str | Path, post: frontmatter.Post) -> None:
    """Serialize and atomically replace a frontmatter document."""
    content = frontmatter.dumps(post, sort_keys=False)
    atomic_write_text(path, content)


def content_revision(data: bytes) -> str:
    """Return the stable SHA-256 revision used by machine-facing commands."""
    return hashlib.sha256(data).hexdigest()
