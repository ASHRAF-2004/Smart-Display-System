# Architecture

The [architecture.mermaid](architecture.mermaid) diagram shows selected components and data flows.

- `index.html`, `upgrade.js`, `upgrade.css` own browser presentation and editing.
- `app.py` owns HTTP routes, authoritative SQLite business data and revisioned SSE updates.
- `security.py` owns Google token validation, local allowlist, session cookies, CSRF, deadlines and media validation.
- `.data/smartdisplay.db` stores tasks, settings, authorization and media metadata.
- `.data/media/` stores validated uploaded media. The static server does not expose raw project paths.
- `scripts/first_run.py` creates private site/identity configuration, including IANA timezone, and optionally authorizes an Editor.
- A task's optional visibility start/end timestamps are stored separately from its completion deadline. The client filters TV visibility without erasing database history.
- The application binds to 127.0.0.1; exposing it externally requires a properly configured HTTPS proxy or tunnel.

There is **one global selected weekday**. Each TV can select its own task channel using a URL parameter. Full independent playlists and per-screen weekday scheduling remain unimplemented. Theme preference remains local to each browser.

This is an architectural overview, not a complete import or dependency inventory.
