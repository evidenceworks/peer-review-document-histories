# Reproduction

The public notebooks recalculate the main study measures from released derived records. They require Python 3.9 or newer and no third-party Python packages.

Run locally from the repository root:

```sh
python scripts/quick_check.py --output aOIR_Quick.zip
python scripts/core_check.py --output aOIR_Core.zip
```

The complete source replay uses the separately retained archive of publisher documents and historical service responses:

```sh
python scripts/full_replay.py --archive /path/to/source-archive.zip --output ../review_replay
```

The source archive is not redistributed because reuse rights differ among providers. See [third-party sources and rights](../third-party.md).

Figure source code and data are retained in `FigureProduction/`. The figures can be generated locally using `scripts/figure_check.py` after installing the packages listed in `FigureProduction/Code/requirements.txt`.
