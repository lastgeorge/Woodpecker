"""CLI subcommand: woodpecker compare-waveforms

Compare signal shapes between real data and simulation by aligning
per-channel waveforms and computing averaged profiles for each wire plane.

Algorithm
---------
For each wire plane (U, V, W):
  1. Extract a window of *nticks* samples starting at a per-channel tick offset
     that varies linearly from tick_start (first channel) to tick_end (last
     channel) — following the track slant across the APA.
  2. Shift each channel's waveform so its peak aligns to the centre bin,
     then accumulate and average ("aligned mean waveform").
  3. FFT each extracted window and accumulate the power spectrum
     ("averaged power density").
  4. Scale the simulation waveforms so that the W-plane peak matches data.
  5. Plot data vs simulation for each plane and save to PNG / PDF.

Usage
-----
  woodpecker compare-waveforms \\
      --data  woodpecker_data/protodune-sp-frames-raw-anode0.tar.bz2 \\
      --sim   woodpecker_data/protodune-sp-frames-sim-anode0.tar.bz2 \\
      --selection woodpecker_data/selection-anode0.json

  # Override tick windows per plane (raw tick indices):
  woodpecker compare-waveforms --data ... --sim ... --selection ... \\
      --nticks 200

  # Save result to a specific file:
  woodpecker compare-waveforms ... --out comparison.png
"""

from __future__ import annotations

import argparse
import io
import json
import os
import re
import sys
import tarfile
from typing import Dict, List, Optional, Tuple

import numpy as np


# ---------------------------------------------------------------------------
# Archive loading
# ---------------------------------------------------------------------------

def _load_archive(path: str) -> dict:
    """Return dict of name-without-.npy → ndarray for every .npy in archive."""
    data: dict = {}
    with tarfile.open(path, "r:bz2") as tf:
        for member in tf.getmembers():
            if member.name.endswith(".npy"):
                raw = tf.extractfile(member).read()
                data[member.name[:-4]] = np.load(io.BytesIO(raw))
    return data


def _find_tag(raw_data: dict, requested_tag: Optional[str], anode_id: int) -> str:
    """Return the best matching frame tag available in the archive."""
    tag_re = re.compile(r"^frame_(.+)_\d+$")
    available = []
    for k in raw_data:
        m = tag_re.match(k)
        if m:
            available.append(m.group(1))
    if not available:
        raise ValueError(f"No frame_* keys found. Available: {list(raw_data)[:10]}")
    if requested_tag:
        if requested_tag not in available:
            raise ValueError(f"Tag '{requested_tag}' not found. Available: {available}")
        return requested_tag
    for preferred in [f"raw{anode_id}", "raw", f"gauss{anode_id}", "gauss",
                      f"wiener{anode_id}", "wiener"]:
        if preferred in available:
            return preferred
    return available[0]


def _split_planes(frame: np.ndarray, channels: np.ndarray) -> List[Tuple[np.ndarray, np.ndarray]]:
    """Split (nch, ntick) into list of (plane_frame, plane_channels) by channel gaps."""
    diffs = np.diff(channels)
    gap_idx = list(np.where(diffs > 1)[0])
    starts = [0] + [i + 1 for i in gap_idx]
    ends = [i + 1 for i in gap_idx] + [len(channels)]
    return [(frame[s:e], channels[s:e]) for s, e in zip(starts, ends)]


def _load_frames(path: str, tag: Optional[str] = None) -> Tuple[np.ndarray, np.ndarray, np.ndarray, str]:
    """Load frame, channels, tickinfo from a tar.bz2 archive.

    Returns (frame, channels, tickinfo, used_tag).
    frame shape: (nch, ntick), dtype float32.
    """
    m = re.search(r"anode(\d+)", os.path.basename(path))
    anode_id = int(m.group(1)) if m else 0
    raw_data = _load_archive(path)
    used_tag = _find_tag(raw_data, tag, anode_id)
    frame_key = next((k for k in raw_data if k.startswith(f"frame_{used_tag}_")), None)
    ch_key    = next((k for k in raw_data if k.startswith(f"channels_{used_tag}_")), None)
    ti_key    = next((k for k in raw_data if k.startswith(f"tickinfo_{used_tag}_")), None)
    if frame_key is None:
        raise ValueError(f"frame_{used_tag}_* not found in {path}")
    frame    = raw_data[frame_key].astype(np.float32)
    channels = raw_data[ch_key]
    tickinfo = raw_data[ti_key] if ti_key is not None else np.array([0, frame.shape[1], 0.5])
    return frame, channels, tickinfo, used_tag


