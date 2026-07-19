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
  support requires current capability preflight and provider-specific calibration.
- A prepared new-session command does not prove destination-app installation, account
  authentication, or model entitlement. The browser cannot safely own an interactive terminal
  process, so the command must be run in a real terminal; CLI execution remains attached until the
  new session exits. The default Docker container cannot discover destination CLIs installed only
  on its host; downloading the checked handoff remains available.
- Browser clipboard behavior may be restricted by browser permissions. The raw path and file URI
  remain visibly copyable.
- Version 0.3.0 uses one intentionally light visual theme so every shipped state has consistent
  contrast. It does not currently follow the operating system's dark-mode preference.
- Local Chroma persistence is appropriate for one serialized writer, not multi-user server scale.
- Streamlit Session State is a presentation cache, not durable job authority.
- Automatic pre-compaction depends on a separately installed, tested harness hook. Manual
  pre-compact generation is portable across harnesses.
- The 0.3.0 adoption release changes presentation, packaging, documentation, and supported extension
  paths, not the deployment or provider proof boundary. The project has no hosted, multi-tenant,
  live-provider-calibration, or production claim unless a later environment-specific validation
  record supplies that evidence.
