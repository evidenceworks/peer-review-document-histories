#!/usr/bin/env python3
"""Recalculate document coverage, temporal precision and comparator results."""

import argparse
from reproduce_results import run


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', help='Optional ZIP of calculated results')
    args = parser.parse_args()
    run('Core', args.output)


if __name__ == '__main__':
    main()
