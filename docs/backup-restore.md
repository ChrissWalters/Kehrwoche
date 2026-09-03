# Backup and restore

A backup nobody has ever restored is a hope, not a backup. Both directions are below —
please try the second one once, on a spare directory, before you need it.

*[Back to the README](../README.md)*

## The one backup you get for free

On SQLite the container copies the database **by itself** before every migration, into
`backups/` inside the data volume — named after the versions it moves between, three kept
at a time. If the migration fails, that copy goes straight back and the container stops.

That covers exactly one accident, the one an update can cause. It is not a backup: it is
not taken on a schedule, it does not leave the machine, and it does not include the
pictures. Everything below still applies.

With PostgreSQL there is no such copy — the container cannot dump a database it does not
own. It does not need one either: PostgreSQL applies all migrations in a transaction and
rolls a failed one back by itself. A dump before a major update is still good practice.

## What has to be saved

Everything lives in the data volume, `/data` inside the container:

| Path | Contents | Recreatable? |
|---|---|---|
| `/data/kehrwoche.db` | The database, with SQLite | **No.** This is the household. |
| `/data/media/` | Avatars and household pictures | **No.** |
| `/data/tls/` | The self-signed certificate | Yes — but a new one means a new browser warning on every device. |

With PostgreSQL the database is in the database container instead, and
`/data/media/` still matters.

## SQLite

The application brings its own backup command. Use it rather than copying the file: a
plain `cp` of a database that is being written to can produce a file that will not open.

```bash
docker compose exec kehrwoche kehrwoche-admin backup /data/backups
docker compose cp kehrwoche:/data/backups ./kehrwoche-backups
```

Given a directory it writes a file named after the moment it ran; given a file name it
uses that name. The copy is consistent even while people are using the app.

Pictures are separate:

```bash
docker compose cp kehrwoche:/data/media ./kehrwoche-backups/media
```

### Automatically, every night

```bash
0 3 * * * cd /srv/kehrwoche && docker compose exec -T kehrwoche \
  kehrwoche-admin backup /data/backups
```

Point your existing backup software at the volume afterwards, and prune old files — the
command keeps every one it writes.

## PostgreSQL

```bash
docker compose exec db pg_dump -U kehrwoche kehrwoche > kehrwoche.sql
```

## Restoring

Stop the application first. Restoring underneath a running instance produces a state that
matches neither the old nor the new data.

```bash
docker compose stop kehrwoche
```

**SQLite:** put the file back where the database belongs, restore the pictures beside it,
and hand both to the account inside the container.

```bash
docker compose cp ./kehrwoche-backups/kehrwoche-20260810-030001.db \
  kehrwoche:/data/kehrwoche.db
docker compose cp ./kehrwoche-backups/media kehrwoche:/data/media
docker compose exec -u 0 kehrwoche chown -R 10001:10001 /data
```

**Do not skip the third line.** A copy into the container keeps the ownership it had
outside, and the application does not run as root — it runs as uid 10001. Without the
`chown` the instance starts, shows everything, and fails the moment somebody ticks a chore
off, because the database file is readable but not writable. See
[the note on file ownership](#a-note-on-file-ownership).

**PostgreSQL:**

```bash
docker compose exec -T db psql -U kehrwoche kehrwoche < kehrwoche.sql
```

Then start it again:

```bash
docker compose start kehrwoche
```

If the backup came from an older version, the migrations run automatically at start-up and
bring the schema up to date. The other direction does not work: a database from a newer
version cannot be used by an older image.

## Moving to another machine

The same two steps, in order: restore the data, then start the new container. Two details
save an evening:

* If the new machine has a different address, set `EXTERNAL_HOSTNAMES` accordingly and
  delete `/data/tls/` so a matching certificate is generated.
* Everybody stays signed in — sessions are in the database. If you would rather they
  did not, each person can revoke their sessions in their profile.

## Checking a backup

Restore it into a throwaway instance rather than over the real one:

```bash
docker volume create kehrwoche-check
docker run -d --name kehrwoche-check -p 8444:8443 \
  -v kehrwoche-check:/data ghcr.io/chrisswalters/kehrwoche:1
docker cp kehrwoche-20260810-030001.db kehrwoche-check:/data/kehrwoche.db
docker exec -u 0 kehrwoche-check chown -R 10001:10001 /data
docker restart kehrwoche-check
```

Open `https://localhost:8444`, sign in, look at a household. Then clean up:

```bash
docker rm -f kehrwoche-check && docker volume rm kehrwoche-check
```

Ten minutes, once — and you know.

## Where to put the data

A named volume and a directory of your own are the same thing underneath: a directory on
the host, holding the same files, on the same filesystem. Neither is more durable than the
other, and `docker compose down` removes neither — only `down -v` or `docker volume rm`
do, and that is worth knowing before you type it.

What differs is *where* it lands and *who owns it*.

**A named volume** goes wherever Docker keeps them, usually `/var/lib/docker/volumes/…`,
and Docker creates it from the image so it already belongs to the right account. It is the
foolproof first start, which is why the compose files use it. Reaching the files means
going through `docker cp` or looking the path up.

**A directory of your own** — `-v /srv/appdata/kehrwoche:/data` — is the better choice as
soon as backups matter, and on a NAS that is immediately. `/var/lib/docker` is often on
the system disk, outside the snapshots and the backup job that cover the data pool; a path
you chose is inside them, and your existing backup software can simply take it. Two
conditions:

* **The owner has to match.** The application runs as an unprivileged account, uid 10001,
  and never as root — so unlike most images it cannot simply write wherever it is pointed,
  and it cannot repair the ownership itself either. Either hand the directory over once
  with `chown -R 10001:10001`, or run the container as whoever owns it already:

  ```bash
  stat -c '%u:%g' /srv/appdata/kehrwoche      # -> 1001:998
  ```

  ```yaml
      user: "1001:998"
  ```

  Ask the directory for the numbers rather than deriving them from names: `id` only knows
  users, so `id -g docker` answers *no such user* even when a directory plainly belongs to
  the group `docker` (that would be `getent group docker`). `stat` sidesteps the question
  and reads the two numbers off the directory you are about to mount.

  Both ways work. Do not combine `user:` with a named volume — that one is already set up
  for uid 10001.

  A directory that does not exist yet is created by Docker itself, as **root**, which is
  why a folder full of application data often shows nothing but `root:root`. Most images
  run as root inside and never notice; this one does, and says so at the first write.
* **Keep it on a local filesystem.** SQLite on an NFS or SMB share will corrupt sooner or
  later: its locking does not survive network file systems. With an external database this
  matters less, but the pictures still want a real disk.

**`docker cp` keeps the ownership of the source**, which is why every restore above ends
with a `chown` inside the container — regardless of which of the two you chose.

The symptom of getting the owner wrong is always the same and always confusing: the
instance starts, everything is readable, and the first write fails.
