"""Main GUI application — assembles figure, wires widgets to controller.

Entry point: run_ui(frame_data, ...) -> Selection
"""

from __future__ import annotations

import os
from typing import Optional

import matplotlib
if not os.environ.get("MPLBACKEND"):
    matplotlib.use("QtAgg")
import matplotlib.pyplot as plt
import numpy as np

from woodpecker.core.selection import Selection
from woodpecker.gui.controller import SelectionController, STEPS, STEP_COLORS
from woodpecker.gui.overlays import clear_overlays, draw_hband, draw_vband
from woodpecker.gui.widgets import (
    make_instruction_text,
    make_save_button,
    make_span_selectors,
    make_summary_text,
)
from woodpecker.io.frame_data import FrameData

PLANE_LABELS = ["U", "V", "W"]


def run_ui(
    frame_data: FrameData,
    out_path: Optional[str] = None,
    vmax: Optional[float] = None,
    vmin: float = 0,
    cmap: str = "Blues",
    on_save_callback=None,
) -> Optional[Selection]:
    """
    Open the interactive selection UI.

    Parameters
    ----------
    frame_data : FrameData
    out_path   : default output path (shown to user; actual save triggered via callback)
    on_save_callback : callable(selection, out_path) invoked when user clicks Save
    """
    if out_path is None:
        base = os.path.splitext(os.path.splitext(frame_data.source_path)[0])[0]
        out_path = base + "-selected.tar.bz2"

    controller = SelectionController()
    span_refs = []
    result: list = [None]  # mutable container for the final selection

    # ── build figure ──────────────────────────────────────────────────────────
    fig = plt.figure(figsize=(19, 8))
    axes = [
        fig.add_axes([0.04 + i * 0.32, 0.18, 0.28, 0.68])
        for i in range(3)
    ]

    instr_text = make_instruction_text(fig)
    summary_text = make_summary_text(fig)
    btn_ax, save_btn = make_save_button(fig)

    fig.suptitle(
        f"APA anode {frame_data.anode_id} — {frame_data.filter_tag.capitalize()} frames",
        fontsize=13, y=0.98,
    )

    # Draw images
    for col, plane in enumerate(frame_data.planes):
        ax = axes[col]
        vm = vmax
        if vm is None:
            nz = plane.frame[plane.frame != 0]
            vm = float(np.percentile(nz, 99)) if len(nz) else 1.0
        im = ax.imshow(
            plane.frame.T,
            aspect="auto", origin="lower",
            extent=[plane.ch_min - 0.5, plane.ch_max + 0.5,
                    frame_data.start_tick, frame_data.end_tick + 1],
            vmin=vmin, vmax=vm, cmap=cmap, interpolation="none",
        )
        ax.set_title(f"Plane {plane.name}  (ch {plane.ch_min}–{plane.ch_max})")
        ax.set_xlabel("Channel number")
        ax.set_ylabel("Time tick")
        fig.colorbar(im, ax=ax, label="ADC")

    # ── overlay helpers (step-aware) ───────────────────────────────────────────

    def _refresh_spans(step_idx: int) -> None:
        for sp in span_refs:
            sp.set_active(False)
        span_refs.clear()
        if step_idx < 0:
            return
        _, _, direction, active = STEPS[step_idx]
        color = STEP_COLORS[step_idx]
        new_spans = make_span_selectors(
            axes, active, direction, color,
            on_select=controller.span_selected,
        )
        span_refs.extend(new_spans)

    def _update_instruction(step_idx: int) -> None:
        if step_idx < 0:
            instr_text.set_text("✓ All selections done! — Click [Save selection] or press 'r' to redo")
            instr_text.set_bbox(dict(boxstyle="round", facecolor="lightgreen", alpha=0.6))
            for ax in axes:
                for spine in ax.spines.values():
                    spine.set_linewidth(1)
                    spine.set_edgecolor("gray")
            btn_ax.set_visible(True)
        else:
            label, desc, _, active = STEPS[step_idx]
            color = STEP_COLORS[step_idx]
            for i, ax in enumerate(axes):
                for spine in ax.spines.values():
                    spine.set_linewidth(3 if i in active else 0.8)
                    spine.set_edgecolor(color if i in active else "gray")
            instr_text.set_text(f"{label}:  {desc}    [press ENTER to confirm]")
            instr_text.set_bbox(dict(boxstyle="round", facecolor=color, alpha=0.3))
        fig.canvas.draw_idle()

    def _update_summary() -> None:
        sel = controller.selection
        parts = []
        if sel.tick_range:
            t0, t1 = sel.tick_range
            parts.append(f"Ticks: {t0}–{t1} (n={t1-t0+1})")
        else:
            parts.append("Ticks: (not set)")
        for i, label in enumerate(PLANE_LABELS):
            r = sel.ch_ranges[i]
            if r:
                pch = frame_data.planes[i].channels
                n = int(((pch >= r.ch_min) & (pch <= r.ch_max)).sum())
                parts.append(f"Plane {label}: ch {r.ch_min}–{r.ch_max} (n={n})")
            else:
                parts.append(f"Plane {label}: (not set)")
        summary_text.set_text("   |   ".join(parts))
        fig.canvas.draw_idle()

    def _print_final(sel: Selection) -> None:
        print("\n" + "=" * 55)
        print("=== Final Selection ===")
        t0, t1 = sel.tick_range if sel.tick_range else (frame_data.start_tick, frame_data.end_tick)
        print(f"Tick range : {t0} – {t1}  (n={t1-t0+1})")
        for i, label in enumerate(PLANE_LABELS):
            pch = frame_data.planes[i].channels
            r = sel.ch_ranges[i]
            c0 = r.ch_min if r else int(pch[0])
            c1 = r.ch_max if r else int(pch[-1])
            chosen = pch[(pch >= c0) & (pch <= c1)]
            print(f"Plane {label} ch  : {c0} – {c1}  (n={len(chosen)})"
                  f"  first5={chosen[:5].tolist()}")
        print("=" * 55 + "\n")
        print(f"Click [Save selection] to write  {out_path}")

    # ── controller callbacks ───────────────────────────────────────────────────

    def _on_step_changed(step_idx: int) -> None:
        _refresh_spans(step_idx)
        _update_instruction(step_idx)
        _update_summary()

    def _on_selection_complete(sel: Selection) -> None:
        result[0] = sel
        _print_final(sel)

    def _on_preview(step_idx: int, vlo: float, vhi: float) -> None:
        color = STEP_COLORS[step_idx]
        if step_idx == 0:
            for ax in axes:
                draw_hband(ax, vlo, vhi, color, "tick_preview")
        else:
            pi = STEPS[step_idx][3][0]
            draw_vband(axes[pi], vlo, vhi, color, f"ch_preview_{pi}")
        fig.canvas.draw_idle()

    controller.on_step_changed = _on_step_changed
    controller.on_selection_complete = _on_selection_complete
    controller.on_preview = _on_preview

    # ── confirm / reset ────────────────────────────────────────────────────────

    def _confirm_step_and_draw() -> None:
        idx = controller.current_step
        pending = controller._pending  # read before confirm clears it
        controller.confirm_step()

        # draw confirmed overlays
        if pending is not None:
            vlo, vhi = pending
            if idx == 0:
                for ax in axes:
                    clear_overlays(ax, "tick_preview")
                    draw_hband(ax, vlo, vhi, STEP_COLORS[0], "tick_final", alpha=0.18)
            else:
                pi = STEPS[idx][3][0]
                clear_overlays(axes[pi], f"ch_preview_{pi}")
                draw_vband(axes[pi], vlo, vhi, STEP_COLORS[idx], f"ch_final_{pi}", alpha=0.22)
        _update_summary()

    def _reset_and_draw() -> None:
        for ax in axes:
            for tag in ["tick_preview", "tick_final",
                        "ch_preview_0", "ch_final_0",
                        "ch_preview_1", "ch_final_1",
                        "ch_preview_2", "ch_final_2"]:
                clear_overlays(ax, tag)
        btn_ax.set_visible(False)
        save_btn.label.set_text("Save selection")
        controller.reset()

    # ── save button ────────────────────────────────────────────────────────────

    def _on_save(_event) -> None:
        sel = controller.selection
        if on_save_callback:
            on_save_callback(sel, out_path)
        save_btn.label.set_text(f"Saved → {os.path.basename(out_path)}")
        save_btn.color = "lightgreen"
        fig.canvas.draw_idle()

    save_btn.on_clicked(_on_save)

    # ── keyboard ──────────────────────────────────────────────────────────────

    def _on_key(event) -> None:
        if event.key in ("enter", "return"):
            _confirm_step_and_draw()
        elif event.key == "r":
            _reset_and_draw()

    fig.canvas.mpl_connect("key_press_event", _on_key)

    # ── initial state ─────────────────────────────────────────────────────────
    _refresh_spans(0)
    _update_instruction(0)
    _update_summary()

    print("\nUI ready.")
    print("  Step 1 — drag UP/DOWN    on any plot → tick range")
    print("  Step 2 — drag LEFT/RIGHT on plane U  → U channel range")
    print("  Step 3 — drag LEFT/RIGHT on plane V  → V channel range")
    print("  Step 4 — drag LEFT/RIGHT on plane W  → W channel range")
    print("  Press ENTER after each step to confirm and advance")
    print("  Press 'r' to reset | Click [Save selection] when done\n")

    plt.show()

    return result[0]
