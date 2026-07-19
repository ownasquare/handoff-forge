# Harness integrations

Handoff Forge prepares a validated output for a genuinely new CLI session. Built-in profiles cover
Codex, Claude, Gemini, and Grok; a custom profile can supply a fixed executable and argument template.

## Safety contract

- The handoff path must exist inside the configured managed output root.
- The model identifier uses a conservative validated character set.
- An adapter returns an argument list, never a shell command string.
- Terminal execution uses `shell=False`, stays attached to the calling terminal, reports the real
  exit status, and never adds a resume/continue flag.
- Preview is the default. The operator must explicitly request execution.
- Logs record the harness name, executable basename, output ID, and exit/session state, not
  credential values or uploaded content.

## Actions

`launch` previews or invokes a new harness session. Execution is available from an interactive
terminal through `handoff-forge launch ... --execute`; it remains attached until the harness exits.
The Streamlit workbench shows both the exact harness argv and a copyable Handoff Forge terminal
command, but does not detach a CLI from a browser server and pretend it has an interactive terminal.
`copy-path` offers both the raw absolute path and the canonical percent-encoded `file://` URI. `open`
reveals the file or containing directory through the operating system's argument-vector API.
Headless systems return the copyable path and an actionable message instead of reporting a false
success.

## Optional Codex snapshots

Codex pre-compaction snapshots are opt-in and require a source install. First configure one Handoff
Forge project for the current workspace:

```console
uv run --no-dev --frozen handoff-forge lifecycle codex install --project PROJECT
```

Setup reads the effective `hooks` state from `codex features list` before changing anything. When
that state is enabled, it adds one valid declaration to `~/.codex/hooks.json` and preserves
unrelated events and handlers. It does not grant trust to its own command. Start Codex in the bound
workspace, enter `/hooks`, inspect the exact Handoff Forge command and workspace, and trust it only
if they are correct. Do not use the trust-bypass flag as a permanent setup shortcut.

Then check the configuration and exercise one real compaction:

```console
uv run --no-dev --frozen handoff-forge lifecycle codex verify BINDING_ID
# In Codex, run /compact and confirm that Handoff Forge reports a verified output.
```

`verify` separates local declaration, binding state, and effective feature state. File presence and
feature enablement do not prove Codex trust or delivery; the verified output from an actual
compaction is the runtime proof. If the feature row is disabled, missing, or unreadable, setup exits
before changing the hook file. Updating the installed command can change its trust hash, so review
it again through `/hooks` after reinstalling.

The hook generates from evidence already ingested into the selected Handoff Forge workspace. It
does not read the Codex transcript into Handoff Forge or rescan changed project files. Codex also
permits a compaction delivery with no transcript path; that delivery is skipped because no safe
revision identity is available for deduplication. Codex does not expose a trustworthy automatic
task-completion event, so create the completion snapshot explicitly when the task is truly finished:

```console
uv run --no-dev --frozen handoff-forge lifecycle run --project PROJECT --event post-task
```

Rollback remains explicit and ownership-scoped:

```console
uv run --no-dev --frozen handoff-forge lifecycle codex disable BINDING_ID
uv run --no-dev --frozen handoff-forge lifecycle codex uninstall BINDING_ID
```

Disable keeps the declaration but makes deliveries no-ops. Uninstall removes only the exact
Handoff Forge-owned handler and its local binding; unrelated hook groups are preserved.

## Custom profile

A custom profile may define fixed arguments and the allowed placeholders `{model}`,
`{handoff_path}`, `{prompt}`, and `{cwd}`. It cannot introduce a shell fragment, redirection,
pipeline, glob, environment mutation, or executable path from an uploaded document. Version 0.3.0
and later support trusted installed harness packages through the explicit `handoff_forge.harnesses` entry
point and repeatable `--enable-extension NAME` allowlist. The UI shows an enabled profile only when
its executable is installed. Follow [Extending Handoff Forge](extending.md) and test the exact argv
through a fake executable before using a custom profile with a real harness.

Installed-CLI preview proves local discovery and command construction. It does not prove that a
provider account, model entitlement, or live session is available; those are separate checks.
The default Docker container does not inherit CLIs installed on its host, so use the source install
for automatic host-CLI discovery or download the handoff and start the destination manually.

## Optional maintainer live check

Maintainers can opt in to one real Claude Code new-session check. Set
`HANDOFF_FORGE_LIVE_DESTINATION=claude`, optionally set an exact
`HANDOFF_FORGE_LIVE_DESTINATION_MODEL`, then run:

```console
uv run --frozen pytest -s -m live tests/live/test_destination_session.py
```

The call may consume subscription or API usage and remains outside normal CI. A passing check proves
that this machine and account opened one genuinely new session and read back a fixed token from a
temporary validated handoff. It does not prove another account's access, every model, provider
uptime, or production readiness. Sanitized diagnostics exclude the token, prompt, handoff path,
session identifiers, CLI output, and account details.

On the 2026-07-19 validation machine, login preflight reported an authenticated state, but the
actual call returned the sanitized category `authentication_error`. The account lacked eligible
Claude Code access, so authenticated destination proof remains unestablished. Login status alone is
not a successful live session; rerun the usage-bearing check only after account eligibility changes.
