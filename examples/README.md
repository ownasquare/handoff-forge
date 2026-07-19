# Two-minute sample

The sample workspace demonstrates the complete local workflow with synthetic project material. It needs no account, provider key, or network connection.

## Browser tour

1. Start Handoff Forge using the [Docker or source instructions](../docs/getting-started.md).
2. On the first screen, choose **Explore sample workspace**.
3. Open **Files** and review the two Markdown/MDC handoffs and the PDF continuity review.
4. Open **Create handoff**, choose **Save progress**, and create the handoff using the ready local option.
5. Confirm that the result is marked **Checked**, then download the handoff if you want to inspect
   or share the file.

That is the complete sample outcome; no destination command-line app is required. If Codex,
Claude, Gemini, or Grok is installed in the environment running Handoff Forge, you can optionally
open **Start session**, select that app, and choose **Show launch command**. A Docker container does
not see host-installed CLIs by default. Handoff Forge prepares a reviewable command but does not run
it for you.

## Command-line tour

From a source install:

```bash
uv run --no-dev --frozen handoff-forge demo
uv run --no-dev --frozen handoff-forge project list
```

The command prints the sample workspace, ingested files, generated handoff, and validation result. Use `uv run --no-dev --frozen handoff-forge outputs --project SAMPLE_WORKSPACE_ID` to list its saved handoffs.

## Included files

- `handoffs/project-alpha.mdc` and `handoffs/project-beta.mdc` model two partially overlapping continuation records.
- `northstar-continuity-review.pdf` adds page text and visual evidence.
- All names, facts, and project details are synthetic and safe to inspect locally.

The sample is designed to teach the core flow, not to prove live-provider access or destination-app authentication.
