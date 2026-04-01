"""Top-level CLI entry point with subcommands.

Commands are organised into three groups:

  Workflow commands  — core data-selection and waveform-comparison pipeline
  WCT commands       — invoke wire-cell (require a WCT installation)
  Helper tools       — standalone utilities (frame inspection, format conversion)
"""

from __future__ import annotations

import argparse
import sys

from woodpecker.cli import (
    # workflow
    cmd_select,
    cmd_mask,
    cmd_extract,
    cmd_select_parallelogram,
    cmd_compare_waveforms,
    # wct
    cmd_run_img,
    cmd_run_clustering,
    cmd_run_sim_check,
    # helpers
    cmd_plot_frames,
    cmd_frames_to_root,
)


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(
        prog="woodpecker",
        description=(
            "WireCell targeted region selection and debugging tool.\n\n"
            "Commands are grouped into:\n"
            "  Workflow : select, mask, extract-tracks, select-refine, compare-waveforms\n"
            "  WCT      : run-img, run-clustering, run-sim-check  (require wire-cell)\n"
            "  Helpers  : plot-frames, frames-to-root"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version", version="%(prog)s 0.1.0")

    subparsers = parser.add_subparsers(dest="command", metavar="<command>")
    subparsers.required = True

    # ── Workflow commands ──────────────────────────────────────────────────────
    cmd_select.add_parser(subparsers)
    cmd_mask.add_parser(subparsers)
    cmd_extract.add_parser(subparsers)
    cmd_select_parallelogram.add_parser(subparsers)
    cmd_compare_waveforms.add_parser(subparsers)

    # ── WCT commands (require wire-cell) ──────────────────────────────────────
    cmd_run_img.add_parser(subparsers)
    cmd_run_clustering.add_parser(subparsers)
    cmd_run_sim_check.add_parser(subparsers)

    # ── Helper tools ──────────────────────────────────────────────────────────
    cmd_plot_frames.add_parser(subparsers)
    cmd_frames_to_root.add_parser(subparsers)

    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
