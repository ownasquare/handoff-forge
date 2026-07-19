# Contributing

Thank you for improving Handoff Forge. Favor clear workflows, local-first behavior, preserved evidence, and plain language.

## Start here

1. Fork and clone the repository.
2. Install Python 3.11–3.13 and [uv](https://docs.astral.sh/uv/).
3. Run `uv sync --frozen` to install the application and development tools.
4. Create a focused branch and include tests or documentation for user-visible changes.

Useful maps:

- [Architecture](docs/architecture.md) explains storage, parsing, retrieval, generation, and merge boundaries.
- [Extending Handoff Forge](docs/extending.md) covers custom providers and destination-app profiles.
- [Schema profiles](docs/schema-profiles.md) defines compatibility-sensitive handoff formats.
- [Security and privacy](docs/security.md) defines the non-negotiable safety boundary.

## Validate a change

```bash
uv run ruff format .
uv run ruff check .
uv run mypy src
uv run pytest -m "not live and not e2e"
```

Add unit tests for pure behavior, integration tests for persistence or search, contract tests for public guarantees, and Playwright tests for rendered workflows. Live-provider checks must remain separately marked and explicitly opted in.

For the broad local code-and-package check, run `make validate`. It covers formatting, linting,
typing, offline application tests, security scans, package building, and the public-repository
boundary. Browser E2E, live-provider, container, and hosted-CI checks remain separate proof layers.
When UI behavior changes, inspect the rendered desktop and mobile workflows and update the relevant
screenshot.

## Compatibility-sensitive changes

Changes to section headings, confidence labels, profile wrappers, provider capabilities, storage schemas, or destination-app argument templates require:

- a compatibility note in `CHANGELOG.md`;
- focused contract tests;
- a migration or explicit breaking-change explanation when existing data is affected.

## Documentation style

Lead with the user's task and keep advanced detail one link away. Prefer short labels and inline help for unfamiliar terms. Avoid exposing internal implementation detail in the core Files → Create handoff → Start session workflow.

## Protect user privacy

Never commit real private documents, provider responses, credentials, machine-local paths, or application-data directories. Use synthetic fixtures and sanitized diagnostic output. Remote providers must never become an implicit fallback.

Open a privacy-safe issue before a large or compatibility-sensitive change. Pull requests should explain user impact, tests run, proof boundaries, and any schema or security effect.
