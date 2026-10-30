# Installation and first-run configuration

1. Install Python 3.11+.
2. Clone the repository, create a virtual environment and install `requirements.txt`.
3. Run `python scripts/first_run.py --site-name "My Display" --timezone "Europe/London"`.
4. Start `python app.py --port 8787`.
5. Open `http://127.0.0.1:8787/` for the public read-only view.

A new instance has **zero Editor accounts**. To enable editing, create a Google OAuth Web Client, register your JavaScript origin, and run:

```bash
python scripts/first_run.py --google-client-id "YOUR_ID.apps.googleusercontent.com" --editor "your-account@example.com"
```

For HTTPS deployments add your public origin:

```bash
python scripts/first_run.py --public-origin "https://display.example.com"
```

The application itself stays bound to `127.0.0.1:8787` and should be placed behind an HTTPS reverse proxy or tunnel. Do not copy private config or a real organization's SQLite database into a public repository.

**Screens:** the default URL shows channel `general`. For another screen, open `/?channel=lobby` and assign selected tasks to `lobby` in the Editor. A channel controls display selection only, not read permissions.

**Logo:** sign in as an approved Editor, then upload your organization's image using the existing logo control. The placeholder remains until then.

**Application name:** rerun `first_run.py --site-name "New name"`. The title is stored privately in `.data/site-config.json`.

**Current limitations:** selected weekday is global, not per screen; recurrence and independent screen playlists are not implemented. Optional visibility start/end dates are available, independent of task deadlines. Display scheduling is not a privacy boundary. See [requirements](REQUIREMENTS.md).
