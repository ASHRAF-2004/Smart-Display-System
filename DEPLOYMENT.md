# thebekal.com production deployment

The security/media/TV upgrade is documented below. Its current acceptance status is `.logs\security-media-tv-upgrade.json`; isolated HTTP/browser evidence is `.logs\upgrade-staging\results.json`, production evidence is `.logs\upgrade-production\results.json`, and real Google acceptance evidence is `.logs\upgrade-real-google\results.json`. The older 2026-08-26 audit remains historical evidence, not certification of the new features.

## Security: Viewer and Editor

Public users are Viewers: no login is needed to read tasks, announcements, selected weekday, TV or live SSE updates. The public header and TV surface do not carry a persistent login strip. Selecting **Manager / HR Panel** while signed out opens a focused, localized staff-access screen explaining that an approved Google account is required and presents the official Google Identity Services button. The management workspace remains inert and hidden until the server confirms an Editor session. Once authenticated, that same view shows the Editor identity and Logout action without adding authentication clutter to TV Presentation Mode. Only approved Google staff may create/edit/delete, change status/deadlines/day, upload/remove media, import/reset tasks or change business settings. All mutation handlers enforce authorization before parsing or changing business data. Anonymous requests return 401; authenticated inactive/unapproved users return 403. Hiding controls is supplementary to backend enforcement.

The six seeded staff addresses are `ammar@your-uni.com`, `omar@your-uni.com`, `amin@your-uni.com`, `mustafa@your-uni.com`, `hisham@your-uni.com`, and `nurul@your-uni.com`. Emails are normalized to lowercase. `authorized_users` is the server-side authorization source of truth and stores department, role (`editor`), active state, first verified Google `sub`, and timestamps. Seed insertion is idempotent and never reactivates a disabled user. Final production authorization contains exactly these six active Editors. The temporary developer identity used for real Google acceptance was removed from `authorized_users` after successful testing, together with its sessions and `google_sub` binding; it was never hardcoded or seeded.

Google Identity Services requests identity only, with no Gmail, Drive, Calendar or Contacts permissions. The official `google-auth` Python library verifies Google's signature, issuer, client-ID audience and expiry; the backend additionally requires verified email, an active Editor database row and a one-use nonce tied to an HttpOnly login cookie. Google signing keys are fetched from Google's official verifier endpoint; there is no configurable certificate URL or production test bypass. Sign-in attempts are bounded.

Application sessions last seven days. A cryptographically random 48-byte token is placed in the `__Host-youruni_session` cookie with Path=/, Secure, HttpOnly and SameSite=Lax. Only its SHA-256 hash is stored in SQLite; the token never enters localStorage. The backend parses only its exact URL-safe cookie names so unrelated Google GIS cookies cannot mask a valid application session. Session validity and staff activity are checked on each write. Logout invalidates the database session and clears the cookie. Frontend session checks run at startup, focus and every minute.

All writes require an allowed same-origin Origin and a per-session `X-CSRF-Token`. The only browser origins admitted are `https://thebekal.com` and the server's own loopback HTTP port; loopback still requires the same valid Editor session. Cross-origin requests receive no permissive CORS headers. Authentication initiation also checks Origin. Secure cookies are intended for the public HTTPS site.

To configure Google: create a **Web application** OAuth client in the company's Google Cloud project, authorize JavaScript origin `https://thebekal.com`, and use only OpenID/email/profile identity scopes. The callback uses the GIS JavaScript credential response, so no application redirect callback is required. Configure the client ID locally:

```powershell
& '.\.tools\upgrade\configure-auth.ps1' -ClientId '<web-client-id>.apps.googleusercontent.com'
```

The protected `.data\auth-config.json` file holds the client ID. No client secret is required for this GIS ID-token flow. Configuration is read on demand; no restart is needed. Until configuration exists, login explains that setup is pending, and all business writes remain locked. The production GIS flow was accepted with a real, temporarily authorized developer Google identity, including credential verification, Editor session creation, secure cookie behavior and logout. That temporary authorization was removed after acceptance; only the six staff Editors remain.

Activate/deactivate an existing approved user from a trusted local administrator terminal using a parameterized SQLite update (set `active=0` to deactivate; `1` to reactivate). For example:

