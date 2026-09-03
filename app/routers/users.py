"""The own account: profile, avatar and giving it up."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, File, Response, UploadFile, status

from app.deps import AuthenticatedUser, CurrentUser, DbSession, SettingsDep
from app.images import MAX_UPLOAD_BYTES
from app.schemas.auth import UserResponse
from app.schemas.users import DeleteAccountRequest, ProfileUpdateRequest
from app.security import clear_session_cookie
from app.services import users as user_service

router = APIRouter(tags=["profile"])

#: Uploads are read into memory once; the limit keeps that bounded.
UploadedFile = Annotated[UploadFile, File(description=f"At most {MAX_UPLOAD_BYTES} bytes")]


@router.patch("/me", response_model=UserResponse, summary="Change your profile")
def update_me(
    payload: ProfileUpdateRequest,
    current_user: AuthenticatedUser,
    db: DbSession,
    settings: SettingsDep,
) -> UserResponse:
    """Reachable even while a password change is pending — it is an account endpoint."""
    user = user_service.update_profile(
        db,
        current_user,
        settings,
        first_name=payload.first_name,
        last_name=payload.last_name,
        locale=payload.locale,
        email=payload.email,
    )
    return UserResponse.model_validate(user)


@router.post("/me/avatar", response_model=UserResponse, summary="Upload an avatar")
async def upload_avatar(
    current_user: CurrentUser,
    db: DbSession,
    settings: SettingsDep,
    file: UploadedFile,
) -> UserResponse:
    """The picture is decoded and re-encoded before it is stored — see `app/images.py`."""
    data = await file.read()
    return UserResponse.model_validate(user_service.set_avatar(db, current_user, data, settings))


@router.delete("/me", status_code=status.HTTP_204_NO_CONTENT, summary="Delete your account")
def delete_me(
    payload: DeleteAccountRequest,
    current_user: CurrentUser,
    db: DbSession,
    settings: SettingsDep,
    response: Response,
) -> None:
    """Anonymises the account and signs the device out — it cannot be undone."""
    user_service.delete_account(db, current_user, payload.password, settings)
    clear_session_cookie(response, settings)
