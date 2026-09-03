# Installation

Kehrwoche runs as a single container. It brings its own web server, its own TLS
certificate and its own database schema — there is nothing to set up beside it unless you
want an external database.

*[Back to the README](../README.md)*

## Requirements

* Docker with the Compose plugin (`docker compose version` should answer).
* A machine that stays on: a NAS, a small server, a Raspberry Pi 4 or newer.
* About 300 MB of disk for the image, plus whatever your pictures need.

Nothing else. The application never contacts the internet on its own.

## One file, annotated

There is a single [docker-compose.yml](../docker-compose.yml), and it has every option in
the place where it belongs: the three TLS modes, an external database, the language
volume, where the data should live. It runs unedited — SQLite, a self-signed certificate,
one volume — and everything else is a comment.

Download it, `docker compose up -d`, and read the comments when a question comes up.
Delete the ones you have answered for yourself; nothing depends on them.

## Pulling from a private registry

While the repository is private, so is its container image: `docker compose pull` on
another machine will fail with `denied` or `unauthorized` until it can identify itself.

**Either publish the image.** On GitHub: *Packages* → *kehrwoche* → *Package settings* →
*Change visibility* → *Public*. The code stays private; only the image becomes pullable.
Nothing else has to change.

**Or sign in on the machine that pulls.** Create a *classic* personal access token with
the single scope `read:packages` (*Settings* → *Developer settings* → *Personal access
tokens* → *Tokens (classic)*), then, on that machine:

```bash
echo 'ghp_yourtokenhere' | docker login ghcr.io -u YourGitHubName --password-stdin
```

The credentials land in `~/.docker/config.json` **of the user running the command**. That
is the usual reason this appears not to work: on a NAS the compose stack runs as `root`,
so the login has to happen as `root` too (`sudo -i` first, not just `sudo docker login`).

After that, `docker compose pull` works and keeps working — the token does not have to be
repeated.

## Getting it running

```bash
curl -O https://raw.githubusercontent.com/ChrissWalters/Kehrwoche/main/docker-compose.yml
docker compose up -d
docker compose logs -f kehrwoche
```

Before the first start, set `EXTERNAL_HOSTNAMES` in the file to every name and address you
will actually use — that is what the certificate is issued for.

