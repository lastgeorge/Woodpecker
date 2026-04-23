"""CLI subcommand: woodpecker run-img

Generate and optionally execute the wire-cell imaging command derived from
files already saved in ./woodpecker_data/ by 'woodpecker select'.

The command is built from:
  - the masked frame files in --datadir  (e.g. ./woodpecker_data/)
  - their anode IDs (parsed from filenames)
  - the shared input_prefix (the part before -anodeN.tar.bz2)

It produces the equivalent of:
  wire-cell -l stdout -L debug \\
    --tla-str  input_prefix='<prefix>' \\
    --tla-str  input='<datadir>' \\
    --tla-code anode_indices='[N, ...]' \\
    -c <jsonnet>

Usage
-----
  woodpecker run-img                          # auto-detect from ./woodpecker_data/
  woodpecker run-img --dry-run               # print command, don't run
  woodpecker run-img --datadir /some/path
  woodpecker run-img --jsonnet /path/to/wct-img-all.jsonnet
  woodpecker run-img --prefix protodune-sp-frames-part
"""

from __future__ import annotations

import argparse
import glob
import os
import re
import shutil
import subprocess
import sys


# Tools bundled with woodpecker
_TOOLS_DIR = os.path.join(os.path.dirname(__file__), "..", "tools")

# Pattern: <prefix>-anode<N>.tar.bz2
_FNAME_RE = re.compile(r"^(.+)-anode(\d+)\.tar\.bz2$")


def add_parser(subparsers) -> None:
    p = subparsers.add_parser(
        "run-img",
        help="Run wire-cell imaging on masked frames saved in ./woodpecker_data/",
    )
    p.add_argument(
        "--datadir", default="woodpecker_data",
        help="Directory containing masked tar.bz2 files (default: ./woodpecker_data/)",
    )
    p.add_argument(
        "--prefix", default=None,
        help="Override input_prefix (auto-detected from filenames if omitted)",
    )
    p.add_argument(
        "--jsonnet", default=None,
        help="Path to the imaging jsonnet (default: auto-search relative to --script-dir)",
    )
    p.add_argument(
        "--script-dir", default=None,
        help="Directory containing wct-img-all.jsonnet; wire-cell runs from here "
             "(default: auto-search for wcp-porting-img/pdvd relative to CWD)",
    )
    p.add_argument(
        "--wct-base", default=None,
        help="WCT_BASE directory. "
             "Sets WIRECELL_PATH to include toolkit/cfg and "
             "dunereco/dunereco/DUNEWireCell/protodunevd",
    )
    p.add_argument(
        "--log-level", default="info", choices=["debug", "info", "warning", "error"],
        help="wire-cell -L log level (default: info)",
    )
    p.add_argument(
        "--anode-indices", default=None,
        help="Override anode indices as JSON list e.g. '[1,2]' "
             "(default: auto-detect from files in --datadir)",
    )
    p.add_argument(
        "--dry-run", action="store_true",
        help="Print the wire-cell command but do not execute it",
    )
    p.add_argument(
        "--bee", action="store_true",
        help="After imaging, convert clusters to bee format and upload; print the URL",
    )
    p.set_defaults(func=run)


# ── helpers ───────────────────────────────────────────────────────────────────

def _find_masked_files(datadir: str):
    """Return list of (prefix, anode_id) for every matching file in datadir."""
    pattern = os.path.join(datadir, "*.tar.bz2")
    matches = []
    for path in sorted(glob.glob(pattern)):
        fname = os.path.basename(path)
        m = _FNAME_RE.match(fname)
        if m:
            matches.append((m.group(1), int(m.group(2)), path))
    return matches


def _resolve_wct_base(wct_base: str | None) -> str | None:
    """Return the WCT_BASE directory, or None if it doesn't exist."""
    if wct_base and os.path.isdir(wct_base):
        return wct_base
    return None


def _build_env(wct_base: str | None) -> dict:
    """Return an environment dict with WIRECELL_PATH augmented for wct_base."""
    env = os.environ.copy()
    if wct_base is None:
        return env
    extra = os.pathsep.join([
        os.path.join(wct_base, "toolkit", "cfg"),
        os.path.join(wct_base, "dunereco", "dunereco",
                     "DUNEWireCell", "protodunevd"),
    ])
    current = env.get("WIRECELL_PATH", "")
    env["WIRECELL_PATH"] = extra + (os.pathsep + current if current else "")
    return env


def _resolve_jsonnet(script_dir: str | None) -> str | None:
    """Try to locate wct-img-all.jsonnet near the script dir or CWD ancestors."""
    candidates = []
    if script_dir:
        candidates.append(os.path.join(script_dir, "wct-img-all.jsonnet"))

    # Search upward from CWD for the wirecell-working root, then look in
    # wcp-porting-img/pdvd/ — works whether you run from woodpecker_working/
    # or the repo root.
    cwd = os.path.abspath(".")
    for _ in range(5):
        candidates.append(os.path.join(cwd, "wcp-porting-img", "pdvd", "wct-img-all.jsonnet"))
        parent = os.path.dirname(cwd)
        if parent == cwd:
            break
        cwd = parent

    for c in candidates:
        if os.path.isfile(c):
            return os.path.abspath(c)
    return None


# ── main ──────────────────────────────────────────────────────────────────────

