#!/usr/bin/env python3
"""Regenerate the publication figures from the released exact source data."""
import argparse
from pathlib import Path
import subprocess
import sys

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--repository', type=Path, default=Path(__file__).resolve().parents[1])
    p.add_argument('--output-dir', type=Path, required=True)
    a=p.parse_args()
    repo=a.repository.resolve()
    program=repo/'FigureProduction/Code/build_figures.py'
    if not program.is_file():
        p.error('FigureProduction/Code/build_figures.py is missing')
    out=a.output_dir.resolve()
    if out==repo or repo in out.parents or out in repo.parents:
        p.error('Select a new output folder outside the source repository')
    if out.exists():
        p.error('Output folder must not exist')
    subprocess.run([sys.executable,'-B',str(program),'--output',str(out)],check=True)

if __name__=='__main__': main()
