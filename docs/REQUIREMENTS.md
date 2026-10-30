# Requirements: implemented versus proposed

This document separates current application behavior from new requirements. Only capabilities in the first table are implemented in the application; later items are design proposals.

## Existing system requirements

| ID | Requirement | Verification |
|---|---|---|
| E01 | Editor can create, edit, delete and change tasks | Authenticated API/browser CRUD |
| E02 | Mutations persist immediately in SQLite | Restart and database read |
| E03 | Read-only viewers receive live SSE updates | Two browsers without refresh |
| E04 | Clients reconcile after disconnect/restart | Restart + compare versions/IDs |
| E05 | TV adapts to item count without manual scrolling | 0/1/4/9/many items |
| E06 | Overflow rotates automatically | Fullscreen paging |
| E07 | Images, GIFs and video up to 25 MiB | Upload/playback and invalid-file tests |
| E08 | Task deadline has future validation, overdue computation | Past/future/historical status |
| E09 | Google identity is checked by backend | Genuine approved/denied identity |
| E10 | SQLite allowlist controls Editor authorization | Approved/disabled/unlisted checks |
| E11 | Authenticated writes use Origin/CSRF protection | Same/cross-origin requests |
| E12 | Global selected weekday is shared | Two connected displays |
| E13 | Day/Night preference is browser-local | Two browsers use different themes |
| E14 | Responsive and fullscreen TV views | Mobile/tablet/TV screen sizes |
| E15 | Task visibility has optional start/end UTC windows | Future/active/expired items in TV |
| E16 | Instance timezone is configurable (IANA) | UTC and DST-zone setup, UI conversion |
| E17 | URL-selected screen channels | Assign task channel and use `?channel=...` on each screen |

## New requirements

### P0: General-purpose installation

| ID | Requirement | Definition of done |
|---|---|---|
| N01 | No organization branding | No original staff, office, domain, company mark in HEAD |
| N02 | Neutral default logo | Placeholder shown until an Editor uploads a logo |
| N03 | Empty database first-run | Fresh installation has no business tasks or staff |
| N04 | Configurable editor list and Google client | Local CLI bootstraps a verified account |
| N05 | Configurable public origin | No production hostname in source |
| N06 | Configurable displayed name | New title appears without tracked HTML edits |
| N07 | Portable run instructions | Python + SQLite only; no machine-specific dependency |
| N08 | Safe default reset behavior | Old demonstration reset does not delete real tasks |
| N09 | Honest documentation and testing | CI only claims tests it actually executes |

### P1: High-value signage features

| ID | Requirement | Definition of done |
|---|---|---|
| N10 | Recurring weekday rules | Mon–Fri time rule, occurrence-specific completion |
| N11 | Scheduled visibility windows and due dates | Implemented in E15; retain as a regression requirement |
| N12 | Screen-specific task channel | Implemented in E17; per-screen weekday scheduling remains future work |
| N13 | Draft/preview/publish | Draft items never appear on public screens |
| N14 | Configurable display parameters | Rotation seconds, language, density by screen |
| N15 | Archive/search/filter | History remains accessible and manageable |
| N16 | Timezone configuration | Implemented in E16; verify DST boundaries on a browser |
| N17 | Screenshot/visual regression coverage | Reproducible browser acceptance on PRs |

### P2: Optional advanced capabilities

| ID | Requirement | Definition of done |
|---|---|---|
| N20 | Offline cache | TV continues displaying last valid content during outage |
| N21 | Device heartbeat | Admin can see connected and stale devices |
| N22 | Media library/transcode | Reuse assets; handle unsupported codecs |
| N23 | Audit history/approval | Who published and when, per item |
| N24 | Backup/restore tooling | Verified DB and media restore |
| N25 | Multi-tenant organizations | Independent storage and roles |

## Explicit behavior boundaries

- **Deadline** is a completion due time, **not** an automatic display expiration.
- **Selected weekday** is global. **Channel** filters a screen's tasks but does not establish per-screen selected days or authorization.
- **Recurrence** is not implemented.
- **Viewer** is publicly readable; deployers are responsible for access restrictions if task content is private.
- **Time zone** is configurable per installation and defaults to UTC; DST-boundary browser testing is part of release verification.
- **Visibility scheduling and channels** control which items appear on the TV. Public `/api/state` may still include future item content, so scheduling is **not** a privacy boundary.
