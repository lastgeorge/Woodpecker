# woodpecker

WireCell targeted region selection and debugging tool.

## Install

```bash
cd woodpecker
pip install -e .
```

Requires Python ≥ 3.9, numpy, matplotlib.

After install, activate the environment (e.g. `direnv allow`) so that
`woodpecker` is on your PATH.  Without installing you can also run:

```bash
python -m woodpecker <subcommand> ...
```

---

## Typical workflow

```
protodune-sp-frames-anode<N>.tar.bz2   (WireCell FrameFileSink output)
          │
          ▼
   woodpecker select             → masked anode<N>.tar.bz2  in woodpecker_data_<date>/
          │                        + selection-anode<N>.json
          ▼
   woodpecker run-img      [WCT] → imaging clusters          in woodpecker_data_<date>/
          │
          ▼
   woodpecker run-clustering [WCT] → bee upload.zip + tracks-*.json
          │
          ├─── woodpecker extract-tracks    → inspect / export track list
          │
          ├─── woodpecker run-sim-check [WCT] → simulated raw frames
          │                                     protodune-sp-frames-sim-anode<N>.tar.bz2
          │
          └─── woodpecker select-refine  ──┐
                    (interactive parallelogram       │
                     selection for data + sim)       ▼
                                          woodpecker compare-waveforms
                                             → aligned mean waveform comparison PNG
```

Helper tools :

```
woodpecker plot-frames      → quick U/V/W image of any tar.bz2 frame archive
woodpecker frames-to-root   → convert tar.bz2 frame archive to ROOT TH2D histograms
```

---

## Commands

Commands are grouped into three categories:

- **Workflow** — core data-selection and waveform-comparison pipeline
- **WCT** — invoke `wire-cell`; require a WireCell installation
- **Helpers** — standalone utilities for frame inspection and format conversion

---

## Workflow commands

### `select` — interactive frame selection GUI

Displays the three wire-plane (U/V/W) frame images and walks you through
four sequential selection steps.  The output directory is automatically named
`woodpecker_data_<date>` (e.g. `woodpecker_data_20260329`) to avoid
overwriting previous sessions; pass `--outdir` to override.

```bash
woodpecker select protodune-sp-frames-anode0.tar.bz2
woodpecker select protodune-sp-frames-anode0.tar.bz2 --vmax 1000
woodpecker select protodune-sp-frames-anode0.tar.bz2 --out my_output.tar.bz2
woodpecker select protodune-sp-frames-anode0.tar.bz2 --save-selection sel.json
woodpecker select protodune-sp-frames-anode0.tar.bz2 --outdir my_dir
```

Selection workflow:

| Step | Action | Gesture |
|------|--------|---------|
| 1 | Tick range | drag UP/DOWN on any plot |
| 2 | U channel range | drag LEFT/RIGHT on plane U |
| 3 | V channel range | drag LEFT/RIGHT on plane V |
| 4 | W channel range | drag LEFT/RIGHT on plane W |

- Press **ENTER** to confirm each step and advance.
- Press **r** to restart from Step 1.
- Click **[Save selection]** when all four steps are done.

The output is a new `.tar.bz2` with the same file/array structure as the
input.  Data outside the selected tick and channel ranges is zeroed out.
A `selection-anode<N>.json` sidecar is also saved for use with
`compare-waveforms`.

---

### `mask` — non-interactive (batch) masking

Apply a previously saved selection to an archive without opening a GUI.

```bash
woodpecker select anode0.tar.bz2 --save-selection sel.json
woodpecker mask  anode0.tar.bz2 --selection sel.json --out anode0-masked.tar.bz2
```

---

### `extract-tracks` — derive track directions from 3D imaging clusters

```bash
woodpecker extract-tracks upload.zip
woodpecker extract-tracks upload.zip --out tracks.json
woodpecker extract-tracks upload.zip --min-points 5
```

| Option | Default | Description |
|--------|---------|-------------|
| `--out` | auto (same dir as input zip) | Save results as JSON |
| `--min-points` | 2 | Skip clusters with fewer points |

Output fields per cluster: `cluster_id`, `n_points`, `total_charge`,
`centroid`, `direction`, `length_cm`, `start`, `end`, `linearity`,
`theta_deg`, `phi_deg`.

---

### `select-refine` — interactive parallelogram region selection for data/sim comparison

Opens a 2×3 grid (data row top, sim row bottom) showing all six wire-plane
images.  Guides you through 12 steps (6 per dataset) to define a slanted
parallelogram window matching the track crossing the APA.

