"""CLI subcommand: extract-tracks

Usage
-----
  woodpecker extract-tracks data/upload.zip
  woodpecker extract-tracks data/upload.zip --out tracks.json
  woodpecker extract-tracks data/upload.zip --min-points 5
"""

from __future__ import annotations

import argparse
import json
import os

# Ensure source and processing step are registered
import woodpecker.io.cluster_source          # noqa: F401
import woodpecker.processing.track_extractor # noqa: F401

from woodpecker.core.registry import SourceRegistry, StepRegistry
from woodpecker.pipeline.context import PipelineContext


def add_parser(subparsers) -> None:
    p = subparsers.add_parser(
        "extract-tracks",
        help="Extract dominant track directions from a 3D imaging cluster file",
    )
    p.add_argument("cluster_file", help="Cluster zip file (e.g. upload.zip)")
    p.add_argument(
        "--out", default=None,
        help="Save results as JSON (default: ./woodpecker_data/tracks-<input-stem>.json)",
    )
    p.add_argument("--outdir", default=None,
                   help="Output directory (default: same directory as the input file)")
    p.add_argument(
        "--min-points", type=int, default=2,
        help="Skip clusters with fewer than this many points (default: 2)",
    )
    p.set_defaults(func=run)


def run(args: argparse.Namespace) -> None:
    # Load cluster data
    source = SourceRegistry.get("clusters")()
    cluster_data = source.load(args.cluster_file)

    # Filter tiny clusters before extraction
    if args.min_points > 1:
        before = len(cluster_data.clusters)
        cluster_data.clusters = [
            c for c in cluster_data.clusters if len(c.points) >= args.min_points
        ]
        dropped = before - len(cluster_data.clusters)
        if dropped:
            print(f"  Dropped {dropped} clusters with < {args.min_points} points")

    # Run extraction via pipeline
    ctx = PipelineContext(cluster_data=cluster_data)
    step = StepRegistry.get("extract_tracks")()
    step.run(ctx)

    results = ctx.outputs["track_results"]

    # Save JSON — use explicit --out, else default to <input_dir>/tracks-<stem>.json
    stem = os.path.splitext(os.path.basename(args.cluster_file))[0]
    outdir = args.outdir or os.path.dirname(args.cluster_file) or "."
    out_path = args.out or os.path.join(outdir, f"tracks-{stem}.json")
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    _save_json(results, out_path)
    print(f"Results saved to {out_path}")


def _save_json(results, path: str) -> None:
    """Serialise track results to a JSON file."""
    out = []
    for r in results:
        theta, phi = r.direction_angles_deg()
        out.append({
            "cluster_id":   r.cluster_id,
            "n_points":     r.n_points,
            "source_file":  r.source_file,
            "total_charge": round(r.total_charge, 2),
            "centroid":     r.centroid.tolist(),
            "direction":    r.direction.tolist(),
            "length_cm":    round(float(r.length), 4),
            "start":        r.start.tolist(),
            "end":          r.end.tolist(),
            "linearity":    round(r.linearity, 6),
            "theta_deg":    round(theta, 4),
            "phi_deg":      round(phi, 4),
        })
    with open(path, "w") as f:
        json.dump(out, f, indent=2)
