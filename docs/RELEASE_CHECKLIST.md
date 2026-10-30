# Release checklist

Complete these checks **before** calling a new version production-ready.

- [ ] Clean clone and `python -m pip install -r requirements.txt` on at least one supported host.
- [ ] Fresh `python scripts/first_run.py` creates an empty task board and no Editors.
- [ ] App loads with placeholder logo, the configured title and theme.
- [ ] Google OAuth sign-in succeeds for an approved account and denies an unapproved one.
- [ ] Create/edit/delete task; attach and remove each supported media format.
- [ ] Verify future display window, active display window and expired display window.
- [ ] Verify deadline is independent of visibility end time.
- [ ] Verify correct local timezone and a daylight-saving transition with a configured DST zone.
- [ ] Verify SSE reconnect, fullscreen TV and 0/1/many task layouts.
- [ ] Verify read-only responses and direct API authorization denial.
- [ ] Verify SQLite integrity and a full backup restore.
- [ ] Confirm no private data, company identities or secrets are present in the HEAD tree.
- [ ] Record testing gaps and only then publish a release.

CI smoke tests do **not** replace these real-browser and Google sign-in checks.
