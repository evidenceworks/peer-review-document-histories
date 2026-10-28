#!/usr/bin/env python3
"""Reproduce archived analyses using a separately held original source package."""
import argparse
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
A=ROOT/'analysis'

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--archive', type=Path, required=True, help='Original complete scientific package (kept private)')
    parser.add_argument('--output', type=Path, required=True)
    args=parser.parse_args()
    original=args.archive.resolve()
    if not original.is_file():parser.error('Original source archive was not supplied')
    out=args.output.resolve()
    if out.exists():parser.error('Output folder already exists')
    if out==ROOT or ROOT in out.parents or out in ROOT.parents:
        parser.error('Output must be outside the source repository')
    with tempfile.TemporaryDirectory(prefix='peer-review-replay-') as temp:
        stage=Path(temp)/'research'
        shutil.copytree(A,stage)
        with zipfile.ZipFile(original) as archive:
            bad=archive.testzip()
            if bad:raise ValueError('Source archive CRC failure: '+bad)
            names=[info for info in archive.infolist() if not info.is_dir()]
            selected=[]
            seen=set()
            for info in names:
                path=info.filename.replace('\\','/')
                marker='/Repository/'
                if marker not in path:continue
                rel=path.split(marker,1)[1]
                if not (rel.startswith('Inputs/') or rel.startswith('Evidence/')):continue
                pp=Path(rel)
                if pp.is_absolute() or any(part in ('..','') for part in pp.parts) or rel in seen:
                    raise ValueError('Unsafe or duplicate source path: '+rel)
                seen.add(rel)
                selected.append((info,pp))
            if len(selected)!=2188:
                raise ValueError(f'Expected 2,188 archived input files; found {len(selected)}')
            for info,pp in selected:
                dest=stage/pp
                dest.parent.mkdir(parents=True,exist_ok=True)
                with archive.open(info) as input_stream, dest.open('wb') as output_stream:
                    shutil.copyfileobj(input_stream,output_stream)
        # reproduce.py validates every preserved original SHA-256 before any calculation.
        subprocess.run([sys.executable,'-B',str(stage/'reproduce.py'),'--root',str(stage),'--output',str(out)],check=True)
    print('Full archived-input replay completed. Results and receipt:',out)

if __name__=='__main__':main()
