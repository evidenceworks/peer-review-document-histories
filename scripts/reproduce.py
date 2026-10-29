#!/usr/bin/env python3
"""Recalculate document-history results from the released study data."""

import argparse
from pathlib import Path
from reproduce_results import run


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', default='outputs/document_history_results.zip',
                        help='Destination for the result archive')
    args = parser.parse_args()
    run(args.output)


if __name__ == '__main__':
    main()
