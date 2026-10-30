# Branding and instance identity

Smart Display System is intended to be **brand-neutral by default**.

A fresh instance should use a neutral placeholder logo and generic sample content, not an organization's logo, employee identities, private email addresses, domain, or office location. Administrators should be able to supply their own display name and logo without editing source code.

Brand identity, authorized staff, public origins, time zone, and deployment paths must be configurable per installation. Credentials, runtime databases, and uploaded media belong in ignored local data directories and must never be copied from an existing organization into a published repository.

The public repository is an installable application. It is **not** intended to be configured as GitHub's "Template repository" product. Reusable deployment and onboarding are application features, not repository metadata.

See the conversion proposal in this branch for the implementation and remaining validation.
