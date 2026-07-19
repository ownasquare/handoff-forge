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

## Custom profile

A custom profile may define fixed arguments and the allowed placeholders `{model}`,
`{handoff_path}`, `{prompt}`, and `{cwd}`. It cannot introduce a shell fragment, redirection,
pipeline, glob, environment mutation, or executable path from an uploaded document. Version 0.3.0
supports trusted installed harness packages through the explicit `handoff_forge.harnesses` entry
point and repeatable `--enable-extension NAME` allowlist. The UI shows an enabled profile only when
its executable is installed. Follow [Extending Handoff Forge](extending.md) and test the exact argv
through a fake executable before using a custom profile with a real harness.

Installed-CLI preview proves local discovery and command construction. It does not prove that a
provider account, model entitlement, or live session is available; those are separate checks.
The default Docker container does not inherit CLIs installed on its host, so use the source install
for automatic host-CLI discovery or download the handoff and start the destination manually.