```powershell
& '.\.tools\python\python.exe' -c "import sqlite3,sys; c=sqlite3.connect('.data/thebekal.db'); c.execute('UPDATE authorized_users SET active=?,updated_at=CURRENT_TIMESTAMP WHERE email=?',(int(sys.argv[2]),sys.argv[1].lower())); c.commit(); c.close()" 'ammar@your-uni.com' 0
```

Disabling a user blocks existing sessions immediately on the backend. Delete that user's session rows when access is permanently removed; optionally clear `google_sub` only when the authorization row itself is being removed. Do not manually create production sessions. Task creation/update/media changes record the authenticated email in `created_by`/`updated_by`; deletion/reset actor emails are written to the private application log.

## Media

Each task has one primary upload. JPG/JPEG, PNG, WebP (including animation), GIF, MP4 and WebM are allowed, up to **25 MiB per file**. Image dimensions are limited to 40 million pixels. The task dialog offers a native mobile-compatible picker, drag/drop, preview, replacement/removal, file information, upload progress and recoverable errors. A failed media step retains the already-created task ID so retry does not duplicate the task.

Files use generated random names under `.data\media`. SQLite `task_media` stores the task relationship, original filename, MIME, byte size, SHA-256, actor and creation time. Extensions and MIME must agree; Pillow verifies actual image contents, MP4 box structure is checked, and WebM must contain its expected container markers. Executables, HTML, scripts, arbitrary SVG, traversal filenames and oversized data are rejected. The media directory is not directly served. Only exact opaque `/api/media/<id>` URLs resolve committed metadata; responses use the verified type and `nosniff`, sandbox policy, and byte-range support for video.

Media writes commit SQLite before the existing revisioned SSE broadcast. TV, phone and desktop update without refresh. Replaced/removed files are deleted only after their committed references are gone. A one-hour grace period covers abandoned in-flight uploads; startup cleanup removes only unreferenced generated media names. Referenced files are never orphan-cleanup targets.

The existing logo upload remains a validated raster data image setting for compatibility. That one server-backed setting drives the normal header, TV header, task media fallback and the no-task TV state; an SSE settings update refreshes already-open displays. Logos retain their aspect ratio and render without a decorative box or forced background. Arbitrary task image URLs are no longer accepted for new writes; legitimate existing images remain readable.

## Adaptive TV

TV shows active tasks with media/logo, title, Malaysia deadline, concise instructions and an Overdue label when useful. Completed task history and full descriptions remain in the normal task view. Compliance Directives, Supervisors On Duty and Daily Progress were removed from the TV surface; management functions remain available to Editors.

The existing frame/fullscreen surface uses a bounded grid. One item receives a large hero card; additional items use a balanced grid based on available width and height. At 1920×1080 fullscreen it fits up to nine cards; at smaller viewports it pages sooner to preserve readable text. Overflow uses automatic 15-second page rotation. Live changes clamp the page to the valid range, weekday changes reset it, and hidden page videos stop. Images preserve aspect ratio; video uses muted autoplay, inline playback, looping and metadata preload, with a media failure fallback. The TV content grid never requires manual scrolling.

Native fullscreen and CSS presentation fallback remain on `#view-tv`, with an Exit Full Screen control and the same SSE connection. TV never needs staff login. Staff controls and the normal footer are hidden only during presentation. The Day/Night theme stays device-local. The TV clock uses a distinct cream/gold Day treatment and dark/gold Night treatment so the clock visibly follows the selected theme while retaining high contrast.

## Tasks and Malaysia deadlines

Add/Edit uses a themed native dialog with prefilled fields, weekday, department, priority, status, full description, existing schedule note and primary media. Cancel preserves stored state; Save Changes commits immediately, disables duplicate submission, and retains form contents on error. There is no Save All button or batch-save handler.

Deadline is a semantic date picker plus time picker, explicitly labeled **Asia/Kuala_Lumpur (UTC+8)**. A schedule note is not a deadline input. Browser selections are converted using an explicit +08:00 offset and SQLite stores UTC ISO timestamps. Display uses an explicit Malaysia timezone regardless of the device timezone. Server time is authoritative; client checks use a refreshed server clock offset.

