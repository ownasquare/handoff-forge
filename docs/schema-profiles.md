# Handoff schema profiles

## Invariant core

Every valid package contains these headings exactly once and in order:

1. Project Identity & Strategic Context
2. Current System State & Architecture Map
3. Critical Decisions & Reasoning History
4. Recent Work & Iteration Log (High Priority)
5. Testing, Validation & Quality Framework
6. Debugging History & Failure Modes
7. Established Processes & Effective Patterns
8. Risks, Technical Debt & Strict Preservation Rules
9. Key Artifacts & References
10. Next Steps & Prioritized Backlog
11. Continuation & Working Style Instructions
12. Confidence & Freshness Assessment

All required fields stay present. A missing fact is rendered as `Unknown`, `None known`, or
`Needs re-validation`; it is never invented or omitted. Section 12 contains one assessment for each
of Sections 1-11 and does not assess itself.

## `goal-v1`

This portable profile matches the user-facing 12-section request. It uses the plain values:

- `High - recently verified in this session`
- `Medium - solid but older`
- `Low - needs re-validation`

The parser also accepts the corresponding Unicode em-dash form and the historical unnumbered first
heading. Rendering always normalizes numbering and uses one stable syntax.

## `codex-precompact-v1`

This profile is an in-progress context snapshot, never a completion claim. It uses the filename
suffix `.precompact.handoff.mdc`, labels unfinished evidence explicitly, and preserves the plain
confidence syntax required by the pre-compact rule. Scheduled packages begin Section 10 with either
`Next run mode: CONTINUATION_REQUIRED` or `Next run mode: INVENTORY_REFRESH_REQUIRED`.

## `codex-post-chat-v1`

This profile contains MDC frontmatter with `description` and `alwaysApply: false`, followed by a
top-level `INVENTORY NEXT ITEMS` section and then the invariant 12-section core. Every inventory item
includes owner, issue, discovery evidence, exact repair location and state, detail, acceptance
criteria, definition of done, root cause, P0-P4 priority and rationale, regression prevention,
testing, audit policies, and adjacent considerations. Section 10 is seeded from the inventory.

Its confidence assessment uses:

- `✅ High - recently verified in this session`
- `⚠️ Medium - solid but older`
- `❓ Low - needs re-validation`

## Compatibility rules

- Wrappers are not counted as a thirteenth handoff section.
- Parsers retain unknown frontmatter keys but validators enforce required keys for the selected
  profile.
- A package rendered for one profile must be re-rendered, not text-replaced, to become another.
- Profile version and schema version are stored in the route/output manifest.
- Changes to exact headings, inventory fields, or confidence syntax require a new profile version.