The named volume in the file is the foolproof start. On a NAS you probably want the data
on the pool your backups already cover instead; that works just as well and is arguably
the better choice, it only needs `chown -R 10001:10001` on the directory before the first
start. Which to pick, and why, is in
[backup-restore.md](backup-restore.md#where-to-put-the-data).

The log ends with uvicorn reporting that it is listening. Open
`https://<the machine's address>:8443` and register — the first account that founds a
household becomes its admin.

### What happens on the first start

1. The configuration is checked. A wrong value stops the container with one readable line
   instead of failing later, mid-request.
2. The database schema is created or migrated (`alembic upgrade head`).
3. In the default TLS mode a self-signed certificate is generated into
   `/data/tls/` and kept there. It is not regenerated on later starts — otherwise
   everybody would have to confirm a new browser warning after every restart.
4. The server starts.

## Using a server database

SQLite is the default and is not a compromise: one household writing occasionally is far
inside what a single file and a single process do comfortably, and it is the only option
with a backup command built into the application.

Reach for a server database if you already run one, or if one instance serves many
households. Both are tested against every change, so the schema is the same everywhere —
PostgreSQL is the stronger engine, MariaDB the more familiar one on a NAS.

In `docker-compose.yml`:

1. Uncomment the `DATABASE_URL` line for the database you want.
2. Uncomment the matching `db:` service and the `db-data:` volume at the bottom.
3. Uncomment the `depends_on:` block, so the application waits for the database to be
   ready before it migrates.
4. **Replace `CHANGE-ME`.** It is a placeholder, not a password.

The application container waits for the database to accept connections, so a slow first
initialisation is not a problem. The data volume stays necessary either way: pictures and
certificates do not live in the database.

## Reaching it from other devices

The certificate is issued for the names in `EXTERNAL_HOSTNAMES` — by default
`kehrwoche.local`. If you reach the instance by IP address, put that address in as well
so the certificate matches:

```yaml
    environment:
      EXTERNAL_HOSTNAMES: kehrwoche.local,192.168.1.20
```

Then delete `/data/tls/kehrwoche.crt` and `/data/tls/kehrwoche.key` and restart — the new
certificate is generated with the new names.

For access from outside your network, put a reverse proxy in front of it rather than
forwarding the port: [reverse-proxy.md](reverse-proxy.md).

## Accounts and administration

Everyday administration happens in the app, by whoever is admin of a household. The
things that cannot be done from inside — because they concern the whole instance — have a
command line inside the container:

```bash
docker compose exec kehrwoche kehrwoche-admin user list
docker compose exec kehrwoche kehrwoche-admin user reset-password alex
docker compose exec kehrwoche kehrwoche-admin user lock alex
docker compose exec kehrwoche kehrwoche-admin household list
```

`kehrwoche-admin --help` lists all of them. Resetting a password prints a new one and
forces a change at the next sign-in.

## Updating

```bash
docker compose pull
docker compose up -d
```

That is the whole procedure. Images are tagged `1`, `1.0`, `1.0.0` and `latest`. Pinning
`:1` gets you fixes and new features without a breaking change; pinning `:1.0.0` gets you
exactly this release.

### What the container does before it serves anything

Every start runs the same guarded sequence, and **stops at the first thing that is not
right** rather than serving something half-built:

1. The configuration is read. A value it cannot make sense of stops it here.
2. An external database has to answer. It waits up to two minutes.
3. The data directory has to be writable. If it is not — the usual cause is a directory
   from the host with the wrong owner — it says so and names the fix.
4. The database's schema version is compared with the one this image knows.
   - **Newer than the image** (you stepped back to an older version): it refuses, and
     tells you so. Nothing is touched. See below.
   - **Older**: a migration is due.
5. **On SQLite, the database is copied first**, into `backups/` inside the data volume,
   named after the versions it moves between. The three newest copies are kept.
6. The migration runs. If it fails on SQLite, **the copy is put back** and the container
   stops with the reason. Your data is as it was before the update.
7. Only then does the server start.

Because the server starts last, a failed update cannot have changed a chore, an expense
or a password: nobody was able to send one.

### With an external database

The container cannot back up a database it does not own — it ships no `mariadb-dump` and
no `pg_dump`, and your server may be shared and large. So when a migration is due it says
so in the log, names the dump command, and goes ahead.

* **PostgreSQL** applies all migrations in one transaction and rolls the whole thing back
  by itself if one fails. The risk here is small.
* **MariaDB/MySQL** does not: it commits each step as it goes. A migration that fails
  half way leaves the schema between two versions, and a dump is the only way back.
  **Take one before updating** — see [backup-restore.md](backup-restore.md).

### When it refuses to start

Read the last lines of `docker compose logs kehrwoche`; the reason is written in full
sentences, not in stack traces. The two you are most likely to meet:

**"This database is at revision …, which this version does not know."** You are running
an older image against a newer database. Start the newer image again, or restore a backup
from before the update — on SQLite, the copies in `backups/` inside the data volume.

**"The data directory … cannot be written to."** The directory belongs to somebody other
than the account inside the container. `chown -R 10001:10001` it, or run the container as
its owner — [backup-restore.md](backup-restore.md#where-to-put-the-data) has both.

### Rolling back on purpose

Every migration can be undone, so stepping back a version is possible — it is just not
automatic:

```bash
docker compose stop kehrwoche
# with the NEW image still in place, wind the schema back one revision:
docker compose run --rm --entrypoint alembic kehrwoche downgrade -1
# then pin the older version in docker-compose.yml and start it
docker compose up -d
```

On SQLite the simpler route is the copy the container took by itself: stop, put the file
from `backups/` back, start the older image.

## Removing it

```bash
docker compose down          # stops it, keeps the data
docker compose down -v       # also deletes the volumes — everything is gone
```

The second one is irreversible. Take a backup first if there is any doubt.
