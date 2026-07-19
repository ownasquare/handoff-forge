# Troubleshooting

Start with the diagnostic check:

```bash
uv run --no-dev --frozen handoff-forge doctor
```

For Docker, review the visible `docker compose up` output. Diagnostic reports contain readiness state, not credential values or source-document content.

| Symptom | Likely cause | What to do |
|---|---|---|
| `uv: command not found` | `uv` is not installed or is not on `PATH` | Follow the [official uv installation guide](https://docs.astral.sh/uv/getting-started/installation/), reopen the terminal, and run `uv --version`. |
| Python version is rejected | Handoff Forge requires Python 3.11–3.13 | Install a supported Python version or use Docker. |
| Port `8517` is already in use | Another process is using the default port | Start with another port, such as `uv run --no-dev --frozen handoff-forge ui --port 8518`, then open that address. |
| The browser does not open automatically | The local server cannot control your browser | Open the exact `http://127.0.0.1:<port>` address shown in the terminal. Do not expose the port to an untrusted network. |
| OCR is unavailable | Tesseract or the requested language pack is missing | Install Tesseract for your platform or use Docker. Native PDF text still works; scanned pages remain preserved as images. |
| A workspace cannot be created or saved | The data directory is not writable | Run `doctor`, then choose a writable private directory with `HANDOFF_FORGE_DATA_ROOT`. Do not point it at a shared or public folder. |
| Local search or Chroma initialization fails | The local index is unavailable or stale | Stop other writers, run `doctor`, and rebuild the affected workspace index from its preserved source files. Do not delete originals first. |
| A destination app is unavailable | Its CLI is not installed or is not on `PATH` | Install and authenticate that destination CLI, reopen the terminal, then prepare the session again. Downloading the handoff still works. |
| A host destination app is missing in Docker | Containers do not inherit host-installed CLIs | Download the checked handoff, or use the source install so Handoff Forge and the destination CLI share the same `PATH`. |
| A remote provider is disabled or unconfigured | Network access, its optional SDK, or its key is missing | Keep using the complete offline route, or follow [Model providers](providers.md). Never paste a key into an issue or screenshot. |
| A remote run remains blocked | Per-run upload consent was not granted | Review the selected content and provider, then grant consent only if you intend to send that content for this run. |
| Docker starts but data is missing after recreation | A different project name or volume was used | Run `docker compose ls` and `docker volume ls`. Avoid `docker compose down --volumes` unless permanent deletion is intended. |

## Request help safely

If the problem remains, read [Support](../SUPPORT.md) and open the matching issue form. Use synthetic files and sanitized diagnostics. Never attach API keys, private handoffs, raw application data, or unreviewed logs.
