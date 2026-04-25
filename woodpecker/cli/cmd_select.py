"""CLI subcommand: woodpecker select <archive>

Opens the interactive GUI, collects a Selection, then runs the mask_frames step.
Optionally writes the Selection to a JSON sidecar.
"""

from __future__ import annotations

import datetime
import os
import random
import sys

# Ensure sources and processing steps are registered before use
import woodpecker.io.frame_source      # noqa: F401
import woodpecker.processing.masker    # noqa: F401

from woodpecker.core.registry import SourceRegistry
from woodpecker.gui import app as gui_app
from woodpecker.pipeline.context import PipelineContext
from woodpecker.pipeline.runner import PipelineRunner


def add_parser(subparsers) -> None:
    p = subparsers.add_parser(
        "select",
        help="Interactive GUI to select tick/channel range and save masked archive",
    )
    p.add_argument("archive", help="protodune-sp-frames-anodeN.tar.bz2")
    p.add_argument("--out", default=None,
                   help="Output tar.bz2 path (default: ./woodpecker_data/<prefix>-anodeN.tar.bz2)")
    p.add_argument("--prefix", default="protodune-sp-frames-part",
                   help="Output filename prefix (default: protodune-sp-frames-part)")
    p.add_argument("--outdir", default="woodpecker_data",
                   help="Output directory (default: ./woodpecker_data/)")
    p.add_argument("--vmax", type=float, default=None)
    p.add_argument("--vmin", type=float, default=0)
    p.add_argument("--cmap", default="Blues")
    p.add_argument("--save-selection", default=None, metavar="JSON",
                   help="Write selection to JSON (default: ./woodpecker_data/selection-anodeN.json)")
    p.add_argument(
        "--detector", default="vd", choices=["vd", "hd", "sbnd"],
        help=(
            "Detector type controlling U/V/W plane splitting. "
            "'vd' (default): split on channel number gaps (ProtoDUNE-VD). "
            "'hd': split at fixed offsets 800/1600 (ProtoDUNE-HD). "
            "'sbnd': split at fixed offsets 1984/3968 (SBND)."
        ),
    )
    p.set_defaults(func=run)


def run(args) -> None:
    # 1. Load data
    source_cls = SourceRegistry.get("frames")
    frame_data = source_cls().load(args.archive, detector=args.detector)

    # 2. Resolve output directory name (do NOT create it yet — defer until save).
    if args.outdir == "woodpecker_data":
        date_str = datetime.datetime.now().strftime("%Y%m%d")
        rand_str = f"{random.randint(0, 99):02d}"
        outdir = f"woodpecker_data_{date_str}_{rand_str}"
    else:
        outdir = args.outdir

    ctx = PipelineContext(frame_data=frame_data, config={})

    # 3. Run GUI; on_save_callback creates the directory and triggers the pipeline
    def on_save(selection, _):
        # Create directory only now that the user confirmed a save
        os.makedirs(outdir, exist_ok=True)

        out_path = args.out
        if out_path is None:
            out_path = os.path.join(outdir,
                                    f"{args.prefix}-anode{frame_data.anode_id}.tar.bz2")
        else:
            os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)

        ctx.selection = selection
        ctx.config["out_path"] = out_path
        PipelineRunner(["mask_frames"]).run(ctx)

        sel_path = args.save_selection or os.path.join(
            outdir, f"selection-anode{frame_data.anode_id}.json"
        )
        with open(sel_path, "w") as f:
            f.write(selection.to_json())
        print(f"Selection JSON saved to {sel_path}")

    gui_app.run_ui(
        frame_data,
        out_path=None,
        vmax=args.vmax,
        vmin=args.vmin,
        cmap=args.cmap,
        on_save_callback=on_save,
    )
