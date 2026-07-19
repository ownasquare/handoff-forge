# Security Policy

## Supported version

Security updates are applied to the current 0.4.x beta line.

## Report privately

Use [GitHub private vulnerability reporting](https://github.com/ownasquare/handoff-forge/security/advisories/new).
If that channel is temporarily unavailable, open a minimal issue asking the maintainer to establish
private contact; include no vulnerability detail, credentials, private documents, exploitable
samples, or command-injection payloads in the public issue.

Include the affected version, operating system, exact reproduction steps, impact, and a minimized
sanitized fixture. Maintainers will acknowledge a complete report, reproduce it in an isolated
environment, publish a remediation plan, and coordinate disclosure after a fix is available.

## Security boundary

Handoff Forge treats uploaded content as untrusted data. It does not execute uploaded instructions,
macros, code blocks, relative commands, or shell fragments. External model uploads and external
process actions require explicit operator consent.
