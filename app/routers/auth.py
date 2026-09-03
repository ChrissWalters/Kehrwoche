"""Registration, sign-in, sign-out, sessions and the profile of the signed-in person."""

from __future__ import annotations

from fastapi import APIRouter, Request, Response, status

from app.deps import AuthenticatedUser, DbSession, RateLimitersDep, SettingsDep, client_ip
from app.errors import AppError, ErrorCode
from app.schemas.auth import (
    ChangePasswordRequest,
    LoginRequest,
    RegisterRequest,
    SessionResponse,
    UserResponse,
)
from app.security import (
    SESSION_COOKIE_NAME,
    clear_csrf_cookie,
    clear_session_cookie,
    generate_csrf_token,
    hash_session_token,
    set_csrf_cookie,
    set_session_cookie,
)
from app.services import auth as auth_service

router = APIRouter(tags=["auth"])

USER_AGENT_HEADER = "user-agent"


@router.post(
    "/auth/register",
    response_model=UserResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create an account",
)
def register(
    payload: RegisterRequest,
    request: Request,
    db: DbSession,
    settings: SettingsDep,
    limiters: RateLimitersDep,
) -> UserResponse:
    """Registration does not sign the person in — the client calls ``login`` next."""
    if not settings.registration_open:
        # A closed instance says so plainly instead of pretending the form worked.
        raise AppError(
            403,
            ErrorCode.FORBIDDEN,
            "Registration is closed on this instance. Ask an admin for an account.",
            message_key="error.registration_closed",
        )

    ip_key = f"ip:{client_ip(request)}"
    limiters.register.check(ip_key)
    limiters.register.record(ip_key)

    user = auth_service.register_user(
        db,
        settings,
        username=payload.username,
        email=payload.email,
        password=payload.password,
        first_name=payload.first_name,
        last_name=payload.last_name,
        locale=payload.locale,
    )
    return UserResponse.model_validate(user)


@router.post("/auth/login", response_model=UserResponse, summary="Sign in")
def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    db: DbSession,
    settings: SettingsDep,
    limiters: RateLimitersDep,
) -> UserResponse:
    # Both keys are checked: one address must not be guessable from many machines, and
    # one machine must not work its way through many addresses.
    ip_key = f"ip:{client_ip(request)}"
    account_key = f"account:{auth_service.normalise_username(payload.username)}"
    limiters.login.check(ip_key)
    limiters.login.check(account_key)

    try:
        user = auth_service.authenticate(db, payload.username, payload.password)
    except AppError:
        limiters.login.record(ip_key)
        limiters.login.record(account_key)
        raise

    limiters.login.reset(ip_key)
    limiters.login.reset(account_key)

    token = auth_service.start_session(
        db, user, settings, user_agent=request.headers.get(USER_AGENT_HEADER)
    )
    set_session_cookie(response, token, settings)
    # A fresh CSRF token per sign-in, so a token from before never stays in play.
    set_csrf_cookie(response, generate_csrf_token(), settings)
    return UserResponse.model_validate(user)


@router.post(
    "/auth/logout",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Sign out on this device",
)
def logout(request: Request, response: Response, db: DbSession, settings: SettingsDep) -> None:
    auth_service.end_session(db, request.cookies.get(SESSION_COOKIE_NAME))
    clear_session_cookie(response, settings)
    clear_csrf_cookie(response, settings)


@router.post(
    "/auth/change-password",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Change the password and sign out other devices",
)
def change_password(
    payload: ChangePasswordRequest,
    request: Request,
    current_user: AuthenticatedUser,
    db: DbSession,
) -> None:
    auth_service.change_password(
        db,
        current_user,
        payload.current_password,
        payload.new_password,
        keep_token=request.cookies.get(SESSION_COOKIE_NAME),
    )


@router.get(
    "/auth/sessions",
    response_model=list[SessionResponse],
    summary="Devices this account is signed in on",
)
def list_sessions(
    request: Request, current_user: AuthenticatedUser, db: DbSession
) -> list[SessionResponse]:
    token = request.cookies.get(SESSION_COOKIE_NAME)
    current_hash = hash_session_token(token) if token else None
    sessions = []
    for session in auth_service.list_sessions(db, current_user):
        response = SessionResponse.model_validate(session)
        response.current = session.token_hash == current_hash
        sessions.append(response)
    return sessions


@router.delete(
    "/auth/sessions/{session_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Sign out one device",
)
def revoke_session(session_id: int, current_user: AuthenticatedUser, db: DbSession) -> None:
    auth_service.revoke_session(db, current_user, session_id)


@router.get("/me", response_model=UserResponse, summary="The signed-in person")
def me(current_user: AuthenticatedUser) -> UserResponse:
    return UserResponse.model_validate(current_user)