```bash
woodpecker select-refine \
    --data woodpecker_data/protodune-sp-frames-raw-anode0.tar.bz2 \
    --sim  woodpecker_data/protodune-sp-frames-sim-anode0.tar.bz2 \
    --out  woodpecker_data/compare-selection-anode0.json
```

#### Parallelogram model

Three shared horizontal tick lines (same values across all three planes)
define the window for each dataset:

| Line | Style | Meaning |
|------|-------|---------|
| t1 | solid | Start tick at the **first** channel (`ch_min`) |
| t2 | dashed | Start tick at the **last** channel (`ch_max`) |
| t3 | dotted | Top edge; `nticks = t3 − t1` |

The slope per plane is encoded by the difference `t2 − t1` combined with
`ch_max − ch_min`.  Each plane's channel range is set independently by a
horizontal drag.

Parallelogram corners for each plane (normal slope):

```
(ch_min, t1) → (ch_max, t2) → (ch_max, t2+nticks) → (ch_min, t1+nticks)
```

#### Step sequence

| Steps | Action | How |
|-------|--------|-----|
| 1–3 (Data) | Set t1, t2, t3 | Drag horizontal line across Data row, ENTER to confirm |
| 4–6 (Data) | Set U, V, W channel ranges | Drag left/right on each plane, ENTER |
| 7–9 (Sim)  | Set t1, t2, t3 | Same for Sim row |
| 10–12 (Sim) | Set U, V, W channel ranges | Same for Sim row |

#### Per-subplot buttons

Each of the 6 subplots has two buttons below it:

- **Rev [plane]** — toggle reversed slope (swaps `ch_min`/`ch_max` in the
  saved JSON so `ch_min` gets `t2` and `ch_max` gets `t1`)
- **Track [plane]** — enable two-point track selection mode: click P1 then P2
  directly on the subplot image.  The two points `[channel, tick]` are saved
  in the JSON and used by `compare-waveforms` to apply the `align2` alignment
  algorithm for that plane.

#### Keyboard shortcuts

| Key | Action |
|-----|--------|
| ENTER | Confirm current step |
| r | Undo last confirmed step |

#### Output JSON format

```json
{
  "data": {
    "U": {"ch_min": 272, "ch_max": 394, "tick_start": 2915, "tick_end": 1444, "nticks": 289,
          "track_points": {"p1": [272, 2915], "p2": [394, 1444]}},
    "V": { ... },
    "W": { ... }
  },
  "sim": {
    "U": { ... }, "V": { ... }, "W": { ... }
  }
}
```

`track_points` is `null` when the Track button was not used for that plane.

---

### `compare-waveforms` — compare data and simulation signal shapes

Loads data and simulation frame archives, extracts waveforms from the
selected region, and produces a PNG with peak-aligned mean waveforms for
each wire plane.

```bash
# Data-only (no sim):
woodpecker compare-waveforms \
    --data      woodpecker_data/protodune-sp-frames-raw-anode0.tar.bz2 \
    --selection woodpecker_data/compare-selection-anode0.json

# Using legacy selection from 'woodpecker select':
woodpecker compare-waveforms \
    --data      woodpecker_data/protodune-sp-frames-raw-anode0.tar.bz2 \
    --sim       woodpecker_data/protodune-sp-frames-sim-anode0.tar.bz2 \
    --selection woodpecker_data/selection-anode0.json

# Using parallelogram selection from 'woodpecker select-refine':
woodpecker compare-waveforms \
    --data      woodpecker_data/protodune-sp-frames-raw-anode0.tar.bz2 \
    --sim       woodpecker_data/protodune-sp-frames-sim-anode0.tar.bz2 \
    --selection woodpecker_data/compare-selection-anode0.json \
    --show-power \
    --no-w-scale
```

| Option | Default | Description |
|--------|---------|-------------|
| `--data` | required | Raw data tar.bz2 archive |
| `--sim` | optional | Simulation tar.bz2 archive (omit to plot data only) |
| `--selection` | required | JSON from `woodpecker select` or `select-refine` |
| `--data-tag` | auto-detect | Frame tag in data archive |
| `--sim-tag` | auto-detect | Frame tag in sim archive |
| `--half-window` | 200 | Half-width of output waveform array (ticks) |
| `--show-power` | off | Also plot FFT power density spectra |
| `--no-w-scale` | off | Disable W-plane peak normalization of sim |
| `--out` | `compare-waveforms-anode<N>.png` | Output image path |
| `--dpi` | 150 | Output image DPI |

#### Algorithm

For each wire plane:

1. **Window extraction** — a window of `nticks` samples is extracted per
   channel, with start tick interpolated linearly from `tick_start` (at
   `ch_min`) to `tick_end` (at `ch_max`), following the track slant.
