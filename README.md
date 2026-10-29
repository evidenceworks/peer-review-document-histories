# Public peer review: document histories

Code and derived data for *Discovery and documentary completeness in public peer review*. The study examines the discovery of peer-review documents and the reconstruction of evaluation histories in the eLife publication record.

## Reproduce the results

- [Quick check in Google Colab](https://colab.research.google.com/github/evidenceworks/peer-review-document-histories/blob/main/notebooks/quick.ipynb): review-event coverage and principal document-history counts.
- [Core analysis in Google Colab](https://colab.research.google.com/github/evidenceworks/peer-review-document-histories/blob/main/notebooks/core.ipynb): document coverage, timestamp precision and independent comparisons.

Each notebook downloads a ZIP containing calculated results and a short summary. To run locally with Python 3.9 or newer:

```sh
python scripts/quick_check.py --output aOIR_Quick.zip
python scripts/core_check.py --output aOIR_Core.zip
```

## Files

`analysis/` contains the research code, derived records and 65 original result files. `scripts/` and `tests/` provide the public reproduction checks. Original source captures remain outside this repository; see [reproduction details](analysis/README.md) and [source rights](third-party.md).

## Licenses

Original code is [MIT licensed](LICENSE). Original documentation and author-generated derived data are available under [CC BY 4.0](licenses/CC-BY-4.0.txt). Third-party records retain their own terms.
