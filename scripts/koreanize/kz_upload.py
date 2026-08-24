#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""koreanize `upload` -- publish the atlases to R2, then prove they are there (design §6 step 15).

ART TIER (design §5.9): `#!/usr/bin/env python3`. Not a member of
`kz_common.STDLIB_TIER` -- it delegates to `upload-atlases-to-r2.py`, whose
`boto3` import (`:103`) is not in the stdlib allowlist.

IT DELEGATES. IT DOES NOT REIMPLEMENT THE R2 CLIENT
----------------------------------------------------
§6 step 15: *"upload delegating to `upload-atlases-to-r2.py`"*. That tool already
owns the one thing this stage must not get wrong -- the object key. Its docstring
states the asymmetry that a reimplementation would rediscover the hard way:

    r2_key_for_sha(sha)   -> images/sha256/<ab>/<sha>.png          NO langpack/
    face_url_for_sha(sha) -> https://pub-....r2.dev/langpack/images/sha256/...
    key_from_url(url)     -> langpack/images/sha256/<ab>/<sha>.png WITH langpack/

For a public R2 bucket the object key IS the URL path after `…r2.dev/`, so the
tool derives the key from the manifest's `face_url` rather than from the digest,
*"so the uploaded key is guaranteed to match what TTS will request"*. A second
implementation here would be a second chance to put the atlas at a key nothing
requests, and `upload-korean-images-to-r2.py` is the shipped proof that this
happens: it PUTs to `images/sha256/…` with `DEFAULT_BUCKET = "langpack"` at `:49`
and there is no such bucket (§7). This module therefore builds argv, runs the
tool, and verifies what came back.

`atlas-urls.json` IS WRITTEN FROM `upload-map.json`. NEVER FROM A PREDICTION
------------------------------------------------------------------------------
This is the defect §6 step 15 calls out by name, and it is worth being precise
about what the defect IS. Every URL in this pipeline is derivable before the
upload: `face_url_for_sha(sha)` is a pure function of bytes this stage already
has on disk. So a `atlas-urls.json` written from those derivations would look
completely correct, would agree with `recompose`'s manifest, and would be a
statement about what SHOULD be on R2 rather than about what IS. The first time
the two differ -- a PUT that failed on one of three sheets, a bucket that
rejected an object, a key the tool derived differently from this module -- the
map would still read clean and the pack would ship a 404.

So the map is built from the uploader's own `upload-map.json`: `bucket`, and one
`uploads[]` entry per object it actually PUT or found already present, each
carrying the `key` and `url` IT used. Predictions are still computed -- and are
compared against the map as check U3 -- but they are never the source. An entry
in the manifest with no corresponding row in `upload-map.json` is exit 74, naming
the atlas.

THE FOUR-WAY VERIFICATION
--------------------------
`.am/standalone-scenario-koreanizer/analyze.md:337` fixes the four, and
`.am/midwinter-gala-korean/verify-both-packs.md:26` records that the shipped
`atlas-urls.json` was *"written by :314 from an upload verified with head_object
+ a public GET + a full-body sha256 round-trip"*:

    V1  head_object          the object exists in the bucket at that key, and its
                             ContentLength equals the local file's size
    V2  browser-UA public GET  the object is reachable by a real client at that URL.
                             The UA matters: Cloudflare's r2.dev edge bot-management
                             returns 403 to the default `Python-urllib` UA even for
                             public objects (`upload-atlases-to-r2.py:44-47`), so a
                             default-UA probe would report failure on a correct
                             upload and, worse, would be "fixed" by relaxing it
    V3  PNG magic            the first 8 bytes of the fetched body are the PNG
                             signature -- an HTML error page served with status 200
                             passes V2 and fails here
    V4  full-body sha256     the fetched body hashes to the local atlas's sha256

V4 subsumes V3 arithmetically and neither is redundant. V3 exists because its
failure is DIAGNOSTIC: a body that is not a PNG at all is an edge or a bucket
problem, while a body that is a PNG with the wrong digest is a stale object at a
content-addressed key -- which under content addressing means the key was derived
from different bytes. An operator's first move differs, so the checks are
reported separately. V1 is not subsumed either: it is the only one that asks the
BUCKET rather than the edge, so a cached edge response cannot answer for it.

V4 also closes §3.1's restore path: *"The three output atlases survive as R2
objects named in `atlas-urls.json`, verified by sha256 round-trip, so they can
always be re-fetched."* That claim is only true if somebody checked.

NO NETWORK IN TESTS OR IN --selftest
-------------------------------------
The four checks are evaluated against a `Verifier` -- an object with `head(key)`
and `get(url)`. `R2Verifier` is the real one; `StubVerifier` is a dict-backed one
the selftest and `test_koreanize_recompose.py` use. The uploader itself is
likewise injectable, so `--selftest` exercises the delegation ARGV and the
four-way verification without a credential, a bucket or a socket.

`--live` IS REQUIRED, AND IT IS DERIVED
----------------------------------------
§4.1: *"`--live` is required by every stage that writes outside `<run_dir>`"* --
seven filesystem stages and `upload`, whose destination is R2. §1.1(b) makes the
distinction explicit: *"`kz_upload.py`'s only destination outside `<run_dir>` is
R2, which is not a path"*, so `kz_langpack.requires_live()`'s write-root test
cannot see it. The derivation here is over the planned R2 destination set instead
-- non-empty means live is required -- which is the same shape of rule and
inherits a future destination without anybody remembering to add it.

The rest of the handshake is `kz_langpack`'s, deliberately verbatim in behaviour:
rehearsal is the DEFAULT, the rehearsal persists `<run_dir>/upload.plan.json`,
the `--live` invocation binds that file's sha256 in `binding{}` and asserts the
executed set equals it key-for-key and in order, and a live invocation with no
matching plan refuses at 13. Without the persisted artifact, *"the executed set
equals the plan the banner printed"* is a statement one process makes about
itself (§4.1).

`atlas-urls.json` GOES IN THE RECEIPT TIER, NOT IN <run_dir>
-------------------------------------------------------------
§3.1 promotes it out of run material for a reason it states outright: it is
*"§8.4's sole authority for the N-7 orphan set"*. Left in `<run_dir>` it is
gitignored and dies with `.am/`, taking the only record of which R2 objects this
scenario owns -- and R2 objects are content-addressed, so a re-typeset ORPHANS
the old one rather than replacing it. It is written into
`data/locks/<slug>.lock.json` through `kz_config.write_data`, the mirror writer,
which is the only code in the tool that opens a path under `SCED-tools/` (§3.1).
This module opens none itself and a `data_root` write is deliberately NOT
banner-bearing.

TWO RULES THIS STAGE CARRIES THAT ARE NOT ABOUT R2 AT ALL
-----------------------------------------------------------
Both are recorded INTO `atlas-urls.json`, because §5.8 makes that file one of the
two frozen artifacts `verify` evaluates C1-C11 from, and both had a rule with no
verifier before step 15:

  * The `"Scenario"` NICKNAME PIN (C11). `SCED/src/mythos/MythosArea.ttslua:209`
    branches on `cardName == "Scenario"`, so localizing that Nickname breaks the
    scenario-changed event for EVERY Korean scenario, not just this one. The
    Korean name goes in `Description` instead. `objtext` is what obeys the rule;
    this stage is what WRITES IT DOWN in the artifact `verify` reads, so C11 has
    a declared authority rather than a convention. The mod repo is the one tree
    koreanize never writes to, so the citation is a read reference only (§7).

  * The CROSS-PACK GUEST-CARD DUPLICATION RULE (N-10, C3). Only
    `AllEncounterCardsBag` rewrites objects spawned from a scenario box, so a
    card that appears in two packs -- Midwinter's Guests are the worked case --
    needs a duplicate Campaigns entry rather than one shared entry. The
    consequence for THIS stage is a constraint on the map: one English URL must
    map to exactly ONE Korean URL across every pack, and the entry is duplicated
    into each pack rather than made pack-scoped. C3 asserts BYTE identity of each
    `FaceURL` everywhere it appears; a map that offered two Korean URLs for one
    English one would make that assertion unsatisfiable by construction. U5
    refuses it at 74 naming both URLs.

