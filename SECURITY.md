# Security policy

Smart Display System is a self-hosted application. The public Viewer mode exposes content intentionally; do not use it for confidential information unless your deployment adds network or proxy access controls.

## Reporting

Do not publish vulnerabilities or credentials in a public issue. Contact the repository owner privately using GitHub's **Report a vulnerability** workflow (Security tab) if available.

Include a concise reproduction, affected version/commit and security impact. Do not test against installations you do not own.

## Deployment considerations

- Keep `.data/`, database, media storage and OAuth configuration private.
- Use your own Google OAuth Web client; never copy a real organization's client, sessions, editor list or credentials.
- Authorize exact editors server-side, not only by hiding buttons.
- Serve public instances over HTTPS. The application defaults to loopback binding.
- Review Origin, CSRF, cookie and CORS behavior whenever changing auth or proxy topology.
- Keep dependencies patched and make verified backups of database plus uploaded media.

The included unit/smoke tests do not constitute an independent security audit.
