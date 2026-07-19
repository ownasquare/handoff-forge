## Summary

Describe the user-visible continuity or safety improvement.

## Evidence

- Tests and audits run:
- Local/offline, mock, browser, container, live-provider, hosted, and production boundaries:
- Schema/profile compatibility impact:
- Screenshots inspected when UI changed:

## Safety checklist

- [ ] Uploaded content remains untrusted data.
- [ ] No remote provider became an implicit fallback.
- [ ] Provenance and Do Not Touch constraints survive transformations.
- [ ] External actions still use validated argv with `shell=False`.
- [ ] No credential or private document entered fixtures, logs, or docs.
