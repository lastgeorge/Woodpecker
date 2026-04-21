#!/usr/bin/env python
# Convert ms-active and ms-masked cluster tarballs to bee format.
# ms-active blobs go into data/0/, ms-masked blobs go into data/1/.
#
# Accepts any number of active and masked files (not limited to 8 anodes).
# The anode index is parsed from the filename (clusters-apa-anode<N>-ms-*.tar.gz).
#
# Usage:
#   python wct-img-2-bee-combined.py \
#     [--active clusters-apa-anode0-ms-active.tar.gz ...] \
#     [--masked clusters-apa-anode0-ms-masked.tar.gz ...]
#
#   Or positional (all active first, then all masked), with --split N:
#   python wct-img-2-bee-combined.py --split 4 \
#     a0-active.tar.gz a1-active.tar.gz a2-active.tar.gz a3-active.tar.gz \
#     a0-masked.tar.gz a1-masked.tar.gz a2-masked.tar.gz a3-masked.tar.gz

import argparse
import os
import re
import sys

# anode index -> (speed, x0) for ProtoDUNE-VD
_ANODE_PARAMS = {
    0: ('-1.56*mm/us', '-341.5*cm'),
    1: ('-1.56*mm/us', '-341.5*cm'),
    2: ('-1.56*mm/us', '-341.5*cm'),
    3: ('-1.56*mm/us', '-341.5*cm'),
    4: ( '1.56*mm/us',  '341.5*cm'),
    5: ( '1.56*mm/us',  '341.5*cm'),
    6: ( '1.56*mm/us',  '341.5*cm'),
    7: ( '1.56*mm/us',  '341.5*cm'),
}
_DEFAULT_PARAMS = ('-1.56*mm/us', '-341.5*cm')

_ANODE_RE = re.compile(r'anode(\d+)', re.IGNORECASE)


def _anode_idx(filepath):
    """Extract anode index from filename, e.g. clusters-apa-anode3-ms-active.tar.gz -> 3."""
    m = _ANODE_RE.search(os.path.basename(filepath))
    if m:
        return int(m.group(1))
    raise ValueError(f"Cannot parse anode index from filename: {filepath}")


def bee_blobs(tarball, outfile, anode_idx, density=1):
    speed, x0 = _ANODE_PARAMS.get(anode_idx, _DEFAULT_PARAMS)
    cmd = ('wirecell-img bee-blobs -g protodunevd -s uniform -d %f'
           ' --speed "%s" --t0 "0*us" --x0 "%s"'
           ' -o %s %s') % (density, speed, x0, outfile, tarball)
    print(cmd)
    os.system(cmd)


def main(active_files, masked_files):
    if os.path.exists('data/0'):
        print('found old data, removing ...')
        os.system('rm -rf data')
    if os.path.exists('upload.zip'):
        os.system('rm -f upload.zip')
    os.system('mkdir -p data/0')

    for fp in active_files:
        idx = _anode_idx(fp)
        bee_blobs(fp, 'data/0/0-apa%d-active.json' % idx, idx)

    for fp in masked_files:
        idx = _anode_idx(fp)
        bee_blobs(fp, 'data/0/0-apa%d-masked.json' % idx, idx)

    os.system('zip -r upload data')


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Convert WCT imaging cluster tarballs to bee display format")
    parser.add_argument(
        '--active', nargs='+', metavar='FILE', default=[],
        help='ms-active cluster tar.gz files (any number of anodes)')
    parser.add_argument(
        '--masked', nargs='+', metavar='FILE', default=[],
        help='ms-masked cluster tar.gz files (any number of anodes)')
    parser.add_argument(
        '--split', type=int, default=None, metavar='N',
        help='Split positional args: first N are active, rest are masked')
    parser.add_argument(
        'files', nargs='*', metavar='FILE',
        help='Positional files (use --split N to divide active/masked)')
    args = parser.parse_args()

    active_files = list(args.active)
    masked_files = list(args.masked)

    if args.files:
        if args.split is not None:
            active_files += args.files[:args.split]
            masked_files += args.files[args.split:]
        else:
            # Guess by filename
            for f in args.files:
                if 'masked' in f:
                    masked_files.append(f)
                else:
                    active_files.append(f)

    if not active_files and not masked_files:
        parser.print_help()
        sys.exit(1)

    main(active_files, masked_files)
