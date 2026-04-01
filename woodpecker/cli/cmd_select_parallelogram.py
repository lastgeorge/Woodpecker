"""CLI subcommand: woodpecker select-refine

Interactive GUI to select parallelogram signal regions separately for raw data
and simulation frames.

Parallelogram model (same tick range across all 3 planes, per-plane slope)
---------------------------------------------------------------------------
Three shared horizontal tick lines define the window (same value shown on all
three plane axes simultaneously):

  Line 1 (t1) — lower tick at the FIRST channel of each plane  (solid)
  Line 2 (t2) — lower tick at the LAST  channel of each plane  (dashed)
                The track slant per plane is encoded by combining t1, t2
                with that plane's ch_min / ch_max.
  Line 3 (t3) — top edge tick; nticks = t3 - t1                (dotted)

For each plane the parallelogram corners are:
  (ch_min, t1), (ch_max, t2), (ch_max, t2+nticks), (ch_min, t1+nticks)

Channel ranges are selected independently per plane with a horizontal drag.

Step sequence (8 steps per dataset, 16 total):
  1. t1   — drag line across all 3 planes (DATA row)
  2. t2   — drag line across all 3 planes (DATA row)
  3. t3   — drag line across all 3 planes (DATA row)  → nticks auto
  4. U ch — drag left/right on DATA plane U
  5. V ch — drag left/right on DATA plane V
  6. W ch — drag left/right on DATA plane W
  7-12.   same six steps for SIM row

JSON output schema:
  {
    "data": {
      "U": {"ch_min":int,"ch_max":int,"tick_start":int,"tick_end":int,"nticks":int},
      "V": {…}, "W": {…}
    },
    "sim": { "U": {…}, "V": {…}, "W": {…} }
  }

Usage
-----
  woodpecker select-refine \\
      --data woodpecker_data/protodune-sp-frames-raw-anode0.tar.bz2 \\
      --sim  woodpecker_data/protodune-sp-frames-sim-anode0.tar.bz2 \\
      --out  woodpecker_data/compare-selection-anode0.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Dict, List, Optional, Tuple

import numpy as np

import woodpecker.io.frame_source  # noqa: F401
from woodpecker.core.registry import SourceRegistry
from woodpecker.io.frame_data import FrameData

PLANE_LABELS = ["U", "V", "W"]


def _empty_plane_params() -> dict:
    return {"ch_min": None, "ch_max": None,
            "tick_start": None, "tick_end": None, "nticks": None,
            "track_points": None}   # None or {"p1":[ch,tick],"p2":[ch,tick]}


def _selection_to_dict(data_params: dict, sim_params: dict,
                       slope_reversed: dict) -> dict:
    """Build output dict, swapping ch_min/ch_max for reversed-slope planes."""
    out = {}
    for ds, params in (("data", data_params), ("sim", sim_params)):
        out[ds] = {}
        for pl, p in params.items():
            entry = dict(p)
            if slope_reversed[ds][pl] and entry.get("ch_min") is not None:
                entry["ch_min"], entry["ch_max"] = entry["ch_max"], entry["ch_min"]
            out[ds][pl] = entry
    return out


# ---------------------------------------------------------------------------
# GUI
# ---------------------------------------------------------------------------

def _run_compare_ui(
    data_fd: FrameData,
    sim_fd: FrameData,
    out_path: str,
) -> Optional[dict]:
    """Open the interactive selection UI.  Returns the selection dict or None."""
    try:
        import matplotlib
        matplotlib.use("QtAgg")
        import matplotlib.pyplot as plt
        from matplotlib.widgets import SpanSelector, Button, TextBox
        import matplotlib.patches as mpatches
        from matplotlib.lines import Line2D
        from matplotlib.colors import TwoSlopeNorm
    except ImportError:
        print("ERROR: matplotlib with Qt backend required.", file=sys.stderr)
        sys.exit(1)

    # ── persistent selection state -------------------------------------------
    # tick lines are shared across planes; channels are per-plane
    # ds_ticks[ds] = {"t1": float|None, "t2": float|None, "t3": float|None}
    ds_ticks: Dict[str, Dict[str, Optional[float]]] = {
        "data": {"t1": None, "t2": None, "t3": None},
        "sim":  {"t1": None, "t2": None, "t3": None},
    }
    # params[ds][plane] filled at the end from ds_ticks + channel drags
    params: Dict[str, Dict[str, dict]] = {
        "data": {lbl: _empty_plane_params() for lbl in PLANE_LABELS},
        "sim":  {lbl: _empty_plane_params() for lbl in PLANE_LABELS},
    }
    result: list = [None]

    # ── step sequence ─────────────────────────────────────────────────────────
    # 6 sub-steps per dataset × 2 datasets = 12 steps
    # sub 0: t1, sub 1: t2, sub 2: t3, sub 3/4/5: ch U/V/W
    NSTEPS = 12

    def _step_info(step: int) -> Tuple[str, str]:
        """Return (dataset, what).  what: 't1','t2','t3','U','V','W'."""
        ds = "data" if step < 6 else "sim"
        sub = step % 6
        what = ["t1", "t2", "t3", "U", "V", "W"][sub]
        return ds, what

    DS_LABELS   = {"data": "Data", "sim": "Sim"}
    DS_COLORS   = {"data": "darkorange", "sim": "mediumpurple"}
    TICK_STYLES = {"t1": ("-",  2.5), "t2": ("--", 2.5), "t3": (":",  2.5)}
    PLANE_COLORS = {"U": "royalblue", "V": "forestgreen", "W": "crimson"}

    # ── figure layout ─────────────────────────────────────────────────────────
    fig, axes_grid = plt.subplots(
        2, 3, figsize=(22, 10),
        gridspec_kw={"hspace": 0.38, "wspace": 0.25},
    )
    fig.suptitle(
        "select-refine  ·  Data (top) and Sim (bottom)\n"
        "Drag line, press ENTER to confirm each step  ·  'r' = undo last step",
        fontsize=11, y=0.999,
    )
    axes_data = list(axes_grid[0])
    axes_sim  = list(axes_grid[1])

    def _axes_row(ds: str) -> List:
        return axes_data if ds == "data" else axes_sim

    def _fd(ds: str) -> FrameData:
        return data_fd if ds == "data" else sim_fd

    # draw frame images
    for col, pl in enumerate(PLANE_LABELS):
        for row_i, (fd, ax) in enumerate(
            [(data_fd, axes_data[col]), (sim_fd, axes_sim[col])]
        ):
            plane = fd.planes[col]
            if pl == "W":
                vmin, vmax = 0, 200
                norm = None
                cmap = "Blues"
            else:
                vmin, vmax = -100, 100
                norm = TwoSlopeNorm(vcenter=0, vmin=vmin, vmax=vmax)
                cmap = "RdBu_r"
            im = ax.imshow(
                plane.frame.T,
                aspect="auto", origin="lower",
                extent=[plane.ch_min - 0.5, plane.ch_max + 0.5,
                        fd.start_tick, fd.end_tick + 1],
                norm=norm,
                vmin=vmin if norm is None else None,
                vmax=vmax if norm is None else None,
                cmap=cmap, interpolation="none",
            )
            fig.colorbar(im, ax=ax, fraction=0.02, pad=0.01)
            ds_lbl = DS_LABELS["data" if row_i == 0 else "sim"]
            ax.set_title(f"{ds_lbl} — Plane {pl}  "
                         f"(ch {plane.ch_min}–{plane.ch_max})", fontsize=9)
            ax.set_xlabel("Channel")
            ax.set_ylabel("Tick")

    # ── slope-reversed state (per ds, per plane) ──────────────────────────────
    # When reversed, ch_min gets t2 and ch_max gets t1 (slope flipped)
    slope_reversed: Dict[str, Dict[str, bool]] = {
        "data": {pl: False for pl in PLANE_LABELS},
        "sim":  {pl: False for pl in PLANE_LABELS},
    }

    # ── track-point mode (per ds, per plane) ─────────────────────────────────
    # When enabled, the next two clicks on that subplot set p1 and p2.
    # track_mode[ds][pl]: False | "wait_p1" | "wait_p2"
    track_mode: Dict[str, Dict[str, str]] = {
        "data": {pl: False for pl in PLANE_LABELS},
        "sim":  {pl: False for pl in PLANE_LABELS},
    }

    # ── UI widgets ────────────────────────────────────────────────────────────
    instr_ax = fig.add_axes([0.01, 0.955, 0.98, 0.038])
    instr_ax.axis("off")
    instr_txt = instr_ax.text(
        0.5, 0.5, "", transform=instr_ax.transAxes,
        fontsize=10, ha="center", va="center",
        bbox=dict(boxstyle="round", facecolor="lightyellow", alpha=0.9),
    )

    summary_ax = fig.add_axes([0.01, 0.005, 0.72, 0.042])
    summary_ax.axis("off")
    summary_txt = summary_ax.text(
        0.01, 0.5, "— no selection yet —",
        transform=summary_ax.transAxes,
        fontsize=8, va="center", family="monospace",
        bbox=dict(boxstyle="round", facecolor="#e8f4e8", alpha=0.8),
    )

    save_btn_ax = fig.add_axes([0.82, 0.005, 0.16, 0.042])
    save_btn_ax.set_visible(False)
    save_btn = Button(save_btn_ax, "Save selection",
                      color="0.85", hovercolor="lightgreen")

    # ── slope-reverse toggle buttons (one per subplot, below each axes) ───────
    # We use figure.add_axes with positions derived after draw so we store refs.
    # Buttons are placed manually in figure-fraction coords.
    # Layout: 2 rows × 3 cols. Each cell approx x=[col*0.33, col*0.33+0.28],
    # y=[row*0.50, row*0.50+0.45]. Button strip at bottom of each cell.
    _rev_btns: Dict[Tuple[str, str], Button] = {}
    _trk_btns: Dict[Tuple[str, str], Button] = {}

    def _make_plane_buttons():
        """Create per-subplot buttons: reverse-slope and track-points toggle."""
        col_lefts  = [0.075, 0.385, 0.695]
        col_widths = [0.105, 0.105, 0.105]  # half-width each, two buttons side by side
        col_lefts2 = [c + 0.113 for c in col_lefts]  # right button x
        row_bottoms = [0.51, 0.05]
        btn_h = 0.022

        for row_i, ds in enumerate(("data", "sim")):
            for col, pl in enumerate(PLANE_LABELS):
                # Reverse-slope button (left half)
                ax_rev = fig.add_axes(
                    [col_lefts[col], row_bottoms[row_i] - btn_h - 0.005,
                     col_widths[col], btn_h]
                )
                btn_rev = Button(ax_rev, f"Rev [{pl}]",
                                 color="0.88", hovercolor="lightsalmon")
                _rev_btns[(ds, pl)] = btn_rev

                def _make_rev_cb(ds_=ds, pl_=pl):
                    def _cb(_event):
                        slope_reversed[ds_][pl_] = not slope_reversed[ds_][pl_]
                        rev = slope_reversed[ds_][pl_]
                        _rev_btns[(ds_, pl_)].label.set_text(
                            f"{'[REV] ' if rev else ''}Rev [{pl_}]"
                        )
                        _rev_btns[(ds_, pl_)].color = "lightsalmon" if rev else "0.88"
                        _draw_parallelogram(ds_, pl_)
                        fig.canvas.draw_idle()
                    return _cb

                btn_rev.on_clicked(_make_rev_cb())

                # Track-points button (right half)
                ax_trk = fig.add_axes(
                    [col_lefts2[col], row_bottoms[row_i] - btn_h - 0.005,
                     col_widths[col], btn_h]
                )
                btn_trk = Button(ax_trk, f"Track [{pl}]",
                                 color="0.88", hovercolor="lightcyan")
                _trk_btns[(ds, pl)] = btn_trk

                def _make_trk_cb(ds_=ds, pl_=pl):
                    def _cb(_event):
                        cur = track_mode[ds_][pl_]
                        if cur:
                            # toggle off — clear pending state
                            track_mode[ds_][pl_] = False
                            btn_lbl = f"Track [{pl_}]"
                            btn_color = "0.88"
                        else:
                            # toggle on — start waiting for p1
                            track_mode[ds_][pl_] = "wait_p1"
                            btn_lbl = f"[CLK P1] Track [{pl_}]"
                            btn_color = "lightcyan"
                        _trk_btns[(ds_, pl_)].label.set_text(btn_lbl)
                        _trk_btns[(ds_, pl_)].color = btn_color
                        fig.canvas.draw_idle()
                    return _cb

                btn_trk.on_clicked(_make_trk_cb())

    _make_plane_buttons()

    # ── shared horizontal lines ───────────────────────────────────────────────
    # For each (ds, tick_key) we keep one Line2D per axes column.
    # _confirmed_lines[(ds, tkey)] = list of Line2D (one per axes in that row)
    _confirmed_lines: Dict[Tuple[str, str], List] = {}
    # The current "dragging" line (one per axes column of the active row)
    _drag_lines: List = []

    def _draw_hline_row(ds: str, tkey: str, y: float,
                        alpha: float = 1.0) -> List:
        """Draw a horizontal line at tick y across all 3 axes of the ds row."""
        ls, lw = TICK_STYLES[tkey]
        color = DS_COLORS[ds]
        lines = []
        for ax in _axes_row(ds):
            xl = ax.get_xlim()
            ln, = ax.plot(xl, [y, y], color=color, lw=lw, ls=ls,
                          alpha=alpha, zorder=6)
            ln._hline_ds   = ds
            ln._hline_tkey = tkey
            lines.append(ln)
        return lines

    def _remove_hlines(ds: str, tkey: str):
        key = (ds, tkey)
        for ln in _confirmed_lines.pop(key, []):
            ln.remove()

    def _update_drag_lines(y: float):
        for ln in _drag_lines:
            ln.set_ydata([y, y])
            ln.set_xdata(ln.axes.get_xlim())

    # ── track-point drawing helpers ───────────────────────────────────────────
    def _draw_track_points(ds: str, pl: str):
        """Draw/update the two track points and connecting line on the subplot."""
        col = PLANE_LABELS.index(pl)
        ax = _axes_row(ds)[col]
        # remove old track artists
        for ln in list(ax.lines):
            if getattr(ln, "_trk_tag", None) == f"{ds}_{pl}":
                ln.remove()
        for sc in list(ax.collections):
            if getattr(sc, "_trk_tag", None) == f"{ds}_{pl}":
                sc.remove()
        tp = params[ds][pl].get("track_points")
        if not tp or "p1" not in tp or "p2" not in tp:
            return
        color = PLANE_COLORS[pl]
        p1, p2 = tp["p1"], tp["p2"]
        ln, = ax.plot([p1[0], p2[0]], [p1[1], p2[1]],
                      color=color, lw=2, ls="-.", zorder=7, marker="o", ms=6)
        ln._trk_tag = f"{ds}_{pl}"

    # ── parallelogram overlay per plane ──────────────────────────────────────
    def _draw_parallelogram(ds: str, plane_lbl: str):
        """Draw/update the parallelogram for this ds/plane using current params."""
        col = PLANE_LABELS.index(plane_lbl)
        ax = _axes_row(ds)[col]
        p = params[ds][plane_lbl]
        # remove old para patch
        for patch in list(ax.patches):
            if getattr(patch, "_para_tag", None) == f"{ds}_{plane_lbl}":
                patch.remove()
        t1 = ds_ticks[ds]["t1"]
        t2 = ds_ticks[ds]["t2"]
        t3 = ds_ticks[ds]["t3"]
        ch_min = p["ch_min"]
        ch_max = p["ch_max"]
        if None in (t1, t2, t3, ch_min, ch_max):
            return
        nticks = t3 - t1
        # Parallelogram vertices (channel, tick):
        # Normal:   ch_min→t1, ch_max→t2
        # Reversed: ch_min→t2, ch_max→t1  (slope flipped)
        from matplotlib.patches import Polygon
        rev = slope_reversed[ds][plane_lbl]
        if rev:
            verts = np.array([
                [ch_min, t2],
                [ch_max, t1],
                [ch_max, t1 + nticks],
                [ch_min, t2 + nticks],
            ])
        else:
            verts = np.array([
                [ch_min, t1],
                [ch_max, t2],
                [ch_max, t2 + nticks],
                [ch_min, t1 + nticks],
            ])
        color = PLANE_COLORS[plane_lbl]
        patch = Polygon(verts, closed=True, color=color, alpha=0.20, zorder=3)
        patch._para_tag = f"{ds}_{plane_lbl}"
        ax.add_patch(patch)

    def _redraw_all_parallelograms(ds: str):
        for pl in PLANE_LABELS:
            _draw_parallelogram(ds, pl)
        fig.canvas.draw_idle()

    # ── channel span selector ─────────────────────────────────────────────────
    _spans: list = []

    def _install_ch_span(ds: str, plane_lbl: str):
        for sp in _spans:
            sp.set_active(False)
        _spans.clear()
        col = PLANE_LABELS.index(plane_lbl)
        ax = _axes_row(ds)[col]
        color = PLANE_COLORS[plane_lbl]
        sp = SpanSelector(
            ax, _on_ch_drag, direction="horizontal",
            useblit=False,
            props=dict(alpha=0.3, facecolor=color),
            interactive=True, drag_from_anywhere=False,
        )
        _spans.append(sp)

    _ch_pending: list = [None]   # [(vlo, vhi)] mutable container

    def _on_ch_drag(vlo: float, vhi: float):
        _ch_pending[0] = (vlo, vhi)

    # ── border highlight ──────────────────────────────────────────────────────
    def _highlight(ds: str, what: str):
        tick_step = what in ("t1", "t2", "t3")
        for col, pl in enumerate(PLANE_LABELS):
            for row_i, ax in enumerate([axes_data[col], axes_sim[col]]):
                row_ds = "data" if row_i == 0 else "sim"
                if tick_step:
                    active = row_ds == ds
                    color  = DS_COLORS[ds]
                else:
                    active = row_ds == ds and pl == what
                    color  = PLANE_COLORS[what]
                lw = 3 if active else 0.8
                ec = color if active else "gray"
                for sp in ax.spines.values():
                    sp.set_linewidth(lw)
                    sp.set_edgecolor(ec)

    # ── instruction text ──────────────────────────────────────────────────────
    def _update_instruction(step: int, live_y: Optional[float] = None):
        if step >= NSTEPS:
            instr_txt.set_text("All done! — Click [Save selection]  |  'r' = reset")
            instr_txt.set_bbox(dict(boxstyle="round", facecolor="lightgreen", alpha=0.6))
            fig.canvas.draw_idle()
            return
        ds, what = _step_info(step)
        t = ds_ticks[ds]
        color = DS_COLORS[ds] if what in ("t1","t2","t3") else PLANE_COLORS[what]
        live = f"  tick={int(live_y)}" if live_y is not None else ""
        if what == "t1":
            msg = (f"[{DS_LABELS[ds]}] Step {step%6+1}/6  —  "
                   f"Drag to set t1 (solid line = start tick at first channel){live}"
                   f"    ENTER to confirm")
        elif what == "t2":
            msg = (f"[{DS_LABELS[ds]}] Step {step%6+1}/6  —  "
                   f"Drag to set t2 (dashed = start tick at last channel){live}"
                   f"    [t1={int(t['t1'])}]    ENTER to confirm")
        elif what == "t3":
            nt = f"{int(live_y - t['t1'])}" if live_y and t['t1'] else "?"
            msg = (f"[{DS_LABELS[ds]}] Step {step%6+1}/6  —  "
                   f"Drag to set t3 (dotted = top edge; nticks=t3-t1={nt}){live}"
                   f"    [t1={int(t['t1'])}  t2={int(t['t2'])}]    ENTER to confirm")
        else:
            t1, t2, t3 = t["t1"], t["t2"], t["t3"]
            nt = int(t3 - t1) if (t3 and t1) else "?"
            msg = (f"[{DS_LABELS[ds]}] Step {step%6+1}/6  —  "
                   f"Drag LEFT/RIGHT on Plane {what} → channel range"
                   f"    [t1={int(t1)} t2={int(t2)} nticks={nt}]    ENTER to confirm")
        instr_txt.set_text(msg)
        instr_txt.set_bbox(dict(boxstyle="round", facecolor=color, alpha=0.25))
        fig.canvas.draw_idle()

    # ── summary ───────────────────────────────────────────────────────────────
    def _update_summary():
        parts = []
        for ds in ("data", "sim"):
            t = ds_ticks[ds]
            if t["t1"] is not None:
                parts.append(
                    f"{DS_LABELS[ds]}: t1={int(t['t1'])} t2={int(t['t2'] or 0)} "
                    f"nticks={int((t['t3'] or t['t1']) - t['t1'])}"
                )
            for pl in PLANE_LABELS:
                p = params[ds][pl]
                if p["ch_min"] is not None:
                    parts.append(f"{ds[0].upper()}-{pl}:ch {p['ch_min']}–{p['ch_max']}")
        summary_txt.set_text("   ".join(parts) if parts else "— no selection yet —")
        fig.canvas.draw_idle()

    # ── mouse drag for tick lines ─────────────────────────────────────────────
    _drag_active = [False]
    _drag_y      = [None]

    def _on_press(event):
        if event.inaxes is None:
            return

        # ── track-point capture (independent of step flow) ────────────────
        for ds_ in ("data", "sim"):
            for col, pl_ in enumerate(PLANE_LABELS):
                if track_mode[ds_][pl_] and event.inaxes == _axes_row(ds_)[col]:
                    ch   = event.xdata
                    tick = event.ydata
                    if ch is None or tick is None:
                        return
                    tp = params[ds_][pl_].get("track_points") or {}
                    if track_mode[ds_][pl_] == "wait_p1":
                        tp["p1"] = [int(round(ch)), int(round(tick))]
                        params[ds_][pl_]["track_points"] = tp
                        track_mode[ds_][pl_] = "wait_p2"
                        _trk_btns[(ds_, pl_)].label.set_text(
                            f"[CLK P2] Track [{pl_}]")
                        print(f"  Track {DS_LABELS[ds_]} {pl_} P1 = "
                              f"ch={tp['p1'][0]} tick={tp['p1'][1]}")
                    elif track_mode[ds_][pl_] == "wait_p2":
                        tp["p2"] = [int(round(ch)), int(round(tick))]
                        params[ds_][pl_]["track_points"] = tp
                        track_mode[ds_][pl_] = False
                        _trk_btns[(ds_, pl_)].label.set_text(
                            f"[SET] Track [{pl_}]")
                        _trk_btns[(ds_, pl_)].color = "palegreen"
                        print(f"  Track {DS_LABELS[ds_]} {pl_} P2 = "
                              f"ch={tp['p2'][0]} tick={tp['p2'][1]}")
                    _draw_track_points(ds_, pl_)
                    _update_summary()
                    fig.canvas.draw_idle()
                    return  # consumed

        step = _cur_step[0]
        if step >= NSTEPS:
            return
        ds, what = _step_info(step)
        if what not in ("t1", "t2", "t3"):
            return
        if event.inaxes not in _axes_row(ds):
            return
        _drag_active[0] = True
        _drag_y[0] = event.ydata
        # create drag lines if not present
        if not _drag_lines:
            lines = _draw_hline_row(ds, what, event.ydata, alpha=0.7)
            _drag_lines.extend(lines)
        else:
            _update_drag_lines(event.ydata)
        _update_instruction(step, event.ydata)
        fig.canvas.draw_idle()

    def _on_motion(event):
        if not _drag_active[0] or event.inaxes is None:
            return
        step = _cur_step[0]
        if step >= NSTEPS:
            return
        ds, what = _step_info(step)
        if event.inaxes not in _axes_row(ds):
            return
        y = event.ydata
        _drag_y[0] = y
        _update_drag_lines(y)
        _update_instruction(step, y)
        fig.canvas.draw_idle()

    def _on_release(event):
        _drag_active[0] = False

    fig.canvas.mpl_connect("button_press_event",  _on_press)
    fig.canvas.mpl_connect("motion_notify_event", _on_motion)
    fig.canvas.mpl_connect("button_release_event", _on_release)

    # ── step counter (mutable) ────────────────────────────────────────────────
    _cur_step = [0]

    def _advance():
        for sp in _spans:
            sp.set_active(False)
        _spans.clear()
        _drag_lines.clear()
        _ch_pending[0] = None
        _drag_y[0] = None
        _cur_step[0] += 1
        step = _cur_step[0]
        if step < NSTEPS:
            ds, what = _step_info(step)
            _highlight(ds, what)
            if what not in ("t1", "t2", "t3"):
                _install_ch_span(ds, what)
        else:
            # done — remove all highlights
            for col in range(3):
                for ax in axes_data + axes_sim:
                    for sp in ax.spines.values():
                        sp.set_linewidth(0.8)
                        sp.set_edgecolor("gray")
        _update_instruction(step)
        _update_summary()

    # ── confirm ───────────────────────────────────────────────────────────────
    def _confirm():
        step = _cur_step[0]
        if step >= NSTEPS:
            return
        ds, what = _step_info(step)

        if what in ("t1", "t2", "t3"):
            y = _drag_y[0]
            if y is None:
                fd = _fd(ds)
                y = (fd.start_tick + fd.end_tick) / 2.0
                print(f"  no drag — defaulting to tick {int(y)}")
            # remove drag preview lines
            for ln in list(_drag_lines):
                ln.remove()
            _drag_lines.clear()
            # store and draw confirmed line
            _remove_hlines(ds, what)
            ds_ticks[ds][what] = y
            lines = _draw_hline_row(ds, what, y, alpha=1.0)
            _confirmed_lines[(ds, what)] = lines
            print(f"  {DS_LABELS[ds]} {what} = {int(y)}")
            # when t3 confirmed, auto-fill nticks for all planes and
            # redraw parallelograms (with whatever channels are set so far)
            if what == "t3":
                t1 = ds_ticks[ds]["t1"]
                t2 = ds_ticks[ds]["t2"]
                nt = int(y - t1)
                for pl in PLANE_LABELS:
                    params[ds][pl]["tick_start"] = int(t1)
                    params[ds][pl]["tick_end"]   = int(t2)
                    params[ds][pl]["nticks"]      = nt
                print(f"  {DS_LABELS[ds]} nticks = {nt}")
            _redraw_all_parallelograms(ds)
            _advance()
            return

        # channel step
        pending = _ch_pending[0]
        if pending is None:
            print(f"  step {step+1}: no channel drag — skipped")
            _advance()
            return
        vlo, vhi = pending
        params[ds][what]["ch_min"] = int(min(vlo, vhi))
        params[ds][what]["ch_max"] = int(max(vlo, vhi))
        # tick info already set when t3 was confirmed
        _draw_parallelogram(ds, what)
        fig.canvas.draw_idle()
        print(f"  {DS_LABELS[ds]} plane {what}: "
              f"ch {params[ds][what]['ch_min']}–{params[ds][what]['ch_max']}")
        _advance()

    # ── undo last step ────────────────────────────────────────────────────────
    def _undo_last_step():
        step = _cur_step[0]
        if step == 0:
            print("  nothing to undo.")
            return

        # clear any active drag / span
        _drag_active[0] = False
        _drag_y[0] = None
        _ch_pending[0] = None
        for ln in list(_drag_lines):
            ln.remove()
        _drag_lines.clear()
        for sp in _spans:
            sp.set_active(False)
        _spans.clear()
        save_btn_ax.set_visible(False)

        # go back one step
        prev_step = step - 1
        _cur_step[0] = prev_step
        ds, what = _step_info(prev_step)

        if what in ("t1", "t2", "t3"):
            # remove the confirmed hline for this tick key
            _remove_hlines(ds, what)
            ds_ticks[ds][what] = None
            # if undoing t3, also clear nticks/tick_start/tick_end from all planes
            # and remove all parallelograms for this ds
            if what == "t3":
                for pl in PLANE_LABELS:
                    params[ds][pl]["tick_start"] = None
                    params[ds][pl]["tick_end"]   = None
                    params[ds][pl]["nticks"]      = None
                _redraw_all_parallelograms(ds)
            print(f"  Undo: cleared {DS_LABELS[ds]} {what}")
        else:
            # channel step — clear ch_min/ch_max and redraw parallelogram
            params[ds][what]["ch_min"] = None
            params[ds][what]["ch_max"] = None
            _draw_parallelogram(ds, what)
            _draw_track_points(ds, what)
            fig.canvas.draw_idle()
            print(f"  Undo: cleared {DS_LABELS[ds]} plane {what} channel range")

        _highlight(ds, what)
        if what not in ("t1", "t2", "t3"):
            _install_ch_span(ds, what)
        _update_instruction(prev_step)
        _update_summary()

    # ── save ──────────────────────────────────────────────────────────────────
    def _on_save(_event):
        sel = _selection_to_dict(params["data"], params["sim"], slope_reversed)
        with open(out_path, "w") as f:
            json.dump(sel, f, indent=2)
        result[0] = sel
        save_btn.label.set_text(f"Saved → {os.path.basename(out_path)}")
        save_btn.color = "lightgreen"
        print(f"\nSaved compare selection to {out_path}")
        fig.canvas.draw_idle()

    save_btn.on_clicked(_on_save)

    # done detection: show save button when all 16 steps complete
    orig_advance = _advance

    def _advance_with_done_check():
        orig_advance()
        if _cur_step[0] >= NSTEPS:
            save_btn_ax.set_visible(True)
            fig.canvas.draw_idle()

    # patch _advance reference used inside _confirm
    import types
    _confirm_code = _confirm  # keep reference

    def _confirm():  # noqa: F811 — redefine to wrap _advance
        step = _cur_step[0]
        if step >= NSTEPS:
            return
        ds, what = _step_info(step)

        if what in ("t1", "t2", "t3"):
            y = _drag_y[0]
            if y is None:
                fd = _fd(ds)
                y = (fd.start_tick + fd.end_tick) / 2.0
                print(f"  no drag — defaulting to tick {int(y)}")
            for ln in list(_drag_lines):
                ln.remove()
            _drag_lines.clear()
            _remove_hlines(ds, what)
            ds_ticks[ds][what] = y
            lines = _draw_hline_row(ds, what, y, alpha=1.0)
            _confirmed_lines[(ds, what)] = lines
            print(f"  {DS_LABELS[ds]} {what} = {int(y)}")
            if what == "t3":
                t1 = ds_ticks[ds]["t1"]
                t2 = ds_ticks[ds]["t2"]
                nt = int(y - t1)
                for pl in PLANE_LABELS:
                    params[ds][pl]["tick_start"] = int(t1)
                    params[ds][pl]["tick_end"]   = int(t2)
                    params[ds][pl]["nticks"]      = nt
                print(f"  {DS_LABELS[ds]} nticks = {nt}")
            _redraw_all_parallelograms(ds)
            _advance_with_done_check()
            return

        pending = _ch_pending[0]
        if pending is None:
            print(f"  step {step+1}: no channel drag — skipped")
            _advance_with_done_check()
            return
        vlo, vhi = pending
        params[ds][what]["ch_min"] = int(min(vlo, vhi))
        params[ds][what]["ch_max"] = int(max(vlo, vhi))
        _draw_parallelogram(ds, what)
        fig.canvas.draw_idle()
        print(f"  {DS_LABELS[ds]} plane {what}: "
              f"ch {params[ds][what]['ch_min']}–{params[ds][what]['ch_max']}")
        _advance_with_done_check()

    # ── keyboard ──────────────────────────────────────────────────────────────
    def _on_key(event):
        if event.key in ("enter", "return"):
            _confirm()
        elif event.key == "r":
            _undo_last_step()

    fig.canvas.mpl_connect("key_press_event", _on_key)

    # ── initial state ─────────────────────────────────────────────────────────
    ds0, what0 = _step_info(0)
    _highlight(ds0, what0)
    _update_instruction(0)

    print("\nselect-refine UI ready.  12 steps total (6 per dataset).")
    print("  Steps 1–3 : drag horizontal line in DATA row, press ENTER")
    print("              t1=solid (start tick @ ch_min)")
    print("              t2=dashed (start tick @ ch_max, sets per-plane slope)")
    print("              t3=dotted (top edge; nticks = t3 - t1)")
    print("  Steps 4–6 : drag LEFT/RIGHT on DATA planes U, V, W → channel ranges")
    print("  Steps 7–12: same for SIM row")
    print("  'r' = undo last step  |  ENTER = confirm each step\n")

    plt.show()
    return result[0]


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def add_parser(subparsers) -> None:
    p = subparsers.add_parser(
        "select-refine",
        help="Interactive GUI to select parallelogram signal regions for data/sim comparison",
    )
    p.add_argument("--data", required=True, metavar="DATA_TAR",
                   help="Raw data tar.bz2 archive")
    p.add_argument("--sim",  required=True, metavar="SIM_TAR",
                   help="Simulation tar.bz2 archive")
    p.add_argument("--out", default=None, metavar="JSON",
                   help="Output JSON path (default: compare-selection-anode<N>.json "
                        "next to data file)")
    p.add_argument("--data-tag", default=None,
                   help="Frame tag for data archive (default: auto-detect)")
    p.add_argument("--sim-tag", default=None,
                   help="Frame tag for sim archive (default: auto-detect)")
    p.set_defaults(func=run)


def run(args: argparse.Namespace) -> None:
    import re
    for path in (args.data, args.sim):
        if not os.path.exists(path):
            print(f"ERROR: file not found: {path}", file=sys.stderr)
            sys.exit(1)

    source_cls = SourceRegistry.get("frames")

    print(f"Loading data:  {args.data}")
    data_fd = source_cls().load(args.data, filter_tag=args.data_tag or "raw")

    print(f"Loading sim:   {args.sim}")
    sim_fd = source_cls().load(args.sim,  filter_tag=args.sim_tag  or "raw")

    m = re.search(r"anode(\d+)", os.path.basename(args.data))
    suffix  = f"-anode{m.group(1)}" if m else ""
    out_dir = os.path.dirname(args.data) or "."
    out_path = args.out or os.path.join(out_dir, f"compare-selection{suffix}.json")

    _run_compare_ui(data_fd, sim_fd, out_path)
