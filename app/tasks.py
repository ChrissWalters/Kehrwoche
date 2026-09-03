"""The in-process scheduler: the only thing in Kehrwoche that acts without a request.

One asyncio task, no broker, no worker container — the instance of a household does not
need a queue to notice that the bin has to go out.
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
from collections.abc import Callable
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession
from sqlalchemy.orm import sessionmaker

from app.config import Settings
from app.models import Chore, Notification, NotificationType, User
from app.models.base import utcnow
from app.security import (
    CERTIFICATE_WATCH_SECONDS,
    certificate_fingerprint,
    certificate_paths,
)
from app.services.notifications import notify
from app.services.users import finish_pending_erasures

logger = logging.getLogger(__name__)

#: How often the scheduler looks for due chores.
INTERVAL_SECONDS = 15 * 60


def _already_notified(db: DbSession, chore: Chore, due_marker: str) -> bool:
    """Has this very due date already been announced?

    The marker is the due date itself, kept in the parameters of the notification.
    Comparing it in Python keeps the query free of JSON functions, which every dialect
    spells differently. Whoever is on duty is deliberately not part of the check: a
    handover does not make the same due date newsworthy twice.
    """
    latest = db.scalar(
        select(Notification)
        .where(
            Notification.type == NotificationType.CHORE_DUE,
            Notification.reference_type == "chore",
            Notification.reference_id == chore.id,
        )
        .order_by(Notification.id.desc())
        .limit(1)
    )
    return latest is not None and latest.params.get("due_at") == due_marker


def notify_due_chores(db: DbSession, *, now: datetime | None = None) -> int:
    """Tell whoever is on duty that their chore is due. Returns how many were sent.

    Overdue chores are included — the reminder is not tied to the exact minute, so a
    restart or a sleeping server cannot swallow it.
    """
    moment = now or utcnow()
    chores = db.scalars(
        select(Chore).where(
            Chore.due_at.is_not(None),
            Chore.due_at <= moment,
            Chore.current_user_id.is_not(None),
        )
    ).all()

    sent = 0
    for chore in chores:
        assert chore.due_at is not None  # narrowed by the query above
        marker = chore.due_at.isoformat()
        if _already_notified(db, chore, marker):
            continue
        responsible = db.get(User, chore.current_user_id)
        if responsible is None:
            continue
        notify(
            db,
            responsible,
            NotificationType.CHORE_DUE,
            params={"chore": chore.title, "due_at": marker},
            reference_type="chore",
            reference_id=chore.id,
        )
        sent += 1

    if sent:
        db.commit()
    return sent


async def run_scheduler(
    session_factory: sessionmaker[DbSession],
    *,
    interval_seconds: int = INTERVAL_SECONDS,
    clock: Callable[[], datetime] = utcnow,
) -> None:
    """Run the due check forever, once per interval.

    A failing round is logged and the loop carries on: a broken database connection must
    not silence the reminders for good.
    """
    while True:
        try:
            with session_factory() as session:
                sent = notify_due_chores(session, now=clock())
                # A balance can reach zero without anybody archiving — a corrected or
                # deleted expense does it too. This is where that gets noticed.
                erased = finish_pending_erasures(session)
            if sent:
                logger.info("scheduler: %s due notifications sent", sent)
            if erased:
                logger.info("scheduler: %s pending erasures completed", erased)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — the loop must survive anything
            logger.exception("scheduler: round failed")
        await asyncio.sleep(interval_seconds)


def request_restart() -> None:
    """Ask the process to stop, so it comes back with the new certificate.

    A running server cannot swap its TLS certificate; the standard answer everywhere is
    a restart. In the container that is invisible — the restart policy brings it straight
    back — and outside it, the log says what happened.
    """
    os.kill(os.getpid(), signal.SIGTERM)


async def watch_certificate(
    settings: Settings,
    *,
    interval_seconds: int = CERTIFICATE_WATCH_SECONDS,
    on_change: Callable[[], None] = request_restart,
) -> None:
    """Notice when a custom certificate is replaced (renewed, for example).

    Only the file marks are compared — no parsing, no key material in memory.
    """
    paths = certificate_paths(settings)
    known = certificate_fingerprint(paths)
    while True:
        await asyncio.sleep(interval_seconds)
        current = certificate_fingerprint(paths)
        if current != known:
            known = current
            logger.warning("TLS certificate changed on disk; restarting to serve it")
            on_change()