# ---------------------------------------------------------------------------
# Core algorithms  (translated from check_waveform_withdata_2.cc)
# ---------------------------------------------------------------------------

def _shift_accumulate(
    wf: np.ndarray,
    out: np.ndarray,
    half_window: int,
) -> bool:
    """Shift *wf* so its abs-peak lands at *half_window* and add to *out*.

    Returns True if any samples were added.
    """
    actual_len = len(wf)
    out_len = len(out)
    peak_bin = int(np.argmax(np.abs(wf)))
    shift = half_window - peak_bin
    src_start = max(0, -shift)
    src_end   = min(actual_len, out_len - shift)
    dst_start = max(0, shift)
    dst_end   = dst_start + (src_end - src_start)
    if dst_end > out_len:
        over = dst_end - out_len
        src_end -= over
        dst_end -= over
    if src_end <= src_start or dst_end <= dst_start:
        return False
    out[dst_start:dst_end] += wf[src_start:src_end]
    return True


def _aligned_mean_waveform(
    frame: np.ndarray,
    channels: np.ndarray,
    ch_sel: np.ndarray,
    start_tick: int,
    tick_start: int,
    tick_end: int,
    nticks: int,
    half_window: int = 200,
) -> np.ndarray:
    """Compute the peak-aligned, channel-averaged waveform for data.

    For each channel the extraction window starts at a tick offset that
    interpolates linearly from *tick_start* (at the first selected channel)
    to *tick_end* (at the last selected channel), matching the track slant.
    Each window is then peak-aligned to *half_window* and accumulated.
    """
    nch = len(ch_sel)
    out = np.zeros(2 * half_window, dtype=np.float64)
    k = (tick_end - tick_start) / max(nch - 1, 1)
    ch_to_row = {int(c): i for i, c in enumerate(channels)}
    count = 0

    for idx, ch in enumerate(ch_sel):
        row = ch_to_row.get(int(ch))
        if row is None:
            continue
        offset = int(round(k * idx + tick_start)) - start_tick
        i0 = max(0, offset)
        i1 = min(frame.shape[1], offset + nticks)
        if i1 <= i0:
            continue
        wf = frame[row, i0:i1].copy()
        if _shift_accumulate(wf, out, half_window):
            count += 1

    if count > 0:
        out /= count
    return out


def _aligned_mean_waveform_full(
    frame: np.ndarray,
    channels: np.ndarray,
    ch_sel: np.ndarray,
    nticks: int,
    half_window: int = 200,
) -> np.ndarray:
    """Peak-aligned, channel-averaged waveform using the full tick range.

    Used for simulation frames whose absolute start_tick is not aligned with
    the data tick axis.  For each selected channel the entire row is searched
    for its peak; a window of *nticks* centred on that peak is extracted and
    peak-aligned into the output array.
    """
    out = np.zeros(2 * half_window, dtype=np.float64)
    ch_to_row = {int(c): i for i, c in enumerate(channels)}
    count = 0

    for ch in ch_sel:
        row = ch_to_row.get(int(ch))
        if row is None:
            continue
        full_wf = frame[row]
        peak_bin = int(np.argmax(np.abs(full_wf)))
        # Extract nticks centred on the peak
        half_n = nticks // 2
        i0 = max(0, peak_bin - half_n)
        i1 = min(frame.shape[1], i0 + nticks)
        i0 = max(0, i1 - nticks)  # adjust start if we hit the end
        wf = full_wf[i0:i1].copy()
        if _shift_accumulate(wf, out, half_window):
            count += 1

    if count > 0:
        out /= count
    return out