New active tasks require a future deadline. Changed deadlines on active tasks must be future. An unchanged overdue deadline can remain when editing another field; completed historical deadlines can remain past. Reopening a completed task with a missing/past deadline requires a future deadline. Backend rejection is HTTP 400 with `deadline_in_past` and a Malaysia-time message. Overdue is computed, not a rewritten status. Existing legacy records are preserved with nullable deadlines until edited; ordinary unrelated edits remain allowed.

The protected default-reset dialog requires an explicit future deadline and warns that it replaces current tasks/media. Bulk imports must supply `deadlineUtc` for active tasks; legacy CSV rows lacking a valid future deadline are rejected instead of silently bypassing validation.

## Upgrade backups, dependencies and tests

Before changes, timestamped source, SQLite-consistent database and protected configuration backups were created under `.backup`, including `.backup\security-media-tv-20260907-181300`. The final authorization/header pass is backed up at `.backup\final-cleanup-header-20260908-094126-484`. The original `.original.html` baseline is untouched. Future complete backups:

```powershell
& '.\.tools\python\python.exe' '.\.tools\upgrade\backup.py'
```

This holds a SQLite write reservation while backing up the database and referenced media, copies relevant application/configuration/tunnel credentials privately, verifies SQLite integrity and every media hash in the isolated backup, and writes a manifest. Backups include session records and must stay private. For a restore, stop the application task, preserve the current database/media, restore the matching database and media set, verify integrity and hashes, then restart the task. Never mix a database backup with an unrelated media snapshot.

The Python runtime, SQLite, SSE, SYSTEM watchdogs, loopback listener and Cloudflare Tunnel are retained. Runtime dependencies are pinned in `.tools\upgrade\requirements.txt` and installed project-locally under `.tools\upgrade\vendor`; no framework or database service was introduced. The main HTML remains the frontend entrypoint, with focused `/upgrade.js` and `/upgrade.css` assets served through exact allowlisted routes.

Run the new isolated final acceptance harness:

```powershell
& '.\.tools\python\python.exe' '.\.tools\upgrade\acceptance.py'
```

It operates exclusively on `.tools\upgrade\stage`, with real local HTTP, SQLite, real RS256 signature verification against ephemeral test certificates, independent Playwright clients, media security tests, all requested screen sizes and real isolated child-process restarts. The certificate transport substitution lives only in the harness process; it is never deployed or accepted by production. Test names begin `CODEX-UPGRADE-TEST-`; tasks, sessions and temporary media are cleaned. Screenshot evidence is `.logs\upgrade-staging`. The separate real-login production harness uses a genuine Google credential and never creates an auth bypass; its output is `.logs\upgrade-real-google`. The older acceptance harnesses predate write authentication and must not be used to attempt anonymous production mutations.

Project-local Impeccable was installed under `.tools\impeccable`; its clarify/polish/adapt guidance informed the contextual permission state, restrained visual hierarchy, text wrapping, theme-aware form/error states, semantic date/time controls, touch targets, previews and responsive layouts. Its detector output is `.logs\upgrade-staging\impeccable-detection.json`. The focused Manager-access evidence is under `.logs\manager-ux-polish`; final authorization cleanup evidence is under `.logs\final-cleanup-header`, and the rendered global-header/footer verification is under `.logs\final-visual-backup`.

## Historical implementation and infrastructure reference

The following sections document the established infrastructure and earlier acceptance. Where old UI behavior differs, the upgrade sections above are authoritative.

## Public application

- Public URL: `https://thebekal.com/`
- Frontend/root response: `youruni_tv_task_management_system update 17 Aug.html`
- Original visual baseline: `.backup\youruni_tv_task_management_system update 17 Aug.original.html`
- Local origin: `http://127.0.0.1:8787/` (loopback only)
- Server: `app.py`, launched by `server.ps1` with `.tools\python\python.exe`
- Database: `.data\thebekal.db`

The local server reads the frontend source on every request; there is no separate build output or duplicate frontend entry file.

## Persistence

SQLite is the sole authoritative task/business-data store. Save All does not exist and no manual save is required. Every create, edit, status change, delete, import, reset, and settings change is validated by the API and committed immediately in a SQLite transaction. Browser local storage is limited to presentation preferences such as language and theme.

