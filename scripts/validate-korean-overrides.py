#!/usr/bin/env python3
"""
Validate Korean langpack URLs before applying overrides to decomposed player
card JSONs.

Inputs:
  --source              path to source-langpack.json (ContainedObjects array)
  --decomposed-root     root of the decomposed player card tree (*.json files)

Outputs:
  - <output-dir>/url_validation_report.json  (machine-readable)
  - <output-dir>/url_validation_report.md    (human-readable)

Exit codes:
  0  all URLs ok or redirect_ok
  1  one or more fail URLs (normal user-review branch)
  2  GMNotes integrity invariant violation
"""

import argparse
import ipaddress
import json
import os
import ssl
import sys
import tempfile
import time
import urllib.error
import urllib.request
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import urlsplit

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_SOURCE = Path(__file__).resolve().parent / "output" / "korean-image-apply" / "source-langpack.json"
DEFAULT_DECOMPOSED_ROOT = (
    REPO_ROOT
    / "SCED-downloads"
    / "decomposed"
    / "language-pack"
    / "Korean - Player Cards"
    / "Korean-PlayerCards.KoreanI"
)
DEFAULT_OUTPUT_DIR = Path(__file__).resolve().parent / "output" / "korean-image-apply"

CONCURRENCY = 15
TIMEOUT_SEC = 5.0


def parse_args(argv=None):
    p = argparse.ArgumentParser(
        description="Validate URLs in Korean langpack overrides."
    )
    p.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    p.add_argument("--decomposed-root", type=Path, default=DEFAULT_DECOMPOSED_ROOT)
    p.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    return p.parse_args(argv)


def _check_gmnotes(obj: dict, location: str) -> str:
    """Return the 'id' value from GMNotes, or exit with code 2 on violation."""
    raw = obj.get("GMNotes", "")
    if not raw:
        print(f"GMNotes invariant violation: missing GMNotes at {location}", file=sys.stderr)
        sys.exit(2)
    try:
        gm = json.loads(raw)
    except json.JSONDecodeError:
        print(f"GMNotes invariant violation: non-JSON GMNotes at {location}", file=sys.stderr)
        sys.exit(2)
    if "id" not in gm:
        print(f"GMNotes invariant violation: missing 'id' key at {location}", file=sys.stderr)
        sys.exit(2)
    return gm["id"]


def _is_blocked_host(host: str) -> bool:
    """Return True if host targets a private/loopback/link-local/metadata address.

    SSRF guard: validation URLs come from a source JSON file (user-controlled
    input). We only ever validate public CDN assets, so refuse anything pointing
    at internal infrastructure (e.g. localhost, 169.254.169.254, RFC1918 IPs).
    Regular public hostnames pass through and are resolved by the HTTP client.
    """
    if not host:
        return True
    host = host.strip("[]")  # strip IPv6 literal brackets
    lowered = host.lower()
    if lowered == "localhost" or lowered.endswith(".localhost") or lowered == "metadata.google.internal":
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False  # ordinary hostname -> allowed (public CDN)
    return (
        ip.is_private or ip.is_loopback or ip.is_link_local
        or ip.is_reserved or ip.is_multicast or ip.is_unspecified
    )


