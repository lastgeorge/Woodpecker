> For full option descriptions and algorithm details see [../README.md](../README.md).

# Woodpecker — package overview

Woodpecker is a Python package for **interactive spatial selection of wire-cell frame data**.
The user points a matplotlib GUI at a `protodune-sp-frames-anode<N>.tar.bz2` archive, drags
out a tick-range × U/V/W channel-range box, and saves a masked version of the archive where
all data outside the box is zeroed. This is the only piece of Woodpecker with non-trivial
standalone logic.

The package also ships thin `wire-cell` wrappers (`run-img`, `run-clustering`) and bee-upload
helpers, but these duplicate functionality already present in the `pdvd/*.sh` pipeline scripts
(see [wct-wrappers.md](wct-wrappers.md)).

## Package layout

| Directory | Contents |
|---|---|
| `woodpecker/cli/` | Argparse subcommand modules (one per command); entry at `cli/main.py:32` |
| `woodpecker/core/` | `Selection` dataclass, `SourceRegistry`/`StepRegistry` plugin framework |
| `woodpecker/io/` | `FrameData`/`PlaneData` dataclasses; `.tar.bz2` reader (`frame_source.py`) |
| `woodpecker/gui/` | Matplotlib interactive GUI (`app.py`, `controller.py`, `widgets.py`, `overlays.py`) |
| `woodpecker/processing/` | `masker.py` (frame masking), `track_extractor.py` (PCA), `sim_driver.py` |
| `woodpecker/pipeline/` | `PipelineContext` + `PipelineRunner` (step-name dispatch via registry) |
| `woodpecker/tools/` | Bundled helpers: `wct-img-2-bee-combined.py`, `upload-to-bee.sh`, `unzip.pl`, `zip-upload.sh` |

## Entry points

```
woodpecker <subcommand>     # console script registered in pyproject.toml
python -m woodpecker <...>  # also works
```

## CLI map

**Workflow** — selection and refinement:

| Command | Purpose |
|---|---|
| `woodpecker select` | GUI: drag tick × channel box, save masked archive (see [select-guide.md](select-guide.md)) |
| `woodpecker mask` | Non-interactive masking using a pre-saved `selection.json` |
| `woodpecker select-refine` | Refine selection using a parallelogram model |
| `woodpecker extract-tracks` | PCA-based track extraction from a selection |
| `woodpecker compare-waveforms` | Side-by-side waveform comparison using a saved selection sidecar |

**WCT wrappers** — thin `wire-cell` shells (see [wct-wrappers.md](wct-wrappers.md)):

| Command | Purpose |
|---|---|
| `woodpecker run-img` | Shell out to `wire-cell -c wct-img-all.jsonnet` |
| `woodpecker run-clustering` | Shell out to `wire-cell -c wct-clustering.jsonnet` + bee upload |
| `woodpecker run-sim-check` | Shell out to `wire-cell -c wct-sim-check-track.jsonnet` |

**Helpers**:

| Command | Purpose |
|---|---|
| `woodpecker plot-frames` | Matplotlib visualization of a frame archive |
| `woodpecker frames-to-root` | Convert `.tar.bz2` frames to ROOT histograms (requires PyROOT) |

## External runtime dependencies

| Dependency | Used by |
|---|---|
| `numpy`, `matplotlib` | always required |
| `wire-cell` on `PATH` | `run-img`, `run-clustering`, `run-sim-check` |
| `wirecell-img` | `run-img --bee` (bee blob conversion) |
| `perl`, `zip`, `unzip` | `run-clustering` post-processing |
| `curl` | `tools/upload-to-bee.sh` (BNL bee server upload) |
| PyROOT | `frames-to-root` only |

Woodpecker does **not** bundle any jsonnet configs. The WCT-wrapper subcommands search upward
from CWD (up to 5 levels) for the `wcp-porting-img/pdvd/` tree; override with `--jsonnet` /
`--script-dir`.

## End-to-end data flow (built-in Woodpecker pipeline)

```
protodune-sp-frames-anode<N>.tar.bz2        (WCT FrameFileSink output, one per anode)
    │
    │  woodpecker select  [GUI]
    ▼
woodpecker_data_<YYYYMMDD>_<NN>/
    <prefix>-anode<N>.tar.bz2               (frames zeroed outside selection)
    selection-anode<N>.json                 (tick/channel sidecar)
    │
    │  woodpecker run-img [--bee]
    ▼  (wire-cell wct-img-all.jsonnet)
    clusters-apa-anode<N>-ms-active.tar.gz
    clusters-apa-anode<N>-ms-masked.tar.gz
    (--bee: upload.zip → phy.bnl.gov/twister/bee → UUID printed)
    │
    │  woodpecker run-clustering
    ▼  (wire-cell wct-clustering.jsonnet → unzip.pl → zip-upload.sh)
    mabc-anode<N>.zip  →  upload.zip  →  bee URL
```

In the **pdvd workflow** only `woodpecker select` is adopted; imaging/clustering/bee use the
`pdvd/*.sh` scripts. See `pdvd/run_select_evt.sh` and the `-s` flag on `run_img_evt.sh` etc.
