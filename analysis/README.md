# Reproduction

The public notebook checks stored results and main study counts from released derived data. It does not repeat the original collection of publisher XML and historical service responses.

## Results check

From the repository root:

```sh
python scripts/quick_check.py
```

## Archived source replay

The complete analytical replay requires the separately retained source archive, which cannot be redistributed with the public repository. Archive holders can run:

```sh
python scripts/full_replay.py --archive /path/to/source-archive.zip --output ../review_replay
```

This checks archived source identities and regenerates the 65 retained results. It does not repeat live data collection.

## Figure production (local)

The figure source code and derived data are retained for independent reproduction. To run locally:

```sh
python -m pip install -r analysis/FigureProduction/Code/requirements.txt
python scripts/figure_check.py --output ../review_figures
```

See [third-party rights](../third-party.md) for details about the original source material.