def _aligned_mean_waveform_align2(
    frame: np.ndarray,
    channels: np.ndarray,
    ch_sel: np.ndarray,
    start_tick: int,
    tick_start: int,
    tick_end: int,
    nticks: int,
    x1: float,
    y1: float,
    x2: float,
    y2: float,
    half_window: int = 200,
) -> np.ndarray:
    """Peak-aligned mean waveform using a user-defined track line (align2).

    Mirrors the ROOT align2() function from check_waveform_withdata_2.cc.

    The extraction window start per channel is the same linear interpolation
    as _aligned_mean_waveform (tick_start → tick_end over the channel range).
    The peak position within that window is corrected using the track slope:

        maxbin = kk * (channel - x1) + y1 - start_bin

    where kk = (y2 - y1) / (x2 - x1) is the track slope in tick/channel.
    The accumulator is shifted so that maxbin lands at half_window.

    Parameters
    ----------
    x1, y1 : channel and tick of the first track point (in frame coordinates)
    x2, y2 : channel and tick of the second track point
    """
    nch = len(ch_sel)
    out = np.zeros(2 * half_window, dtype=np.float64)
    k  = (tick_end - tick_start) / max(nch - 1, 1)   # window-start slope
    kk = (y2 - y1) / (x2 - x1) if x2 != x1 else 0.0  # track slope tick/ch
    ch_to_row = {int(c): i for i, c in enumerate(channels)}
    count = 0

    for idx, ch in enumerate(ch_sel):
        row = ch_to_row.get(int(ch))
        if row is None:
            continue
        start_bin = int(round(k * idx + tick_start)) - start_tick
        i0 = max(0, start_bin)
        i1 = min(frame.shape[1], start_bin + nticks)
        if i1 <= i0:
            continue
        wf = frame[row, i0:i1].copy()

        # Predicted peak position within the extracted window
        maxbin = int(round(kk * (int(ch) - x1) + y1)) - start_tick - start_bin
        # Clamp to valid window range
        maxbin = max(0, min(len(wf) - 1, maxbin))

        # Shift so that maxbin lands at half_window
        shift = half_window - maxbin
        src_start = max(0, -shift)
        src_end   = min(len(wf), half_window * 2 - shift)
        dst_start = max(0, shift)
        dst_end   = dst_start + (src_end - src_start)
        if dst_end > 2 * half_window:
            over = dst_end - 2 * half_window
            src_end -= over
            dst_end -= over
        if src_end > src_start and dst_end > dst_start:
            out[dst_start:dst_end] += wf[src_start:src_end]
            count += 1

    if count > 0:
        out /= count
    return out


def _power_density(
    frame: np.ndarray,
    channels: np.ndarray,
    ch_sel: np.ndarray,
    start_tick: int,
    tick_start: int,
    tick_end: int,
    nticks: int,
    adc_to_mv: float = 1400.0 / (4096 * 4),
) -> Tuple[np.ndarray, np.ndarray]:
    """Channel-averaged FFT power density from a windowed data region."""
    nch = len(ch_sel)
    n_freq = nticks // 2 + 1
    power = np.zeros(n_freq, dtype=np.float64)
    k = (tick_end - tick_start) / max(nch - 1, 1)
    ch_to_row = {int(c): i for i, c in enumerate(channels)}
    count = 0

    for idx, ch in enumerate(ch_sel):
        row = ch_to_row.get(int(ch))
        if row is None:
            continue
        offset = int(round(k * idx + tick_start)) - start_tick
        i0 = max(0, offset)
        i1 = min(frame.shape[1], offset + nticks)
        if i1 - i0 < nticks:
            continue
        wf = frame[row, i0:i0 + nticks].copy()
        power += np.abs(np.fft.rfft(wf, n=nticks))
        count += 1

    if count > 0:
        power /= count
    power *= adc_to_mv
    power = power ** 2
    freqs = np.fft.rfftfreq(nticks, d=0.5e-6) * 1e-6  # MHz
    return freqs, power


def _power_density_full(
    frame: np.ndarray,
    channels: np.ndarray,
    ch_sel: np.ndarray,
    nticks: int,
    adc_to_mv: float = 1400.0 / (4096 * 4),
) -> Tuple[np.ndarray, np.ndarray]:
    """Channel-averaged FFT power density using the full tick range (for sim)."""
    n_freq = nticks // 2 + 1
    power = np.zeros(n_freq, dtype=np.float64)
    ch_to_row = {int(c): i for i, c in enumerate(channels)}
    count = 0

    for ch in ch_sel:
        row = ch_to_row.get(int(ch))
        if row is None:
            continue
        full_wf = frame[row]
        peak_bin = int(np.argmax(np.abs(full_wf)))
        half_n = nticks // 2
        i0 = max(0, peak_bin - half_n)
        i1 = min(frame.shape[1], i0 + nticks)
        i0 = max(0, i1 - nticks)
        if i1 - i0 < nticks:
            continue
        wf = full_wf[i0:i1].copy()
        power += np.abs(np.fft.rfft(wf, n=nticks))
        count += 1

    if count > 0:
        power /= count
    power *= adc_to_mv
    power = power ** 2
    freqs = np.fft.rfftfreq(nticks, d=0.5e-6) * 1e-6  # MHz
    return freqs, power


