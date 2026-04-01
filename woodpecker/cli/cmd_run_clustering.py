"""CLI subcommand: woodpecker run-clustering

Run the wire-cell clustering step on imaging output, then optionally
unzip the result and package it for bee display.

Mirrors the three steps in run_img.sh:

  wire-cell ... --tla-str input='.' --tla-code anode_indices='[N,...]' -c wct-clustering.jsonnet
  ./unzip.pl
  ./zip-upload.sh

Usage
-----
  woodpecker run-clustering                  # auto-detect anodes from woodpecker_data/
  woodpecker run-clustering --dry-run
  woodpecker run-clustering --no-unzip       # skip unzip.pl + zip-upload.sh
  woodpecker run-clustering --no-upload      # run unzip.pl but skip zip-upload.sh
  woodpecker run-clustering --input /other/dir
"""

from __future__ import annotations

import argparse
import glob
import os
import re
import subprocess
import sys

# Tools bundled with woodpecker (unzip.pl, zip-upload.sh)
_TOOLS_DIR = os.path.join(os.path.dirname(__file__), "..", "tools")


_FNAME_RE = re.compile(r"^(.+)-anode(\d+)\.tar\.bz2$")


def add_parser(subparsers) -> None:
    p = subparsers.add_parser(
        "run-clustering",
        help="Run wire-cell clustering on imaging output, then package for bee display",
    )
    p.add_argument(
        "--input", default=None,
        help="Directory containing clusters-apa-anodeN-ms-active/masked.tar.gz "
             "(default: same as --datadir)",
    )
    p.add_argument(
        "--datadir", default="woodpecker_data",
        help="Directory with masked tar.bz2 files from 'woodpecker select' "
             "(default: ./woodpecker_data/) — used to auto-detect anode indices",
    )
    p.add_argument(
        "--anode-indices", default=None,
        help="Override anode indices as JSON list e.g. '[1,2]' "
             "(default: auto-detect from woodpecker_data/ masked frame files)",
    )
    p.add_argument(
        "--jsonnet", default=None,
        help="Path to wct-clustering.jsonnet (default: auto-search for wcp-porting-img/pdvd)",
    )
    p.add_argument(
        "--script-dir", default=None,
        help="Directory containing wct-clustering.jsonnet, unzip.pl, zip-upload.sh",
    )
    p.add_argument(
        "--wct-base", default="/nfs/data/1/xning/wirecell-working",
        help="WCT_BASE directory (default: /nfs/data/1/xning/wirecell-working)",
    )
    p.add_argument(
        "--log-level", default="info", choices=["debug", "info", "warning", "error"],
    )
    p.add_argument(
        "--no-unzip", action="store_true",
        help="Skip unzip.pl and zip-upload.sh after clustering",
    )
    p.add_argument(
        "--no-upload", action="store_true",
        help="Run unzip.pl but skip zip-upload.sh (no bee upload)",
    )
    p.add_argument(
        "--dry-run", action="store_true",
        help="Print commands but do not execute",
    )
    p.set_defaults(func=run)


# ── helpers ───────────────────────────────────────────────────────────────────

def _detect_anode_ids_from_datadir(datadir: str):
    """Read anode IDs from woodpecker_data/ masked frame files — same source as run-img."""
    pattern = os.path.join(datadir, "*.tar.bz2")
    ids = []
    for path in sorted(glob.glob(pattern)):
        m = _FNAME_RE.match(os.path.basename(path))
        if m:
            ids.append(int(m.group(2)))
    return sorted(ids)


def _resolve_script_dir(script_dir: str | None) -> str | None:
    candidates = []
    if script_dir:
        candidates.append(script_dir)
    cwd = os.path.abspath(".")
    for _ in range(5):
        candidates.append(os.path.join(cwd, "wcp-porting-img", "pdvd"))
        parent = os.path.dirname(cwd)
        if parent == cwd:
            break
        cwd = parent
    for c in candidates:
        if os.path.isfile(os.path.join(c, "wct-clustering.jsonnet")):
            return c
    return None


