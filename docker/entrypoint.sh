#!/bin/sh
# Container start: everything that has to be true, then the server.
#
# The sequence itself lives in `app/startup.py`, where each step can be caught and can
# undo what it did. This script only puts the two halves in order — and `set -e` makes
# sure the second never runs if the first refused.
set -eu

# Configuration valid, database reachable, data directory writable, schema up to date.
# On SQLite the database is copied before it is migrated and put back if that fails; a
# database newer than this image stops the container instead of being guessed at. An
# instance that cannot come up correctly stops here, with the reason on stdout, rather
# than serving something half-built.
python -m app.startup prepare

# Certificate (in the self-signed mode, generated once into the data volume) and the
# application server. `exec` hands over the process, so signals reach uvicorn and the
# container stops when it is told to.
exec python -m app.startup serve