# ---------------------------------------------------------------------------
# Selection loading  (supports two JSON schemas)
# ---------------------------------------------------------------------------

def _load_selection(path: str) -> dict:
    """Load a selection JSON file.

    Two formats are supported:

    1. Legacy format from 'woodpecker select':
       {"tick_range": [t0, t1], "ch_ranges": [{"plane": "U", ...}, ...]}

    2. New format from 'woodpecker select-compare':
       {"data": {"U": {"ch_min":…, "ch_max":…, "tick_start":…,
                       "tick_end":…, "nticks":…}, …},
        "sim":  {…}}

    Returns the raw dict; callers use _is_compare_selection() to distinguish.
    """
    with open(path) as f:
        return json.load(f)


def _is_compare_selection(sel: dict) -> bool:
    """Return True if *sel* uses the new select-compare format."""
    return "data" in sel and "sim" in sel


def _plane_params(
    sel: dict,
    plane_label: str,
) -> Optional[dict]:
    """Extract data-side channel/tick parameters for one plane (legacy format).

    nticks is derived from tick_range so the extraction window matches the
    selected region.  Returns None if the plane has no channel range.
    """
    ch_ranges = sel.get("ch_ranges", [])
    tick_range = sel.get("tick_range")
    if tick_range is None:
        return None

    plane_range = None
    for r in ch_ranges:
        if r and r.get("plane") == plane_label:
            plane_range = r
            break

    if plane_range is None:
        return None

    nticks = abs(tick_range[1] - tick_range[0])

    return {
        "ch_min": plane_range["ch_min"],
        "ch_max": plane_range["ch_max"],
        "tick_start": tick_range[0],
        "tick_end":   tick_range[1],
        "nticks": nticks,
    }


def _compare_plane_params(
    sel: dict,
    dataset: str,
    plane_label: str,
) -> Optional[dict]:
    """Extract parameters for one plane from a select-compare JSON.

    *dataset* is "data" or "sim".
    Returns None if the plane entry is missing or incomplete.
    """
    p = sel.get(dataset, {}).get(plane_label)
    if p is None:
        return None
    required = ("ch_min", "ch_max", "tick_start", "tick_end", "nticks")
    if any(p.get(k) is None for k in required):
        return None
    return {k: p[k] for k in required}


# ---------------------------------------------------------------------------
# High-level comparison
# ---------------------------------------------------------------------------

PLANE_LABELS = ["U", "V", "W"]


