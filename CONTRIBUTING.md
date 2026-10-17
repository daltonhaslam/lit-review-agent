# Contributing to lit-review-agent

Thanks for your interest in contributing. This project is a Claude Code skill
that performs academic-physician-grade literature reviews. Contributions
of all kinds — bug fixes, new features, documentation, tests — are welcome.

## Setup

1. Clone the repository and install dependencies (Python 3.10+ recommended; developed on 3.11):

   ```bash
   pip install -r requirements.txt
   ```

2. (Optional) Configure API keys if you plan to exercise live retrieval
   against PubMed, OpenAlex, CrossRef, Semantic Scholar, or Europe PMC. See
   `docs/api_guide.md` for details. Copy your keys into `config/api_keys.yaml`
   (gitignored) or set the corresponding environment variables — never commit
   credentials.

## Running Tests

Run the full test suite before opening a PR:

```bash
python3 -m pytest tests/ -q
```

The suite runs offline: `tests/conftest.py` blocks non-loopback network access,
so mock HTTP in new tests (see `tests/test_semantic_scholar_key_path.py` for an
`httpx.MockTransport` example). A test that genuinely needs the network must be
marked `@pytest.mark.network`; those are skipped unless
`LIT_REVIEW_NETWORK_TESTS=1` is set.

If you changed any API client code, also run the live connectivity check:

```bash
python3 tests/test_api_connectivity.py
```

## Deploying the Skill

The four skills are authored as `skills/{name}.md` (`lit-review`,
`lit-review-quick`, `lit-review-update`, `lit-review-package`) and deployed to
`~/.claude/skills/{name}/SKILL.md`. After editing a skill file, deploy with:

```bash
make deploy
```

## Opening a Pull Request

1. Fork the repository and create a topic branch off `main`.
2. Make your changes, keeping commits focused and well-described.
3. Ensure `python3 -m pytest tests/ -q` passes.
4. Open a pull request describing the change, why it's needed, and how you
   tested it. Link any related issues.

By contributing, you agree that your contributions will be licensed under the
project's MIT License (see `LICENSE`).
