# Getting started

This guide takes you from a clean computer to your first local handoff. No account, API key, or remote model is required.

## Choose an install path

### Docker — recommended for the quickest start

Docker includes Python and English OCR support.

```bash
git clone https://github.com/ownasquare/handoff-forge.git
cd handoff-forge
docker compose up --build
```

Open `http://127.0.0.1:8517`. Press `Ctrl+C` to stop the app. `docker compose down` removes the
container but preserves your named data volume. To irreversibly remove that volume and all Handoff
Forge data stored in it, run `docker compose down --volumes`.

Docker does not automatically see Codex, Claude, Gemini, or Grok installed on the host. You can
still complete the sample and download every checked handoff. Use the source install if you want
Handoff Forge to discover those local CLIs and prepare their launch commands.

### Source install

Install:

- Python 3.11, 3.12, or 3.13
- [uv](https://docs.astral.sh/uv/getting-started/installation/)
- Tesseract only if you need text recognition for scanned PDF pages

Then run:

```bash
git clone https://github.com/ownasquare/handoff-forge.git
cd handoff-forge
uv sync --no-dev --frozen
uv run --no-dev --frozen handoff-forge doctor
uv run --no-dev --frozen handoff-forge ui --port 8517
```

Tesseract is optional. Without it, Handoff Forge keeps each PDF page image and shows an OCR warning instead of discarding the page.

## Platform status

| Platform | Current path | Status |
|---|---|---|
| Linux | Source or Docker | Hosted CI covers Python 3.11–3.13; local container, doctor, UI, and browser proof is also available |
| macOS | Source or Docker Desktop | Hosted Python 3.13 unit, contract, doctor, and demo smoke complements local source, wheel, and browser proof |
| Windows 11 | Docker Desktop recommended | Hosted Python 3.13 unit, contract, doctor, and demo smoke covers the native core; Docker Desktop remains the simplest full path |

GitHub Actions repeats the compatibility matrix on every change to `main` and every pull request.
Windows users who want the same bundled Tesseract and browser environment used by the container proof
should choose Docker Desktop.

## Create your first handoff

Open `http://127.0.0.1:8517`, then choose **Explore sample workspace** for a credential-free walkthrough or create your own workspace.

1. Open **Files** and add Markdown, handoff (`.mdc`), or PDF files. Originals remain unchanged.
2. Open **Create handoff**. Choose **Save progress** for unfinished work or **Finish and hand off** for completed work, then create the handoff.
3. Open **Start session** and download the checked handoff.
4. Optional: if a supported destination CLI is installed in the same environment, select it and
   choose **Show launch command**. Review and copy that command into a real terminal.

The browser prepares the command but does not launch a detached coding session. A real terminal must own that interactive process and its exit status.

**Combine** is optional. Use it only when two or more handoffs need one continuation plan with conflicts and constraints preserved.

## Try the command line

From a source install:

```bash
uv run --no-dev --frozen handoff-forge demo
uv run --no-dev --frozen handoff-forge project list
uv run --no-dev --frozen handoff-forge --help
```

The demo creates synthetic local source files and a validated handoff. Follow the [two-minute sample guide](../examples/README.md) to inspect the result.

## Data and privacy

The default data directory is the operating system's private application-data location. Set `HANDOFF_FORGE_DATA_ROOT` to use a dedicated directory; `HANDOFF_FORGE_DATA_DIR` remains accepted for compatibility.

Remote providers are optional and disabled by default. Installing a provider does not enable it, and selecting one does not grant upload permission. Read [Security and privacy](security.md) and [Model providers](providers.md) before turning on network access.

## If something goes wrong

Run `uv run --no-dev --frozen handoff-forge doctor` for a source install or check the container output for Docker. The [troubleshooting guide](troubleshooting.md) covers the most common setup and runtime problems.