The mutation order is:

`client mutation -> API validation -> SQLite transaction/commit -> committed response -> SSE broadcast`

No uncommitted state is broadcast.

## Global Selected Day

The selected operational weekday is global server state, separate from the visual Day/Night theme. It is stored as the `selectedDay` row in SQLite's `settings` table in `.data\thebekal.db`; it is never sourced from `localStorage`, `sessionStorage`, or a browser-only variable. The only valid values are the application's existing Saturday-through-Friday task days.

An actual user change in the Manager / HR selector sends one `PUT /api/selected-day` request. The server validates the value, serializes the transaction with other business mutations, commits the new setting and monotonic state revision, returns the committed value, and then publishes one `selected_day_changed` delta through the existing SSE stream. Programmatic selector synchronization does not dispatch another mutation.

`/api/state` includes `selectedDay`. Every page-start and reconnect reconciliation therefore applies the authoritative SQLite value before rendering day-specific task views. Already-open Manager/HR, mobile, desktop, TV Display, and TV Presentation Mode clients update in place without refresh; a disconnected TV converges through `/api/state` when its SSE connection returns. Simple last-committed-value behavior applies to concurrent valid changes.

The Day/Night visual theme remains an independent device-local preference under `youruni_theme`; changing the shared weekday does not change or synchronize theme preferences.

## Realtime

`/api/events` uses Server-Sent Events (SSE). The client has one intended EventSource and one canonical `Map<database ID, task>`. Filtering and day grouping are derived views only.

The realtime flow is:

`SQLite commit -> revisioned SSE event -> canonical ID-keyed client state -> UI`

The server serializes commit/revision/publication order. Task create/update events carry the committed record, delete events carry the stable database ID, and snapshots carry the current revision.

## Reconnection

On page start and every SSE `ready` event, the browser requests `/api/state`. During reconciliation it buffers incoming business events, replaces canonical state with the authoritative snapshot, applies buffered events in arrival order, renders once, and resumes live processing. Concurrent requests are coalesced into at most one follow-up reconciliation, so older responses cannot race over newer state.

A named heartbeat detects dead proxied streams. A low-rate heartbeat-triggered snapshot is also used because a Cloudflare Tunnel can replace its origin-side connection without closing the browser-facing stream. Reconnection never calls `location.reload()`.

## Duplicate protection

Create/update/delete handling is idempotent by SQLite database ID:

- Snapshot replacement collapses duplicate IDs.
- Repeated create/update delivery upserts one map entry.
- Repeated delete delivery is a safe no-op after the first removal.
- API-response application followed by the same SSE event cannot create another record.
- Each rendered business record has `data-task-id="<database-id>"`; one ID renders once in the active view.

## TV Presentation Mode

Open **TV Display View** and select **Full Screen**. The application first enables its always-available presentation layout on the `#view-tv` surface: the app header/navigation, unrelated views, and normal site footer are hidden; the TV surface fills `100vw` by `100dvh`; and page-level overflow is disabled. It then requests native browser fullscreen on that same TV element from the user's click. It never depends on F11 or attempts automatic fullscreen on page load.

If the browser supports and permits the Fullscreen API, browser chrome is also hidden. If the API is unavailable or rejects the request, the presentation layout remains active as a reliable fallback without showing an error. The in-presentation **Exit Full Screen** control calls `document.exitFullscreen()` when necessary and restores the normal application. A `fullscreenchange` listener also restores application state when the browser or TV exits fullscreen through its own controls.

Presentation mode does not create a new page, database, or realtime connection. The existing canonical task state and SSE connection continue in place. Production acceptance verified live create/update/status/delete while the TV was fullscreen, then killed only the SYSTEM application child: the watchdog replaced it, `/api/state` reconciliation completed, the same TV remained fullscreen, and the next mutation arrived live without refresh.

## Global theme

Day/Night mode is one application-wide UI state. The selected value is applied to `document.documentElement.dataset.theme`, mirrored to Tailwind's root `dark` class, and consumed by centralized light/dark color tokens plus the existing component theme classes. It covers the header/navigation, TV, control panel, sheets view, forms, inputs, buttons, task cards, responsive drawer/overlay, and footer.

