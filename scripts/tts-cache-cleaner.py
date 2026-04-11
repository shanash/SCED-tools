#!/usr/bin/env python3
"""
TTS Cache Cleaner for SCED
Manages Tabletop Simulator's cached image/model files on macOS.

Usage:
  python3 tts-cache-cleaner.py                  # Show cache analysis
  python3 tts-cache-cleaner.py --clean korean    # Clean Korean langpack cache
  python3 tts-cache-cleaner.py --clean steam     # Clean Steam CDN cache
  python3 tts-cache-cleaner.py --clean all       # Clean all image cache
  python3 tts-cache-cleaner.py --clean full      # Clean everything (images, models, PDF, etc.)
  python3 tts-cache-cleaner.py --dry-run korean  # Preview what would be deleted
"""

import argparse
import os
import sys
from pathlib import Path

# TTS cache root
TTS_MODS = Path.home() / "Library" / "Tabletop Simulator" / "Mods"

# File name patterns for source identification
PATTERNS = {
    "korean": {
        "desc": "Korean Langpack (R2 CDN)",
        "prefix": "httpspub05b4fa32b44341d797f5c66d59384724r2dev",
    },
    "steam": {
        "desc": "Steam CDN (card spritesheets)",
        "prefixes": [
            "httpsteamusercontentaakamaihd",
            "httpcloud3steamusercontent",
            "httpcloud2steamusercontent",
        ],
    },
    "github": {
        "desc": "GitHub (fan content, misc)",
        "prefix": "httpsgithubcom",
    },
}

# Directories that contain paired cached files
IMAGE_DIRS = ["Images", "Images Raw"]
ALL_CACHE_DIRS = ["Images", "Images Raw", "Models", "Models Raw", "PDF", "Assetbundles", "Audio"]


def format_size(size_bytes):
    """Format bytes into human-readable size."""
    for unit in ["B", "KB", "MB", "GB"]:
        if size_bytes < 1024:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024
    return f"{size_bytes:.1f} TB"


def classify_file(filename):
    """Classify a cache file by its source."""
    lower = filename.lower()
    if lower.startswith(PATTERNS["korean"]["prefix"]):
        return "korean"
    for prefix in PATTERNS["steam"]["prefixes"]:
        if lower.startswith(prefix):
            return "steam"
    if lower.startswith(PATTERNS["github"]["prefix"]):
        return "github"
    return "other"


def scan_directory(dir_path):
    """Scan a cache directory and classify files by source."""
    stats = {}
    if not dir_path.exists():
        return stats

    for f in dir_path.iterdir():
        if f.name.startswith("."):
            continue
        category = classify_file(f.name)
        if category not in stats:
            stats[category] = {"count": 0, "size": 0, "files": []}
        stats[category]["count"] += 1
        try:
            stats[category]["size"] += f.stat().st_size
        except OSError:
            pass
        stats[category]["files"].append(f)

    return stats


def analyze_cache():
    """Analyze and display TTS cache breakdown."""
    if not TTS_MODS.exists():
        print(f"TTS Mods directory not found: {TTS_MODS}")
        sys.exit(1)

    print(f"TTS Cache Directory: {TTS_MODS}")
    print("=" * 70)

    grand_total_size = 0
    grand_total_files = 0

    # Scan each cache directory
    for dir_name in ALL_CACHE_DIRS:
        dir_path = TTS_MODS / dir_name
        if not dir_path.exists():
            continue

        stats = scan_directory(dir_path)
        if not stats:
            continue

        dir_total_size = sum(s["size"] for s in stats.values())
        dir_total_files = sum(s["count"] for s in stats.values())
        grand_total_size += dir_total_size
        grand_total_files += dir_total_files

        print(f"\n📁 {dir_name}/ ({format_size(dir_total_size)}, {dir_total_files} files)")
        print("-" * 50)

        # Sort by size descending
        for category in sorted(stats, key=lambda c: stats[c]["size"], reverse=True):
            s = stats[category]
            label = PATTERNS.get(category, {}).get("desc", "Other / Unknown")
            bar_len = int(s["size"] / dir_total_size * 20) if dir_total_size > 0 else 0
            bar = "█" * bar_len + "░" * (20 - bar_len)
            print(f"  {label:35s} {bar} {format_size(s['size']):>10s} ({s['count']:4d} files)")

    print(f"\n{'=' * 70}")
    print(f"Total: {format_size(grand_total_size)} ({grand_total_files} files)")


