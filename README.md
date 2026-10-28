# Public peer-review document histories

Code and derived data for *Discovery and documentary completeness in public peer review*. The study examines the discovery and reconstruction of review documents in the eLife publication record.

## Reproduce

[Run the results check in Google Colab](https://colab.research.google.com/github/evidenceworks/peer-review-document-histories/blob/main/notebooks/quick.ipynb). [Rebuild the five figures in Google Colab](https://colab.research.google.com/github/evidenceworks/peer-review-document-histories/blob/main/notebooks/figures.ipynb).

The results check also runs locally with Python 3.9 or newer:

```sh
python scripts/quick_check.py
```

## Files

- `analysis/`: research code, derived data and 65 stored result files.
- `notebooks/`: browser-based checks.
- `scripts/` and `tests/`: local checks.
- `verification/`: test record.

The initial data collection is not included because some source documents have separate reuse restrictions. See [reproduction details](analysis/README.md) and [data sources and rights](third-party.md).

## License

Original code: [MIT](LICENSE). Original documentation and author-generated derived material: [CC BY 4.0](licenses/CC-BY-4.0.txt). Third-party records retain their own terms.
