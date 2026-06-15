#!/usr/bin/env python3
"""
Regenerate downloadable/korean_campaigns.json from the Korean - Campaigns
decomposed tree.

Two paths (see .am/korean-image-apply/design.md §5.5):

  B-prime (default, recommended):
    Invoke the same TTSModManager-Linux binary that SCED-downloads/build.py
    uses, but point it at the Campaigns decomposed root. Byte-identical output
    ordering; depends on the user having run `make init` in SCED-downloads.

  pure-python (fallback, `--pure-python`):
    Walk the decomposed tree, resolve ContainedObjects_order + _path,
    inline sibling .gmnotes into GMNotes. Produces a working combined JSON
    but does not guarantee byte-identical key ordering — only URL-line
    parity (verify-combined-diff.sh enforces this).

Both paths default to --indent 0 (single-line minified) to match the
existing file on disk.
"""

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from sced_io import atomic_write_text

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_DECOMPOSED_ROOT = (
    REPO_ROOT
    / "SCED-downloads"
    / "decomposed"
    / "language-pack"
    / "Korean - Campaigns"
    / "Korean-Campaigns.KoreanC"
)
DEFAULT_OUTPUT = (
    REPO_ROOT / "SCED-downloads" / "downloadable" / "korean_campaigns.json"
)
SCED_DOWNLOADS_ROOT = REPO_ROOT / "SCED-downloads"


