# YourUni production upgrade execution plan

Inspected DEPLOYMENT.md, final-production-audit.json, Python/PowerShell entrypoints, SQLite schema and integrity, SSE revision ordering/reconciliation, canonical task IDs, selected weekday, fullscreen/fallback, themes, mobile CSS, tools and existing screenshots.

1. Preserve the Python HTTP server, SQLite, SSE, current HTML/Tailwind technology, theme preferences, logo/purple/gold identity and SYSTEM/Cloudflare watchdog architecture.
2. Timestamp and verify production source/database/config backup before modification. Implement in `.tools/upgrade/stage` against an isolated SQLite copy.
3. Add an independently auditable security helper: official Google verification, exact six-user database allowlist, hashed random sessions, CSRF/origin guards, timezone/deadline validation and safe media storage/serving.
4. Extend the existing client using `upgrade.js` and `upgrade.css`, retaining its canonical state and SSE reconciliation. Add staff state, full task dialog, media UI and adaptive timed TV pages. Remove the three management-only TV sections.
5. Apply project-local Impeccable harden/adapt guidance, inherited design-system context, and its mechanical detector. Validate at every requested TV/mobile size, both themes, semantic deadlines, failure states, signed token edge cases, media signatures, persistence and live changes.
6. Deploy verified files in place, restart only the application child through its existing watchdog, verify production readers and denied anonymous writes, then test app and tunnel recovery with retained browsers.
7. Update DEPLOYMENT.md and the honest consolidated audit. Google Cloud client configuration and real staff sign-in are human-only dependencies; isolated signed fixtures never count as real Google sign-in.

Initial backup: `.backup/security-media-tv-20260907-181300`. Original baseline remains untouched.
