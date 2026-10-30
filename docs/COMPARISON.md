# Digital signage landscape and product gaps

**Research checkpoint:** 30 October 2026. Comparisons use publicly documented feature categories, not side-by-side deployments or performance benchmarks. Feature availability and editions change. See the upstream links before citing vendor-specific details.

Smart Display System is a **small, self-hosted task and announcement board**. It is not intended to replace an enterprise signage CMS. Its advantages are an uncomplicated local stack, read-only screens, task deadlines, real-time SSE and a familiar browser admin workflow.

## Comparable systems

| Project | Typical focus | Where Smart Display System differs | Relevant next step |
|---|---|---|---|
| [Anthias](https://github.com/Screenly/Anthias) | Screen/player-oriented image, video and web signage | We focus on task cards, deadlines and live changes, not playlist-first playback | Offline player/cache, content types |
| [Concerto](https://github.com/concerto/concerto) | Screen templates, content feeds and moderation | We have URL screen channels but no feed moderation or approval workflow | Draft/preview/publish, content feeds |
| [Xibo](https://xibosignage.com/) | Full CMS, layouts, scheduling and device operations | We do not have proof-of-play, remote device monitoring or complex campaigns | Fleet health and playlists only if demand justifies |
| [ScreenTinker](https://github.com/screentinker/screentinker) | Self-hosted signage scheduling and multi-screen management | We have simple channel filtering rather than full device management | Device configuration and channel-specific schedules |

These references are for requirement analysis only. No external system's source code or branding was copied.

## Existing capabilities, verified in repository CI

| Capability | Current state | Evidence |
|---|---|---|
| Central task and announcement editing | Implemented | Python integration tests; existing app routes |
| Read-only viewer and protected Editor | Implemented | Anonymous mutation returns HTTP 401 in isolated tests |
| SQLite persistence and SSE | Implemented | Repository code and integration smoke coverage |
| TV adaptive layout, fullscreen and media | Implemented | Existing runtime code; not all devices/codecs verified in CI |
| Site name and uploaded logo | Implemented | First-run settings, neutral logo fallback |
| Media types | JPG/PNG/WebP/GIF/MP4/WebM, 25 MiB limit | Server-side validation source |
| Configurable IANA timezone | Implemented | CLI smoke and real browser configured-timezone check |
| Visibility start/end separate from deadline | Implemented | Schema tests and real Chromium scheduled/expired filtering |
| Distinct channels for different TVs | Implemented | `/?channel=general`, `/?channel=lobby` browser checks |
| Public display refresh | SSE-based | Runtime code; browser fallback/network faults remain release checks |
| Multi-theme responsive UI | Implemented | Chromium 390-pixel smoke; wider matrix is a manual release check |

A working feature is **not necessarily independently security-audited or verified on every platform**. The [release checklist](RELEASE_CHECKLIST.md) documents remaining validation.

## New requirements, ordered by practical value

| Priority | Gap | Definition of done | Status |
|---|---|---|---|
| P1 | Recurring daily/weekday tasks | Monday–Friday rules, individual date-specific completion and overdue state, no duplicate manual entry | Not implemented |
| P1 | Draft / preview / publish | Drafts cannot appear on public TVs or through unintended SSE payloads | Not implemented |
| P1 | Per-screen selected day / playlist | Each TV chooses and persists its own channel, day, rotation and items | Partially addressed by channels |
| P1 | Searchable task archive | Completed tasks remain retrievable with filters and history | Not implemented |
| P2 | Media scheduling policies | Dedicated media visibility and transitions independent of task completion | Partially addressed by task windows |
| P2 | Offline recovery | TV safely reuses last valid content after a temporary server/network outage | Not implemented |
| P2 | Screen monitoring | View online/offline and last heartbeat with privacy protections | Not implemented |
| P2 | Link/web and content templates | Safe configured assets and reusable layout presets | Not implemented |
| P3 | Media optimization | Optional codec checks, thumbnails, transcode when supported | Not implemented |
| P3 | Audit/proof of play | Historical publish/display events with retention controls | Not implemented |
| P3 | Multi-organization isolation | Separate tenant admin, secrets and data permissions | Not implemented |

## Product principles

1. **Accuracy over feature claims.** Distinguish implemented, tested, and proposed capabilities.
2. **Simple first installation.** A new user should be able to run Python, configure their Google identity, and see a blank screen with a logo placeholder.
3. **Separation of concepts.** Deadline, display visibility, recurring occurrence, selected weekday, and screen channel are different requirements.
4. **Safe defaults.** A cloned repository must not inherit real people, organizations, credentials, or business records.
5. **Keep the screen dependable.** Reconnection, persistent state and readability outweigh broad but unreliable integrations.

See [REQUIREMENTS.md](REQUIREMENTS.md) for feature-level acceptance criteria and [testing](testing.md) for verification lanes.

## References

- Anthias: https://github.com/Screenly/Anthias
- Concerto: https://github.com/concerto/concerto
- Xibo: https://xibosignage.com/
- ScreenTinker: https://github.com/screentinker/screentinker
