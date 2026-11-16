# Public peer review document histories

Data and code for *Discovery and documentary completeness in public peer review*.

[Run in Google Colab](https://colab.research.google.com/github/evidenceworks/peer-review-document-histories/blob/main/notebooks/reproduce.ipynb)

## Reproduce

In Colab, select **Runtime → Run all**. The notebook retrieves the current public GitHub revision, runs the analysis and tests, and downloads `document_history_results.zip` with four numerical files: `statistics.json`, `family_outcomes.csv`, `step_coverage.csv` and `conditional_estimates.csv`.

For a local run with Python 3.9 or newer, open a terminal in the repository directory:

```sh
python scripts/reproduce.py
python -m unittest discover -s tests -v
```

## Data

The [data dictionary](data/dictionary.txt) defines the eLife, BMC and EMBO records, selections and counting units. To regenerate text fingerprints from separately retained EEB response bodies:

```sh
python scripts/extract_text_identity.py --source-dir /path/to/response_bodies --output text_identity.csv
```

Original code is MIT licensed. Author-generated analytical data and documentation are CC BY 4.0. Original source rights are acknowledged in [third-party.md](third-party.md).
