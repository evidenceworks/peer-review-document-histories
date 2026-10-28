#!/usr/bin/env python3
"""Rebuild the five figures and execute the independent numerical verification."""
import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
A = ROOT / 'analysis'

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args=parser.parse_args()
    destination=args.output.resolve()
    if destination.exists():parser.error('Output path must not already exist')
    if ROOT==destination or ROOT in destination.parents or destination in ROOT.parents:
        parser.error('Use an output folder outside the repository')
    commands=[
        [sys.executable,'-B',str(A/'FigureProduction/Code/build_figures.py'),'--output',str(destination)],
        [sys.executable,'-B',str(A/'FigureProduction/Code/verify_science.py'),'--figures',str(destination),'--report',str(destination/'verification.json')],
    ]
    for command in commands:subprocess.run(command,check=True)
    report=json.loads((destination/'verification.json').read_text())
    if report['status']!='PASS' or report['checks_passed']!=2008:
        raise AssertionError('Figure scientific verification did not pass the expected 2,008 checks')
    print('Five figures rebuilt; 2,008 scientific checks passed.')

if __name__=='__main__':main()
