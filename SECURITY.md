# Security policy

## Supported versions

Only the latest release receives security fixes while TurnPing is in alpha (0.x).

## Reporting a vulnerability

Please **do not open a public issue**. Report privately through GitHub's
"Report a vulnerability" (Security → Advisories) on this repository. If that's
unavailable, contact the maintainers listed in the repository profile.

Include the TurnPing version, Windows version, steps to reproduce and impact. Please
don't include real secrets or private transcripts. Expect an acknowledgement within 7
days. Fixes are coordinated before public disclosure.

Especially relevant: anything that lets event content execute code, changes an agent's
permission decision, leaks prompts/secrets to disk or notifications, or corrupts user
configuration. See [docs/SECURITY.md](docs/SECURITY.md) for the design and known
limitations.
