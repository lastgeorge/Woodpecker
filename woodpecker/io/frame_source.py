"""Gauss/wiener frame source — loads .npy arrays from a tar.bz2 archive.

Self-registers as "frames" in SourceRegistry.
"""

from __future__ import annotations

import io
import os
import re
import sys
import tarfile
from typing import List, Tuple

import numpy as np

from woodpecker.core.exceptions import LoadError
from woodpecker.core.registry import SourceRegistry
from woodpecker.io.base import DataSource
from woodpecker.io.frame_data import FrameData, PlaneData

PLANE_LABELS = ["U", "V", "W"]


def _load_archive_raw(path: str) -> dict:
    """Return dict of basename-without-.npy -> ndarray for every .npy in archive."""
    data = {}
    with tarfile.open(path, "r:bz2") as tf:
        for member in tf.getmembers():
            if member.name.endswith(".npy"):
                raw = tf.extractfile(member).read()
                data[member.name[:-4]] = np.load(io.BytesIO(raw))
    return data


def _split_planes(frame: np.ndarray, channels: np.ndarray,
                  boundaries: list | None = None) -> List[Tuple[np.ndarray, np.ndarray]]:
    """Split (nch, ntick) frame into [(frame_U,ch_U), (frame_V,ch_V), (frame_W,ch_W)].

    boundaries: channel-count offsets where new planes begin (e.g. [800, 1600] for HD).
                None → auto-detect from gaps in channel numbering (VD).
    """
    if boundaries:
        starts = [0] + boundaries
        ends = boundaries + [len(channels)]
    else:
        diffs = np.diff(channels)
        gap_idx = np.where(diffs > 1)[0]
        starts = [0] + list(gap_idx + 1)
        ends = list(gap_idx + 1) + [len(channels)]
    return [(frame[s:e], channels[s:e]) for s, e in zip(starts, ends)]


@SourceRegistry.register("frames")
class GaussFrameSource(DataSource):
    """Load a WireCell gauss (or wiener) frame archive."""

    def load(self, path: str, filter_tag: str = "gauss",
             detector: str = "vd", **kwargs) -> FrameData:
        """
        Parameters
        ----------
        path : str
            Path to protodune-sp-frames-anodeN.tar.bz2
        filter_tag : str
            "gauss" or "wiener"
        detector : str
            "vd" (default): split planes on channel number gaps.
            "hd": split at fixed offsets 800/1600 (800 U + 800 V + 960 W per APA).
        """
        print(f"Loading {path} ...")
        raw_data = _load_archive_raw(path)

        m = re.search(r"anode(\d+)", os.path.basename(path))
        anode_id = int(m.group(1)) if m else 0

        frame_key = next(
            (k for k in raw_data if k.startswith(f"frame_{filter_tag}{anode_id}_")), None
        )
        ch_key = next(
            (k for k in raw_data if k.startswith(f"channels_{filter_tag}{anode_id}_")), None
        )
        ti_key = next(
            (k for k in raw_data if k.startswith(f"tickinfo_{filter_tag}{anode_id}_")), None
        )

        if frame_key is None:
            raise LoadError(
                f"No {filter_tag} frame found in {path}. "
                f"Available keys: {list(raw_data)[:10]}"
            )

        frame = raw_data[frame_key]
        channels = raw_data[ch_key]
        tickinfo = raw_data[ti_key]

        hd_boundaries = [800, 1600] if detector == "hd" else None
        plane_tuples = _split_planes(frame, channels, hd_boundaries)
        planes = [
            PlaneData(name=label, frame=pf, channels=pc)
            for label, (pf, pc) in zip(PLANE_LABELS, plane_tuples)
        ]

        return FrameData(
            anode_id=anode_id,
            filter_tag=filter_tag,
            frame=frame,
            channels=channels,
            tickinfo=tickinfo,
            planes=planes,
            raw_data=raw_data,
            source_path=path,
        )
