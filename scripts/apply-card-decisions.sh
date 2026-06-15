#!/usr/bin/env bash
# Orchestrates: snapshot → build-korean-overrides → apply-grid-dims → verify → auto-restore
#
# Usage:
#   apply-card-decisions.sh
#     --source <synthetic-source.json>
#     --decisions <review_decisions.json>
#     --decomposed-root <PATH>
#     --output-dir <PATH>
#     [--text-source untouched|v2|v1|v0]
#     [--dry-run]
#     [--skip-snapshot]
#     [--no-auto-restore]
#
# Exit codes:
#   0  OK
#   1  warnings
#   60 snapshot creation failed
#   61 build-korean-overrides.py failed
#   62 apply-grid-dims.py failed
#   63 post-condition verify mismatch
#   64 auto-restore performed

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON="${PYTHON:-python3}"

SOURCE=""
DECISIONS=""
DECOMPOSED_ROOT=""
OUTPUT_DIR=""
TEXT_SOURCE="untouched"
DRY_RUN=0
SKIP_SNAPSHOT=0
NO_AUTO_RESTORE=0

# Parse args
while [[ $# -gt 0 ]]; do
  case "$1" in
    --source)           SOURCE="$2";         shift 2 ;;
    --decisions)        DECISIONS="$2";      shift 2 ;;
    --decomposed-root)  DECOMPOSED_ROOT="$2"; shift 2 ;;
    --output-dir)       OUTPUT_DIR="$2";     shift 2 ;;
    --text-source)      TEXT_SOURCE="$2";    shift 2 ;;
    --dry-run)          DRY_RUN=1;           shift   ;;
    --skip-snapshot)    SKIP_SNAPSHOT=1;     shift   ;;
    --no-auto-restore)  NO_AUTO_RESTORE=1;   shift   ;;
    *) echo "Unknown argument: $1" >&2; exit 1 ;;
  esac
done

# Validate required args
for var in SOURCE DECISIONS DECOMPOSED_ROOT OUTPUT_DIR; do
  if [[ -z "${!var}" ]]; then
    echo "Missing required argument: --${var//_/-}" >&2
    exit 1
  fi
done

mkdir -p "$OUTPUT_DIR"

APPLY_SUMMARY="$OUTPUT_DIR/apply_summary.json"
SNAPSHOT_DIR="$OUTPUT_DIR/snapshots"
SNAPSHOT_PATH=""

# Helper: write apply_summary.json
write_summary() {
  local failed_step="$1"
  local orig_exit="$2"
  local wrapper_exit="$3"
  local restored="$4"
  local snap="${SNAPSHOT_PATH:-}"

  # Convert shell true/false strings to Python booleans
  local py_restored
  if [[ "$restored" == "true" ]]; then
    py_restored="True"
  else
    py_restored="False"
  fi

  local py_step
  if [[ -z "$failed_step" || "$failed_step" == "None" ]]; then
    py_step="None"
  else
    py_step="'$failed_step'"
  fi

  python3 -c "
import json
d = {
  'failed_step': $py_step,
  'original_exit_code': $orig_exit,
  'wrapper_exit_code': $wrapper_exit,
  'auto_restore_performed': $py_restored,
  'snapshot_path': '$snap',
}
print(json.dumps(d, indent=2))
" > "$APPLY_SUMMARY"
}

# Helper: auto-restore from snapshot
auto_restore() {
  local failed_step="$1"
  local orig_exit="$2"

  if [[ "$NO_AUTO_RESTORE" -eq 1 ]]; then
    echo "Auto-restore skipped (--no-auto-restore)" >&2
    write_summary "$failed_step" "$orig_exit" 64 "false"
    exit 64
  fi

  if [[ -z "$SNAPSHOT_PATH" || ! -f "$SNAPSHOT_PATH" ]]; then
    echo "No snapshot available for auto-restore." >&2
    write_summary "$failed_step" "$orig_exit" 64 "false"
    exit 64
  fi

  echo "Auto-restoring from snapshot: $SNAPSHOT_PATH" >&2
  local parent_dir
  parent_dir="$(dirname "$DECOMPOSED_ROOT")"
  local base_name
  base_name="$(basename "$DECOMPOSED_ROOT")"

  if tar xzf "$SNAPSHOT_PATH" -C "$parent_dir"; then
    echo "Auto-restore complete." >&2
    write_summary "$failed_step" "$orig_exit" 64 "true"
  else
    echo "Auto-restore failed!" >&2
    write_summary "$failed_step" "$orig_exit" 64 "false"
  fi
  exit 64
}

# Step 1: Snapshot
if [[ "$SKIP_SNAPSHOT" -eq 0 && "$DRY_RUN" -eq 0 ]]; then
  mkdir -p "$SNAPSHOT_DIR"
  SNAPSHOT_PATH="$SNAPSHOT_DIR/$(date -u +%Y%m%dT%H%M%SZ).tar.gz"
  echo "Creating snapshot: $SNAPSHOT_PATH"

  DECOMPOSED_PARENT="$(dirname "$DECOMPOSED_ROOT")"
  DECOMPOSED_BASE="$(basename "$DECOMPOSED_ROOT")"

  if ! tar czf "$SNAPSHOT_PATH" -C "$DECOMPOSED_PARENT" "$DECOMPOSED_BASE"; then
    echo "Snapshot creation failed." >&2
    write_summary "snapshot" 1 60 "false"
    exit 60
  fi

  echo "$SNAPSHOT_PATH" > "$OUTPUT_DIR/snapshot_path.txt"
  echo "Snapshot created: $SNAPSHOT_PATH"