def compare_waveforms(
    data_path: str,
    sim_path: Optional[str],
    selection: dict,
    data_tag: Optional[str] = None,
    sim_tag: Optional[str] = None,
    half_window: int = 200,
    normalize_w: bool = True,
) -> dict:
    """Compute aligned mean waveforms and power spectra for data and sim.

    Returns a dict with keys "U", "V", "W", each containing:
      {
        "channels": np.ndarray,          # selected channel IDs
        "data_wf":  np.ndarray,          # aligned mean waveform (data)
        "sim_wf":   np.ndarray,          # aligned mean waveform (sim, rescaled)
        "data_pd":  (freqs, power),      # power density (data)
        "sim_pd":   (freqs, power),      # power density (sim, rescaled)
        "tick_axis": np.ndarray,         # tick axis for waveform plot
        "ratio": float,                  # sim/data scale factor (from W plane)
      }
    """
    print(f"Loading data:  {data_path}")
    data_frame, data_ch, data_ti, data_used_tag = _load_frames(data_path, data_tag)
    print(f"  tag={data_used_tag}, shape={data_frame.shape}, "
          f"start_tick={int(data_ti[0])}")

    has_sim = sim_path is not None
    if has_sim:
        print(f"Loading sim:   {sim_path}")
        sim_frame, sim_ch, sim_ti, sim_used_tag = _load_frames(sim_path, sim_tag)
        print(f"  tag={sim_used_tag}, shape={sim_frame.shape}, "
              f"start_tick={int(sim_ti[0])}")
        sim_start = int(sim_ti[0])
    else:
        sim_frame = sim_ch = sim_ti = sim_used_tag = sim_start = None
        print("  (no sim — data-only mode)")

    data_start = int(data_ti[0])

    is_compare = _is_compare_selection(selection)
    if is_compare:
        print("  (using select-compare format: separate tick ranges for data and sim)")

    results: dict = {}

    for label in PLANE_LABELS:
        if is_compare:
            data_p = _compare_plane_params(selection, "data", label)
            sim_p  = _compare_plane_params(selection, "sim",  label) if has_sim else None
            if data_p is None:
                print(f"  Plane {label}: incomplete data entry in selection — skipping")
                continue
            if has_sim and sim_p is None:
                print(f"  Plane {label}: incomplete sim entry in selection — running data-only")
        else:
            data_p = _plane_params(selection, label)
            if data_p is None:
                print(f"  Plane {label}: no channel range in selection — skipping")
                continue
            sim_p = data_p if has_sim else None  # legacy: same params used for sim

        # ch_min may be > ch_max when slope is reversed (channels swapped in JSON).
        # Build channel array from low to high; tick_start/tick_end already encode
        # slope direction (tick_start is tick at ch_min, tick_end at ch_max).
        d_ch_lo = min(data_p["ch_min"], data_p["ch_max"])
        d_ch_hi = max(data_p["ch_min"], data_p["ch_max"])
        ch_sel_data = np.arange(d_ch_lo, d_ch_hi + 1)
        # If reversed, flip tick_start/tick_end so interpolation runs correctly
        if data_p["ch_min"] > data_p["ch_max"]:
            data_p = dict(data_p,
                          tick_start=data_p["tick_end"],
                          tick_end=data_p["tick_start"])

        if sim_p is not None:
            s_ch_lo = min(sim_p["ch_min"], sim_p["ch_max"])
            s_ch_hi = max(sim_p["ch_min"], sim_p["ch_max"])
            ch_sel_sim = np.arange(s_ch_lo, s_ch_hi + 1)
            if sim_p["ch_min"] > sim_p["ch_max"]:
                sim_p = dict(sim_p,
                             tick_start=sim_p["tick_end"],
                             tick_end=sim_p["tick_start"])
            n_sim = sim_p["nticks"]
        else:
            ch_sel_sim = None
            n_sim = None

        n_data = data_p["nticks"]

        if sim_p is not None:
            print(f"  Plane {label}: "
                  f"data ch {data_p['ch_min']}–{data_p['ch_max']} "
                  f"ticks {data_p['tick_start']}–{data_p['tick_end']} n={n_data} | "
                  f"sim ch {sim_p['ch_min']}–{sim_p['ch_max']} "
                  f"ticks {sim_p['tick_start']}–{sim_p['tick_end']} n={n_sim}")
        else:
            print(f"  Plane {label}: "
                  f"data ch {data_p['ch_min']}–{data_p['ch_max']} "
                  f"ticks {data_p['tick_start']}–{data_p['tick_end']} n={n_data} | "
                  f"sim: none")

        # Aligned mean waveforms
        # Use align2 if track_points are provided for this plane
        data_tp = data_p.get("track_points")
        sim_tp  = sim_p.get("track_points") if sim_p is not None else None

        if data_tp and "p1" in data_tp and "p2" in data_tp:
            x1, y1 = data_tp["p1"]
            x2, y2 = data_tp["p2"]
            print(f"    data plane {label}: using align2 track "
                  f"p1=({x1},{y1}) p2=({x2},{y2})")
            data_wf = _aligned_mean_waveform_align2(
                data_frame, data_ch, ch_sel_data, data_start,
                data_p["tick_start"], data_p["tick_end"], n_data,
                x1, y1, x2, y2, half_window,
            )
        else:
            data_wf = _aligned_mean_waveform(
                data_frame, data_ch, ch_sel_data, data_start,
                data_p["tick_start"], data_p["tick_end"], n_data, half_window,
            )

        if sim_p is not None:
            if is_compare:
                if sim_tp and "p1" in sim_tp and "p2" in sim_tp:
                    x1, y1 = sim_tp["p1"]
                    x2, y2 = sim_tp["p2"]
                    print(f"    sim plane {label}: using align2 track "
                          f"p1=({x1},{y1}) p2=({x2},{y2})")
                    sim_wf = _aligned_mean_waveform_align2(
                        sim_frame, sim_ch, ch_sel_sim, sim_start,
                        sim_p["tick_start"], sim_p["tick_end"], n_sim,
                        x1, y1, x2, y2, half_window,
                    )
                else:
                    sim_wf = _aligned_mean_waveform(
                        sim_frame, sim_ch, ch_sel_sim, sim_start,
                        sim_p["tick_start"], sim_p["tick_end"], n_sim, half_window,
                    )
            else:
                # Legacy: sim tick axis not aligned to data — search full frame
                sim_wf = _aligned_mean_waveform_full(
                    sim_frame, sim_ch, ch_sel_sim, n_sim, half_window,
                )
        else:
            sim_wf = None

        # Power density
        data_freqs, data_pd = _power_density(
            data_frame, data_ch, ch_sel_data, data_start,
            data_p["tick_start"], data_p["tick_end"], n_data,
        )
        if sim_p is not None:
            if is_compare:
                sim_freqs, sim_pd = _power_density(
                    sim_frame, sim_ch, ch_sel_sim, sim_start,
                    sim_p["tick_start"], sim_p["tick_end"], n_sim,
                )
            else:
                sim_freqs, sim_pd = _power_density_full(
                    sim_frame, sim_ch, ch_sel_sim, n_sim,
                )
        else:
            sim_freqs, sim_pd = None, None

        tick_axis = np.arange(2 * half_window) - half_window

        results[label] = {
            "channels": ch_sel_data,
            "data_wf": data_wf,
            "sim_wf": sim_wf,
            "data_pd": (data_freqs, data_pd),
            "sim_pd": (sim_freqs, sim_pd),
            "tick_axis": tick_axis,
        }

    # Normalise simulation to data using the W-plane peak (optional)
    ratio = 1.0
    if normalize_w and "W" in results and results["W"]["sim_wf"] is not None:
        w_data_peak = float(np.max(np.abs(results["W"]["data_wf"])))
        w_sim_peak  = float(np.max(np.abs(results["W"]["sim_wf"])))
        if w_data_peak > 0 and w_sim_peak > 0:
            ratio = w_sim_peak / w_data_peak
            print(f"  W-plane peak ratio (sim/data): {ratio:.4f}")
    else:
        if not normalize_w:
            print("  W-plane normalization disabled (--no-w-scale)")

    for label in results:
        if ratio != 0 and results[label]["sim_wf"] is not None:
            results[label]["sim_wf"] = results[label]["sim_wf"] / ratio
            results[label]["sim_pd"] = (
                results[label]["sim_pd"][0],
                results[label]["sim_pd"][1] / ratio,
            )
        results[label]["ratio"] = ratio

    return results


