# Operations

## Diagnostics

Run `handoff-forge doctor`. The report includes package and Python versions, writable data-root
status, Tesseract availability, Chroma initialization, installed harness executables, provider
adapter state, and offline/network policy. It never prints environment values.

## Backup and restore

Stop active writers, then copy the complete data root. Originals and canonical manifests are the
authority. After restore, run diagnostics and rebuild the affected project index; do not copy only a
Chroma directory and call the project restored.

## Index rebuild

Use the project rebuild command after an embedding, parser, or node-serializer upgrade. The new
collection fingerprint prevents incompatible vectors from mixing. Keep the canonical project data
until the rebuilt collection passes count and retrieval readback.

Chroma `0.6.3` is intentionally pinned. Do not replace it with Chroma 1.x against an existing data
root or treat a downgrade as a safe rollback. Migration testing found that an older runtime can
write to a 1.x-touched index and leave the next 1.x open unreadable. A future upgrade must use a new
versioned derived-index directory, preserve the complete pre-upgrade data root, rebuild from
canonical project data, and pass restart/readback before the old index is retired.

## Deletion

Delete only an explicit generated project ID. Handoff Forge rejects broad roots and unresolved
paths, deletes scoped vectors and project files, and verifies that both are absent. Backups and files
copied outside the managed root are not deleted automatically.

## OCR

Install Tesseract plus the selected language packs or use the supplied container. A missing engine,
unsupported language, or timeout leaves an actionable warning and the page render. It does not
invalidate successfully parsed pages.

## Recovery

Generation jobs checkpoint inventory and completed sections. Correct the provider or route issue,
then resume the same job. Cancel takes effect at a section boundary. A failed or cancelled job is not
published as a valid final handoff.

## Logs and telemetry

The application writes bounded local operational metadata only. Product telemetry is off by
default. Sanitized model request identifiers and usage may be retained with a generation job; source
content and credentials are not copied into diagnostic output.