The preference is stored only in browser `localStorage` under `youruni_theme`; it is not business data and does not synchronize between devices. A small script in the document head applies the saved value before the stylesheet loads to avoid a noticeable light-theme flash. The actual global `#app-header` uses shared theme tokens: Day mode has the explicit computed background `rgb(255, 253, 246)` with purple text, gold accents and a high-contrast purple active state, while Night mode preserves the established `rgb(56, 21, 101)` treatment. The mobile menu button and drawer follow the same tokens. Versioned upgrade asset URLs prevent an older Cloudflare/browser-cached stylesheet from masking a deployed theme correction. Focused production checks passed at 1440 x 900 and 390 x 844 in both themes, plus 320px and 768px overflow checks; the internal TV brand header remains purple and TV Presentation Mode still hides normal application chrome.

## TV and mobile

An open TV receives changes automatically without refresh, repairs missed state after an interruption, and continues receiving later live mutations. The canonical ID-keyed state and SSE recovery behavior are unchanged by presentation or responsive layout.

At widths through `768px`, desktop header controls become a focusable overlay drawer. It is collapsed by default, opened by a 44px-or-larger menu control, closes from its close button/backdrop/Escape/navigation selection, locks background scrolling, and fits within modern viewport/safe-area bounds. Desktop navigation remains unchanged above `768px`.

Mobile layouts use stacked TV information, responsive TV cards, single-column management sections, readable 16px form controls, full-width image/ticker controls, touch-sized status/delete actions, internally usable overlays, and a wrapping footer. Day and Night screenshots and programmatic overflow/form/action checks passed at `320 x 568`, `360 x 800`, `390 x 844`, `430 x 932`, and `768 x 1024`; document scroll width equaled client width in TV, management, and sheets views at every size.

## Attribution

The normal application ends with the centralized footer `#youruni-site-attribution` containing:

`© Copyright 2026 YourUni. All rights reserved. Developed by ASHRAF2004`

The YourUni/ASHRAF2004 footer attribution is an intentional UI requirement and should be retained exactly, including the leading `©` symbol and fixed year 2026. The source uses the HTML entity `&copy;` so the browser renders Unicode code point 169 without `Â` mojibake. It is protected against accidental removal by a nearby source maintenance comment, one stable centralized footer element, this deployment documentation, public/source acceptance assertions, and normal-page responsive screenshot coverage. It is theme-aware and wraps without overflow at 320px. It is hidden only while TV Presentation Mode is active so it does not distract from the television surface.

## Desktop visual verification

The untouched baseline `.backup\youruni_tv_task_management_system update 17 Aug.original.html` was served only on loopback by `.tools\visual-baseline\server.py`. Baseline and production were captured with the same Chrome Dev engine, device scale 1, zoom 1, locale, timezone, fonts, and viewport. The baseline Save All control was removed in the isolated comparison because that earlier feature was intentionally retired. Masks were limited to the live clock/date, dynamic database tasks/progress/ticker/logo content, the new Full Screen control, and the new normal-page footer. Stable UI pixels were compared with a small antialiasing tolerance.

| Viewport | Valid compared pixels | Masked pixels | Changed pixels | Changed % | Result |
|---|---:|---:|---:|---:|---|
| 1366 x 768 | 732,114 | 316,974 | 0 | 0.000000% | PASS |
| 1440 x 900 | 939,503 | 356,497 | 0 | 0.000000% | PASS |
| 1920 x 1080 | 1,600,048 | 473,552 | 0 | 0.000000% | PASS |

No stable compared pixel differed at any desktop size. Header, navigation, background, typography, color, cards, borders, shadows, buttons, table geometry, and component positions passed. UX artifacts are under `.tools\ux-acceptance\artifacts`.

## Database backup and restore

Create a SQLite-consistent backup while the application is running:

```powershell
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
& '.\.tools\python\python.exe' -c "import sqlite3,sys; src=sqlite3.connect('.data/thebekal.db'); dst=sqlite3.connect(sys.argv[1]); src.backup(dst); dst.close(); src.close()" ".backup\thebekal-$stamp.db"
```