# ---------------------------------------------------------------------------
# Plotting
# ---------------------------------------------------------------------------

def _plot_results(
    results: dict,
    out_path: str,
    data_label: str = "data",
    sim_label: str = "sim",
    show_power: bool = False,
    dpi: int = 150,
) -> None:
    """Save comparison plots to *out_path*."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        print("ERROR: matplotlib is required. Install with: pip install matplotlib",
              file=sys.stderr)
        sys.exit(1)

    planes = [lbl for lbl in PLANE_LABELS if lbl in results]
    n_rows = len(planes) * (2 if show_power else 1)
    if n_rows == 0:
        print("No planes to plot.", file=sys.stderr)
        return

    fig, axes = plt.subplots(n_rows, 1, figsize=(10, 4 * n_rows))
    if n_rows == 1:
        axes = [axes]

    ax_idx = 0
    for label in planes:
        r = results[label]
        tick_axis = r["tick_axis"]
        data_wf   = r["data_wf"]
        sim_wf    = r["sim_wf"]
        data_freqs, data_pd = r["data_pd"]
        sim_freqs, sim_pd   = r["sim_pd"]

        ax = axes[ax_idx]
        ax_idx += 1
        ax.plot(tick_axis, data_wf, color="black", label=data_label, linewidth=1.2)
        if sim_wf is not None:
            ax.plot(tick_axis, sim_wf, color="red", label=sim_label, linewidth=1.2)
        ax.set_title(f"Plane {label} — aligned mean waveform")
        ax.set_xlabel("Tick (peak-aligned)")
        ax.set_ylabel("ADC")
        ax.legend()
        ax.grid(True, alpha=0.3)

        if show_power:
            ax2 = axes[ax_idx]
            ax_idx += 1
            mask = data_freqs <= 1.0  # show up to 1 MHz
            ax2.plot(data_freqs[mask], data_pd[mask], color="black", label=data_label, linewidth=1.2)
            if sim_freqs is not None and sim_pd is not None:
                ax2.plot(sim_freqs[mask], sim_pd[mask], color="red", label=sim_label, linewidth=1.2)
            ax2.set_title(f"Plane {label} — power density")
            ax2.set_xlabel("Frequency (MHz)")
            ax2.set_ylabel("|C(ω)|² (mV²)")
            ax2.legend()
            ax2.grid(True, alpha=0.3)

    plt.tight_layout()
    plt.savefig(out_path, dpi=dpi, bbox_inches="tight")
    plt.close()
    print(f"Saved to {out_path}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def add_parser(subparsers) -> None:
    p = subparsers.add_parser(
        "compare-waveforms",
        help="Compare signal shapes between data and simulation",
    )
    p.add_argument(
        "--data", required=True, metavar="DATA_TAR",
        help="Raw data tar.bz2 archive (e.g. protodune-sp-frames-raw-anode0.tar.bz2)",
    )
    p.add_argument(
        "--sim", default=None, metavar="SIM_TAR",
        help="Simulation tar.bz2 archive (optional; omit to plot data only)",
    )
    p.add_argument(
        "--selection", required=True, metavar="SELECTION_JSON",
        help="Selection JSON from 'woodpecker select' (e.g. selection-anode0.json)",
    )
    p.add_argument(
        "--half-window", type=int, default=200,
        help="Half-width of the peak-aligned output array (default: 200)",
    )
    p.add_argument(
        "--data-tag", default=None,
        help="Frame tag to load from data archive (default: auto-detect raw > gauss > wiener)",
    )
    p.add_argument(
        "--sim-tag", default=None,
        help="Frame tag to load from sim archive (default: auto-detect raw > gauss > wiener)",
    )
    p.add_argument(
        "--no-w-scale", action="store_true",
        help="Disable W-plane peak normalization of simulation (default: enabled)",
    )
    p.add_argument(
        "--show-power", action="store_true",
        help="Also plot FFT power density spectra",
    )
    p.add_argument(
        "--out", default=None,
        help="Output image path (default: compare-waveforms-anode<N>.png next to data file)",
    )
    p.add_argument(
        "--dpi", type=int, default=150,
        help="Output image DPI (default: 150)",
    )
    p.set_defaults(func=run)


def run(args: argparse.Namespace) -> None:
    check_paths = [args.data, args.selection]
    if args.sim:
        check_paths.append(args.sim)
    for path in check_paths:
        if not os.path.exists(path):
            print(f"ERROR: file not found: {path}", file=sys.stderr)
            sys.exit(1)

    selection = _load_selection(args.selection)

    results = compare_waveforms(
        data_path=args.data,
        sim_path=args.sim,
        selection=selection,
        data_tag=args.data_tag,
        sim_tag=args.sim_tag,
        half_window=args.half_window,
        normalize_w=not args.no_w_scale,
    )

    if not results:
        print("ERROR: no planes processed — check selection JSON", file=sys.stderr)
        sys.exit(1)

    # Default output path
    if args.out:
        out_path = args.out
    else:
        m = re.search(r"anode(\d+)", os.path.basename(args.data))
        suffix = f"-anode{m.group(1)}" if m else ""
        out_dir = os.path.dirname(args.data) or "."
        out_path = os.path.join(out_dir, f"compare-waveforms{suffix}.png")

    _plot_results(
        results,
        out_path=out_path,
        show_power=args.show_power,
        dpi=args.dpi,
    )
