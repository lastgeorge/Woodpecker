"""CLI subcommand: woodpecker frames-to-root

Convert a WireCell FrameFileSink tar.bz2 archive to a ROOT file.
Each frame tag (raw, gauss, wiener, …) is written as a TH2D named after
the tag.  The histogram axes are channel number (x) and tick (y).

Usage
-----
  woodpecker frames-to-root data/protodune-sp-frames-anode0.tar.bz2
  woodpecker frames-to-root data.tar.bz2 --out frames.root
  woodpecker frames-to-root data.tar.bz2 --tag gauss --out gauss.root
  woodpecker frames-to-root data.tar.bz2 --tag raw --tag gauss
"""

from __future__ import annotations

import argparse
import io
import os
import re
import sys
import tarfile

import numpy as np


def add_parser(subparsers) -> None:
    p = subparsers.add_parser(
        "frames-to-root",
        help="Convert a FrameFileSink tar.bz2 to a ROOT file with TH2D per tag",
    )
    p.add_argument("frame_file", help="Path to *-anode<N>.tar.bz2")
    p.add_argument(
        "--tag", action="append", dest="tags", default=None, metavar="TAG",
        help="Frame tag(s) to convert (default: all tags found). "
             "May be repeated: --tag raw --tag gauss",
    )
    p.add_argument(
        "--out", default=None,
        help="Output ROOT file path (default: <frame_file>.root)",
    )
    p.add_argument(
        "--detector", default="vd", choices=["vd", "hd"],
        help=(
            "Detector type controlling U/V/W plane splitting. "
            "'vd' (default): split on channel number gaps (ProtoDUNE-VD). "
            "'hd': split at fixed offsets 800/1600 (ProtoDUNE-HD)."
        ),
    )
    p.set_defaults(func=run)


# ── helpers ───────────────────────────────────────────────────────────────────

def _load_archive(path: str) -> dict:
    data = {}
    with tarfile.open(path, "r:bz2") as tf:
        for member in tf.getmembers():
            if member.name.endswith(".npy"):
                raw = tf.extractfile(member).read()
                data[member.name[:-4]] = np.load(io.BytesIO(raw))
    return data


def _find_all_tags(raw_data: dict) -> list:
    tag_re = re.compile(r"^frame_(.+)_\d+$")
    seen = []
    for k in raw_data:
        m = tag_re.match(k)
        if m:
            t = m.group(1)
            if t not in seen:
                seen.append(t)
    return seen


def _split_planes(frame: np.ndarray, channels: np.ndarray,
                  boundaries: list | None = None):
    """Split (nch, ntick) into [(frame_slice, ch_slice), ...].

    boundaries: channel-count offsets where new planes begin (e.g. [800, 1600] for HD).
                None → auto-detect from gaps (VD).
    """
    if boundaries:
        starts = [0] + boundaries
        ends = boundaries + [len(channels)]
    else:
        diffs = np.diff(channels)
        gap_idx = list(np.where(diffs > 1)[0])
        starts = [0] + [i + 1 for i in gap_idx]
        ends = [i + 1 for i in gap_idx] + [len(channels)]
    return [(frame[s:e], channels[s:e]) for s, e in zip(starts, ends)]


# ── main ──────────────────────────────────────────────────────────────────────

def run(args: argparse.Namespace) -> None:
    path = args.frame_file
    if not os.path.exists(path):
        print(f"ERROR: file not found: {path}", file=sys.stderr)
        sys.exit(1)

    try:
        import ROOT
        ROOT.gROOT.SetBatch(True)
    except ImportError:
        print("ERROR: PyROOT is required. Make sure ROOT is installed and "
              "accessible in your Python environment.", file=sys.stderr)
        sys.exit(1)

    print(f"Loading {path} ...")
    raw_data = _load_archive(path)
    print(f"  Keys found: {sorted(raw_data)}")

    all_tags = _find_all_tags(raw_data)
    if not all_tags:
        print("ERROR: no frame_* keys found in archive.", file=sys.stderr)
        sys.exit(1)

    requested_tags = args.tags or all_tags
    # Validate
    for t in requested_tags:
        if t not in all_tags:
            print(f"ERROR: tag '{t}' not found. Available: {all_tags}",
                  file=sys.stderr)
            sys.exit(1)

    # Default output path: strip .tar.bz2 / .bz2 etc., add .root
    if args.out:
        out_path = args.out
    else:
        base = os.path.basename(path)
        for ext in (".tar.bz2", ".tar.gz", ".bz2", ".gz"):
            if base.endswith(ext):
                base = base[: -len(ext)]
                break
        else:
            base = os.path.splitext(base)[0]
        out_path = os.path.join(os.path.dirname(path) or ".", base + ".root")

    tfile = ROOT.TFile(out_path, "RECREATE")
    if tfile.IsZombie():
        print(f"ERROR: could not open {out_path} for writing.", file=sys.stderr)
        sys.exit(1)

    plane_labels = ["U", "V", "W"]

    for tag in requested_tags:
        frame_key = next((k for k in raw_data if re.match(rf"^frame_{re.escape(tag)}_\d+$", k)), None)
        ch_key    = next((k for k in raw_data if re.match(rf"^channels_{re.escape(tag)}_\d+$", k)), None)
        ti_key    = next((k for k in raw_data if re.match(rf"^tickinfo_{re.escape(tag)}_\d+$", k)), None)

        if frame_key is None:
            print(f"  WARNING: frame_{tag}_* not found — skipping")
            continue

        frame    = raw_data[frame_key].astype(np.float64)
        channels = raw_data[ch_key]
        tickinfo = raw_data[ti_key] if ti_key is not None else np.array([0, frame.shape[1], 0.5])

        start_tick = int(tickinfo[0])
        nticks     = frame.shape[1]
        end_tick   = start_tick + nticks

        print(f"  Tag '{tag}': {len(channels)} total channels, "
              f"ticks {start_tick}–{end_tick} ({nticks} ticks)")

        hd_boundaries = [800, 1600] if args.detector == "hd" else None
        planes = _split_planes(frame, channels, hd_boundaries)
        # pad to 3 if fewer splits found
        while len(planes) < 3:
            planes.append((np.zeros((1, nticks)), np.array([0])))

        for plane_frame, plane_ch, plane_lbl in zip(
                [p[0] for p in planes[:3]],
                [p[1] for p in planes[:3]],
                plane_labels):

            nch    = len(plane_ch)
            ch_min = int(plane_ch[0])
            ch_max = int(plane_ch[-1])
            hist_name = f"{tag}_{plane_lbl}"

            print(f"    Plane {plane_lbl}: ch {ch_min}–{ch_max} ({nch} ch)")

            h = ROOT.TH2D(
                hist_name,
                f"Frame {tag} plane {plane_lbl};Channel;Tick",
                nch,    ch_min - 0.5, ch_max + 0.5,
                nticks, start_tick,   end_tick,
            )
            h.SetDirectory(tfile)

            ch_to_col = {int(c): i for i, c in enumerate(plane_ch)}
            for ch_val, col in ch_to_col.items():
                xbin = h.GetXaxis().FindBin(ch_val)
                for tick_i in range(nticks):
                    h.SetBinContent(xbin, tick_i + 1, float(plane_frame[col, tick_i]))

            h.Write()
            print(f"      → TH2D '{hist_name}' written ({nch} × {nticks} bins)")

    tfile.Close()
    print(f"\nSaved to {out_path}")
