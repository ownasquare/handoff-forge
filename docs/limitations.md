# Limitations and proof boundaries

- The default Files to Create handoff to Start session recipe is local and credential-free. Opening
  Advanced processing does not enable network access or authorize an upload. Remote processing
  still requires an enabled network-capable run, a configured provider, a selected remote route,
  and explicit consent for the current run.
- Local OCR depends on an external Tesseract executable and installed language packs.
- Local and Voyage retrieval indexes text only. Visual blocks are retrievable through Markdown alt
  text or filename descriptions and bounded same-page PDF text/table/OCR context; neither index
  interprets image pixels. A visual with no useful alt text, native page text, table text, or OCR may
  therefore be preserved but rank poorly for a meaning-based query.
- Vision-capable generation adapters receive selected project-managed image crops or page renders
  only after containment, cloud-upload consent, adapter capability checks, and an explicit operator
  attestation that the exact selected model/version accepts image input. Visual-file inclusion is off
  by default. The application does not maintain a live model capability registry, so the attestation
  still requires provider-specific calibration. Adapters do not upload native PDFs and do not use
  provider document-search or file-search attachment routes.
- Heuristic contradiction detection surfaces likely conflicts for review; it is not a formal logic
  solver.
- Provider model names, availability, pricing, context limits, and beta file APIs change. Exact live
  support requires current capability preflight and provider-specific calibration. The editable
  workbench model identifiers are documented starting points checked on 2026-07-19, not a live
  capability registry or account-entitlement claim.
- The opt-in provider canary proves only one fixed, text-only response from one selected account and
  model. It does not prove visual input, full twelve-section generation, provider uptime, production
  latency, quota, or another account's entitlement.
- A prepared new-session command does not prove destination-app installation, account
  authentication, or model entitlement. The browser cannot safely own an interactive terminal
  process, so the command must be run in a real terminal; CLI execution remains attached until the
  new session exits. The default Docker container cannot discover destination CLIs installed only
  on its host; downloading the checked handoff remains available.
- A destination CLI's authentication-status command is only a local preflight. The actual fixed-token
  readback is authoritative because stored login state can still lack product access or model
  entitlement. On the 2026-07-19 validation machine, Claude login preflight passed but the one
  attempted new-session call returned sanitized `authentication_error`; authenticated destination
  proof therefore remains unestablished until the account has eligible Claude Code access.
- Browser clipboard behavior may be restricted by browser permissions. The raw path and file URI
  remain visibly copyable.
- Version 0.4.0 uses one intentionally light visual theme so every shipped state has consistent
  contrast. It does not currently follow the operating system's dark-mode preference.
- Local Chroma persistence is appropriate for one serialized writer, not multi-user server scale.
- Chroma `0.6.3` remains deliberately pinned. A tested 1.5.9 in-place migration exposed an unsafe
  rollback-write-forward path, so a 1.x update requires a versioned derived-index migration and
  restart/readback recovery contract rather than a dependency-only bump.
- Streamlit Session State is a presentation cache, not durable job authority.
- Automatic pre-compaction depends on a separately configured, user-reviewed, and trusted Codex
  hook. The hook generates from evidence already stored in the selected Handoff Forge project; it
  does not capture the Codex conversation or rescan the bound workspace. Configuration readback and
  effective feature state do not prove trust or delivery. Only a verified artifact from a real
  compaction establishes runtime proof; manual pre-compact generation remains portable across
  harnesses. Codex permits a `null` transcript path; Handoff Forge safely skips that delivery
  because it cannot distinguish a retry from a later compaction window without a stable transcript
  revision or event ID.
- The 0.4.0 lifecycle release adds local automation and opt-in live-check harnesses, not a hosted or
  provider proof claim. The project has no hosted, multi-tenant, live-provider-calibration,
  authenticated-destination, or production claim unless a later environment-specific validation
  record supplies that evidence.