To restore, stop only `thebekal-local-server`, retain a copy of the current database, replace only `.data\thebekal.db` with a verified backup, and start the task again. Never expose a backup through the web server.

## Cloudflare Tunnel

- Tunnel name: `thebekal-pc`
- Public hostname: `thebekal.com`
- Executable: `.tools\cloudflared\cloudflared.exe`
- Configuration: `.tools\cloudflared\config.yml`
- Log: `.logs\cloudflared.log`

Tunnel credentials remain private under `.tools\cloudflared\credentials\` and are not documented here.

## Startup and recovery

These hidden SYSTEM Scheduled Tasks run at Windows startup whether or not a user is logged on:

- `thebekal-local-server` -> `.tools\start-local-server.ps1` watchdog -> `server.ps1` -> `app.py`
- `thebekal-cloudflared` -> `.tools\start-cloudflared.ps1` watchdog -> `cloudflared.exe`

Each watchdog restarts its child after an unexpected exit. Final acceptance killed only each production child while retained browsers stayed open. Both replacement children returned under SYSTEM in Session 0, local/public HTTP returned 200, clients reconciled without reload or duplicates, and the next mutation propagated live.

Check task state:

```powershell
Get-ScheduledTask -TaskName 'thebekal-local-server','thebekal-cloudflared'
Get-ScheduledTaskInfo -TaskName 'thebekal-local-server'
Get-ScheduledTaskInfo -TaskName 'thebekal-cloudflared'
```

## Security posture

Final read-only verification found Reason Cybersecurity active, Microsoft Defender in passive mode under the third-party antivirus, all Windows Firewall profiles enabled, no exclusion covering this project, application listening only on `127.0.0.1:8787`, and no SQLite listener process. No security configuration was changed.

Public requests for `/.data/`, `/.tools/`, `/.logs/`, `/.backup/`, and `/DEPLOYMENT.md` returned 404.

## Final acceptance harness

Run from an Administrator PowerShell:

```powershell
& '.\.tools\final-acceptance\run.ps1'
```

The harness uses project-local Playwright/Pillow dependencies, three independent public browser contexts, direct SQLite assertions, process recovery checks, and loopback-only visual-baseline tooling. It writes machine-readable results to `.logs\final-acceptance\results.json` and logs to `.logs\final-acceptance\run.log`. Temporary records beginning `CODEX-` are removed in normal and emergency cleanup paths.

Run the production UX acceptance after presentation/theme/responsive/footer changes:

```powershell
& '.\.tools\ux-acceptance\run.ps1'
```

This project-local harness uses the existing Playwright/Pillow vendor tree and writes `.logs\ux-acceptance\results.json` plus `.logs\ux-acceptance\run.log`. It asserts native fullscreen and rejected-API fallback, both exit paths, global theme and refresh persistence, every required mobile viewport in both themes, exact attribution presence, live CRUD/SSE while fullscreen, SYSTEM app-child recovery without leaving presentation, desktop baseline preservation, SQLite integrity, public/local health, and security posture. Temporary records are named `CODEX-UX-TEST-*` and removed through the API in normal and emergency cleanup.

Run the global selected-day acceptance after changes to weekday state, SSE reconciliation, or the Manager / HR day selector:

```powershell
& '.\.tools\global-day-acceptance\run.ps1'
```

It uses three independent public contexts (desktop Manager/HR, 390px mobile Manager/HR, and retained 1920px TV), tests changes in both directions, native TV Presentation Mode, a clean profile, SYSTEM-watchdog application restart, TV-only SSE interruption/reconciliation, one-request/one-commit/one-broadcast loop protection, and task CRUD realtime regression. Results are written to `.logs\global-day-acceptance\results.json`; temporary `CODEX-GLOBAL-DAY-TEST-*` rows are removed in normal and emergency cleanup.

## Operational checks

```powershell
Invoke-WebRequest -UseBasicParsing http://127.0.0.1:8787/
Invoke-WebRequest -UseBasicParsing https://thebekal.com/
Get-Content '.logs\application-server.log' -Tail 50
Get-Content '.logs\cloudflared.log' -Tail 50
```

The host must remain powered on and awake for the public application to remain available.
