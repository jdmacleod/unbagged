# Reporting a security or privacy issue

This project reads files containing people's home addresses, phone numbers, loyalty
card numbers and years of itemized purchases. A bug that moves that data somewhere the
user did not choose is the worst thing that can happen here, and it is worth more to me
than any feature.

## What to report

Please report anything in these categories, including things that are not exploitable
by a remote attacker:

- Personal data reaching a place the user did not choose: a log, a temp file, an
  outbound request, a crash report, a build context, an image layer, git history.
- A gap in the safeguards themselves — `tools/scan_pii.py`, `tools/make_fixtures.py`,
  the `.gitignore` / `.dockerignore` pair, or the CI jobs that run them. A safeguard
  that reports clean on something it should catch is a security bug, not a papercut.
- The server binding, or being reachable, beyond loopback without the user asking.
- Ordinary web vulnerabilities: path traversal, injection, XSS, SSRF.

## How to report

**Do not open a public issue, and do not attach a real report to anything.**

Use GitHub's private vulnerability reporting on this repository:
**Security → Report a vulnerability**. It is a private channel between you and the
maintainer, and it is the only one I read for this.

If you need to show the shape of a file that triggers a bug, reduce it first:

```bash
unbagged sanitize path/to/report.json -o skeleton.json
```

That keeps the structure and throws the values away. See `CONTRIBUTING.md` for what the
skeleton does and does not preserve. Never send the report itself — not a screenshot, not
"just the relevant page", not in a private fork.

## What to expect

I will acknowledge within a week and tell you whether I think it is a real issue and what
I plan to do. There is no bounty. Credit in the changelog if you want it, and none if you
would rather not be named.

## Scope

`unbagged` runs on one person's machine, reads files that person already possesses, makes
no outbound requests, and has no accounts, sessions or multi-tenancy. That shapes what
counts:

- **In scope:** anything above. Also the Docker packaging, the CI workflows, and the
  documentation where it tells someone to do something unsafe.
- **Not in scope:** denial of service against a single-user local app; a user choosing to
  bind `--host 0.0.0.0` after the tool warns them not to; the absence of authentication on
  a loopback-only service that is deliberately account-free.

If you are unsure which side something falls on, report it. I would rather read one that
turns out to be fine.