def run(args: argparse.Namespace) -> None:
    datadir = args.datadir

    if not os.path.isdir(datadir):
        print(f"ERROR: data directory not found: {datadir}", file=sys.stderr)
        sys.exit(1)

    matches = _find_masked_files(datadir)
    if not matches:
        print(f"ERROR: no <prefix>-anodeN.tar.bz2 files found in {datadir}",
              file=sys.stderr)
        sys.exit(1)

    # All files must share the same prefix
    prefixes = {p for p, _, _ in matches}
    if args.prefix:
        prefix = args.prefix
        # filter to only files matching that prefix
        matches = [(p, n, f) for p, n, f in matches if p == prefix]
        if not matches:
            print(f"ERROR: no files with prefix '{prefix}' in {datadir}",
                  file=sys.stderr)
            sys.exit(1)
    elif len(prefixes) > 1:
        print(f"ERROR: multiple prefixes found in {datadir}: {sorted(prefixes)}\n"
              f"Use --prefix to select one.", file=sys.stderr)
        sys.exit(1)
    else:
        prefix = prefixes.pop()

    anode_ids = sorted(n for _, n, _ in matches)

    # Override anode indices if explicitly provided
    if args.anode_indices:
        override_ids = [int(x) for x in re.findall(r"\d+", args.anode_indices)]
        matches = [(p, n, f) for p, n, f in matches if n in override_ids]
        if not matches:
            print(f"ERROR: none of the anode indices {override_ids} found in {datadir}",
                  file=sys.stderr)
            sys.exit(1)
        anode_ids = sorted(n for _, n, _ in matches)

    # Resolve jsonnet path
    jsonnet = args.jsonnet or _resolve_jsonnet(args.script_dir)
    if jsonnet is None:
        print("ERROR: could not find wct-img-all.jsonnet.\n"
              "Use --jsonnet /path/to/wct-img-all.jsonnet", file=sys.stderr)
        sys.exit(1)

    # wire-cell resolves "{input_prefix}-anodeN.tar.bz2" relative to its CWD.
    # We do NOT change CWD — wire-cell runs in the caller's CWD so that
    # relative WIRECELL_PATH entries and geometry files resolve correctly.
    # The datadir and jsonnet are kept as-is (relative or absolute, whatever
    # the user provided / auto-detected).
    anode_list = "[" + ",".join(str(i) for i in anode_ids) + "]"
    rel_prefix = os.path.join(datadir, prefix)   # e.g. woodpecker_data/protodune-sp-frames-part

    wct_base = _resolve_wct_base(args.wct_base)
    env      = _build_env(wct_base)

    cmd = [
        "wire-cell",
        "-l", "stdout",
        "-L", args.log_level,
        "--tla-str",  f"input_prefix={rel_prefix}",
        "--tla-str",  f"output_dir={datadir}",
        "--tla-code", f"anode_indices={anode_list}",
        "-c", jsonnet,
    ]

    print("\n" + "=" * 60)
    print("wire-cell imaging command")
    print("=" * 60)
    print(f"  input_prefix  : {rel_prefix}")
    print(f"  output_dir    : {datadir}")
    print(f"  anode_indices : {anode_list}")
    print(f"  jsonnet       : {jsonnet}")
    print(f"  wct_base      : {wct_base or '(not found, using current WIRECELL_PATH)'}")
    print(f"  WIRECELL_PATH : {env.get('WIRECELL_PATH', '(not set)')}")
    print(f"  files         :")
    for _, n, f in matches:
        print(f"    anode {n}  →  {f}")
    print()
    print("Command:")
    print("  " + " \\\n    ".join(cmd))
    print("=" * 60 + "\n")

    if args.dry_run:
        print("(dry-run: not executing)")
        return

    result = subprocess.run(cmd, env=env)
    if result.returncode != 0:
        sys.exit(result.returncode)

    if not args.bee:
        return

    # ── bee conversion + upload ───────────────────────────────────────────────
    bee_script    = os.path.abspath(os.path.join(_TOOLS_DIR, "wct-img-2-bee-combined.py"))
    upload_script = os.path.abspath(os.path.join(_TOOLS_DIR, "upload-to-bee.sh"))

    # Collect active and masked cluster tarballs produced in datadir
    active_files = sorted(os.path.abspath(f) for f in glob.glob(os.path.join(datadir, "clusters-apa-*-ms-active.tar.gz")))
    masked_files = sorted(os.path.abspath(f) for f in glob.glob(os.path.join(datadir, "clusters-apa-*-ms-masked.tar.gz")))

    if not active_files and not masked_files:
        print("WARNING: no cluster tar.gz files found in %s — skipping bee upload" % datadir,
              file=sys.stderr)
        return

    print("\n" + "=" * 60)
    print("bee conversion")
    print("=" * 60)
    print(f"  active files ({len(active_files)}): {[os.path.basename(f) for f in active_files]}")
    print(f"  masked files ({len(masked_files)}): {[os.path.basename(f) for f in masked_files]}")

    bee_cmd = [sys.executable, bee_script]
    if active_files:
        bee_cmd += ["--active"] + active_files
    if masked_files:
        bee_cmd += ["--masked"] + masked_files

    print("Command:")
    print("  " + " \\\n    ".join(bee_cmd))
    bee_result = subprocess.run(bee_cmd, env=env, cwd=os.path.abspath(datadir))
    if bee_result.returncode != 0:
        print("ERROR: bee conversion failed", file=sys.stderr)
        sys.exit(bee_result.returncode)

    print("\n" + "=" * 60)
    print("bee upload")
    print("=" * 60)
    upload_result = subprocess.run(
        ["sh", upload_script, "upload.zip"],
        env=env,
        cwd=os.path.abspath(datadir),
        capture_output=True,
        text=True,
    )
    # upload-to-bee.sh prints the URL on stdout via echo
    url = upload_result.stdout.strip()
    if upload_result.returncode != 0:
        print("ERROR: upload failed\n" + upload_result.stderr, file=sys.stderr)
        sys.exit(upload_result.returncode)
    print(f"\nBee URL: {url}")
