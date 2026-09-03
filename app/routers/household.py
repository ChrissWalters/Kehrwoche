"""The household itself: founding, reading, changing and the join code."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, File, Request, UploadFile, status

from app.deps import (
    AdminUser,
    CurrentUser,
    DbSession,
    MemberUser,
    RateLimitersDep,
    SettingsDep,
    client_ip,
)
from app.errors import AppError
from app.models import Household
from app.schemas.household import (
    HouseholdCreateRequest,
    HouseholdResponse,
    HouseholdStateResponse,
    HouseholdUpdateRequest,
    JoinCodeResponse,
    JoinRequest,
    MemberResponse,
    MemberRoleRequest,
)
from app.services import household as household_service
from app.services import sync as sync_service
from app.services import users as user_service

router = APIRouter(prefix="/household", tags=["household"])


def _as_response(db: DbSession, household: Household) -> HouseholdResponse:
    members = household_service.list_members(db, household)
    return HouseholdResponse(
        id=household.id,
        name=household.name,
        type=household.type,
        image_file=household.image_file,
        currency=household.currency,
        takeover_keeps_turn=household.takeover_keeps_turn,
        join_code=household.join_code,
        members=[MemberResponse.model_validate(member) for member in members],
    )


@router.post(
    "",
    response_model=HouseholdResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Found a household",
)
def create_household(
    payload: HouseholdCreateRequest, current_user: CurrentUser, db: DbSession
) -> HouseholdResponse:
    """Only for people without a household — the founder becomes its admin."""
    household = household_service.create_household(
        db,
        current_user,
        name=payload.name,
        type=payload.type,
        currency=payload.currency,
    )
    return _as_response(db, household)


@router.get("", response_model=HouseholdResponse, summary="The own household")
def read_household(current_user: MemberUser, db: DbSession) -> HouseholdResponse:
    household = household_service.get_household(db, current_user)
    return _as_response(db, household)


@router.get(
    "/state",
    response_model=HouseholdStateResponse,
    summary="Change markers of the modules",
)
def read_state(current_user: MemberUser, db: DbSession) -> HouseholdStateResponse:
    """Tiny by design: polled every 15 seconds while a tab is visible."""
    household = household_service.get_household(db, current_user)
    return HouseholdStateResponse.model_validate(
        sync_service.household_state(db, household, current_user)
    )


@router.post("/join", response_model=HouseholdResponse, summary="Join with a code")
def join_household(
    payload: JoinRequest,
    request: Request,
    current_user: CurrentUser,
    db: DbSession,
    limiters: RateLimitersDep,
) -> HouseholdResponse:
    """Rate limited per source and per account so codes cannot be guessed."""
    ip_key = f"ip:{client_ip(request)}"
    account_key = f"account:{current_user.id}"
    limiters.join.check(ip_key)
    limiters.join.check(account_key)

    try:
        household = household_service.join_household(db, current_user, payload.join_code)
    except AppError:
        limiters.join.record(ip_key)
        limiters.join.record(account_key)
        raise

    limiters.join.reset(ip_key)
    limiters.join.reset(account_key)
    return _as_response(db, household)


@router.post(
    "/leave",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Move out of the household",
)
def leave_household(current_user: MemberUser, db: DbSession) -> None:
    household_service.leave_household(db, current_user)


@router.patch(
    "/members/{member_id}",
    response_model=MemberResponse,
    summary="Change the role of a member",
)
def set_member_role(
    member_id: int, payload: MemberRoleRequest, current_user: AdminUser, db: DbSession
) -> MemberResponse:
    household = household_service.get_household(db, current_user)
    member = household_service.set_member_role(db, household, current_user, member_id, payload.role)
    return MemberResponse.model_validate(member)


@router.delete(
    "/members/{member_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remove a member",
)
def remove_member(member_id: int, current_user: AdminUser, db: DbSession) -> None:
    household = household_service.get_household(db, current_user)
    household_service.remove_member(db, household, current_user, member_id)


@router.patch("", response_model=HouseholdResponse, summary="Change the household")
def update_household(
    payload: HouseholdUpdateRequest, current_user: AdminUser, db: DbSession
) -> HouseholdResponse:
    household = household_service.get_household(db, current_user)
    household = household_service.update_household(
        db,
        household,
        name=payload.name,
        type=payload.type,
        currency=payload.currency,
        takeover_keeps_turn=payload.takeover_keeps_turn,
    )
    return _as_response(db, household)


@router.post("/image", response_model=HouseholdResponse, summary="Upload a household picture")
async def upload_image(
    current_user: AdminUser,
    db: DbSession,
    settings: SettingsDep,
    file: Annotated[UploadFile, File()],
) -> HouseholdResponse:
    """Admins only — the picture is the face of the household, not of a person."""
    household = household_service.get_household(db, current_user)
    data = await file.read()
    return HouseholdResponse.model_validate(
        user_service.set_household_image(db, household, data, settings)
    )


@router.post(
    "/regenerate-code",
    response_model=JoinCodeResponse,
    summary="Issue a new join code",
)
def regenerate_code(current_user: AdminUser, db: DbSession) -> JoinCodeResponse:
    """The previous code stops working the moment this returns."""
    household = household_service.get_household(db, current_user)
    return JoinCodeResponse(join_code=household_service.regenerate_join_code(db, household))
