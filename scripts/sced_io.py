#!/usr/bin/env python3
"""Shared I/O helpers for SCED-tools scripts.

`atomic_write_json` was previously copy-pasted verbatim into the
korean-image-apply gallery-path scripts (apply / swap / upload). Centralising
it here means a single fix (e.g. fsync, permission handling) propagates to all
callers instead of silently diverging.
"""

import json
import os
import tempfile
from pathlib import Path


def atomic_write_json(path: Path, data) -> None:
    """Write `data` as 2-space-indented UTF-8 JSON to `path` atomically.

    A same-directory temp file is written first and then `os.replace`d over the
    destination, so a crash mid-write never leaves a partially-written target.
    The temp file is removed on any failure before the exception is re-raised.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, ensure_ascii=False)
            fh.write("\n")
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def atomic_write_text(path: Path, text: str) -> None:
    """Write `text` to `path` atomically (same-dir temp + os.replace).

    Unlike `atomic_write_json`, the string is written verbatim — no trailing
    newline is appended and the structure is not re-encoded — so the caller
    keeps full control over the exact byte shape. This is required for the
    minified combined JSON whose on-disk byte form must match the existing file.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def js_safe(text: str) -> str:
    """Escape a ``json.dumps`` string for safe embedding in an HTML <script> island.

    Prevents a premature ``</script>`` close and escapes the two raw Unicode
    line/paragraph separators (U+2028 / U+2029) that are legal in JSON but break
    a JavaScript string literal. Centralised here so the gallery builders share
    one implementation instead of byte-divergent copies.
    """
    return (text.replace("</", "<\\/")
                .replace(" ", "\\u2028")
                .replace(" ", "\\u2029"))


def atomic_write_json_batch(items) -> None:
    """Write many JSON files as a staged two-phase commit.

    `items` maps destination `Path` -> data. Phase 1 serialises and writes
    every entry to a same-directory temp file; if any write fails (disk full,
    non-serialisable data), all temps written so far are removed and the
    exception is re-raised with *zero* destinations touched. Phase 2 `os.replace`s
    each temp into place. Per-file content is byte-identical to `atomic_write_json`
    (2-space indent + trailing newline).

    Same-filesystem renames in phase 2 effectively never fail mid-loop, so the
    batch is all-or-nothing in practice: the failure-prone work (serialisation)
    all happens in phase 1 before any destination is modified. The narrow
    residual window (a crash between two phase-2 renames) leaves only fully
    written individual files, never a partial one.
    """
    staged: list[tuple[str, Path]] = []  # (tmp_path, destination)
    try:
        for dest, data in items.items():
            dest.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(
                prefix=dest.name + ".", suffix=".tmp", dir=str(dest.parent))
            # Record the temp BEFORE writing so a serialisation failure mid-write
            # (which raises before the loop body finishes) is still cleaned up.
            staged.append((tmp, dest))
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(data, fh, indent=2, ensure_ascii=False)
                fh.write("\n")
    except Exception:
        for tmp, _dest in staged:
            try:
                os.unlink(tmp)
            except OSError:
                pass
        raise
    # Phase 2: commit. Same-fs renames are atomic and effectively never fail.
    for tmp, dest in staged:
        os.replace(tmp, dest)