2. **Peak alignment** — each channel's waveform is shifted so its absolute
   peak lands at `half_window`, then accumulated and averaged.
3. **align2 mode** (when `track_points` are provided) — the predicted peak
   position per channel is computed from the track slope
   `kk = (y2−y1)/(x2−x1)` as in the ROOT `align2()` function; the
   accumulation is shifted to that predicted position instead of the actual
   peak.
4. **W-plane normalization** — by default, the simulation is scaled so that
   its W-plane peak matches the data peak.  Disable with `--no-w-scale`.
5. **Sim fallback** (legacy format only) — if the sim `start_tick` is
   misaligned (e.g. −249878), the full frame is searched per channel for its
   peak rather than using absolute tick offsets.

#### Selection format auto-detection

Both JSON formats are accepted:

- **Legacy** (`woodpecker select` output): `{"tick_range": [...], "ch_ranges": [...]}`
- **Compare** (`woodpecker select-refine` output): `{"data": {...}, "sim": {...}}`

---

## WCT commands

These commands build and execute `wire-cell` command lines.  They require a
working WireCell installation (`wire-cell` on PATH) and appropriate jsonnet
configuration files.

### `run-img` — run WireCell imaging on masked frames

```bash
woodpecker run-img
woodpecker run-img --datadir woodpecker_data_20260329
woodpecker run-img --anode-indices '[2]'
woodpecker run-img --dry-run
```

| Option | Default | Description |
|--------|---------|-------------|
| `--datadir` | `woodpecker_data` | Directory with masked `*-anode<N>.tar.bz2` files |
| `--anode-indices` | auto-detect | JSON list e.g. `'[1,2]'` |
| `--output-prefix` | `<datadir>/protodune-sp-frames-img` | Output prefix |
| `--jsonnet` | auto-search | Path to imaging jsonnet |
| `--wct-base` | `/nfs/data/1/xning/wirecell-working` | WCT_BASE directory |
| `--log-level` | `debug` | Wire-cell log level |
| `--dry-run` | false | Print command without executing |

---

### `run-clustering` — run WireCell clustering and upload to bee

```bash
woodpecker run-clustering
woodpecker run-clustering --datadir woodpecker_data_20260329
woodpecker run-clustering --anode-indices '[2]'
woodpecker run-clustering --dry-run
```

Options mirror `run-img`; see `--help` for details.

Output: `woodpecker_data/upload.zip` (bee viewer) and
`woodpecker_data/tracks-<N>.json` (for `extract-tracks` / `run-sim-check`).

---

### `run-sim-check` — simulate longest extracted track

```bash
woodpecker run-sim-check
woodpecker run-sim-check --tracks-file woodpecker_data/tracks-upload.json
woodpecker run-sim-check --anode-indices '[2]'
woodpecker run-sim-check --dry-run
```

| Option | Default | Description |
|--------|---------|-------------|
| `--tracks-file` | auto-detect `tracks-*.json` | Track JSON from `extract-tracks` |
| `--datadir` | `woodpecker_data` | Used for anode auto-detection |
| `--anode-indices` | auto-detect | JSON list e.g. `'[2]'` |
| `--output-prefix` | `<datadir>/protodune-sp-frames-sim` | Output prefix |
| `--jsonnet` | auto-search | Path to simulation jsonnet |
| `--wct-base` | `/nfs/data/1/xning/wirecell-working` | WCT_BASE directory |
| `--log-level` | `info` | Wire-cell log level |
| `--dry-run` | false | Print command without executing |

---

## Helper tools

Standalone utilities for frame inspection and format conversion.
Not part of the main workflow — can be run at any point on any tar.bz2 archive.

### `plot-frames` — draw U/V/W wire plane views from a tar.bz2

```bash
woodpecker plot-frames woodpecker_data/protodune-sp-frames-sim-anode0.tar.bz2
woodpecker plot-frames data.tar.bz2 --tag raw2
woodpecker plot-frames data.tar.bz2 --out frames.png
woodpecker plot-frames data.tar.bz2 --tick-range 1000 3000
woodpecker plot-frames data.tar.bz2 --zrange -50 50
```

| Option | Default | Description |
|--------|---------|-------------|
| `--tag` | auto-detect (raw > gauss > wiener) | Frame tag to display |
| `--out` | `<input>.png` | Output PNG path |
| `--tick-range T0 T1` | full range | Restrict to tick indices T0..T1 |
| `--zrange ZMIN ZMAX` | ±3 × RMS | ADC color scale range |
| `--dpi` | 150 | Output image DPI |

Color scales: induction planes U/V use `RdBu_r` (diverging, white at zero);
collection plane W uses `Blues` (white at zero, blue for positive signal).

