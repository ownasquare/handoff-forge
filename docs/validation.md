# Validation

Handoff Forge separates proof layers so one green result does not imply another.

| Layer | What it proves |
|---|---|
| Unit | Pure parsing, schema, confidence, merge, routing, path, and security behavior |
| Integration | Real local files, PDF rendering, Chroma persistence, restart, deletion, and complete offline workflows |
| Contract | Public package, CLI, profile snapshots, provider/harness registries, docs, and release boundaries |
| Browser | A real Streamlit process and Playwright workspace workflows at desktop, tablet, and mobile sizes |
| Package | Wheel and sdist build, fresh-environment install, entry point, and offline demo |
| Container | Image build, Tesseract presence, non-root startup, persistent volume, and browser smoke |
| Live provider | One explicitly enabled provider/model route with real account and usage evidence |
| Live destination | One explicitly enabled, genuinely new destination session with fixed-token readback |
| Codex hook configuration | Valid merged schema, exact ownership, reversible state, effective feature readback, and deduplicated fixture delivery |
| Codex hook runtime | A trusted hook delivered a real manual or automatic compaction event and produced a verified artifact |
| Hosted/production | External identity, data, network, scaling, monitoring, backup, and operations in that environment |

The default test suite excludes `live` and `e2e`. Browser E2E uses Playwright. Tests deny sockets for
the offline workflow. Screenshots are inspected after capture for errors, clipping, overflow,
readability, and state correctness.

The current 0.4.0 local release gate runs the complete offline suite with branch coverage plus four
rendered Playwright scenarios. A fresh wheel install completed doctor and the bundled demo; the
documented example extension generated a handoff; and a non-root Linux/arm64 container passed doctor
and UI health checks under a read-only filesystem with dropped capabilities. Public GitHub CI repeats
quality, security, package, browser, and Linux/macOS/Windows compatibility checks on every change to
`main` and every pull request. The tag workflow repeats those gates before building and checksumming
release assets; a release is proven separately only after that tag workflow and asset readback pass.
Codex hook runtime remains a separate environment-specific layer: this validation machine could
not run a nested Codex binary, so source-reviewed schema tests and local configuration tests do not
claim a real compaction delivery.

## Open Source Adoption release checks

The 0.4.0 browser and UI acceptance matrix covers the user-facing workflow separately from the
underlying parser, storage, and provider contracts:

- The first screen offers a private workspace path and a credential-free sample path.
- **Home** recommends Files when no files exist, Create handoff when files exist without an
  output, and Start session when a handoff is ready.
- **Files** supports local file addition and an immediate Create handoff action; search, readable
  content, and image/page evidence remain available under Review files, while hashes and extraction
  metadata stay under details.
- **Create handoff** exposes Save progress and Finish and hand off, keeps the local route ready by
  default, keeps Advanced processing closed initially, and blocks remote generation without
  explicit run-scoped consent.
- **Combine handoffs** accepts two or more managed handoffs and preserves source attribution, conflicts,
  constraints, and the unified plan.
- **Start session** validates the selected output, shows only installed destination apps, prepares
  a destination-app command, and states that the command must run in an interactive terminal.
- **Settings** keeps privacy, readiness, storage, and destructive controls out of the normal recipe.
- Desktop, tablet, and mobile proof checks meaningful content, navigation, keyboard focus, console
  and page errors, horizontal overflow, and accidental external requests.

```bash
uv run ruff format --check .
uv run ruff check .
uv run mypy src
uv run pytest --disable-socket -m "not live and not e2e" --cov=handoff_forge --cov-branch
uv run bandit -q -r src
uv run pip-audit
uv build
```

Validation records identify environment, scope, data integrity, mock/fixture use, production status,
localhost integrity, warnings, and any unperformed proof layer.
