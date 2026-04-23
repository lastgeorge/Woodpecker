> For full option descriptions see [../README.md](../README.md).

# Woodpecker WCT wrappers — `run-img`, `run-clustering`, `run-sim-check`

These three subcommands are thin argparse wrappers that construct and execute
`wire-cell` command lines. They are documented here for reference, but the **pdvd
workflow does not use them** — the `pdvd/*.sh` scripts are better-featured (padded
run dirs, 8-anode PDVD defaults, magnify path, per-event logs, fallback search paths).

## Why we don't use these in pdvd

| Woodpecker wrapper | pdvd equivalent | Why the script wins |
|---|---|---|
| `run-img` | `run_img_evt.sh` | 8-anode default, padded `work/` layout, per-anode log, WIRECELL_PATH setup |
| `run-clustering` | `run_clus_evt.sh` | same + correct SUBRUN/EVENT TLAs |
| `run-sim-check` | (not needed) | — |
| `--bee` tail in `run-img` | `run_bee_img_evt.sh` | PDVD-specific bee python script |

## `woodpecker run-img`

**Source:** `woodpecker/cli/cmd_run_img.py`

Wire-cell command built:
```
wire-cell -l stdout -L <level> \
    --tla-str  input_prefix='<datadir>/<prefix>' \
    --tla-str  output_dir='<datadir>' \
    --tla-code anode_indices='[N,...]' \
    -c <wct-img-all.jsonnet>
```

Anode detection: globs `<datadir>/*.tar.bz2`, regex `^(.+)-anode(\d+)\.tar\.bz2$`.
Jsonnet search: walks up to 5 parents from CWD looking for `wcp-porting-img/pdvd/wct-img-all.jsonnet`.
`WIRECELL_PATH` augmented with `toolkit/cfg` and `dunereco/DUNEWireCell/protodunevd` via `--wct-base`.

`--bee` tail: globs cluster tarballs → `wct-img-2-bee-combined.py` → `upload-to-bee.sh` → BNL bee URL.

Key flags: `--datadir`, `--jsonnet`, `--anode-indices`, `--prefix`, `--wct-base`, `--dry-run`, `--log-level`.

## `woodpecker run-clustering`

**Source:** `woodpecker/cli/cmd_run_clustering.py`

Three sequential steps:
1. `wire-cell ... --tla-str input=<input_dir> --tla-code anode_indices=[...] -c wct-clustering.jsonnet`
2. `perl <tools>/unzip.pl` in `<datadir>` (unzips `mabc*.zip` into `data/`, wipes `data/*` first)
3. `sh <tools>/zip-upload.sh` → `zip -r upload data` → `upload-to-bee.sh upload.zip` → bee URL

Jsonnet search: `_resolve_script_dir` walks up from CWD for `wcp-porting-img/pdvd/wct-clustering.jsonnet`.

Key flags: `--datadir`, `--input`, `--anode-indices`, `--no-unzip`, `--no-upload`, `--dry-run`, `--wct-base`.

## `woodpecker run-sim-check`

Shells out to `wire-cell -c wct-sim-check-track.jsonnet`. Used for simulation validation.
Not relevant to data processing in pdvd.
