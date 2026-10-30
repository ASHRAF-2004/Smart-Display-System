# Architecture

The [architecture.mermaid](architecture.mermaid) diagram shows selected components and data flows.

- `index.html`, `upgrade.js`, `upgrade.css` own browser presentation and editing.
- `app.py` owns HTTP routes, authoritative SQLite business data and revisioned SSE updates.
- `security.py` owns Google token validation, local allowlist, session cookies, CSRF, deadlines and media validation.
- `.data/smartdisplay.db` stores tasks, settings, authorization and media metadata.
- `.data/media/` stores validated uploaded media. The static server does not expose raw project paths.
- `scripts/first_run.py` creates local instance configuration and the first authorized Editor.
- The application binds to 127.0.0.1; exposing it externally requires a properly configured HTTPS proxy or tunnel.

There is **one global selected weekday** and no independent screen playlists. Theme preference remains local to each browser.

This is an architectural overview, not a complete import or dependency inventory.
