> For full option descriptions see [../README.md](../README.md).

# `woodpecker select` — interactive frame selection guide

This is the primary Woodpecker command used in the pdvd workflow.
It opens a matplotlib GUI, lets you drag out a region of interest, and writes a new
`tar.bz2` archive where all data **outside** your selection is zeroed.
One invocation per anode file.

## Installation (first time only)

Woodpecker itself is installed with pip from its source directory:

```bash
pip install -e /nfs/data/1/xqian/toolkit-dev/Woodpecker
```

The GUI also requires two additional packages not listed in `pyproject.toml`:

```bash
pip install PyQt5 tornado
```

- **PyQt5** — Qt bindings for the default matplotlib backend (`QtAgg`)
- **tornado** — web server required by the fallback `WebAgg` browser-based backend

## Input

A single `protodune-sp-frames-anode<N>.tar.bz2` produced by WireCell's `FrameFileSink`.
The anode ID `N` is parsed from the filename by regex.

## GUI workflow (4 drag steps)

```
woodpecker select protodune-sp-frames-anode0.tar.bz2 --outdir ./sel/ --prefix protodune-sp-frames
```

1. **Tick range** — drag vertically on any plane to set `[t0, t1]`, press **ENTER**.
2. **U-plane channel range** — drag horizontally on the U plane, press **ENTER**.
3. **V-plane channel range** — drag horizontally on the V plane, press **ENTER**.
4. **W-plane channel range** — drag horizontally on the W plane, press **ENTER**.
5. Click **"Save selection"** to write the outputs.

Code path: `woodpecker/gui/app.py` → `on_save_callback` in `cli/cmd_select.py:60`.

## Outputs

Both files are written to `--outdir`:

| File | Description |
|---|---|
| `<prefix>-anode<N>.tar.bz2` | Masked archive; same internal structure as input |
| `selection-anode<N>.json` | Sidecar: `{"tick_range": [t0,t1], "ch_ranges": [...]}` |

Default prefix is `protodune-sp-frames-part`. Always pass `--prefix protodune-sp-frames` in
the pdvd workflow so the output filename matches what `run_img_evt.sh` expects.

## Masking algorithm

`woodpecker/processing/masker.py:35` (`_build_mask`):

```
mask[tick, ch] = True  iff  tick ∈ [t0, t1]  AND  ch ∈ [ch_min, ch_max]  for that plane
output = np.where(mask, original, 0)
```

The mask is built per `frame_*` key in the npy dict inside the tarball; all keys (gauss,
wiener, etc.) are masked with the same spatial selection. The archive is rewritten in place
preserving original `TarInfo` names.

## All flags

| Flag | Default | Notes |
|---|---|---|
| `archive` | (required) | Input `.tar.bz2` path |
| `--outdir DIR` | `woodpecker_data` | Output directory; auto-suffixed with date+random if left as default |
| `--prefix STR` | `protodune-sp-frames-part` | Output filename prefix before `-anode<N>.tar.bz2` |
| `--out PATH` | auto | Override full output path (overrides `--outdir` + `--prefix`) |
| `--save-selection JSON` | auto | Override sidecar path (default: `<outdir>/selection-anode<N>.json`) |
| `--vmax FLOAT` | None | Colormap max for display |
| `--vmin FLOAT` | 0 | Colormap min for display |
| `--cmap STR` | `Blues` | Matplotlib colormap name |

## Running on a headless server (no X display)

`run_select_evt.sh` detects automatically when no X display is available and switches to
the **WebAgg** backend, which serves the GUI as a web page over localhost.

When it prints:
```
No X display detected — using browser-based GUI (WebAgg).
Once woodpecker prints its URL (e.g. http://127.0.0.1:8988),
forward the port from your local machine:
  ssh -L 8988:localhost:8988 xqian@wcgpu1.phy.bnl.gov
then open http://127.0.0.1:8988 in your browser.
```

1. Wait for woodpecker to also print `127.0.0.1:8988` (the actual server start message).
2. In a **separate terminal on your local machine**, run the printed `ssh -L` command.
3. Open `http://127.0.0.1:8988` in your local browser — the full interactive canvas appears.
4. Make your selection and click **"Save selection"** as normal; the file is written on the server.

The port 8988 is matplotlib's default. If it's already in use, matplotlib picks the next free
port — read the URL from woodpecker's output, not the example above.

## Running all anodes for one event (pdvd)

Use `run_select_evt.sh` which wraps this loop:

```bash
./run_select_evt.sh 039324 1 sel1
# Opens GUI for each anode found in input_data/run039324/evt1/
# Writes masked archives + sidecars to work/039324_1_sel1/input/
```

Or manually for a specific anode:

```bash
woodpecker select input_data/run039324/evt1/protodune-sp-frames-anode0.tar.bz2 \
    --outdir work/039324_1_sel1/input \
    --prefix protodune-sp-frames
```

## Follow-up steps (pdvd workflow)

After selection, pass `-s <sel_tag>` to the existing pipeline scripts:

```bash
./run_sp_to_magnify_evt.sh 039324 1 -s sel1
./run_img_evt.sh           039324 1 -s sel1
./run_clus_evt.sh          039324 1 -s sel1
./run_bee_img_evt.sh       039324 1 -s sel1
```

Outputs go to `work/039324_1_sel1/`; original `work/039324_1/` is untouched.
