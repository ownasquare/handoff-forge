# Extending Handoff Forge

Handoff Forge supports installed provider and harness extensions through explicit Python entry
points. Installation makes extension metadata discoverable; it does not import or run extension
code. An operator must allowlist each trusted extension by name when starting Handoff Forge.

Installed extensions are local Python code with the same permissions as Handoff Forge. Review the
package and its dependencies before enabling it. The allowlist is a deliberate code-trust boundary,
not a sandbox.

## Try the local provider example

From a clean source checkout, run this exact sequence. `uv sync` creates the local environment, and
`--no-sync` keeps the editable example installed for the remaining commands:

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

The last command lists the generated handoff. The example is deterministic, uses no credentials,
and makes no network requests. Repeat
`--enable-extension NAME` to allow more than one extension. The same option works with `ui`; the
allowlist is passed to the attached Streamlit process and shown by `doctor`.

## Provider extension contract

Declare one entry point in the extension package:

```toml
[project.entry-points."handoff_forge.providers"]
my-provider = "my_package.provider:create_provider"
```

The named object must be a callable with this exact safe context:

```python
from pathlib import Path

from handoff_forge.config import HandoffSettings


def create_provider(*, settings: HandoffSettings, managed_root: Path) -> MyProvider:
    return MyProvider(settings=settings, managed_root=managed_root)
```

The returned provider defines `name`, `is_remote`, `capabilities`, `status()`, and `generate()`.
Use `ProviderCapabilities`, `ProviderStatus`, `GenerationRequest`, `GenerationResult`, and
`validate_capabilities` from `handoff_forge.extensions`. Keep optional SDK imports lazy. Remote
providers must report `is_remote = True`; the registry will continue to require network enablement
and explicit run-scoped cloud-upload consent.

Factories receive no credential values. A provider SDK may read its documented environment variable
only after the operator enables both the extension and network use.

## Harness extension contract

Declare the harness entry point:

```toml
[project.entry-points."handoff_forge.harnesses"]
review-cli = "my_package.harness:create_harness"
```

Return a `CustomHarnessProfile` or another compatible profile:

```python
from pathlib import Path

from handoff_forge.config import HandoffSettings
from handoff_forge.harnesses.base import CustomHarnessProfile


def create_harness(
    *,
    settings: HandoffSettings,
    managed_root: Path,
) -> CustomHarnessProfile:
    del settings, managed_root
    return CustomHarnessProfile(
        name="review",
        executable_candidates=("review-cli",),
        arguments=("--handoff", "{handoff_path}", "--model", "{model}"),
    )
```

Handoff Forge displays only harness profiles whose executable is currently installed. Launches stay
preview-first, use a structured argument vector with `shell=False`, and reject resume flags or paths
outside managed storage.

## Failure behavior

- An empty allowlist may inspect supported entry-point metadata for the CLI and Settings list, but
  performs no extension import or factory call.
- An unknown or ambiguous extension name fails before any candidate is imported.
- Import and factory failures report only the extension name and exception type.
- A factory returning an incompatible object fails before registration.
- Provider or harness names cannot replace a built-in registration.

## Parsers and handoff profiles

Out-of-tree parser and handoff-profile plugins are intentionally unsupported in this release.
Adding a file type changes upload signatures, artifact kinds, parser limits, storage behavior, and
security tests. Add it in-tree through `security.py`, `models.py`, `parsing/`, and ingestion contract
tests.

The ordered 12-section handoff core is a versioned compatibility boundary. Add a new profile version
in `handoffs/profiles.py` and `handoffs/validator.py`, then add parser/renderer snapshots and a
changelog compatibility note. Do not mutate an existing profile in place.

## Test an extension

An extension should prove:

1. Installation exposes only its declared entry point.
2. Import has no network or credential side effect.
3. The factory accepts only `settings` and `managed_root`.
4. Offline provider output is deterministic, or remote behavior is covered by fakes with separately
   opted-in live calibration.
5. Provider errors are sanitized and harness commands remain structured argument vectors.

Use `examples/extensions/local-notes-provider/tests/test_plugin.py` as the smallest runnable test.
