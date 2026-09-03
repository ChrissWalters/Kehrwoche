# Frequently asked questions

*[Back to the README](../README.md)*

## Why does my browser warn me about the certificate?

Because the container issued that certificate to itself. Nobody a browser trusts vouched
for it, so the browser says so — once per device. Confirm the exception and it stays
confirmed.

This is not a weaker kind of encryption. The connection is encrypted exactly as it would
be with a bought certificate; what is missing is a third party confirming that the server
is who it claims to be. On your own network, where you set the machine up yourself, that
confirmation adds little.

If the warning bothers you, there are two ways out: a
[reverse proxy](reverse-proxy.md) with a real certificate, or your own certificate
authority and `TLS_MODE=custom`.

## Why do some things only work over HTTPS?

Browsers reserve certain features for a *secure context*. `http://192.168.1.20:8080` is
not one, however private the network is.

* **Copying the join code with one tap.** The clipboard interface only exists in a secure
  context. Kehrwoche notices and selects the code instead so you can copy it by hand — it
  works, it is just a tap longer.
* **Installing the app to the home screen.** Over HTTPS you get a real installation: own
  icon, own window, no address bar. Over HTTP you get a bookmark that opens a browser tab.

Both work with the self-signed certificate once the warning has been confirmed on that
device. That is exactly why `self-signed` is the default and `off` is meant for use behind
a proxy.

## Adding it to the home screen opens a browser tab instead of an app

Two possible reasons.

**Without HTTPS**, no browser will install it — see above.

**With HTTPS, in Firefox for Android**, the shortcut may still open in a tab. Firefox
installs a site as an app only when it considers it fully installable, and its criteria
include a *service worker* — a piece of code that makes a site work offline. Kehrwoche
deliberately does not ship one in version 1: an offline mode that quietly desynchronises a
shared shopping list is worse than no offline mode. It is planned for a later version.

Chromium-based browsers (Chrome, Edge, Brave, Vivaldi) and Safari install it from the
manifest alone and open it in its own window.

## I forgot my password

There is no reset by email — version 1 never sends mail, and an address in a profile is
just a login aid. Somebody with access to the machine resets it:

```bash
docker compose exec kehrwoche kehrwoche-admin user reset-password alex
```

The command prints a new password and requires a change at the next sign-in.

## Somebody moved out. What happens to their stuff?

Leaving a household ends the membership and nothing else. Their completed chores, expenses
and pinboard entries stay where they are — removing them would falsify the balances of
everybody else. They appear as *former member* from then on.

The account itself survives: the same person can found a new household or join another
one, on the same instance, with the same account.

**Deleting the account** is the other thing. Sign-in, sessions, email address, picture and
password end immediately. Their name is kept in one case only: while an unsettled balance
is open. Otherwise the shared expenses would name an amount owed by nobody. As soon as
that period is settled — or the balance reaches zero another way — the name disappears on
its own.

## The container starts and stops again. What now?

It stopped on purpose. Every start runs through a fixed sequence of checks, and it would
rather refuse than serve an instance that is only half right — so the reason is in the
log, in full sentences:

```bash
docker compose logs --tail 30 kehrwoche
```

The two usual ones are a database written by a newer version (you stepped back to an
older image) and a data directory the container is not allowed to write to. Both are
covered, with the way out, in
[installation.md](installation.md#when-it-refuses-to-start).

Nothing has been lost in either case. The server is the last thing to start, so a refused
start never got as far as changing anything — and on SQLite the database is copied before
every migration and put back if one fails.

## Can somebody see another household's data?

No. Every business endpoint filters by the household of whoever is asking, on the server.
An id belonging to another household answers `404`, not `403`: even the existence of the
object is not confirmed.

## Do I have to run one instance per household?

No. One instance serves any number of households, each strictly separated. Whether you
open registration to others is your decision — see `REGISTRATION_OPEN` in
[configuration.md](configuration.md#registration).

## Does it phone home?

No. No telemetry, no update check, no analytics, no error reporting, no fonts or scripts
from a content delivery network. The container works on a machine with no route to the
internet. The only outgoing connection it ever makes is to your database, if it is in
another container.

## How many people can it handle?

A household with SQLite is not a load problem: a few dozen people writing occasionally is
comfortably within what one file and one process do. If you run many households on one
instance, or you like proper concurrent writes, switch `DATABASE_URL` to MariaDB or
PostgreSQL — no other change is needed.

## Can I use it in another language?

German and English ship with it, and each person picks theirs in their profile — the same
household can be read in both at once. Notifications and error messages are stored as keys
rather than finished sentences, so everybody reads them in their own language.

Adding a third language needs no rebuild: drop a JSON file into a folder mounted as
`/app/locales-extra`. See the [translation section](../README.md#translating) and
[configuration.md](configuration.md#extra-language-files).

## Can I get at the data from a script?

Yes — the browser client uses the same API as anything else would, and the instance serves
its own OpenAPI document at `/api/v1/openapi.json` for signed-in admins. Authentication is
the session cookie plus the CSRF header for anything that changes data. A proper access
token per integration is planned for version 2.

## Is there an app for iOS or Android?

No, and there will not be. Kehrwoche is a web app designed for a phone screen first; add
it to the home screen and it behaves like an installed app. Native apps would mean two
more code bases and two app stores between you and your own data.