def parse_args(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--decomposed-root", type=Path, default=DEFAULT_DECOMPOSED_ROOT)
    p.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    p.add_argument("--indent", type=int, default=0,
                   help="JSON indent. 0 = minified single-line (default, matches "
                        "existing downloadable/korean_campaigns.json).")
    p.add_argument("--pure-python", action="store_true",
                   help="Use pure-Python assembly instead of TTSModManager.")
    p.add_argument("--moddir", type=Path, default=SCED_DOWNLOADS_ROOT / "SCED",
                   help="--moddir passed to TTSModManager (B-prime only).")
    p.add_argument("--modexec", type=Path,
                   default=SCED_DOWNLOADS_ROOT / "TTSModManager-Linux",
                   help="Path to TTSModManager-Linux binary (B-prime only).")
    return p.parse_args(argv)


# ------------------------- pure Python path --------------------------------


def _inline_gmnotes(data: dict, base: Path) -> None:
    gm_path = data.get("GMNotes_path")
    if gm_path:
        sibling = base / Path(gm_path).name
        if sibling.exists():
            try:
                with sibling.open(encoding="utf-8") as fh:
                    data["GMNotes"] = fh.read()
            except OSError:
                pass
        data.pop("GMNotes_path", None)


def _load_index(index_path: Path) -> dict:
    with index_path.open(encoding="utf-8") as fh:
        return json.load(fh)


def _assemble_container(index_path: Path, dir_path: Path) -> dict:
    """Recursively assemble one container JSON from its index + child dir."""
    container = _load_index(index_path)

    order = container.pop("ContainedObjects_order", None)
    paths = container.pop("ContainedObjects_path", None)
    # Inline GMNotes if this container itself references a .gmnotes sibling.
    _inline_gmnotes(container, dir_path.parent)

    children: list[dict] = []
    if isinstance(paths, list):
        # ContainedObjects_path is a list of paths relative to the decomposed
        # root (not always to dir_path). We resolve from the SCED-downloads
        # decomposed root the same way TTSModManager does.
        decomposed_root = _find_decomposed_root(index_path)
        for rel in paths:
            rel_path = Path(rel)
            # Two candidates: resolve from decomposed root, or sibling of
            # container dir.
            candidates = [
                decomposed_root / rel_path,
                dir_path / rel_path.name,
                index_path.parent / rel_path,
            ]
            resolved = None
            for c in candidates:
                if c.exists():
                    resolved = c
                    break
            if resolved is None:
                continue
            child_dir = resolved.with_suffix("")
            if child_dir.exists() and child_dir.is_dir():
                children.append(_assemble_container(resolved, child_dir))
            else:
                with resolved.open(encoding="utf-8") as fh:
                    child_data = json.load(fh)
                _inline_gmnotes(child_data, resolved.parent)
                children.append(child_data)

    # Re-order by ContainedObjects_order if available.
    if isinstance(order, list):
        by_name: dict[str, dict] = {}
        for p, d in zip(paths or [], children):
            stem = Path(p).stem
            by_name[stem] = d
        ordered: list[dict] = []
        for name in order:
            if name in by_name:
                ordered.append(by_name[name])
        # Append any leftovers in original order.
        for p, d in zip(paths or [], children):
            stem = Path(p).stem
            if stem not in order:
                ordered.append(d)
        children = ordered

    if children:
        container["ContainedObjects"] = children

    return container


def _find_decomposed_root(path: Path) -> Path:
    """Walk up until we find the `decomposed` directory ancestor."""
    for parent in path.parents:
        if parent.name == "decomposed":
            return parent
    # Fallback: SCED-downloads/decomposed
    return SCED_DOWNLOADS_ROOT / "decomposed"


def build_pure_python(decomposed_root: Path, output: Path, indent: int) -> None:
    """Produce the combined JSON by walking the decomposed tree.

    The root index JSON is named `<root>.json` as a sibling of the `<root>`
    directory. We use that as the container entry point.
    """
    if not decomposed_root.exists():
        print(f"ERROR: decomposed root not found: {decomposed_root}", file=sys.stderr)
        sys.exit(2)
    root_index = decomposed_root.with_suffix(".json")
    if not root_index.exists():
        print(f"ERROR: root index JSON not found: {root_index}", file=sys.stderr)
        sys.exit(2)

    assembled = _assemble_container(root_index, decomposed_root)
    output.parent.mkdir(parents=True, exist_ok=True)
    _write_minified(output, assembled, indent)
    print(f"pure-python build written: {output}")


def _write_minified(path: Path, data, indent: int) -> None:
    """Write JSON with either minified (indent=0) or pretty form, atomically.

    Uses a same-dir temp + os.replace (via sced_io.atomic_write_text) so a crash
    mid-write never leaves korean_campaigns.json partially written. The exact
    byte shape (separators / indent, no trailing newline) is unchanged.
    """
    if indent <= 0:
        text = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    else:
        text = json.dumps(data, ensure_ascii=False, indent=indent)
    atomic_write_text(path, text)


# ------------------------- B-prime (TTSModManager) -------------------------


def build_b_prime(args) -> None:
    if not args.modexec.exists():
        print(
            f"ERROR: TTSModManager binary not found at {args.modexec}.\n"
            f"       Run `make init` under {SCED_DOWNLOADS_ROOT} first "
            "(downloads TTSModManager-Linux), or re-run this script with "
            "`--pure-python` as an unsupported fallback.",
            file=sys.stderr,
        )
        sys.exit(2)
    if not args.moddir.exists():
        print(f"ERROR: --moddir not found: {args.moddir}", file=sys.stderr)
        sys.exit(2)

    # TTSModManager takes a single decomposed input path (the top-level
    # container JSON) and emits a combined JSON to --objout.
    root_index = args.decomposed_root.with_suffix(".json")
    if not root_index.exists():
        print(f"ERROR: root index JSON not found: {root_index}", file=sys.stderr)
        sys.exit(2)

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_out = Path(tmpdir) / "combined.json"
        cmd = [
            str(args.modexec),
            "--objin", str(root_index),
            "--objout", str(tmp_out),
            "--moddir", str(args.moddir),
        ]
        print("Running:", " ".join(cmd))
        try:
            subprocess.run(cmd, check=True)
        except (subprocess.CalledProcessError, FileNotFoundError) as exc:
            print(f"ERROR: TTSModManager invocation failed: {exc}", file=sys.stderr)
            sys.exit(3)

        # Optionally re-minify to match existing --indent 0 byte shape.
        if args.indent <= 0:
            with tmp_out.open(encoding="utf-8") as fh:
                data = json.load(fh)
            args.output.parent.mkdir(parents=True, exist_ok=True)
            _write_minified(args.output, data, 0)
        else:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(tmp_out), str(args.output))
    print(f"B-prime build written: {args.output}")


# ------------------------------- main --------------------------------------


def main(argv=None) -> int:
    args = parse_args(argv)
    if args.pure_python:
        build_pure_python(args.decomposed_root, args.output, args.indent)
    else:
        build_b_prime(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
