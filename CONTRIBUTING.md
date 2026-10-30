# Contributing to Smart Display System

We welcome focused bug fixes, documentation improvements, tests and small features. For substantial changes to storage, authorization, synchronization or deployment, open an issue first to discuss the impact.

## Choosing a contribution

Keep each pull request focused on an observable improvement. Explain the problem, expected behavior, important edge cases and any compatibility impact. Search existing issues and [requirements](docs/REQUIREMENTS.md) first. Ideas without a complete implementation are welcome as feature requests.

Read the [architecture map](docs/architecture.mermaid) before changing shared paths. The map shows selected owners; it is not a complete import inventory.

## Development setup

1. Fork or clone this repository and create a branch from `main`.
2. Create a Python 3.11+ virtual environment.
3. Run `python -m pip install -r requirements.txt`.
4. Initialize an empty local instance with `python scripts/first_run.py --site-name "Development Display"`.
5. Run `python app.py --port 8787` and open `http://127.0.0.1:8787`.

Keep `.data/`, credentials, uploaded media and private organization information outside commits.

## Checks and verification

For Python changes, run:

```bash
python -m compileall -q app.py security.py scripts tests
python -m unittest discover -s tests -v
```

For frontend changes, check the actual UI in a browser at mobile and TV sizes, both themes, and with 0/1/many items. For auth/security changes, run a real valid/invalid account flow on a **test instance**, not somebody else's production service.

See [testing](docs/testing.md) for the verification scope and limitations. Report what ran, what passed and any unavailable tests. Do not claim CI or production validation that you have not observed.

## Pull requests

Describe the problem, implementation, relevant screenshots/API behavior, regression risks, test commands and outcomes. Contributors own the review of AI-assisted code and its claims. A draft PR is fine for early feedback. Never check real staff identities, secrets, private databases or user media into the repository.

## Security reports

Do not post sensitive vulnerabilities or credentials in public issues. Follow [SECURITY.md](SECURITY.md).
