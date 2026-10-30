# Testing and verification

Testing is necessary for this application because authentication, SQLite migrations, media validation, real-time updates and full-screen display behavior can break in different ways.

## Fast local checks

```bash
python -m compileall -q app.py security.py scripts tests
python -m unittest discover -s tests -v
```

These cover Python syntax and isolated smoke behavior. Passing them **does not** certify real Google sign-in, browser layout, tunnel operation or production security.

## Manual / browser integration checks

Use an isolated test instance and test data, not a live organization's records.

| Lane | Verify |
|---|---|
| Viewer | GET root/state works; anonymous POST/PATCH/DELETE is denied |
| Identity | Approved Google account can edit; unapproved account stays viewer |
| Task management | Create, edit, complete, delete; records survive restart |
| Media | JPG/PNG/GIF/WebP and MP4/WebM; rejected invalid types, size limits, playback |
| SSE | Two browser clients update without refresh; reconnect after restart |
| TV | Zero, one, four, nine and many tasks; no manual scrolling; automatic rotation |
| Responsive | 320, 390, 768, 1366 and 1920 px; both themes; no horizontal overflow |
| Deadlines | Past blocked, future accepted, overdue computed; current Malaysia timezone |
| Hardening | Protected files never served; loopback binding; Origin/CSRF enforced |
| Recovery | Database integrity, stop/restart and re-read consistent state |

Run each affected lane when changing its behavior and record unavailable environments explicitly.

## SQLite integrity

For the local instance, stop writes before manual inspection and run:

```bash
python -c "import sqlite3; c=sqlite3.connect('.data/smartdisplay.db'); print(c.execute('PRAGMA integrity_check').fetchone()[0])"
```

Expected output: `ok`.

## Production verification

Production HTTPS, Google OAuth origin registration, cookie security and reverse-proxy behavior require a real deployment. This repository does not claim they are proven by the small smoke suite alone. Keep test identities and media temporary and clean them afterward.
