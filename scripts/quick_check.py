#!/usr/bin/env python3
"""Check the principal study results using the public derived records."""

import argparse
from reproduce_results import run


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', help='Optional ZIP of calculated results')
    args = parser.parse_args()
    run('Quick', args.output)


if __name__ == '__main__':
    main()