---

### `frames-to-root` — convert tar.bz2 frame archive to ROOT TH2D histograms

Reads a WireCell `FrameFileSink` tar.bz2 archive and writes a ROOT file
containing one `TH2D` per tag per wire plane.  Planes are auto-detected from
channel-number gaps in the channel array.

```bash
# Convert all tags (raw, gauss, …) found in the archive:
woodpecker frames-to-root data/run040475/protodune-sp-frames-anode0.tar.bz2

# Convert a specific tag only:
woodpecker frames-to-root data.tar.bz2 --tag gauss

# Convert multiple specific tags:
woodpecker frames-to-root data.tar.bz2 --tag raw --tag gauss

# Specify output path:
woodpecker frames-to-root data.tar.bz2 --out frames.root
```

| Option | Default | Description |
|--------|---------|-------------|
| `frame_file` | required | Path to `*-anode<N>.tar.bz2` |
| `--tag TAG` | all tags found | Frame tag(s) to convert; may be repeated |
| `--out` | `<frame_file>.root` (same directory) | Output ROOT file path |

#### Output histogram naming

For each tag and each plane a `TH2D` is created named `<tag>_<plane>`:

| Example tag | Histograms written |
|-------------|-------------------|
| `raw4` | `raw4_U`, `raw4_V`, `raw4_W` |
| `gauss` | `gauss_U`, `gauss_V`, `gauss_W` |

Histogram axes:
- **x** — channel number (one bin per channel)
- **y** — absolute tick number (from `tickinfo` start tick)
- **z** — ADC value (or signal amplitude)

Requires PyROOT.  Make sure `PYTHONPATH` and `LD_LIBRARY_PATH` include the
ROOT library directory (set in `.envrc` via `path_add PYTHONPATH $PWD/local/lib/root`).

---

## Architecture

```
woodpecker/
├── core/
│   ├── selection.py           # Selection dataclass (tick_range + ch_ranges)
│   ├── registry.py            # SourceRegistry / StepRegistry — plugin system
│   └── exceptions.py          # Exception hierarchy
├── io/
│   ├── base.py                # DataSource ABC
│   ├── frame_data.py          # FrameData / PlaneData dataclasses
│   ├── frame_source.py        # tar.bz2 frame loader  [registered as "frames"]
│   └── cluster_source.py      # WCP zip cluster loader [registered as "clusters"]
├── gui/
│   ├── app.py                 # Figure assembly and event wiring
│   ├── controller.py          # Step-machine state (no matplotlib dependency)
│   ├── widgets.py             # SpanSelector / Button / text bar factories
│   └── overlays.py            # Highlight band helpers
├── processing/
│   ├── base.py                # ProcessingStep ABC
│   ├── masker.py              # Write masked archive   [registered as "mask_frames"]
│   └── track_extractor.py     # PCA track extraction   [registered as "extract_tracks"]
├── pipeline/
│   ├── context.py             # PipelineContext carrier dataclass
│   └── runner.py              # Resolve steps by name and run in sequence
└── cli/
    ├── main.py                     # Top-level argparse with subcommands (grouped)
    │
    │   ── Workflow commands ──────────────────────────────────────────────────
    ├── cmd_select.py               # `select`          — GUI frame selection + masking
    ├── cmd_mask.py                 # `mask`            — non-interactive masking
    ├── cmd_extract.py              # `extract-tracks`  — PCA track directions
    ├── cmd_select_parallelogram.py # `select-refine`   — data/sim parallelogram GUI
    ├── cmd_compare_waveforms.py    # `compare-waveforms` — waveform comparison
    │
    │   ── WCT commands (require wire-cell) ───────────────────────────────────
    ├── cmd_run_img.py              # `run-img`         — WireCell imaging
    ├── cmd_run_clustering.py       # `run-clustering`  — WireCell clustering
    ├── cmd_run_sim_check.py        # `run-sim-check`   — track simulation
    │
    │   ── Helper tools ───────────────────────────────────────────────────────
    ├── cmd_plot_frames.py          # `plot-frames`     — U/V/W PNG from any archive
    └── cmd_frames_to_root.py       # `frames-to-root`  — tar.bz2 → ROOT TH2D
```

### How the plugin system works

Sources and processing steps register themselves by name using decorators:

```python
@SourceRegistry.register("clusters")
class ClusterSource(DataSource): ...

@StepRegistry.register("extract_tracks")
class TrackExtractor(ProcessingStep): ...
```

CLI commands look them up by name at runtime.  Adding a new source or step
requires no changes to any existing file — only the new file and a one-line
import in the relevant CLI command.
