# Changelog

All notable changes to this project are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), the numbering follows
[semantic versioning](https://semver.org/spec/v2.0.0.html).

## [1.0.0] — 2026-09-03

The first release. Everything below is new, so this entry describes what the software
does rather than what changed.

### Chores

* Recurring chores that rotate through the household in a defined order, and fixed chores
  that always belong to the same person.
* Due dates, points per chore, and a leaderboard with the completion history.
* Completing a chore takes one tap and can be undone for five minutes — including the due
  date and whose turn it was.
* Completing on behalf of somebody else, for the person who is standing at the sink.
* **"Taking over does not count as a turn"**, a household setting. Doing somebody else's
  turn normally hands the turn on to whoever did it, which in a household of two means
  the same person twice in a row. With the switch on, whoever was on duty stays on duty;
  the due date, the points and the history are unaffected.
* A reminder that can be sent to whoever is on duty, at most once a day per chore and per
  sender.
* Localised templates for setting up the first chores.
* Admins can reset the statistics; the history is kept.

### Shopping list

* Add, tick off, untick, and clear everything bought in one bundled step.
* Autocomplete from a localised suggestion list and from what the household bought
  recently.
* Optimistic updates with a snackbar, so the list keeps up with a supermarket aisle.

### Shared expenses

* Expenses with payer, date, amount and participants; the even split is exact to the cent,
  with the remainder going to the lowest member ids so it is deterministic.
* Running balances per person, and a settlement that clears everybody with at most one
  payment fewer than there are people.
* Settling archives the period instead of deleting it; archived periods stay readable
  with all their expenses and payments.
* People who have left keep their name in the expenses until the balance is settled — and
  are shown as *former member* afterwards.

### Pinboard

* System events from all three modules, mixed with posts people write themselves.
* Creating, editing and deleting a chore all appear; the entry for an edit names the
  fields it touched.
* Likes and comments; system entries can be liked and commented on but never edited or
  deleted, which makes the pinboard the audit log.

### Household and accounts

* Households with a name, type, picture and currency; a join code that can be
  regenerated.
* Admin and member roles, with the role transfer that lets a last admin leave.
* Leaving keeps the account: the same person can then found or join another household.
* Deleting an account ends sign-in, sessions, email address, picture and password at once;
  the name is kept only while an unsettled balance is open, and removed automatically
  afterwards.
* Profiles with name, picture, language, password and a list of active sessions that can
  be revoked individually.

### Notifications

* In-app notifications for due chores, manual reminders, comments and likes on your own
  posts, and new settlement proposals.
* Stored as keys rather than finished sentences, so everybody reads them in their own
  language.

### Languages

* German and English, switchable per person and effective across devices.
* Additional or overriding language files can be mounted at runtime under
  `/app/locales-extra`, without rebuilding anything.
* Error messages travel as keys as well and are shown in the reader's language.

### Operation

* One container: FastAPI, the browser client, migrations and the admin CLI.
* SQLite by default, MariaDB/MySQL and PostgreSQL supported by changing `DATABASE_URL`
  alone. All three are tested on every change.
* **The container checks itself before it serves anything**: configuration, database,
  whether the data directory can be written to, and whether the schema matches the image
  — in that order, stopping at the first thing that is not right, with the reason in
  plain sentences rather than a stack trace. A database newer than the image stops the
  start instead of being guessed at.
* **On SQLite the database is copied before every migration**, into `backups/` inside the
  data volume, and put back if the migration fails; the three newest copies are kept.
  With MariaDB or PostgreSQL the container cannot copy a database it does not own, so it
  names the dump command in the log instead — and warns that MariaDB does not roll a
  failed migration back by itself.
* Updating is a pull and a restart: migrations run at start-up, and the server starts
  last, so a failed update cannot have changed anything.
* TLS from the container with a self-signed certificate (generated once, ten years, for
  the configured names), your own certificate, or off for use behind a reverse proxy.
* Admin command line for accounts, households, pending erasures and SQLite backups.
* Argon2id passwords with a list of the 1000 most common ones refused, server-side
  sessions, CSRF double-submit tokens, rate limits per IP and per account, a strict
  content security policy without `unsafe-inline` or `unsafe-eval`, and uploads that are
  re-encoded rather than trusted.
* No telemetry, no update check, no CDN: the instance works without access to the
  internet.

### Interface

* Designed, built and accepted on a phone first; the desktop layout switches to a sidebar
  above 900 px.
* Installable to the home screen over HTTPS, with its own icon and window.
* Light and dark following the system setting.

[1.0.0]: https://github.com/ChrissWalters/Kehrwoche/releases/tag/v1.0.0
