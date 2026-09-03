# Security policy

Kehrwoche is a self-hosted application for private households. It holds names, chores,
shopping lists, shared expenses and the conversations around them — little of it
dramatic on its own, all of it nobody else's business.

## Reporting a vulnerability

Please report security problems **privately first**, not as a public issue:

* GitHub → *Security* → *Report a vulnerability* (private advisory), or
* email to the address in the repository profile.

Useful in a report: what you did, what happened, what you expected, and the version
(`GET /api/v1/meta` returns it). A proof of concept helps, a working exploit is not
required.

You will get a first answer within **seven days**. Fixed problems are named in the
release notes, and — unless you would rather not be — you are credited there.

There is no bounty programme; this is a volunteer project under the AGPL-3.0.

## What is in scope

The application itself: authentication and sessions, the household boundary
(multi-tenancy), CSRF protection, rate limiting, uploads, the admin CLI, the container
image and the deployment examples in this repository.

Out of scope: findings that require an already compromised host or database, missing
hardening in *your* reverse proxy or operating system, and reports about a household
member misusing rights they legitimately have inside their own household.

## How the application protects itself

| Area | Measure |
|---|---|
| Passwords | Argon2id, minimum length, list of the 1000 most common passwords refused |
| Sessions | Server-side, only the SHA-256 of the token is stored, revocable per device |
| Cookies | `HttpOnly`, `SameSite=Lax`, `Secure` whenever TLS is on |
| CSRF | Double-submit token on every unsafe method |
| Multi-tenancy | Every business endpoint filters by household; a foreign id answers `404`, never `403` |
| Brute force | Sliding-window rate limits per IP and per account, with exponential backoff |
| Uploads | Size limit, type decided by content, re-encoded through Pillow, stored under a content hash |
| Browser | `Content-Security-Policy: default-src 'self'` without `unsafe-inline`/`unsafe-eval`, `nosniff`, `Referrer-Policy: same-origin`, `X-Frame-Options: DENY` |
| Transport | TLS terminated by the container (`self-signed` or `custom`), or `off` behind your own proxy |
| Privacy | No telemetry, no phone-home, no CDN — the application works without internet access |

## Running it on the public internet

The intended home of a Kehrwoche instance is a home network or a VPN. If you do expose
it, this is the minimum:

1. **Terminate TLS in front of it** with a real certificate (Caddy, Traefik, nginx) and
   run the container with `TLS_MODE=off` behind that proxy — never plain HTTP to the
   outside.
2. **Close registration** with `REGISTRATION_OPEN=false` and create accounts with
   `kehrwoche-admin user reset-password`, or open it only while people sign up.
3. **Keep the image current.** Updates are a pull and a restart; migrations run on their
   own at start-up.
4. **Back the data up** — `kehrwoche-admin backup <path>` for SQLite, the usual dump
   tools for MariaDB or PostgreSQL — and check that a backup can be restored.
5. **Let the proxy pass the real client address** (`X-Forwarded-For`) *and* set
   `FORWARDED_ALLOW_IPS` on the container so the header is believed — by default only
   `127.0.0.1` is trusted, which a proxy in a neighbouring container is not. Sending the
   header without trusting it counts every visitor as the same one, and the rate limits
   stop protecting anybody. See
   [docs/reverse-proxy.md](docs/reverse-proxy.md).
6. **Watch the logs** for repeated `429` and failed sign-ins.

Kehrwoche is written to survive on the internet, but it is not audited software. The
safest deployment remains the one nobody else can reach.
