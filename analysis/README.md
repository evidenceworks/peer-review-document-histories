# Reproduction

The public analysis recalculates study measures from the released derived records. It requires Python 3.9 or newer and no third-party packages.

From the repository root:

```sh
python scripts/reproduce.py
```

The program creates `outputs/document_history_results.zip`, containing the calculated measures, numerical checks and a short summary.

The original publisher documents and historical service responses are retained separately. Researchers with access to those sources can reproduce the full analysis:

```sh
python scripts/full_replay.py --archive /path/to/source-archive.zip --output ../review_replay
```

See [third-party sources and rights](../third-party.md). Code and source tables for the article figures are retained in `FigureProduction/`.
