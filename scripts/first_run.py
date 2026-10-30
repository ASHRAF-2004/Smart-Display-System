#!/usr/bin/env python3
"""Initialize a private instance; no real organization data ships in the repository."""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def valid_origin(value: str) -> bool:
    try:
        parsed = urlsplit(value)
        return (
            parsed.netloc != ""
            and parsed.username is None
            and parsed.password is None
            and parsed.path in ("", "/")
            and not parsed.query
            and not parsed.fragment
            and (
                parsed.scheme == "https"
                or (parsed.scheme == "http" and parsed.hostname in ("127.0.0.1", "localhost"))
            )
        )
    except ValueError:
        return False


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--site-name", help="Display name, 1 to 60 characters")
    parser.add_argument("--google-client-id", help="Public OAuth Web Client ID, not a client secret")
    parser.add_argument("--editor", help="Email of an approved Google editor")
    parser.add_argument("--public-origin", help="Public HTTPS origin, no path")
    args = parser.parse_args()

    if args.site_name and not 1 <= len(args.site_name.strip()) <= 60:
        parser.error("Site name must be 1–60 characters")
    if args.google_client_id and not re.fullmatch(r"[0-9]+-[A-Za-z0-9_-]+\.apps\.googleusercontent\.com", args.google_client_id):
        parser.error("Invalid Google OAuth Web Client ID")
    if args.editor and not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", args.editor.strip()):
        parser.error("Enter a valid Google email address")
    if args.public_origin and not valid_origin(args.public_origin):
        parser.error("Use a valid HTTPS origin, with no path/query")

    import app
    app.initialise_database()
    config_dir = ROOT / ".data"
    config_dir.mkdir(exist_ok=True)
    auth_path = config_dir / "auth-config.json"
    auth = json.loads(auth_path.read_text(encoding="utf-8")) if auth_path.exists() else {}
    auth.setdefault("google_client_id", "")
    auth.setdefault("allowed_origins", [])
    if args.google_client_id:
        auth["google_client_id"] = args.google_client_id
    if args.public_origin and args.public_origin not in auth["allowed_origins"]:
        auth["allowed_origins"].append(args.public_origin)
    auth_path.write_text(json.dumps(auth, indent=2) + "\n", encoding="utf-8")

    site_path = config_dir / "site-config.json"
    site = json.loads(site_path.read_text(encoding="utf-8")) if site_path.exists() else {}
    if args.site_name:
        site["site_name"] = args.site_name.strip()
    site_path.write_text(json.dumps(site, indent=2) + "\n", encoding="utf-8")

    if args.editor:
        email = args.editor.strip().lower()
        with app.db() as connection:
            connection.execute("""INSERT INTO authorized_users
                (email, display_name, department, role, active, created_at, updated_at)
                VALUES (?, ?, 'Administration', 'editor', 1, ?, ?)
                ON CONFLICT(email) DO UPDATE SET active=1, updated_at=excluded.updated_at""",
                (email, email.split("@")[0], app.now(), app.now()))
        print("Editor authorized:", email)

    print("Instance initialized. Runtime data and config are stored privately in .data/")
    if not auth["google_client_id"]:
        print("Viewer mode is ready; add your Google client ID and Editor account to enable editing.")


if __name__ == "__main__":
    main()