fi

# Step 2: build-korean-overrides.py
BUILD_OVERRIDES="$SCRIPT_DIR/build-korean-overrides.py"
echo "Running build-korean-overrides.py..."

OVERRIDE_ARGS=(
  "--source" "$SOURCE"
  "--decomposed-root" "$DECOMPOSED_ROOT"
  "--output-dir" "$OUTPUT_DIR"
  "--skipped-source-only-out" "$OUTPUT_DIR/skipped-source-only.txt"
)

if [[ "$TEXT_SOURCE" != "untouched" ]]; then
  : # build-korean-overrides handles text; synthesize-source already embedded it
fi

if [[ "$DRY_RUN" -eq 1 ]]; then
  OVERRIDE_ARGS+=("--dry-run")
fi

set +e
"$PYTHON" "$BUILD_OVERRIDES" "${OVERRIDE_ARGS[@]}"
OVERRIDE_EXIT=$?
set -e

if [[ "$OVERRIDE_EXIT" -ne 0 ]]; then
  echo "build-korean-overrides.py failed with exit code $OVERRIDE_EXIT" >&2
  auto_restore "build-korean-overrides.py" "$OVERRIDE_EXIT"
fi

# Step 3: apply-grid-dims.py
GRID_DIMS="$SCRIPT_DIR/apply-grid-dims.py"
echo "Running apply-grid-dims.py..."

GRID_ARGS=(
  "--decisions" "$DECISIONS"
  "--decomposed-root" "$DECOMPOSED_ROOT"
)

if [[ "$DRY_RUN" -eq 1 ]]; then
  GRID_ARGS+=("--dry-run")
fi

set +e
"$PYTHON" "$GRID_DIMS" "${GRID_ARGS[@]}"
GRID_EXIT=$?
set -e

if [[ "$GRID_EXIT" -ne 0 ]]; then
  echo "apply-grid-dims.py failed with exit code $GRID_EXIT" >&2
  auto_restore "apply-grid-dims.py" "$GRID_EXIT"
fi

# Step 4: Post-condition verify (inline Python)
if [[ "$DRY_RUN" -eq 0 ]]; then
  echo "Running post-condition verification..."

  set +e
  "$PYTHON" - <<'PYEOF' "$DECISIONS" "$DECOMPOSED_ROOT"
import json, sys
from pathlib import Path

decisions_path = Path(sys.argv[1])
decomposed_root = Path(sys.argv[2])

with decisions_path.open(encoding="utf-8") as fh:
    decisions_data = json.load(fh)

decisions_list = [
    d for d in decisions_data.get("decisions", [])
    if d.get("choice") in ("v0", "v1", "v2")
]

# Build index of decomposed files
files_by_id = {}
for path in sorted(decomposed_root.rglob("*.json")):
    with path.open(encoding="utf-8") as fh:
        try:
            obj = json.load(fh)
        except json.JSONDecodeError:
            continue
    raw = obj.get("GMNotes", "")
    if isinstance(raw, dict):
        gm = raw
    elif raw:
        try:
            gm = json.loads(raw)
        except Exception:
            continue
    else:
        continue
    arkham_id = str(gm.get("id", ""))
    if arkham_id:
        files_by_id.setdefault(arkham_id, []).append((path, obj))

mismatches = []
for dec in decisions_list:
    arkham_id = dec.get("arkham_id", "")
    expected_face = dec.get("face_url")
    expected_back = dec.get("back_url")
    expected_w = dec.get("num_width")
    expected_h = dec.get("num_height")

    entries = files_by_id.get(arkham_id, [])
    if not entries:
        mismatches.append(f"{arkham_id}: target file not found")
        continue

    for path, obj in entries:
        cd = obj.get("CustomDeck", {})
        if not cd:
            mismatches.append(f"{arkham_id}: no CustomDeck in {path}")
            continue
        dk = list(cd.keys())[0]
        d = cd[dk]
        if d.get("FaceURL") != expected_face:
            mismatches.append(f"{arkham_id}: FaceURL mismatch (got {d.get('FaceURL')!r})")
        if d.get("BackURL") != expected_back:
            mismatches.append(f"{arkham_id}: BackURL mismatch")
        if d.get("NumWidth") != expected_w:
            mismatches.append(f"{arkham_id}: NumWidth mismatch (got {d.get('NumWidth')!r}, expected {expected_w!r})")
        if d.get("NumHeight") != expected_h:
            mismatches.append(f"{arkham_id}: NumHeight mismatch (got {d.get('NumHeight')!r}, expected {expected_h!r})")

if mismatches:
    print("Post-condition verify FAILED:", file=sys.stderr)
    for m in mismatches:
        print(f"  {m}", file=sys.stderr)
    sys.exit(63)

print(f"Post-condition verify OK: {len(decisions_list)} decisions checked.")
sys.exit(0)
PYEOF
  VERIFY_EXIT=$?
  set -e

  if [[ "$VERIFY_EXIT" -ne 0 ]]; then
    echo "Post-condition verification failed with exit code $VERIFY_EXIT" >&2
    auto_restore "post-condition-verify" "$VERIFY_EXIT"
  fi
fi

# Success
write_summary "None" 0 0 "false"

echo "apply-card-decisions.sh completed successfully."
exit 0
