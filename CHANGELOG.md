# Changelog

All notable changes to Handoff Forge are recorded here.

## 0.4.0 - 2026-07-19

- Added opt-in Codex pre-compaction configuration with effective-feature gating, explicit `/hooks`
  trust review, layered verification, and ownership-safe disable/uninstall; unavailable runtimes
  fail before changing Codex hooks.
- Added an explicit post-task lifecycle command because a Codex turn stop is not reliable evidence
  that a task is complete.
- Added durable lifecycle receipts, deterministic generation jobs, idempotent output recovery, and
  verified hash/profile readback so retried events do not publish duplicate handoffs.
- Added bounded, opt-in live checks for one text-only cloud-provider canary and one genuinely new
  authenticated destination session, with sanitized proof output and no default network use.
- Clarified exactly what an opted-in cloud run uploads, kept visual evidence behind separate
  consent, and made source-checkout provider setup stay inside the documented `uv` environment.
- Expanded lifecycle, contributor, extension, Docker cleanup, and proof-boundary documentation for
  safer public adoption.
- Added checksum and GitHub artifact-attestation gates before immutable release publication, plus
  public verification instructions for both wheels and source archives.
- Pinned every CI and release action to a reviewed commit SHA while retaining version comments and
  Dependabot update coverage.
- Validated and merged the artifact-action, ReportLab, mypy, and Google Gen AI dependency updates;
  kept Chroma pinned after a mixed-version write test exposed a derived-index migration hazard.

## 0.3.0 - 2026-07-19

- Focused the primary workflow on Files, Create handoff, and Start session, with advanced controls kept one step away from the core task.
- Added complete Docker and source-install paths, a platform-status matrix, a two-minute sample tour, and symptom-based troubleshooting.
- Expanded contributor and extension guidance around architecture, validation, compatibility, documentation, and privacy boundaries.
- Added allowlisted provider and destination-app extensions, metadata-only extension discovery, a
  local example provider, and explicit offline/network policy enforcement for third-party adapters.
- Scoped generated-result, review, upload, merge, and launch-preview state to each workspace; invalid
  handoffs are blocked before any destination command can be prepared.
- Added privacy-safe bug and feature-request forms for public collaboration.
- Kept the default experience local and credential-free while preserving explicit per-run consent for remote processing.

## 0.2.0 - 2026-07-19

- Replaced the technical dashboard shell with a light, compact local workspace organized around
  Home, Sources, Create handoff, Combine, Continue, and Settings.
- Added a first-run choice between creating a private workspace and exploring a credential-free
  sample workspace.
- Added Home recommendations, recent files and handoffs, and quick actions for the normal local
  Sources to Create handoff to Continue recipe.
- Moved provider, exact model, visual-file, per-section routing, storage, validation-profile, and
  path details behind progressive Advanced or Details surfaces.
- Kept the offline route ready by default and retained explicit run-scoped consent before any
  selected content can be sent to a remote provider.
- Added human-readable source browsing, automatic output validation, destination-app selection,
  and a focused new-session preparation flow.
- Preserved the truthful terminal boundary: the browser prepares a copyable command, while an
  interactive terminal owns the new harness process and its real exit status.

## 0.1.0 - 2026-07-19

- Added secure Markdown, MDC, and multimodal PDF ingestion.
- Added canonical artifacts, local LlamaIndex chunking, and persistent Chroma retrieval.
- Added exact goal, pre-compact, and post-chat handoff profiles.
- Added per-section offline and opt-in cloud model routing.
- Added deterministic multi-handoff merge planning with conflict preservation.
- Added safe Codex, Claude, Gemini, Grok, clipboard, and directory action adapters.
- Added Streamlit, CLI, packaging, tests, security gates, and operator documentation.
