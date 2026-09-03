# Configuration

Everything is configured through environment variables. There is no configuration file to
edit and no settings page in the application for any of this — an instance is described
entirely by its compose file.

A value the application does not understand stops the container at start-up with a
readable message, rather than being silently ignored.

*[Back to the README](../README.md)*

## Reference

| Variable | Default | What it does |
|---|---|---|
| `DATABASE_URL` | `sqlite:////data/kehrwoche.db` | Where the data lives. SQLite, MariaDB/MySQL (`mysql+pymysql://…`) or PostgreSQL (`postgresql+psycopg://…`). |
| `DATA_DIR` | `/data` | The data volume: database file, uploaded pictures (`media/`), certificates (`tls/`). |
| `TLS_MODE` | `self-signed` | `self-signed`, `custom` or `off`. See below. |
| `TLS_CERT_FILE` | — | Certificate file, required for `TLS_MODE=custom`. |
| `TLS_KEY_FILE` | — | Private key, required for `TLS_MODE=custom`. |
| `EXTERNAL_HOSTNAMES` | `kehrwoche.local` | Comma-separated names and IP addresses the self-signed certificate is issued for. |
| `PORT` | `8443`, or `8080` when TLS is off | The port inside the container. |
| `SESSION_MAX_AGE_DAYS` | `30` | How long staying signed in lasts. |
| `REGISTRATION_OPEN` | `true` | Whether anybody who can reach the instance may create an account. |
| `EMAIL_VALIDATION` | `false` | Whether email addresses have to look like email addresses. |
| `LOCALES_EXTRA_DIR` | `/app/locales-extra` | Optional folder with additional or overriding language files. |
| `LOG_LEVEL` | `info` | `debug`, `info`, `warning` or `error`. |
| `FORWARDED_ALLOW_IPS` | `127.0.0.1` | Which peers may set the client address through `X-Forwarded-For`. Read by the application server, not by Kehrwoche itself — see below. |

## TLS modes

**`self-signed` (default).** The container generates a certificate on first start, valid
for ten years, for the names in `EXTERNAL_HOSTNAMES`, and keeps it in `/data/tls/`.
Browsers will warn once per device because nobody signed it — that is the nature of a
self-signed certificate, not a fault. It is the right choice for a home network: the
traffic is encrypted, passwords and session cookies do not travel in the clear, and
several browser features only work over HTTPS at all (see [the FAQ](faq.md)).

To change the names, set `EXTERNAL_HOSTNAMES`, delete `/data/tls/kehrwoche.crt` and
`/data/tls/kehrwoche.key`, and restart.

**`custom`.** You supply the certificate — from your own certificate authority, or copied
from a proxy. Set `TLS_CERT_FILE` and `TLS_KEY_FILE` to paths inside the container and
mount them. If either file is missing the container refuses to start rather than falling
back to something less safe. A replaced certificate is noticed while running: the process
stops so the container manager restarts it with the new files, which is what makes
automatic renewal work.

**`off`.** No TLS inside the container — for running behind a proxy that terminates TLS
itself. The session cookie loses its `Secure` flag in this mode, which is why the
application logs a warning at start-up and reports `insecure_transport` in
`GET /api/v1/meta`. Never expose this mode directly.

There is deliberately **no HSTS header**. It would make the one-time warning of a
self-signed certificate impossible to click through, which would lock people out of their
own household.

## Behind a proxy: who the client is

Kehrwoche never reads `X-Forwarded-For` itself — a header anybody can write is not
evidence. It uses the peer address of the connection, which the application server
rewrites from the forwarding header **only for peers it trusts**. That list is
`FORWARDED_ALLOW_IPS`, and it contains `127.0.0.1` alone unless you say otherwise.

A proxy in a neighbouring container is not `127.0.0.1`. Leave the variable out and every
visitor arrives under the proxy's address: the rate limits stop distinguishing people, and
one person guessing their own password wrong locks out everybody.

```yaml
    environment:
      TLS_MODE: "off"
      FORWARDED_ALLOW_IPS: "*"
```

`*` is safe when the container publishes no ports and only the proxy can reach it — the
shape shown in [reverse-proxy.md](reverse-proxy.md). If the port is published as well,
name the proxy's address instead of trusting everyone.

Without a proxy this does not apply: leave it alone.

## Registration

`REGISTRATION_OPEN=true` is the default because the first person has to be able to sign up
somehow. Once everybody has an account, set it to `false` and restart — the sign-up form
disappears and the endpoint refuses. New people then get an account from the command line:

```bash
docker compose exec kehrwoche kehrwoche-admin user reset-password newperson
```

An instance reachable from the internet should not leave registration open.

## Email addresses

In version 1 an email address is a login aid and nothing else: no mail is ever sent, there
is no password reset by email, and nothing is verified. That is why `EMAIL_VALIDATION` is
off by default — `alex@wg` is a perfectly good identifier in a flat share. Turn it on if
your instance is open to more than the people you live with and you want the format
checked.

## Extra language files

Mount a folder as `/app/locales-extra` (or point `LOCALES_EXTRA_DIR` elsewhere) and drop
`<code>.json` files into it. At start-up the server merges them over the bundled
catalogues — key by key, with your file winning — and offers every language it finds.

```yaml
    volumes:
      - ./locales-extra:/app/locales-extra:ro
```

This works two ways:

* **A new language.** A file `fr.json` makes French appear in the language picker. Keys
  you leave out fall back to English, so a partial translation is immediately usable.
* **Changing single words.** A file `de.json` containing only the keys you want different
  overrides exactly those and leaves the rest alone — handy if your household calls the
  pinboard something else.

Details on keys and placeholders are in the translation section of the
[README](../README.md#translating).

## Time zones and money

Times are stored in UTC and rendered in the browser's time zone, so there is nothing to
configure. Amounts are stored as whole cents; the currency is a property of each household
(admins set it in the app) and only decides which symbol is shown — nothing is converted.
