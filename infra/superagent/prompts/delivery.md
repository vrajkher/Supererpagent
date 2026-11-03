Commit author: the agent account. Every commit message ends with a blank line and `Co-authored-by` trailers only for credited human reviewers.
Before you report a PR ready, run `pytest -q` in the repository root with the `.venv` the prepare command created, and paste the summary line into the PR description. A red test is never "unrelated" until you have shown it fails on the base branch too.
Open every PR as a draft first. Use the repository's PR layout: Summary, Test plan (checked items you actually ran), and "Not verified here" for anything needing live Tally, a real sshd or Windows.
No merge CLI is configured: a person (vrajkher) merges every PR. Request review from vrajkher.
Never commit secrets, real client data, bank statements or Tally exports. Fixtures must be synthetic or anonymized and live under `fixtures/` or `tests/`.
