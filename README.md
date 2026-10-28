# Public peer review: discovery and document histories

Code and study data for *Discovery and documentary completeness in public peer review*. The study examines how review-related events can be discovered, matched to publisher documents and assembled into evaluation histories in the eLife publication record.

## Run

- [Quick: check the reported results in Google Colab](https://colab.research.google.com/github/evidenceworks/peer-review-document-histories/blob/main/notebooks/quick.ipynb)
- [Figures: rebuild and verify all five figures in Google Colab](https://colab.research.google.com/github/evidenceworks/peer-review-document-histories/blob/main/notebooks/figures.ipynb)

From a local clone, the Quick check requires Python 3.9 or newer and no additional packages:

```sh
python -B scripts/quick_check.py
```

To rebuild the figures, install the requirements in `analysis/FigureProduction/Code/requirements.txt` and Nimbus Roman (provided by `fonts-urw-base35` on Linux), then run:

```sh
python -B scripts/figure_check.py --output ../peer_review_figures
```

## Contents

`analysis/Results/` contains 65 retained result files. `analysis/FigureProduction/Data/` supplies source tables for the five figures. `analysis/Code/` and `analysis/ScientificAdditions/` contain the original analytical programs. `scripts/` provides verification commands and `notebooks/` contains the runnable Colab notebooks.

The public notebooks reproduce numerical checks and figures from the released derived records. Full offline replay of all 65 outputs also requires the original captured service responses and source projections, retained outside this public repository because not all third-party text has verified redistribution rights. Holders of that archive can run:

```sh
python -B scripts/full_replay.py --archive /path/to/source-archive.zip --output ../peer_review_full_replay
```

The full source audit examined 13,230 XML files and 517 historical captures. This release checks its preserved projections rather than repeating that initial extraction. Two historical identities and four endpoint rows remain unresolved.

## Rights and sources

Original code is [MIT-licensed](LICENSE); original documentation and derived tables are [CC BY 4.0](licenses/CC-BY-4.0.txt). Source metadata and third-party records are not relicensed. See [third-party data and rights](third-party.md) and the included source and result hash manifests.