def _head_request(url: str) -> dict:
    """Perform HTTP HEAD with one retry on 5xx/timeout/unknown. Returns status dict."""
    parts = urlsplit(url)
    if parts.scheme != "https":
        return {"status": "malformed", "elapsed_ms": 0, "redirect_chain": [], "final_url": url}
    if _is_blocked_host(parts.hostname or ""):
        # SSRF guard: refuse to probe internal/private hosts named by the source.
        return {"status": "blocked_host", "elapsed_ms": 0, "redirect_chain": [], "final_url": url}

    def _attempt():
        start = time.monotonic()
        try:
            req = urllib.request.Request(url, method="HEAD")
            # Follow redirects manually to record the chain.
            redirect_chain = []
            opener = urllib.request.build_opener(urllib.request.HTTPRedirectHandler())
            with opener.open(req, timeout=TIMEOUT_SEC) as resp:
                elapsed = int((time.monotonic() - start) * 1000)
                # urllib follows redirects automatically; we can't get the chain
                # from the standard opener, so we record only the final URL.
                final_url = resp.geturl()
                if final_url != url:
                    redirect_chain.append(final_url)
                code = resp.status
                return code, elapsed, redirect_chain, final_url, None
        except urllib.error.HTTPError as e:
            elapsed = int((time.monotonic() - start) * 1000)
            return e.code, elapsed, [], url, None
        except urllib.error.URLError as e:
            elapsed = int((time.monotonic() - start) * 1000)
            reason = str(e.reason) if hasattr(e, "reason") else str(e)
            if "timed out" in reason.lower() or "timeout" in reason.lower():
                return "timeout", elapsed, [], url, reason
            if "name or service not known" in reason.lower() or "nodename nor servname" in reason.lower() or "getaddrinfo" in reason.lower():
                return "dns", elapsed, [], url, reason
            if isinstance(e.reason, ssl.SSLError) or "ssl" in reason.lower() or "certificate" in reason.lower():
                return "tls", elapsed, [], url, reason
            # Unclassified URLError: surface the cause instead of masking it as a timeout.
            return "unknown", elapsed, [], url, reason
        except TimeoutError:
            elapsed = int((time.monotonic() - start) * 1000)
            return "timeout", elapsed, [], url, "TimeoutError"
        except Exception as e:
            # Never swallow the cause: record the exception type/message so the
            # report shows why this URL could not be validated.
            elapsed = int((time.monotonic() - start) * 1000)
            return "unknown", elapsed, [], url, repr(e)

    code, elapsed, chain, final_url, detail = _attempt()

    # Retry once on 5xx, timeout, or unknown (possibly transient).
    if code in ("timeout", "unknown") or (isinstance(code, int) and 500 <= code <= 599):
        code2, elapsed2, chain2, final_url2, detail2 = _attempt()
        if isinstance(code2, int) and code2 < 500:
            code, elapsed, chain, final_url, detail = code2, elapsed2, chain2, final_url2, detail2
        elif isinstance(code2, int):
            # Retry still returned an HTTP code (5xx): prefer the retry result.
            # A non-int retry (timeout/dns/tls/unknown) keeps the first attempt.
            code, elapsed, chain, final_url, detail = code2, elapsed2, chain2, final_url2, detail2

    # Classify.
    if isinstance(code, int):
        if 200 <= code <= 299:
            status = "redirect_ok" if chain else "ok"
        elif code == 404:
            status = "404"
        elif 400 <= code <= 499:
            status = "4xx_other"
        elif 500 <= code <= 599:
            status = "5xx"
        else:
            status = "4xx_other"
    else:
        status = code  # "timeout", "dns", "tls", "unknown" (or "malformed"/"blocked_host" handled earlier)

    result = {
        "status": status,
        "elapsed_ms": elapsed,
        "redirect_chain": chain,
        "final_url": final_url,
    }
    if detail:
        result["detail"] = detail
    return result


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # Unique temp file in the same directory (not a shared "<stem>.tmp") so
    # concurrent runs can't clobber each other; os.replace is atomic same-fs.
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.chmod(tmp_name, 0o644)  # mkstemp creates 0600; keep reports readable
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def main(argv=None):
    args = parse_args(argv)

    # Step 1: Load source JSON + GMNotes integrity check.
    with args.source.open(encoding="utf-8") as fh:
        source_data = json.load(fh)

    contained = source_data.get("ContainedObjects", [])
    src_id_to_objects: dict[str, list[dict]] = defaultdict(list)

    for i, obj in enumerate(contained):
        card_id = _check_gmnotes(obj, f"{args.source}[{i}]")
        src_id_to_objects[card_id].append(obj)

    # Warn about source duplicates.
    for card_id, objs in src_id_to_objects.items():
        if len(objs) > 1:
            print(
                f"WARNING: source id={card_id} appears {len(objs)} times",
                file=sys.stderr,
            )

    # Step 2: Walk target decomposed directory + GMNotes integrity check.
    tgt_by_id_to_paths: dict[str, list[Path]] = defaultdict(list)
    for path in sorted(args.decomposed_root.rglob("*.json")):
        with path.open(encoding="utf-8") as fh:
            try:
                tgt = json.load(fh)
            except json.JSONDecodeError:
                print(f"Invalid JSON in target file: {path}", file=sys.stderr)
                sys.exit(2)
        card_id = _check_gmnotes(tgt, str(path))
        tgt_by_id_to_paths[card_id].append(path)

    # Step 3: Compute intersection.
    src_ids = set(src_id_to_objects.keys())
    tgt_ids = set(tgt_by_id_to_paths.keys())
    applicable_ids = src_ids & tgt_ids

    # Step 4: Collect unique URLs from applicable cards + build URL→card_ids mapping.
    url_to_card_ids: dict[str, set[str]] = defaultdict(set)
    for card_id in applicable_ids:
        for obj in src_id_to_objects[card_id]:
            for deck_val in obj.get("CustomDeck", {}).values():
                face = deck_val.get("FaceURL")
                back = deck_val.get("BackURL")
                if face:
                    url_to_card_ids[face].add(card_id)
                if back:
                    url_to_card_ids[back].add(card_id)

    all_urls = list(url_to_card_ids.keys())
    print(f"Validating {len(all_urls)} unique URLs for {len(applicable_ids)} applicable cards...")

    # Step 5: HTTP HEAD validation (concurrent).
    url_results: dict[str, dict] = {}
    with ThreadPoolExecutor(max_workers=CONCURRENCY) as pool:
        future_to_url = {pool.submit(_head_request, url): url for url in all_urls}
        for future in as_completed(future_to_url):
            url = future_to_url[future]
            result = future.result()
            result["affected_card_ids"] = sorted(url_to_card_ids[url])
            url_results[url] = result

    # Step 6: Build nickname lookup for human-readable report.
    id_to_nickname: dict[str, str] = {}
    for card_id, objs in src_id_to_objects.items():
        if objs:
            id_to_nickname[card_id] = objs[0].get("Nickname", card_id)

    # Step 7: Write JSON report.
    args.output_dir.mkdir(parents=True, exist_ok=True)
    json_report = {url: res for url, res in sorted(url_results.items())}
    _atomic_write(
        args.output_dir / "url_validation_report.json",
        json.dumps(json_report, ensure_ascii=False, indent=2),
    )

    # Step 8: Write Markdown report.
    by_status: dict[str, list[tuple[str, dict]]] = defaultdict(list)
    for url, res in url_results.items():
        by_status[res["status"]].append((url, res))

    fail_statuses = {"404", "4xx_other", "5xx", "timeout", "dns", "tls", "malformed", "unknown", "blocked_host"}
    has_failures = any(s in fail_statuses for s in by_status)

    md_lines = [
        "# Korean Langpack URL Validation Report",
        "",
        f"Total URLs checked: {len(all_urls)}  ",
        f"Applicable card IDs: {len(applicable_ids)}  ",
        "",
    ]

    status_order = ["ok", "redirect_ok", "404", "4xx_other", "5xx", "timeout", "dns", "tls", "malformed", "unknown", "blocked_host"]
    for status in status_order:
        items = by_status.get(status, [])
        if not items:
            continue
        md_lines.append(f"## {status} ({len(items)} URLs)")
        md_lines.append("")
        if status in fail_statuses:
            for url, res in sorted(items):
                card_list = ", ".join(
                    f"{cid} ({id_to_nickname.get(cid, '?')})"
                    for cid in res.get("affected_card_ids", [])
                )
                md_lines.append(f"- `{url}`")
                md_lines.append(f"  - Affected cards: {card_list}")
                md_lines.append(f"  - Elapsed: {res['elapsed_ms']}ms")
                if res.get("detail"):
                    md_lines.append(f"  - Detail: {res['detail']}")
        else:
            for url, res in sorted(items):
                md_lines.append(f"- `{url}` ({res['elapsed_ms']}ms)")
        md_lines.append("")

    _atomic_write(
        args.output_dir / "url_validation_report.md",
        "\n".join(md_lines),
    )

    print(f"Report written to {args.output_dir / 'url_validation_report.json'}")
    print(f"           and to {args.output_dir / 'url_validation_report.md'}")

    if has_failures:
        fail_count = sum(len(by_status[s]) for s in fail_statuses if s in by_status)
        print(f"FAIL: {fail_count} URLs failed validation (see report for details)")
        return 1

    print("PASS: all URLs ok or redirect_ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
