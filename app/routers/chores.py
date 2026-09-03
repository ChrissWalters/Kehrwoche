"""The chore module: list, create, change, complete, undo and remind."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query, status

from app.deps import AdminUser, DbSession, MemberUser, RateLimitersDep, SettingsDep
from app.schemas.chores import (
    ChoreCompleteRequest,
    ChoreCompletionResponse,
    ChoreCreateRequest,
    ChoreResponse,
    ChoreTemplateResponse,
    ChoreUpdateRequest,
    HistoryEntryResponse,
    HistoryPageResponse,
    MemberStatisticsResponse,
    RemindResponse,
)
from app.services import chores as chore_service
from app.services import household as household_service

router = APIRouter(prefix="/chores", tags=["chores"])


@router.get("", response_model=list[ChoreResponse], summary="All chores of the household")
def list_chores(current_user: MemberUser, db: DbSession) -> list[ChoreResponse]:
    household = household_service.get_household(db, current_user)
    return [
        ChoreResponse.model_validate(chore) for chore in chore_service.list_chores(db, household)
    ]


@router.post(
    "",
    response_model=ChoreResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Add a chore",
)
def create_chore(
    payload: ChoreCreateRequest, current_user: MemberUser, db: DbSession
) -> ChoreResponse:
    household = household_service.get_household(db, current_user)
    chore = chore_service.create_chore(
        db,
        household,
        current_user,
        title=payload.title,
        description=payload.description,
        points=payload.points,
        rotation_seconds=payload.rotation_seconds,
        fixed=payload.fixed,
        member_order=payload.member_order,
        due_at=payload.due_at,
    )
    return ChoreResponse.model_validate(chore)


@router.get(
    "/templates",
    response_model=list[ChoreTemplateResponse],
    summary="Suggestions for setting up",
)
def list_templates(
    current_user: MemberUser,
    settings: SettingsDep,
    locale: Annotated[str | None, Query(max_length=8)] = None,
) -> list[ChoreTemplateResponse]:
    """In the language on screen — ``locale`` wins over the profile of the requester."""
    return [
        ChoreTemplateResponse.model_validate(template)
        for template in chore_service.list_templates(current_user, settings, locale)
    ]


@router.get("/history", response_model=HistoryPageResponse, summary="Completed chores")
def read_history(
    current_user: MemberUser,
    db: DbSession,
    cursor: Annotated[int | None, Query(ge=1)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = chore_service.HISTORY_PAGE_SIZE,
) -> HistoryPageResponse:
    household = household_service.get_household(db, current_user)
    rows, next_cursor = chore_service.history(db, household, cursor=cursor, limit=limit)
    return HistoryPageResponse(
        items=[
            HistoryEntryResponse(
                id=completion.id,
                chore_id=completion.chore_id,
                chore_title=title,
                user_id=completion.user_id,
                booked_by_id=completion.booked_by_id,
                done_at=completion.done_at,
                points_awarded=completion.points_awarded,
            )
            for completion, title in rows
        ],
        next_cursor=next_cursor,
    )


@router.get(
    "/statistics",
    response_model=list[MemberStatisticsResponse],
    summary="Points and completions per member",
)
def read_statistics(current_user: MemberUser, db: DbSession) -> list[MemberStatisticsResponse]:
    household = household_service.get_household(db, current_user)
    return [
        MemberStatisticsResponse.model_validate(row)
        for row in chore_service.statistics(db, household)
    ]


@router.post(
    "/reset-statistics",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Set every score back to zero",
)
def reset_statistics(current_user: AdminUser, db: DbSession) -> None:
    """Admins only. The history is kept — only the points start over."""
    household = household_service.get_household(db, current_user)
    chore_service.reset_statistics(db, household, current_user)


@router.patch("/{chore_id}", response_model=ChoreResponse, summary="Change a chore")
def update_chore(
    chore_id: int, payload: ChoreUpdateRequest, current_user: MemberUser, db: DbSession
) -> ChoreResponse:
    household = household_service.get_household(db, current_user)
    chore = chore_service.get_chore(db, household, chore_id)
    chore = chore_service.update_chore(
        db,
        household,
        chore,
        actor=current_user,
        title=payload.title,
        description=payload.description,
        points=payload.points,
        rotation_seconds=payload.rotation_seconds,
        fixed=payload.fixed,
        member_order=payload.member_order,
        current_user_id=payload.current_user_id,
        due_at=payload.due_at,
    )
    return ChoreResponse.model_validate(chore)


@router.delete(
    "/{chore_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a chore",
)
def delete_chore(chore_id: int, current_user: MemberUser, db: DbSession) -> None:
    household = household_service.get_household(db, current_user)
    chore_service.delete_chore(
        db, chore_service.get_chore(db, household, chore_id), actor=current_user
    )


@router.post(
    "/{chore_id}/complete",
    response_model=ChoreCompletionResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Book a chore as done",
)
def complete_chore(
    chore_id: int,
    current_user: MemberUser,
    db: DbSession,
    payload: ChoreCompleteRequest | None = None,
) -> ChoreCompletionResponse:
    """Anybody may do the work — the points go to whoever books it, or to `for_user_id`."""
    household = household_service.get_household(db, current_user)
    chore = chore_service.get_chore(db, household, chore_id)
    completion = chore_service.complete_chore(
        db, chore, current_user, for_user_id=payload.for_user_id if payload else None
    )
    return ChoreCompletionResponse.model_validate(completion)


@router.post(
    "/{chore_id}/undo-complete",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Take the last completion back",
)
def undo_complete(chore_id: int, current_user: MemberUser, db: DbSession) -> None:
    household = household_service.get_household(db, current_user)
    chore = chore_service.get_chore(db, household, chore_id)
    chore_service.undo_completion(db, chore, current_user)


@router.post(
    "/{chore_id}/remind",
    response_model=RemindResponse,
    summary="Nudge whoever is on duty",
)
def remind(
    chore_id: int, current_user: MemberUser, db: DbSession, limiters: RateLimitersDep
) -> RemindResponse:
    """Once a day per chore **and per person** — a reminder that arrives hourly is just
    nagging, but the limit belongs to whoever sends it. Otherwise the first reminder of
    the day would silence everybody else in the household."""
    household = household_service.get_household(db, current_user)
    chore = chore_service.get_chore(db, household, chore_id)

    key = f"chore:{chore.id}:by:{current_user.id}"
    limiters.reminder.check(key)
    responsible = chore_service.remind(db, chore, current_user)
    limiters.reminder.record(key)

    return RemindResponse(user_id=responsible.id, first_name=responsible.first_name)
