# Geo verification

No live end-to-end draft, Slack delivery, website preview, deployment or schedule has passed yet. Record only checks actually run. See SETUP-PROGRESS.md for confirmed Basecamp and repository discovery. Local regression results will be appended after adaptation.

## Local baseline, October 4

`pytest -q`: 302 passed.
`ruff check geo_blog tools tests main.py`: passed.
`ruff format --check geo_blog tools tests main.py`: passed.
`mypy`: no issues in 51 source files.
`python -m tools.check_project`: passed, largest module content.py, 736 lines.
`main.py doctor`: incomplete configuration; provider keys, Slack app, queue and verified website integration pending.

These are local offline results, not evidence of a working live publishing pipeline.
