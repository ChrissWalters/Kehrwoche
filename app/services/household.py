"""Founding a household, reading and changing it, and the join code."""

from __future__ import annotations

import secrets

from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from app.errors import AppError, ErrorCode
from app.models import FeedEventType, Household, HouseholdType, User, UserRole
from app.models.household import JOIN_CODE_LENGTH
from app.services.chores import add_member_to_rotations, remove_member_from_rotations
from app.services.feed import emit_event

#: Characters that cannot be confused when read out loud or copied by hand:
#: no 0/O, no 1/I/L. Everything upper case for the same reason.
JOIN_CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
#: Practically never exhausted; guards against an endless loop all the same.
JOIN_CODE_ATTEMPTS = 20


def generate_join_code(db: DbSession) -> str:
    for _ in range(JOIN_CODE_ATTEMPTS):
        code = "".join(secrets.choice(JOIN_CODE_ALPHABET) for _ in range(JOIN_CODE_LENGTH))
        if find_by_join_code(db, code) is None:
            return code
    raise AppError(500, ErrorCode.INTERNAL_ERROR, "Could not generate a unique join code.")


def normalise_join_code(code: str) -> str:
    """Codes are passed on in groups of four — spaces and case must not matter."""
    return "".join(code.split()).upper()


def find_by_join_code(db: DbSession, code: str) -> Household | None:
    return db.scalar(select(Household).where(Household.join_code == normalise_join_code(code)))


def get_household(db: DbSession, user: User) -> Household:
    """The household of the requesting person; anything else does not exist for them."""
    household = db.get(Household, user.household_id) if user.household_id else None
    if household is None:
        raise AppError(
            404,
            ErrorCode.NOT_FOUND,
            "Household not found.",
            message_key="error.household.not_found",
        )
    return household


def list_members(db: DbSession, household: Household) -> list[User]:
    return list(db.scalars(select(User).where(User.household_id == household.id).order_by(User.id)))


def create_household(
    db: DbSession,
    user: User,
    *,
    name: str,
    type: HouseholdType,
    currency: str,
) -> Household:
    if user.household_id is not None:
        raise AppError(409, ErrorCode.ALREADY_IN_HOUSEHOLD, "You already belong to a household.")

    household = Household(
        name=name.strip(),
        type=type,
        currency=currency.upper(),
        join_code=generate_join_code(db),
    )
    db.add(household)
    db.flush()

    # The founder runs the household.
    user.household_id = household.id
    user.role = UserRole.ADMIN

    emit_event(
        db,
        household,
        FeedEventType.MEMBER_JOINED,
        actor=user,
        reference_type="user",
        reference_id=user.id,
    )
    db.commit()
    db.refresh(household)
    return household


def join_household(db: DbSession, user: User, code: str) -> Household:
    """Join by code. An unknown code is a 404 — it must not be distinguishable."""
    if user.household_id is not None:
        raise AppError(409, ErrorCode.ALREADY_IN_HOUSEHOLD, "You already belong to a household.")

    household = find_by_join_code(db, code)
    if household is None:
        raise AppError(
            404,
            ErrorCode.NOT_FOUND,
            "No household with this code.",
            "join_code",
            message_key="error.household.code_unknown",
        )

    user.household_id = household.id
    user.role = UserRole.MEMBER
    db.flush()
    # Whoever joins takes part in the existing rotations, at the end of each.
    add_member_to_rotations(db, household.id, user.id)
    emit_event(
        db,
        household,
        FeedEventType.MEMBER_JOINED,
        actor=user,
        reference_type="user",
        reference_id=user.id,
    )
    db.commit()
    db.refresh(household)
    return household


def get_member(db: DbSession, household: Household, member_id: int) -> User:
    member = db.get(User, member_id)
    if member is None or member.household_id != household.id:
        raise AppError(
            404,
            ErrorCode.NOT_FOUND,
            "Member not found.",
            message_key="error.member.not_found",
        )
    return member


def count_admins(db: DbSession, household: Household) -> int:
    return len([member for member in list_members(db, household) if member.role is UserRole.ADMIN])


def set_member_role(
    db: DbSession, household: Household, actor: User, member_id: int, role: UserRole
) -> User:
    member = get_member(db, household, member_id)

    demoting_last_admin = (
        member.role is UserRole.ADMIN
        and role is UserRole.MEMBER
        and count_admins(db, household) == 1
    )
    if demoting_last_admin:
        raise AppError(
            409,
            ErrorCode.LAST_ADMIN,
            "The household would be left without an admin.",
            "role",
            message_key="error.household.last_admin_demote",
        )

    member.role = role
    db.commit()
    db.refresh(member)
    return member


def remove_member(db: DbSession, household: Household, actor: User, member_id: int) -> None:
    """Admins remove others; leaving is everyone's own decision (see ``leave_household``)."""
    if member_id == actor.id:
        raise AppError(
            409,
            ErrorCode.CANNOT_TARGET_SELF,
            "Use leaving the household for yourself.",
            message_key="error.household.use_leave",
        )
    member = get_member(db, household, member_id)
    _detach_member(db, household, member, actor=actor)
    db.commit()


def leave_household(db: DbSession, user: User) -> None:
    """Move out. The last member takes the household with them."""
    household = get_household(db, user)
    members = list_members(db, household)
    is_last_member = len(members) == 1

    if user.is_admin and not is_last_member and count_admins(db, household) == 1:
        raise AppError(
            409,
            ErrorCode.LAST_ADMIN,
            "Pass the admin role on before leaving.",
            message_key="error.household.last_admin_leave",
        )

    _detach_member(db, household, user, actor=user)
    if is_last_member:
        # Nobody is left to look after it; chores, shopping, expenses and feed go along.
        db.delete(household)
    db.commit()


def _detach_member(db: DbSession, household: Household, member: User, actor: User) -> None:
    """Common part of leaving and being removed — without committing."""
    remove_member_from_rotations(db, household.id, member.id)
    member.household_id = None
    member.role = UserRole.MEMBER
    emit_event(
        db,
        household,
        FeedEventType.MEMBER_LEFT,
        actor=actor,
        reference_type="user",
        reference_id=member.id,
    )


def update_household(
    db: DbSession,
    household: Household,
    *,
    name: str | None = None,
    type: HouseholdType | None = None,
    currency: str | None = None,
    takeover_keeps_turn: bool | None = None,
) -> Household:
    if name is not None:
        household.name = name.strip()
    if type is not None:
        household.type = type
    if currency is not None:
        household.currency = currency.upper()
    if takeover_keeps_turn is not None:
        household.takeover_keeps_turn = takeover_keeps_turn
    db.commit()
    db.refresh(household)
    return household


def regenerate_join_code(db: DbSession, household: Household) -> str:
    """Issue a new code; the previous one stops working immediately."""
    household.join_code = generate_join_code(db)
    db.commit()
    return household.join_code
