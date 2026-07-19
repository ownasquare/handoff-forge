# Local notes provider example

This package demonstrates the smallest supported Handoff Forge provider extension. It is
deterministic, credential-free, and never opens a network connection.

From a clean Handoff Forge source checkout, run this exact sequence. `uv sync` creates the local
environment, and `--no-sync` keeps the editable example installed for the remaining commands:

```bash
uv sync --frozen
uv pip install --editable examples/extensions/local-notes-provider
uv run --no-sync handoff-forge --data-root .data/local-notes-quickstart --enable-extension local-notes doctor
uv run --no-sync handoff-forge --data-root .data/local-notes-quickstart project create "Plugin demo"
uv run --no-sync handoff-forge --data-root .data/local-notes-quickstart ingest README.md --project plugin-demo
uv run --no-sync handoff-forge --data-root .data/local-notes-quickstart --enable-extension local-notes generate \
  --project plugin-demo \
  --provider local-notes \
  --model deterministic-v1 \
  --mode pre-compact
uv run --no-sync handoff-forge --data-root .data/local-notes-quickstart outputs --project plugin-demo
```

The final command lists the generated handoff. Delete `.data/local-notes-quickstart` when you no
longer need the disposable example workspace.

The entry point is declared in `pyproject.toml` under `handoff_forge.providers`. Installing the
package only makes its metadata discoverable. Handoff Forge imports and runs it only when the
operator passes `--enable-extension local-notes`.

Installed extensions are trusted local Python code and run with the same filesystem permissions as
Handoff Forge. Review an extension before enabling it.
