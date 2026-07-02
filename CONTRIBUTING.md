# Contributing to lit-review-agent

Thanks for your interest in contributing. This project is a Claude Code skill
that performs academic-physician-grade literature reviews. Contributions
of all kinds — bug fixes, new features, documentation, tests — are welcome.

## Setup

1. Clone the repository and install dependencies:

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

If you changed any API client code, also run the connectivity check:

```bash
python3 tests/test_api_connectivity.py
```

## Deploying the Skill

The skill is authored at `skills/lit-review.md` and deployed to
`~/.claude/skills/lit-review/SKILL.md`. After editing the skill file, deploy it
with:

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
