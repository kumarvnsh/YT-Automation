"""Persist the daily Shorts renderer toggle in the GitHub repository."""
from __future__ import annotations

import argparse
import subprocess


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=('on', 'off'), help='on: 3D; off: cinematic 2D illustrations')
    parser.add_argument('--repo', help='GitHub OWNER/REPO (defaults to this checkout)')
    args = parser.parse_args(argv)
    command = ['gh', 'variable', 'set', 'HISTOLD_3D', '--body', args.mode]
    if args.repo:
        command.extend(['--repo', args.repo])
    subprocess.run(command, check=True)
    print(f'Histold 3D {args.mode}: applies to newly created daily/manual GitHub runs.')


if __name__ == '__main__':
    main()
