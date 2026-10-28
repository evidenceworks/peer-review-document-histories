# Reproduction

The public notebooks check retained results and regenerate the figures from the released derived data. They do not repeat the original collection of publisher XML and historical service responses.

## Local use

From the repository root, run the results check:

```sh
python scripts/quick_check.py
```

To rebuild the figures, install the plotting dependencies and then run:

```sh
python -m pip install -r analysis/FigureProduction/Code/requirements.txt
python scripts/figure_check.py --output ../review_figures
```

The figure code uses an available serif font, preferring Liberation Serif. It does not require downloading a font. The output folder must not exist before running the command.

## Original-source replay

The complete analytical replay requires the separately retained source archive, which is not redistributed here because it includes third-party documents. Archive holders can run:

```sh
python scripts/full_replay.py --archive /path/to/source-archive.zip --output ../review_replay
```

The archived-input replay verifies source-file hashes and regenerates the 65 stored results. It does not repeat the original live data collection. See [third-party rights](../third-party.md) for the source restrictions.