def _build_env(wct_base: str | None) -> dict:
    env = os.environ.copy()
    if wct_base and os.path.isdir(wct_base):
        extra = os.pathsep.join([
            os.path.join(wct_base, "toolkit", "cfg"),
            os.path.join(wct_base, "dunereco", "dunereco",
                         "DUNEWireCell", "protodunevd"),
        ])
        current = env.get("WIRECELL_PATH", "")
        env["WIRECELL_PATH"] = extra + (os.pathsep + current if current else "")
    return env


def _run_or_print(cmd, dry_run: bool, env: dict, label: str, cwd=None) -> None:
    print(f"\n--- {label} ---")
    if cwd:
        print(f"  (cwd: {cwd})")
    print("  " + " \\\n    ".join(str(c) for c in cmd))
    if not dry_run:
        result = subprocess.run(cmd, env=env, cwd=cwd)
        if result.returncode != 0:
            print(f"ERROR: {label} exited with code {result.returncode}", file=sys.stderr)
            sys.exit(result.returncode)


# ── main ──────────────────────────────────────────────────────────────────────

def run(args: argparse.Namespace) -> None:
    script_dir = _resolve_script_dir(args.script_dir)
    if script_dir is None:
        print("ERROR: could not find wct-clustering.jsonnet.\n"
              "Use --script-dir /path/to/pdvd", file=sys.stderr)
        sys.exit(1)

    jsonnet = args.jsonnet or os.path.join(script_dir, "wct-clustering.jsonnet")

    # Determine anode indices — same source as run-img (woodpecker_data/ masked frames)
    if args.anode_indices:
        anode_list = args.anode_indices
        anode_ids  = [int(x) for x in re.findall(r"\d+", anode_list)]
    else:
        anode_ids = _detect_anode_ids_from_datadir(args.datadir)
        if not anode_ids:
            print(f"ERROR: no masked frame files found in '{args.datadir}'.\n"
                  f"Run 'woodpecker select' first, or use --anode-indices.",
                  file=sys.stderr)
            sys.exit(1)
        anode_list = "[" + ",".join(str(i) for i in anode_ids) + "]"

    env = _build_env(args.wct_base)

    print("\n" + "=" * 60)
    print("wire-cell clustering")
    print("=" * 60)
    datadir     = args.datadir
    input_dir   = args.input if args.input is not None else datadir
    output_dir  = datadir   # cluster output and bee zips all go to datadir
    unzip_script  = os.path.abspath(os.path.join(_TOOLS_DIR, "unzip.pl"))
    upload_script = os.path.abspath(os.path.join(_TOOLS_DIR, "zip-upload.sh"))

    print(f"  datadir       : {datadir}  (source of anode indices and output destination)")
    print(f"  input dir     : {input_dir}  (where imaging cluster files are read from)")
    print(f"  output_dir    : {output_dir}  (where mabc-*.zip will be written)")
    print(f"  anode_indices : {anode_list}")
    print(f"  jsonnet       : {jsonnet}")
    print(f"  unzip script  : {unzip_script}")
    print(f"  upload script : {upload_script}")
    print(f"  WIRECELL_PATH : {env.get('WIRECELL_PATH', '(not set)')}")
    print("=" * 60)

    # Step 1 — wire-cell clustering
    cmd_clus = [
        "wire-cell",
        "-l", "stdout",
        "-L", args.log_level,
        "--tla-str",  f"input={input_dir}",
        "--tla-str",  f"output_dir={output_dir}",
        "--tla-code", f"anode_indices={anode_list}",
        "-c", jsonnet,
    ]
    _run_or_print(cmd_clus, args.dry_run, env, "wire-cell clustering")

    if args.no_unzip:
        if args.dry_run:
            print("\n(dry-run: skipping unzip.pl and zip-upload.sh)")
        return

    # Step 2 — unzip.pl (runs in woodpecker_data/ so it finds the mabc-*.zip files)
    _run_or_print(["perl", unzip_script], args.dry_run, env, "unzip.pl",
                  cwd=os.path.abspath(output_dir))

    if args.no_upload:
        if args.dry_run:
            print("\n(dry-run: skipping zip-upload.sh)")
        return

    # Step 3 — zip-upload.sh (runs in woodpecker_data/ where upload.zip will be created)
    _run_or_print(["sh", upload_script], args.dry_run, env, "zip-upload.sh",
                  cwd=os.path.abspath(output_dir))