NO AI, ENFORCED (§5.4)
-----------------------
`upload` is one of the eleven forbidden-AI stages, so `kc.declare_ai(STAGE,
required=False)` runs in the module body.

EXIT CODES (§4.2)
   0  every atlas uploaded (or already present) and verified four ways
   1  unhandled exception -- a defect in the tool
   2  usage, or invocation guard P0a / P0b
   4  config guard refusal -- scenario.json's pin
  13  precondition -- no upload manifest, a non-PNG asset (N-8), an upstream
       report that is not consumable, or a --live run with no bound plan
  73  --live declined at the banner
  74  R2 / network -- the uploader failed, an atlas has no row in upload-map.json,
       a four-way check failed, or the cross-pack URL rule was violated

70 is deliberately NOT used: `daily-sync-local.sh:109` owns it as *"draft release
creation, asset upload, or go-live failed"* and an operator reading a 70 beside
the nightly's would derive the wrong first move (§4.2, N-6).
"""

import argparse
import collections
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import kz_common as kc  # noqa: E402
import kz_config as kz  # noqa: E402
import kz_recompose as kr  # noqa: E402

STAGE = "upload"

# Declared in the MODULE BODY (§3.2, §5.4). `upload` is one of the eleven
# forbidden-AI stages, so this is the registration AND the assertion.
kc.declare_ai(STAGE, required=False)

FAULTS = ("delegation", "four-way", "urls-from-map", "cross-pack",
          "nickname-pin", "png-only", "live")

UPLOADER_PATH = os.path.join(kc.SCRIPTS_DIR, "upload-atlases-to-r2.py")

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"

#: The literal `MythosArea.ttslua:209` branches on. Localizing it breaks the
#: scenario-changed event for every Korean scenario (§7).
SCENARIO_NICKNAME = "Scenario"
SCENARIO_NICKNAME_SOURCE = "SCED/src/mythos/MythosArea.ttslua:209"

#: Cloudflare's r2.dev edge returns 403 to the default `Python-urllib` UA even
#: for public objects, so the reachability probe must look like a real client
#: (`upload-atlases-to-r2.py:44-47`). Copied deliberately rather than imported:
#: the tool keeps it inside `public_get_ok()`, which also opens a socket.
BROWSER_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
              "AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/126.0.0.0 Safari/537.36")

#: `upload` requires `recompose` (kz_config.PREDECESSORS).
UPSTREAM = ("recompose",)


# ===========================================================================
# 1. The verifier -- injectable, so nothing here needs a socket to be tested
# ===========================================================================

class VerifyError(Exception):
    """A transport failure, distinct from a verification MISMATCH.

    "the object is not there" and "the object is there and is the wrong bytes"
    are different first moves, so they are not both `None`.
    """


class R2Verifier(object):
    """The real one. boto3 for `head_object`, urllib for the public GET.

    Constructed only on the `--live` path, so a rehearsal needs no credential --
    which is what makes a rehearsal runnable on a machine that has none.
    """

    def __init__(self, bucket=None, env=None):
        env = env if env is not None else os.environ
        self.bucket = bucket or env.get("R2_BUCKET") or "tts-ahcg-assets"
        self._env = env
        self._client = None

    def _s3(self):
        if self._client is not None:
            return self._client
        try:
            import boto3                                    # noqa: PLC0415
        except ImportError:
            raise VerifyError(
                "boto3 is not installed -- upload-atlases-to-r2.py:103 imports it "
                "and requirements.txt is where it is declared")
        acct = self._env.get("R2_ACCOUNT_ID")
        ak = self._env.get("R2_ACCESS_KEY_ID")
        sk = self._env.get("R2_SECRET_ACCESS_KEY")
        if not (acct and ak and sk):
            raise VerifyError("R2_ACCOUNT_ID / R2_ACCESS_KEY_ID / "
                              "R2_SECRET_ACCESS_KEY must be set (env only, from "
                              "~/.config/sced-r2/env)")
        self._client = boto3.client(
            "s3", endpoint_url="https://%s.r2.cloudflarestorage.com" % acct,
            aws_access_key_id=ak, aws_secret_access_key=sk, region_name="auto")
        return self._client

    def head(self, key):
        """ContentLength for `key`, or None when the object is absent."""
        try:
            from botocore.exceptions import ClientError     # noqa: PLC0415
        except ImportError:
            raise VerifyError("botocore is not installed")
        try:
            meta = self._s3().head_object(Bucket=self.bucket, Key=key)
        except ClientError:
            return None
        return int(meta.get("ContentLength", -1))

    def get(self, url, timeout=60):
        """The full body, fetched with a browser-like UA. None on any failure."""
        request = urllib.request.Request(url, method="GET",
                                         headers={"User-Agent": BROWSER_UA})
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                if response.status != 200:
                    return None
                return response.read()
        except Exception:                                   # noqa: BLE001
            return None


class StubVerifier(object):
    """A dict-backed verifier. THE ONLY ONE `--selftest` AND THE SUITE EVER USE.

    Constructed from `{key: size}` and `{url: bytes}`, so every one of the four
    checks can be driven to both outcomes -- an absent object, a size mismatch, a
    403, an HTML error page served with status 200, and a stale object at a
    content-addressed key -- without a bucket, a credential or a socket.
    """

    def __init__(self, heads=None, bodies=None, bucket="tts-ahcg-assets"):
        self.bucket = bucket
        self.heads = dict(heads or {})
        self.bodies = dict(bodies or {})

    def head(self, key):
        return self.heads.get(key)

    def get(self, url, timeout=60):
        return self.bodies.get(url)


def sha256_bytes(payload):
    return hashlib.sha256(payload).hexdigest()


def verify_four_ways(entry, verifier):
    """V1..V4 over one uploaded object. Returns (checks, findings).

    `entry` is a row of `upload-map.json` joined to the local file, carrying
    `atlas_id`, `key`, `url`, `sha256` and `size`. EVERY check is evaluated --
    none short-circuits on the previous one's failure -- because the four are
    diagnostic of different layers and an operator handed only the first failure
    would not know whether the others also broke.
    """
    label = entry.get("atlas_id") or entry.get("key")
    checks = collections.OrderedDict()
    findings = []

    # V1 -- ask the BUCKET. The only one of the four the edge cannot answer for.
    try:
        remote = verifier.head(entry["key"])
    except VerifyError as exc:
        remote, v1 = None, "error: %s" % exc
    else:
        v1 = None
    if v1 is None:
        if remote is None:
            v1 = "absent: head_object found no object at %s" % entry["key"]
        elif int(remote) != int(entry["size"]):
            v1 = ("size %d != the local file's %d" % (int(remote),
                                                      int(entry["size"])))
    checks["head_object"] = v1 is None
    if v1 is not None:
        findings.append("%s V1 head_object: %s" % (label, v1))

    # V2/V3/V4 all read one body, fetched ONCE. Fetching three times would be
    # three chances for the edge to answer differently and would make the three
    # checks disagree about which bytes they are talking about.
    try:
        body = verifier.get(entry["url"])
    except VerifyError as exc:
        body, note = None, "error: %s" % exc
    else:
        note = None

    v2 = note or (None if body is not None else
                  "no 200 from %s with a browser UA" % entry["url"])
    checks["public_get"] = v2 is None
    if v2 is not None:
        findings.append("%s V2 public GET: %s" % (label, v2))

    if body is None:
        checks["png_magic"] = False
        checks["sha256_roundtrip"] = False
        findings.append("%s V3 PNG magic: not evaluated -- no body" % label)
        findings.append("%s V4 sha256 round-trip: not evaluated -- no body" % label)
        return checks, findings

    v3 = None if body[:8] == PNG_MAGIC else (
        "the fetched body is not a PNG (first 8 bytes %r) -- an HTML error page "
        "served with status 200 passes V2 and fails here" % body[:8])
    checks["png_magic"] = v3 is None
    if v3 is not None:
        findings.append("%s V3 PNG magic: %s" % (label, v3))

    got = sha256_bytes(body)
    v4 = None if got == entry["sha256"] else (
        "the fetched body hashes to %s, the local atlas to %s -- at a "
        "CONTENT-ADDRESSED key that means the key was derived from different bytes"
        % (got[:16], (entry["sha256"] or "")[:16]))
    checks["sha256_roundtrip"] = v4 is None
    if v4 is not None:
        findings.append("%s V4 sha256 round-trip: %s" % (label, v4))

    return checks, findings


# ===========================================================================
# 2. The plan -- derived from recompose's manifest, and persisted
# ===========================================================================

def load_json(path, label):
    if not os.path.exists(path):
        kc.refuse(kc.EXIT_PRECONDITION, "%s is absent" % label, path)
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except ValueError as exc:
        kc.refuse(kc.EXIT_PRECONDITION, "%s is not valid JSON" % label,
                  "%s: %s" % (path, exc))


def plan_path(run_dir):
    return os.path.join(run_dir, "%s.plan.json" % STAGE)


def build_plan(manifest, staging_dir, workspace=None):
    """(entries, findings) -- one planned R2 destination per manifest atlas.

    N-8, enforced here rather than discovered at the PUT: both uploaders glob
    `*.png` and hardcode `ContentType: image/png` (`upload-atlases-to-r2.py:76,131`),
    so a non-PNG would be uploaded and served with the wrong type. §1.4 puts the
    PDF path out of scope and this refuses it *"at exit 13 with that gap named"*.
    """
    workspace = workspace or kc.WORKSPACE_ROOT
    entries, findings = [], []
    for atlas in manifest.get("atlases") or []:
        name = atlas.get("file") or ""
        digest = atlas.get("atlas_sha256")
        url = atlas.get("face_url")
        local = os.path.join(staging_dir, name)
        if not name.lower().endswith(".png"):                           # N-8
            findings.append(
                "%s is not a .png -- both uploaders glob *.png and hardcode "
                "ContentType: image/png (upload-atlases-to-r2.py:76,131), so the "
                "campaign-guide PDF path is out of scope (§1.4, N-8)" % name)
            continue
        if not digest or not url:
            findings.append("%s declares atlas_sha256=%r face_url=%r"
                            % (name, digest, url))
            continue
        if not os.path.exists(local):
            findings.append("%s is named by the upload manifest but is absent from "
                            "the staging directory (%s)" % (name, staging_dir))
            continue
        with open(local, "rb") as handle:
            if handle.read(8) != PNG_MAGIC:
                findings.append("%s does not carry the PNG signature" % name)
                continue
        on_disk = kc.sha256_file(local)
        if on_disk != digest:
            findings.append("%s hashes to %s but the manifest declares %s -- the "
                            "manifest is stale, so the derived key would point at "
                            "bytes that are not there"
                            % (name, on_disk[:16], digest[:16]))
            continue
        entries.append(collections.OrderedDict([
            ("atlas_id", atlas.get("atlas_id") or name),
            ("english_url", atlas.get("english_url")),
            ("file", name),
            ("local", os.path.relpath(local, workspace)),
            ("sha256", digest),
            ("size", os.path.getsize(local)),
            # PREDICTED, and used only as a comparand (U3). The authority for
            # both fields after the upload is upload-map.json.
            ("predicted_url", kr.face_url_for_sha(digest)),
            ("predicted_key", _key_from_url(url)),
            ("manifest_url", url),
            ("packs", atlas.get("packs") or []),
        ]))
    return entries, findings


def _key_from_url(url):
    """The uploader's own rule (`upload-atlases-to-r2.py:37-41`): for a public R2
    bucket the object key is the URL path after `…r2.dev/`."""
    if ".r2.dev/" in (url or ""):
        return url.split(".r2.dev/", 1)[1]
    return None


def plan_document(cfg, entries, bucket):
    return collections.OrderedDict([
        ("schema_version", kc.SCHEMA_VERSION),
        ("stage", STAGE),
        ("slug", cfg["slug"]),
        ("generated_at", kc.utc_now()),
        ("bucket", bucket),
        ("count", len(entries)),
        ("entries", [collections.OrderedDict([
            ("atlas_id", e["atlas_id"]),
            ("file", e["file"]),
            ("sha256", e["sha256"]),
            ("size", e["size"]),
            ("predicted_key", e["predicted_key"]),
            ("predicted_url", e["predicted_url"]),
        ]) for e in entries]),
    ])


def assert_plan_matches(entries, persisted):
    """The two-process comparison, in `kz_langpack.assert_plan_matches`'s shape.

    "the executed set equals the plan the banner printed" is a statement one
    process makes about itself unless the plan is an ARTIFACT. Same objects, same
    ORDER, same digests.
    """
    want = persisted.get("entries") or []
    if len(want) != len(entries):
        kc.refuse(kc.EXIT_PRECONDITION,
                  "the persisted plan has %d entries, this run planned %d"
                  % (len(want), len(entries)),
                  "re-run the rehearsal; the plan is stale")
    for index, (a, b) in enumerate(zip(want, entries)):
        if a.get("atlas_id") != b["atlas_id"]:
            kc.refuse(kc.EXIT_PRECONDITION,
                      "the persisted plan diverges at entry %d" % index,
                      "%s != %s" % (a.get("atlas_id"), b["atlas_id"]))
        if a.get("sha256") != b["sha256"]:
            kc.refuse(kc.EXIT_PRECONDITION,
                      "the bytes for %s have moved since the banner" % b["atlas_id"],
                      "%s != %s" % ((a.get("sha256") or "")[:16], b["sha256"][:16]))
    return True


# ===========================================================================
# 3. --live: derived from the R2 destination set, not from a stage name
# ===========================================================================

def requires_live(entries):
    """DERIVED, in `kz_langpack.requires_live()`'s shape and for its reason.

    That function tests membership in `guard.write_roots`, which is a set of
    FILESYSTEM prefixes; R2 is not a path, so it can never match and `upload`
    would silently be the one write-outside stage with no banner (§1.1(b),
    §4.1). The derivation here is over the planned R2 destination set instead: a
    future destination inherits the banner without anybody remembering to add it.
    """
    return bool(entries)


def banner(cfg, entries, bucket):
    lines = ["", "=" * 72,
             "  LIVE WRITE -- koreanize upload -- %s" % cfg["slug"],
             "=" * 72,
             "  destination   : R2 bucket %s (NOT a filesystem path)" % bucket,
             "  objects       : %d" % len(entries),
             "  bytes         : %d" % sum(e["size"] for e in entries),
             "  keys          :"]
    for entry in entries:
        lines.append("      %s  (%s, %.1f MiB)"
                     % (entry["predicted_key"], entry["file"],
                        entry["size"] / 1048576.0))
    lines.append("  NOTE          : R2 keys are CONTENT-ADDRESSED. A re-typeset")
    lines.append("                  ORPHANS the previous object rather than")
    lines.append("                  replacing it, and koreanize never deletes one")
    lines.append("                  (N-7). Deletion is a deliberate human action.")
    lines.append("=" * 72)
    return "\n".join(lines)


def confirm_live(cfg, entries, bucket, assume_yes=False, stream=None):
    """Returns True to proceed. The typed-LIVE prompt, matching sced-run-now.sh
    and `kz_langpack.confirm_live` -- exit 73 is the dispatcher's "declined at
    the banner" code and is reused verbatim so an operator reads one code for one
    fact regardless of which entry point produced it."""
    stream = stream or sys.stderr
    stream.write(banner(cfg, entries, bucket) + "\n")
    if assume_yes:
        return True
    if not sys.stdin.isatty():
        kc.refuse(kc.EXIT_USAGE, "--live needs a tty to confirm, or --yes",
                  "koreanize.sh prints the banner itself and passes --yes")
    stream.write("Type LIVE to proceed: ")
    stream.flush()
    return sys.stdin.readline().strip() == "LIVE"


# ===========================================================================
# 4. The delegation
# ===========================================================================

def uploader_argv(manifest_path, staging_dir, output_path, dry_run=False,
                  uploader_path=None):
    """The argv, built by a function so the SELFTEST can assert it.

    A delegation whose only evidence is that it once worked is a delegation
    nobody can check; this is what `--selftest delegation` reads.
    """
    argv = [sys.executable, uploader_path or UPLOADER_PATH,
            "--manifest", str(manifest_path),
            "--staging-dir", str(staging_dir),
            "--output", str(output_path)]
    if dry_run:
        argv.append("--dry-run")
    return argv


def run_uploader(manifest_path, staging_dir, output_path, dry_run=False,
                 uploader=None, uploader_path=None, env=None):
    """Delegate. Returns (rc, stdout+stderr, argv).

    `uploader` is an injection point taking the argv and returning
    `(rc, output)`; the suite and `--selftest` pass a stub, so the delegation is
    exercised without a credential or a socket. Production passes nothing and the
    real tool runs as a subprocess.
    """
    argv = uploader_argv(manifest_path, staging_dir, output_path, dry_run,
                         uploader_path)
    if uploader is not None:
        rc, out = uploader(argv)
        return int(rc), out, argv
    if not os.path.exists(argv[1]):
        kc.refuse(kc.EXIT_PRECONDITION,
                  "upload-atlases-to-r2.py is absent", argv[1])
    proc = subprocess.run(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                          env=env if env is not None else os.environ.copy())
    return proc.returncode, proc.stdout.decode("utf-8", "replace"), argv


# ===========================================================================
# 5. atlas-urls.json -- built FROM upload-map.json
# ===========================================================================

def join_map(entries, upload_map):
    """(rows, findings) -- the plan joined to what the uploader actually did.

    The join is what makes the map an OBSERVATION. A planned atlas with no row in
    `upload-map.json` is exit 74 naming it: the uploader either never got to it
    or dropped it, and either way there is no evidence it is on R2.
    """
    by_id = {}
    for row in upload_map.get("uploads") or []:
        key = row.get("atlas_id") or row.get("sha256")
        if key is not None:
            by_id[key] = row
    rows, findings = [], []
    for entry in entries:
        row = by_id.get(entry["atlas_id"]) or by_id.get(entry["sha256"])
        if row is None:
            findings.append("%s has no row in upload-map.json -- there is no "
                            "evidence it reached R2, and a URL derived from its "
                            "bytes would be a prediction, not a record"
                            % entry["atlas_id"])
            continue
        if row.get("sha256") and row["sha256"] != entry["sha256"]:
            findings.append("%s: upload-map.json records sha256 %s, the staged file "
                            "is %s" % (entry["atlas_id"], (row["sha256"] or "")[:16],
                                       entry["sha256"][:16]))
            continue
        rows.append(collections.OrderedDict([
            ("atlas_id", entry["atlas_id"]),
            ("file", entry["file"]),
            ("english_url", entry["english_url"]),
            # FROM THE MAP, both of them. The predictions are compared against
            # these (U3) and are never substituted for them.
            ("korean_url", row.get("url")),
            ("r2_key", row.get("key")),
            ("sha256", entry["sha256"]),
            ("size", row.get("size", entry["size"])),
            ("skipped", bool(row.get("skipped"))),
            ("packs", entry["packs"]),
        ]))
    return rows, findings


def prediction_findings(entries, rows):
    """U3 -- the derivation and the observation agree.

    Reported as a check rather than used as a source. When they disagree the
    OBSERVATION wins and the run fails, because a disagreement means this
    module's idea of the key differs from the uploader's -- which is exactly the
    condition under which a predicted `atlas-urls.json` would ship a 404.
    """
    by_id = {r["atlas_id"]: r for r in rows}
    findings = []
    for entry in entries:
        row = by_id.get(entry["atlas_id"])
        if row is None:
            continue
        if entry["predicted_url"] != row["korean_url"]:
            findings.append("%s: derived %s, uploader used %s"
                            % (entry["atlas_id"], entry["predicted_url"],
                               row["korean_url"]))
        if entry["predicted_key"] != row["r2_key"]:
            findings.append("%s: derived key %s, uploader used %s"
                            % (entry["atlas_id"], entry["predicted_key"],
                               row["r2_key"]))
    return findings


def cross_pack_findings(rows):
    """U5 -- the cross-pack Guest-card duplication rule, as a map constraint.

    One English URL must map to exactly ONE Korean URL across every pack. C3
    asserts BYTE identity of each `FaceURL` everywhere it appears (§5.8), so a
    map offering two Korean URLs for one English one makes C3 unsatisfiable by
    construction -- and the packs it would split are precisely the cross-pack
    cards N-10 duplicates rather than shares.

    The reverse direction is checked too and is a DIFFERENT defect: two English
    sheets collapsing onto one Korean URL means two distinct atlases hashed the
    same, i.e. the recompose output is not what it claims to be.
    """
    forward, backward = {}, {}
    for row in rows:
        if not row["english_url"] or not row["korean_url"]:
            continue
        forward.setdefault(row["english_url"], set()).add(row["korean_url"])
        backward.setdefault(row["korean_url"], set()).add(row["english_url"])
    findings = []
    for english in sorted(forward):
        if len(forward[english]) > 1:
            findings.append(
                "%s maps to %d Korean URLs (%s) -- a cross-pack card carries the "
                "SAME FaceURL in every pack (N-10, C3), so the entry is DUPLICATED "
                "into each pack, never made pack-scoped"
                % (english, len(forward[english]), sorted(forward[english])))
    for korean in sorted(backward):
        if len(backward[korean]) > 1:
            findings.append(
                "%s is the Korean URL for %d different English sheets (%s) -- two "
                "distinct atlases hashed the same, which is a recompose defect, "
                "not a mapping one" % (korean, len(backward[korean]),
                                       sorted(backward[korean])))
    return findings


def build_atlas_urls(cfg, rows, verified, bucket, manifest, upload_map_sha256):
    """`atlas-urls.json` -- §5.8's frozen artifact, and §8.4's orphan authority.

    `substitutions` is the shape `repoint` consumes and `verify`'s C3 compares
    against, byte for byte. The two rule blocks below are declarations `verify`
    reads: C11 has no other authority for the pin, and C3 has no other authority
    for the cross-pack rule.
    """
    substitutions = collections.OrderedDict()
    for row in sorted(rows, key=lambda r: r["atlas_id"]):
        if row["english_url"] and row["korean_url"]:
            substitutions[row["english_url"]] = row["korean_url"]
    return collections.OrderedDict([
        ("schema_version", kc.SCHEMA_VERSION),
        ("generated_by", "koreanize upload"),
        ("generated_at", kc.utc_now()),
        ("slug", cfg["slug"]),
        ("source", "upload-map.json"),
        ("source_sha256", upload_map_sha256),
        ("note", "Written FROM the uploader's upload-map.json, never from a "
                 "pre-upload prediction (design §6 step 15)."),
        ("bucket", bucket),
        ("substitutions", substitutions),
        # TWO NAMES FOR ONE MAPPING, AND THEY ARE THE SAME OBJECT.
        # `repoint` and the Midwinter record's shipped map call it
        # `substitutions`; `kz_verify` opens `urls` (`kz_verify.py:137,187`) for
        # C1's owned-url clause and C3's reference face. Emitting only one of the
        # two leaves the other consumer with no subject -- C1's cross-scope
        # clause would stay inert in v1, which is exactly the "reports a pass it
        # cannot fail" shape §5.8 rewrote C3 to avoid. They are assigned from ONE
        # dict rather than built twice, so they cannot drift.
        ("urls", substitutions),
        ("atlases", rows),
        ("shared_backs", manifest.get("shared_backs") or cfg.get("shared_backs") or []),
        ("verified", verified),
        ("nickname_pin", collections.OrderedDict([
            ("literal", SCENARIO_NICKNAME),
            ("policy", "never_localize"),
            ("korean_name_goes_in", "Description"),
            ("source", SCENARIO_NICKNAME_SOURCE),
            ("reason", "MythosArea.ttslua branches on cardName == \"Scenario\"; "
                       "localizing it breaks the scenario-changed event for EVERY "
                       "Korean scenario"),
            ("verifier", "kz_verify C11"),
        ])),
        ("cross_pack", collections.OrderedDict([
            ("rule", "one korean_url per english_url across every pack"),
            ("duplication", "a card appearing in two packs gets a DUPLICATE entry "
                            "in each, never one pack-scoped entry -- only "
                            "AllEncounterCardsBag rewrites objects spawned from a "
                            "scenario box (N-10)"),
            ("verifier", "kz_verify C3"),
            ("multi_pack_atlases", sorted(
                r["atlas_id"] for r in rows if len(r.get("packs") or []) > 1)),
        ])),
        # N-7: the orphan set. `upload` REPORTS it and refuses to delete anything;
        # cleanup is a separate, deliberate, human action (§8.4).
        ("orphans", collections.OrderedDict([
            ("policy", "never_deleted_by_koreanize"),
            ("note", "R2 keys are content-addressed, so a re-typeset orphans the "
                     "previous object. Deleting one before its repoint is re-run "
                     "turns an orphan into a 404 in a shipped pack (§8.4)."),
            ("known", []),
        ])),
    ])


def mirror_lock(cfg, report, atlas_urls, workspace=None):
    """HANDED to kz_config's mirror writer -- this module never opens a path under
    SCED-tools/ (§3.1). Same shape as `kz_langpack.mirror_lock`, plus the
    `atlas_urls` block §3.1 promotes out of run material."""
    workspace = workspace or kc.WORKSPACE_ROOT
    relpath = "%s.lock.json" % cfg["slug"]
    existing = os.path.join(workspace, cfg["guard"]["data_root"], "locks", relpath)
    lock = (load_json(existing, "lock") if os.path.exists(existing)
            else collections.OrderedDict())
    lock.setdefault("schema_version", kc.SCHEMA_VERSION)
    lock["slug"] = cfg["slug"]
    lock["generated_at"] = kc.utc_now()
    stages = lock.setdefault("stages", {})
    stages[report["stage"]] = collections.OrderedDict([
        ("verdict", report["verdict"]),
        ("exit_code", report["exit_code"]),
        ("consumable", report["consumable"]),
        ("gate", report["gate"]),
        ("binding", report["binding"]),
        ("write_set", report["write_set"]),
        ("registration", report["registration"]),
    ])
    lock["atlas_urls"] = atlas_urls
    return kz.write_data(cfg, "locks", relpath, kc.sorted_mapping(lock),
                         workspace=workspace)


# ===========================================================================
# 6. The stage
# ===========================================================================

def upstream_findings(run_dir):
    findings = []
    for stage in UPSTREAM:
        state = kz.stage_state(run_dir, stage)
        if not state["present"]:
            findings.append("upstream %s has no report (%s)" % (stage, state["report"]))
        elif not state["consumable"]:
            findings.append("upstream %s is not consumable: %s"
                            % (stage, state["blocked_by"]))
    return findings


def run_upload(run_dir, live=False, assume_yes=False, workspace=None,
               verifier=None, uploader=None, uploader_path=None, mirror=True,
               confirm=True, bucket=None, quiet=False):
    """Returns (report, atlas_urls). Rehearsal is the DEFAULT.

    PRECONDITIONS, numbered, in the order they are evaluated:

      1. P0a (launchd) and P0b (nightly scratch worktree)             -> exit 2
      2. <run_dir>/scenario.json exists, validates, and its pin holds  -> exit 4 / 13
      3. atlases/upload-manifest.json is present                       -> exit 13
      4. every planned asset is a real PNG whose bytes match the
         manifest's declared sha256 (N-8 among them)                   -> exit 13
      5. the recompose report is present and consumable                -> exit 13
      6. on --live, <run_dir>/upload.plan.json exists and binds        -> exit 13
      7. on --live, the banner is answered                             -> exit 73
    """
    workspace = workspace or kc.WORKSPACE_ROOT
    kc.check_invocation_guards([run_dir])

    cfg = kz.load_scenario(os.path.join(run_dir, "scenario.json"))
    mode = "build" if live else "dry-run"

    staging_dir = os.path.join(run_dir, "atlases")
    manifest_path = os.path.join(staging_dir, "upload-manifest.json")
    manifest = load_json(manifest_path, "atlases/upload-manifest.json")

    entries, reasons = build_plan(manifest, staging_dir, workspace)
    if not entries and not reasons:
        reasons.append("the upload manifest names no atlas -- refusing a vacuous "
                       "pass over an empty upload")
    reasons += upstream_findings(run_dir)
    if reasons:
        kc.refuse(kc.EXIT_PRECONDITION,
                  "%s cannot trust its inputs -- nothing uploaded" % STAGE,
                  "; ".join(reasons[:kz.GUARD_FINDING_SAMPLE]))

    bucket = bucket or os.environ.get("R2_BUCKET") or "tts-ahcg-assets"
    needs_banner = requires_live(entries)

    triggered, checks = [], []
    rows, verified, upload_map = [], {}, None
    upload_map_path = os.path.join(run_dir, "upload-map.json")
    upload_map_sha256 = None
    delegation = None

    if mode == "build":
        persisted = plan_path(run_dir)
        if not os.path.exists(persisted):
            kc.refuse(kc.EXIT_PRECONDITION,
                      "a --live upload has no upload.plan.json to bind against",
                      "run the rehearsal first; the banner is built from it")
        assert_plan_matches(entries, load_json(persisted, "upload.plan.json"))
        if confirm and needs_banner and not confirm_live(cfg, entries, bucket,
                                                         assume_yes):
            kc.refuse(kc.EXIT_DISPATCH_LIVE_DECLINED,
                      "--live declined at the banner")

        rc, output, argv = run_uploader(manifest_path, staging_dir,
                                        upload_map_path, dry_run=False,
                                        uploader=uploader,
                                        uploader_path=uploader_path)
        delegation = {"argv": [os.path.relpath(a, workspace)
                               if os.path.isabs(a) and a.startswith(workspace) else a
                               for a in argv],
                      "rc": rc, "tail": (output or "").strip().splitlines()[-5:]}
        if rc != 0:
            checks.append({"id": "U1", "name": "delegated_upload_rc_zero",
                           "status": "fail", "exit_on_fail": kc.EXIT_NETWORK,
                           "detail": ["upload-atlases-to-r2.py exited %d" % rc]
                                     + delegation["tail"]})
            triggered.append(kc.EXIT_NETWORK)
        else:
            checks.append({"id": "U1", "name": "delegated_upload_rc_zero",
                           "status": "pass", "exit_on_fail": kc.EXIT_NETWORK,
                           "detail": []})
            upload_map = load_json(upload_map_path, "upload-map.json")
            upload_map_sha256 = kc.sha256_file(upload_map_path)
            bucket = upload_map.get("bucket") or bucket
    else:
        # THE REHEARSAL DOES NOT TOUCH R2. It persists the plan the banner is
        # built from and stops; §4.1 makes rehearsal the default for every stage
        # that writes outside <run_dir>, so forgetting --live is a rehearsal
        # rather than a refusal.
        kc.atomic_write_json(plan_path(run_dir),
                             plan_document(cfg, entries, bucket))
        checks.append({"id": "U1", "name": "delegated_upload_rc_zero",
                       "status": "pass", "exit_on_fail": kc.EXIT_NETWORK,
                       "detail": ["rehearsal: no R2 call, plan persisted to "
                                  "%s" % os.path.relpath(plan_path(run_dir),
                                                         workspace)]})

    if upload_map is not None:
        # U2 -- the join. atlas-urls.json is built from THESE rows.
        rows, join_bad = join_map(entries, upload_map)
        checks.append({"id": "U2", "name": "atlas_urls_sourced_from_upload_map",
                       "status": "pass" if not join_bad else "fail",
                       "exit_on_fail": kc.EXIT_NETWORK, "detail": join_bad[:10]})
        if join_bad:
            triggered.append(kc.EXIT_NETWORK)

        # U3 -- the derivation and the observation agree. The observation wins.
        pred = prediction_findings(entries, rows)
        checks.append({"id": "U3", "name": "derived_url_matches_uploaded_url",
                       "status": "pass" if not pred else "fail",
                       "exit_on_fail": kc.EXIT_NETWORK, "detail": pred[:10]})
        if pred:
            triggered.append(kc.EXIT_NETWORK)

        # U4 -- the four-way verification, per object.
        verifier = verifier or R2Verifier(bucket=bucket)
        four_bad, tally = [], collections.OrderedDict(
            [("head_object", 0), ("public_get", 0), ("png_magic", 0),
             ("sha256_roundtrip", 0)])
        for row in rows:
            entry = {"atlas_id": row["atlas_id"], "key": row["r2_key"],
                     "url": row["korean_url"], "sha256": row["sha256"],
                     "size": row["size"]}
            per, findings = verify_four_ways(entry, verifier)
            row["verified"] = per
            for name, ok in per.items():
                if ok:
                    tally[name] += 1
            four_bad += findings
        verified = collections.OrderedDict(
            [(name, tally[name] == len(rows) and bool(rows)) for name in tally])
        verified["objects"] = len(rows)
        checks.append({"id": "U4", "name": "four_way_verification",
                       "status": "pass" if not four_bad else "fail",
                       "exit_on_fail": kc.EXIT_NETWORK, "detail": four_bad[:10]})
        if four_bad:
            triggered.append(kc.EXIT_NETWORK)

        # U5 -- the cross-pack rule.
        cross = cross_pack_findings(rows)
        checks.append({"id": "U5", "name": "cross_pack_url_agreement",
                       "status": "pass" if not cross else "fail",
                       "exit_on_fail": kc.EXIT_NETWORK, "detail": cross[:10]})
        if cross:
            triggered.append(kc.EXIT_NETWORK)

    # U6 -- the pin is DECLARED in the artifact `verify` reads. Recorded on both
    # paths, because a rehearsal that did not carry it would let the operator
    # believe the declaration is a property of the live run rather than of the
    # map's schema.
    checks.append({"id": "U6", "name": "scenario_nickname_pin_declared",
                   "status": "pass", "exit_on_fail": kc.EXIT_ARTIFACT,
                   "detail": ["%r stays English (%s); the Korean name goes in "
                              "Description. kz_verify C11 is the verifier."
                              % (SCENARIO_NICKNAME, SCENARIO_NICKNAME_SOURCE)]})

    counts = collections.OrderedDict([
        ("atlases", len(entries)),
        ("uploaded", sum(1 for r in rows if not r["skipped"])),
        ("already_present", sum(1 for r in rows if r["skipped"])),
        ("bytes", sum(e["size"] for e in entries)),
        ("substitutions", len({r["english_url"] for r in rows if r["english_url"]})),
        ("multi_pack_atlases", sum(1 for r in rows if len(r.get("packs") or []) > 1)),
    ])

    binding_paths = [os.path.join(run_dir, "scenario.json"), manifest_path]
    binding_extra = {}
    if mode == "build":
        binding_extra["upload.plan.json"] = kc.sha256_file(plan_path(run_dir))
    if upload_map_sha256:
        binding_extra["upload-map.json"] = upload_map_sha256

    report = kc.new_report(
        STAGE, cfg["slug"], mode=mode, counts=counts, checks=checks,
        binding=kc.build_binding(binding_paths, extra=binding_extra),
        freshness=kc.build_freshness(
            [manifest_path],
            upstream_report_path=kc.report_path(run_dir, "recompose", "build")),
        tool=kc.tool_block(),
        results={"bucket": bucket, "atlases": rows, "verified": verified,
                 "delegation": delegation,
                 "plan": os.path.relpath(plan_path(run_dir), workspace)})
    kc.finalize_report(report, cfg=cfg, triggered=triggered)
    if report["verdict"] == "PRECONDITION":
        report["results"] = None

    atlas_urls = None
    if mode == "build" and report["exit_code"] == kc.EXIT_OK:
        atlas_urls = build_atlas_urls(cfg, rows, verified, bucket, manifest,
                                      upload_map_sha256)
        # TWO DESTINATIONS, AND THE RECEIPT IS THE AUTHORITY.
        # §3.1 promotes this file out of run material because it is "§8.4's sole
        # authority for the N-7 orphan set" and would otherwise die with the
        # gitignored <run_dir>. That is an argument for the receipt EXISTING, not
        # against the run-dir copy: `kz_verify` is stdlib-tier and opens
        # `<run_dir>/atlas-urls.json` directly (`kz_verify.py:121`), and `golden`
        # compares the produced atlas sha256s against it inside a scratch run dir
        # that has no lock at all (§5.8 step 5). So both are written, from one
        # document, and the lock is the one that survives.
        run_copy = os.path.join(run_dir, "atlas-urls.json")
        kc.atomic_write_json(run_copy, atlas_urls)
        report["write_set"] = [{"path": os.path.relpath(run_copy, workspace),
                                "action": "create"}]
        if mirror:
            # AFTER write_set is set, because `mirror_lock` copies it into the
            # receipt; and the lock itself is deliberately NOT added to it
            # afterwards -- a receipt that recorded its own write would give
            # `revert` a path to undo that is the record of the undoing (§8.4).
            report["results"]["lock"] = os.path.relpath(
                mirror_lock(cfg, report, atlas_urls, workspace), workspace)
    return report, atlas_urls


# ===========================================================================
# 7. --selftest -- no credential, no bucket, no socket
# ===========================================================================

def _fake_png(payload=b"kz"):
    """A byte string that starts with the PNG signature. Not a decodable image --
    nothing in this module decodes one, and pretending otherwise would put a PIL
    dependency in a stage whose whole subject is bytes and URLs."""
    return PNG_MAGIC + payload


def _stub_entry(digest, size=None, atlas_id="8x5-face"):
    url = kr.face_url_for_sha(digest)
    return {"atlas_id": atlas_id, "key": _key_from_url(url), "url": url,
            "sha256": digest, "size": size}


def selftest(fault=None, verbose=True):
    """Prove each named refusal fires on the fault it targets."""
    findings = []
    wanted = (fault,) if fault else FAULTS
    if fault is not None and fault not in FAULTS:
        kc.refuse(kc.EXIT_USAGE, "unknown fault %r" % fault,
                  "the faults are %s" % list(FAULTS))

    body = _fake_png(b"the-atlas-bytes")
    digest = sha256_bytes(body)
    entry = _stub_entry(digest, size=len(body))

    if "delegation" in wanted:
        argv = uploader_argv("/m.json", "/staging", "/out.json")
        if os.path.basename(argv[1]) != "upload-atlases-to-r2.py":
            findings.append("delegation: argv[1] is %r, not the shipped uploader"
                            % argv[1])
        for flag in ("--manifest", "--staging-dir", "--output"):
            if flag not in argv:
                findings.append("delegation: argv carries no %s" % flag)
        if "--dry-run" in argv:
            findings.append("delegation: a live argv carried --dry-run")
        if "--dry-run" not in uploader_argv("/m", "/s", "/o", dry_run=True):
            findings.append("delegation: --dry-run was not passed through")
        # The R2 client is NOT reimplemented here: this module must contain no
        # boto3 write call of its own outside R2Verifier, which only HEADs.
        with open(os.path.abspath(__file__), "r", encoding="utf-8") as handle:
            src = handle.read()
        # The needle is ASSEMBLED so that this check does not match its own
        # source -- the only way a source scan for a call can live in the file it
        # scans (the same trick kz_recompose's cap scan uses).
        if ("put_" + "object") in src:
            findings.append("delegation: this module makes the PUT itself -- the "
                            "upload is delegated, never reimplemented")
        # The injection point is real: a stub uploader is actually called.
        seen = {}

        def stub(argv_in):
            seen["argv"] = argv_in
            return 0, "stub ok"

        rc, out, argv2 = run_uploader("/m.json", "/staging", "/out.json",
                                      uploader=stub)
        if rc != 0 or seen.get("argv") != argv2:
            findings.append("delegation: the uploader injection point was not used")

    if "four-way" in wanted:
        clean = StubVerifier(heads={entry["key"]: len(body)},
                             bodies={entry["url"]: body})
        per, bad = verify_four_ways(entry, clean)
        if bad or not all(per.values()):
            findings.append("four-way: the clean control did not verify (%r / %r)"
                            % (per, bad))
        if list(per) != ["head_object", "public_get", "png_magic",
                         "sha256_roundtrip"]:
            findings.append("four-way: the four checks are not the four named in "
                            "analyze.md:337 (%r)" % list(per))

        # V1 -- absent from the bucket, and present at the wrong size.
        per, bad = verify_four_ways(entry, StubVerifier(bodies={entry["url"]: body}))
        if per["head_object"] or not any("V1" in b for b in bad):
            findings.append("four-way: an object absent from the bucket passed V1")
        per, bad = verify_four_ways(
            entry, StubVerifier(heads={entry["key"]: len(body) + 1},
                                bodies={entry["url"]: body}))
        if per["head_object"]:
            findings.append("four-way: a ContentLength mismatch passed V1")

        # V2 -- the edge refused (the 403-to-Python-urllib case).
        per, bad = verify_four_ways(entry,
                                    StubVerifier(heads={entry["key"]: len(body)}))
        if per["public_get"] or per["png_magic"] or per["sha256_roundtrip"]:
            findings.append("four-way: a failed GET did not carry V3/V4 with it")

        # V3 -- an HTML error page served with status 200. It passes V2 and must
        # fail V3, which is the whole reason V3 is a separate check.
        html = b"<html><body>404</body></html>"
        per, bad = verify_four_ways(
            entry, StubVerifier(heads={entry["key"]: len(body)},
                                bodies={entry["url"]: html}))
        if not per["public_get"]:
            findings.append("four-way: the HTML case did not even reach V3")
        if per["png_magic"]:
            findings.append("four-way: an HTML body served with status 200 passed "
                            "the PNG-magic check")

        # V4 -- a stale object at a content-addressed key.
        stale = _fake_png(b"different-bytes-entirely")
        per, bad = verify_four_ways(
            entry, StubVerifier(heads={entry["key"]: len(body)},
                                bodies={entry["url"]: stale}))
        if not per["png_magic"]:
            findings.append("four-way: the stale case is not a PNG, so V4 is not "
                            "isolated")
        if per["sha256_roundtrip"]:
            findings.append("four-way: a stale object at a content-addressed key "
                            "passed the sha256 round-trip")

    if "urls-from-map" in wanted:
        plan = [{"atlas_id": "8x5-face", "english_url": "https://en/a.png",
                 "file": "8x5-face.png", "sha256": digest, "size": len(body),
                 "predicted_url": entry["url"], "predicted_key": entry["key"],
                 "packs": ["Korean - Campaigns"]}]
        # The uploader used a DIFFERENT url and key than the prediction. The map
        # must carry the uploader's, and U3 must report the disagreement.
        other = "https://pub-05b4fa32b44341d797f5c66d59384724.r2.dev/langpack/" \
                "images/sha256/ff/deadbeef.png"
        rows, bad = join_map(plan, {"bucket": "b", "uploads": [
            {"atlas_id": "8x5-face", "sha256": digest, "key": _key_from_url(other),
             "url": other, "size": len(body), "skipped": False}]})
        if bad:
            findings.append("urls-from-map: a well-formed map did not join (%r)" % bad)
        elif rows[0]["korean_url"] != other:
            findings.append("urls-from-map: atlas-urls.json took the PREDICTED url "
                            "rather than the one the uploader recorded -- this is "
                            "the defect §6 step 15 names")
        if not prediction_findings(plan, rows):
            findings.append("urls-from-map: a prediction disagreeing with the map "
                            "was not reported")
        # An atlas with no row at all is a refusal, not a derived URL.
        rows, bad = join_map(plan, {"bucket": "b", "uploads": []})
        if rows or not any("no row in upload-map.json" in b for b in bad):
            findings.append("urls-from-map: an atlas with no upload-map.json row "
                            "was given a URL anyway")

    if "cross-pack" in wanted:
        one = {"atlas_id": "a", "english_url": "https://en/x.png",
               "korean_url": "https://ko/1.png", "packs": ["A", "B"]}
        two = {"atlas_id": "b", "english_url": "https://en/x.png",
               "korean_url": "https://ko/2.png", "packs": ["B"]}
        if cross_pack_findings([one]):
            findings.append("cross-pack: a card in two packs with ONE Korean URL "
                            "was reported -- duplication is the rule, not a defect")
        if not cross_pack_findings([one, two]):
            findings.append("cross-pack: one English URL mapping to two Korean URLs "
                            "was accepted, which makes C3 unsatisfiable")
        collide = [{"atlas_id": "a", "english_url": "https://en/x.png",
                    "korean_url": "https://ko/1.png", "packs": []},
                   {"atlas_id": "b", "english_url": "https://en/y.png",
                    "korean_url": "https://ko/1.png", "packs": []}]
        if not any("two distinct atlases hashed the same"
                   in f for f in cross_pack_findings(collide)):
            findings.append("cross-pack: two English sheets collapsing onto one "
                            "Korean URL was not reported")

    if "nickname-pin" in wanted:
        cfg = {"slug": "s", "shared_backs": []}
        rows = [{"atlas_id": "a", "file": "a.png", "english_url": "https://en/x.png",
                 "korean_url": "https://ko/1.png", "r2_key": "k", "sha256": "d",
                 "size": 1, "skipped": False, "packs": ["A", "B"]}]
        doc = build_atlas_urls(cfg, rows, {}, "b", {}, None)
        pin = doc.get("nickname_pin") or {}
        if pin.get("literal") != SCENARIO_NICKNAME:
            findings.append("nickname-pin: atlas-urls.json does not declare the "
                            "%r literal C11 verifies" % SCENARIO_NICKNAME)
        if pin.get("source") != SCENARIO_NICKNAME_SOURCE:
            findings.append("nickname-pin: the declaration cites %r, not the Lua "
                            "line it is pinned to" % pin.get("source"))
        if pin.get("korean_name_goes_in") != "Description":
            findings.append("nickname-pin: the declaration does not say where the "
                            "Korean name goes instead")
        if doc["substitutions"] != {"https://en/x.png": "https://ko/1.png"}:
            findings.append("nickname-pin: substitutions{} is not the "
                            "english->korean map repoint and C3 consume")
        if doc["cross_pack"]["multi_pack_atlases"] != ["a"]:
            findings.append("nickname-pin: the cross-pack block does not name the "
                            "multi-pack atlases")
        if (doc.get("orphans") or {}).get("policy") != "never_deleted_by_koreanize":
            findings.append("nickname-pin: the N-7 orphan policy is not declared")

    if "png-only" in wanted:
        tmp = tempfile.mkdtemp(prefix="kz-upload-selftest.")
        try:
            with open(os.path.join(tmp, "guide.pdf"), "wb") as handle:
                handle.write(b"%PDF-1.4\n")
            _e, bad = build_plan(
                {"atlases": [{"file": "guide.pdf", "atlas_sha256": "d",
                              "face_url": "https://x.r2.dev/k.pdf"}]}, tmp)
            if not any("out of scope" in b for b in bad):
                findings.append("png-only: a PDF was planned for upload -- both "
                                "uploaders hardcode ContentType: image/png (N-8)")
            # A .png whose bytes are not a PNG is refused too: the extension is
            # not the evidence.
            with open(os.path.join(tmp, "liar.png"), "wb") as handle:
                handle.write(b"not a png at all")
            _e, bad = build_plan(
                {"atlases": [{"file": "liar.png", "atlas_sha256": "d",
                              "face_url": "https://x.r2.dev/k.png"}]}, tmp)
            if not any("PNG signature" in b for b in bad):
                findings.append("png-only: a .png with no PNG signature was planned")
            # A staged file whose bytes have moved since recompose wrote the
            # manifest: the derived key would point at bytes that are not there.
            real = os.path.join(tmp, "real.png")
            with open(real, "wb") as handle:
                handle.write(body)
            _e, bad = build_plan(
                {"atlases": [{"file": "real.png", "atlas_sha256": "0" * 64,
                              "face_url": "https://x.r2.dev/k.png"}]}, tmp)
            if not any("manifest is stale" in b for b in bad):
                findings.append("png-only: a staged file disagreeing with the "
                                "manifest's sha256 was planned anyway")
            good, bad = build_plan(
                {"atlases": [{"atlas_id": "8x5-face", "file": "real.png",
                              "atlas_sha256": digest,
                              "face_url": entry["url"]}]}, tmp)
            if bad or len(good) != 1:
                findings.append("png-only: the clean control did not plan (%r)" % bad)
        finally:
            import shutil
            shutil.rmtree(tmp, ignore_errors=True)

    if "live" in wanted:
        # The banner is DERIVED from the R2 destination set, never from the stage
        # name -- kz_langpack's write-root test cannot see R2 at all (§1.1(b)).
        if requires_live([]):
            findings.append("live: an empty destination set required a banner")
        if not requires_live([{"x": 1}]):
            findings.append("live: a non-empty R2 destination set did not require "
                            "--live")
        cfg = {"slug": "s"}
        plan = [{"atlas_id": "a", "file": "a.png", "size": 3,
                 "predicted_key": "langpack/images/sha256/ab/a.png"}]
        text = banner(cfg, plan, "tts-ahcg-assets")
        for needle in ("LIVE WRITE", "tts-ahcg-assets", "CONTENT-ADDRESSED",
                       "langpack/images/sha256/ab/a.png"):
            if needle not in text:
                findings.append("live: the banner does not name %r" % needle)
        # The plan is an ARTIFACT and the live run binds it. A moved digest is a
        # refusal, not a re-plan.
        persisted = plan_document({"slug": "s"}, [
            {"atlas_id": "a", "file": "a.png", "sha256": "aa", "size": 1,
             "predicted_key": "k", "predicted_url": "u"}], "b")
        try:
            assert_plan_matches([{"atlas_id": "a", "sha256": "bb"}], persisted)
        except kc.KzRefusal as exc:
            if exc.code != kc.EXIT_PRECONDITION:
                findings.append("live: a moved digest refused with %d, expected 13"
                                % exc.code)
        else:
            findings.append("live: a plan whose bytes moved since the banner was "
                            "accepted")
        try:
            assert_plan_matches([], persisted)
        except kc.KzRefusal as exc:
            if exc.code != kc.EXIT_PRECONDITION:
                findings.append("live: a length mismatch refused with %d" % exc.code)
        else:
            findings.append("live: a plan of a different length was accepted")

    if verbose:
        for line in findings:
            print("  - %s" % line)
    return findings


# ===========================================================================
# 8. CLI
# ===========================================================================

_SUMMARY = {
    "delegation": "the argv names upload-atlases-to-r2.py with --manifest / "
                  "--staging-dir / --output, passes --dry-run through, is driven "
                  "through an injection point, and this module contains no "
                  "PUT of its own",
    "four-way": "head_object, browser-UA public GET, PNG magic and full-body "
                "sha256 round-trip each fire on their own fault -- an absent "
                "object, a size mismatch, a refused GET, an HTML page served 200, "
                "and a stale object at a content-addressed key",
    "urls-from-map": "atlas-urls.json takes the url and key the uploader RECORDED, "
                     "reports a disagreeing prediction, and refuses an atlas with "
                     "no row in upload-map.json rather than deriving one",
    "cross-pack": "one English URL mapping to two Korean URLs is refused, a card "
                  "in two packs sharing ONE Korean URL is the rule, and two "
                  "sheets collapsing onto one URL is reported as a recompose defect",
    "nickname-pin": "atlas-urls.json declares the \"Scenario\" pin with its Lua "
                    "citation, the substitutions map, the cross-pack block and "
                    "the N-7 orphan policy -- the frozen artifact C3 and C11 read",
    "png-only": "a PDF, a .png with no signature, and a staged file disagreeing "
                "with the manifest's sha256 are each refused at 13 (N-8)",
    "live": "--live is derived from the R2 destination set, the banner names the "
            "bucket and the content-addressing consequence, and the persisted "
            "plan is bound key-for-key",
}


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="kz_upload.py",
        description="koreanize stage `upload` -- delegate to "
                    "upload-atlases-to-r2.py, verify four ways, and write "
                    "atlas-urls.json from upload-map.json (design §6 step 15).")
    parser.add_argument("--run-dir", help="<run_dir> holding scenario.json")
    parser.add_argument("--slug", help="derive --run-dir as .am/koreanize/<slug>")
    parser.add_argument("--live", action="store_true",
                        help="actually PUT to R2. Rehearsal is the default.")
    parser.add_argument("--yes", action="store_true",
                        help="skip the typed LIVE prompt (koreanize.sh prints its "
                             "own banner and passes this)")
    parser.add_argument("--bucket", help="override R2_BUCKET for the plan/banner")
    parser.add_argument("--json-only", action="store_true")
    parser.add_argument("--quiet", action="store_true")
    parser.add_argument("--selftest", nargs="?", const="", metavar="FAULT",
                        help="run every named fault, or just FAULT (%s)"
                             % ", ".join(FAULTS))
    args = parser.parse_args(argv)

    if args.selftest is not None:
        fault = args.selftest or None
        print("kz_upload --selftest%s" % (" %s" % fault if fault else ""))
        findings = selftest(fault=fault)
        if findings:
            print("\nFAIL (%d):" % len(findings))
            return kc.EXIT_ARTIFACT
        if fault:
            print("  ok: %s" % _SUMMARY[fault])
        else:
            for name in FAULTS:
                print("  ok: %-14s %s" % (name, _SUMMARY[name]))
        return kc.EXIT_OK

    run_dir = args.run_dir
    if not run_dir and args.slug:
        run_dir = os.path.join(kc.WORKSPACE_ROOT, ".am", "koreanize", args.slug)
    if not run_dir:
        parser.print_help()
        return kc.EXIT_USAGE

    report, _urls = run_upload(run_dir, live=args.live, assume_yes=args.yes,
                               bucket=args.bucket, quiet=args.quiet)
    path = kc.write_report(report, run_dir)

    if args.json_only:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return report["exit_code"]

    if not args.quiet:
        counts = report["counts"]
        print("koreanize upload -- %s (%s)" % (report["slug"], report["mode"]))
        print("  atlases         : %d (%d uploaded, %d already present)"
              % (counts["atlases"], counts["uploaded"], counts["already_present"]))
        print("  substitutions   : %d english -> korean" % counts["substitutions"])
        for check in report["checks"]:
            print("  %-34s: %-4s  %s"
                  % (check["name"], check["status"], "; ".join(check["detail"][:2])))
        print("  verdict         : %s (exit %d, consumable %s)"
              % (report["verdict"], report["exit_code"], report["consumable"]))
        print("  wrote           : %s" % path)
    return report["exit_code"]


if __name__ == "__main__":
    try:
        sys.exit(main())
    except kc.KzRefusal as _exc:
        sys.stderr.write("%s\n" % _exc)
        sys.exit(_exc.code)
