# Security and privacy

## Threat model

Uploaded documents are untrusted. They may contain prompt injection, misleading extensions,
malformed containers, traversal names, remote assets, oversized content, hostile markup, or text
that resembles a command or credential. Handoff Forge treats every extracted instruction as quoted
evidence and never promotes it into application control text.

## Controls

- Extension checks are case-insensitive and paired with content signatures.
- Upload, page, character, render, OCR, and provider limits are below upstream maxima.
- Names are display metadata; content-addressed generated paths own storage identity.
- Every read, write, open, copy, and launch target is resolved and checked against its managed root.
- Symlinks and traversal escapes are rejected.
- Manifests and outputs are written atomically under a per-project writer lock.
- Data directories use owner-only permissions where the platform supports them.
- Remote Markdown images are never fetched implicitly.
- Provider uploads require network enablement and per-run consent.
- Chroma telemetry is disabled and Chroma remains derived, deletable state.
- Uploaded Markdown is displayed as text through safe Streamlit components; arbitrary HTML is not
  rendered.
- Harness actions use argument vectors with `shell=False`.
- Codex hook setup preserves unrelated handlers and never writes its own trust state; the operator
  reviews the exact command through Codex `/hooks` before it can run.
- Pre-compaction receipt identity hashes a regular, non-symlink transcript revision without storing
  the transcript path or contents.
- Known secret formats are redacted from errors, manifests, route records, logs, and handoffs.

## Retention and deletion

Data stays local until the operator deletes it or explicitly enables a remote provider. Project
deletion removes originals, derived page images, parsed manifests, outputs, jobs, and scoped vectors,
then reads back the relevant locations and index counts. Backups contain private source material and
must be protected accordingly.

## Verify a downloaded release

Each GitHub release includes the wheel, source archive, and `SHA256SUMS`. Verify the downloaded
files before installation:

```bash
sha256sum --check SHA256SUMS
```

On macOS, use `shasum -a 256 --check SHA256SUMS`. Then verify that GitHub recorded build
provenance from this repository's protected release workflow:

```bash
gh attestation verify handoff_forge-*.whl \
  --repo ownasquare/handoff-forge \
  --signer-workflow ownasquare/handoff-forge/.github/workflows/release.yml
```

Repeat the attestation check for the downloaded `.tar.gz` source archive. A checksum mismatch or
an attestation from a different workflow is a stop condition; do not install that file.

With a current GitHub CLI, also verify that GitHub published an immutable release attestation and
that each local file is one of its protected assets:

```bash
gh release verify v0.4.0 --repo ownasquare/handoff-forge
gh release verify-asset v0.4.0 handoff_forge-0.4.0-py3-none-any.whl \
  --repo ownasquare/handoff-forge
```

## Not provided by the local beta

The local single-user mode does not provide multi-tenant authorization, managed encryption keys,
remote audit retention, malware scanning, sandboxed native parser processes, or a production SLA.
Do not expose the Streamlit port to an untrusted network.
