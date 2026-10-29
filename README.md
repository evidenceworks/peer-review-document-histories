# Public peer review: document histories

Code and derived data for *Discovery and documentary completeness in public peer review*. The study examines how review documents can be discovered and matched to publisher records in the eLife publication history.

## Reproduce the results

[Run the analysis in Google Colab](https://colab.research.google.com/github/evidenceworks/peer-review-document-histories/blob/main/notebooks/reproduce.ipynb)

The notebook recalculates review-event coverage, document reconstruction, timestamp-precision sensitivity and version comparisons. It downloads `document_history_results.zip` with the calculated results and numerical checks.

For a local run with Python 3.9 or newer:

```sh
python scripts/reproduce.py
```

## Contents

`analysis/` contains the research code, derived data and 65 retained result files. `scripts/` and `tests/` contain the reproduction program and tests. The original publisher documents and historical service responses are held separately. See [reproduction details](analysis/README.md) and [source rights](third-party.md).

## Licenses

Original code is [MIT licensed](LICENSE). Original documentation and author-generated derived data are [CC BY 4.0 licensed](licenses/CC-BY-4.0.txt). Third-party records retain their original terms.