def collect_files(target):
    """Collect files to delete based on target category."""
    files_to_delete = []

    if target == "full":
        # Everything in all cache dirs
        for dir_name in ALL_CACHE_DIRS:
            dir_path = TTS_MODS / dir_name
            if dir_path.exists():
                for f in dir_path.iterdir():
                    if not f.name.startswith("."):
                        files_to_delete.append(f)
        return files_to_delete

    # For other targets, only scan image directories
    scan_dirs = IMAGE_DIRS
    for dir_name in scan_dirs:
        dir_path = TTS_MODS / dir_name
        if not dir_path.exists():
            continue

        for f in dir_path.iterdir():
            if f.name.startswith("."):
                continue

            if target == "all":
                files_to_delete.append(f)
            else:
                category = classify_file(f.name)
                if category == target:
                    files_to_delete.append(f)

    return files_to_delete


def clean_cache(target, dry_run=False):
    """Clean cache files for the specified target."""
    if not TTS_MODS.exists():
        print(f"TTS Mods directory not found: {TTS_MODS}")
        sys.exit(1)

    files = collect_files(target)
    if not files:
        print(f"No cached files found for target '{target}'.")
        return

    total_size = sum(f.stat().st_size for f in files if f.exists())

    target_desc = {
        "korean": "Korean Langpack (R2 CDN)",
        "steam": "Steam CDN images",
        "github": "GitHub images",
        "other": "Other/unknown images",
        "all": "ALL image cache (Images + Images Raw)",
        "full": "ALL cache (images, models, PDF, audio, assetbundles)",
    }.get(target, target)

    if dry_run:
        print(f"[DRY RUN] Would delete {len(files)} files ({format_size(total_size)})")
        print(f"Target: {target_desc}")
        print()
        # Show sample files
        for f in files[:10]:
            print(f"  {f.parent.name}/{f.name} ({format_size(f.stat().st_size)})")
        if len(files) > 10:
            print(f"  ... and {len(files) - 10} more files")
        return

    print(f"Target: {target_desc}")
    print(f"Files:  {len(files)}")
    print(f"Size:   {format_size(total_size)}")
    print()
    confirm = input("Delete these files? (y/N): ").strip().lower()
    if confirm != "y":
        print("Cancelled.")
        return

    deleted = 0
    freed = 0
    for f in files:
        try:
            size = f.stat().st_size
            f.unlink()
            deleted += 1
            freed += size
        except OSError as e:
            print(f"  Error deleting {f.name}: {e}")

    print(f"\nDeleted {deleted} files, freed {format_size(freed)}")


def main():
    parser = argparse.ArgumentParser(
        description="TTS Cache Cleaner for SCED",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Targets:
  korean   Korean langpack images (R2 CDN)
  steam    Steam CDN card spritesheets
  github   GitHub-hosted images
  all      All image cache (Images + Images Raw)
  full     Everything (images, models, PDF, audio, assets)

Examples:
  %(prog)s                       Show cache analysis
  %(prog)s --dry-run korean      Preview Korean cache cleanup
  %(prog)s --clean korean        Delete Korean langpack cache
  %(prog)s --clean all           Delete all image cache
        """,
    )
    parser.add_argument(
        "--clean",
        metavar="TARGET",
        choices=["korean", "steam", "github", "other", "all", "full"],
        help="Clean cache for specified target",
    )
    parser.add_argument(
        "--dry-run",
        metavar="TARGET",
        choices=["korean", "steam", "github", "other", "all", "full"],
        help="Preview what would be deleted (no actual deletion)",
    )

    args = parser.parse_args()

    if args.dry_run:
        clean_cache(args.dry_run, dry_run=True)
    elif args.clean:
        clean_cache(args.clean)
    else:
        analyze_cache()


if __name__ == "__main__":
    main()
