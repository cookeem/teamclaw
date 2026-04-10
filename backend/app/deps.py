from __future__ import annotations

import datetime as dt

from fastapi import Depends, HTTPException, Query, Request, WebSocket, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.i18n import tr_app
from app.orm_models import User
from app.security import decode_access_token

bearer_scheme = HTTPBearer(auto_error=False)


def _now_utc() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _to_aware_utc(value: dt.datetime) -> dt.datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=dt.timezone.utc)
    return value.astimezone(dt.timezone.utc)


def _resolve_session_settings(app: object | None) -> tuple[int, int]:
    try:
        state = getattr(app, "state", None)
        config = getattr(state, "config", None)
        session = getattr(config, "session", None)
        idle = int(getattr(session, "idle_timeout_seconds", 3600))
        touch = int(getattr(session, "touch_interval_seconds", 60))
    except Exception:
        return 3600, 60
    if idle <= 0:
        idle = 3600
    if touch <= 0:
        touch = 60
    return idle, touch


def _session_is_inactive(*, user: User, now: dt.datetime, idle_timeout_seconds: int) -> bool:
    last_seen = user.last_active_at or user.last_login_at
    if last_seen is None:
        return False
    return (now - _to_aware_utc(last_seen)).total_seconds() > idle_timeout_seconds


async def _touch_user_activity(
    *,
    db: AsyncSession,
    user: User,
    now: dt.datetime,
    touch_interval_seconds: int,
) -> None:
    previous = user.last_active_at
    if previous is not None:
        elapsed = (now - _to_aware_utc(previous)).total_seconds()
        if elapsed < touch_interval_seconds:
            return
    user.last_active_at = now
    await db.commit()


async def validate_and_touch_user_session(
    *,
    db: AsyncSession,
    user: User,
    idle_timeout_seconds: int,
    touch_interval_seconds: int,
) -> bool:
    now = _now_utc()
    if _session_is_inactive(
        user=user,
        now=now,
        idle_timeout_seconds=idle_timeout_seconds,
    ):
        return False
    await _touch_user_activity(
        db=db,
        user=user,
        now=now,
        touch_interval_seconds=touch_interval_seconds,
    )
    return True


async def get_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: AsyncSession = Depends(get_db),
) -> User:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=tr_app(request.app, "auth.required"),
        )
    return await _get_current_user_from_token(request=request, db=db, token=credentials.credentials)


async def get_current_user_with_query_token(
    request: Request,
    token: str | None = Query(default=None),
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: AsyncSession = Depends(get_db),
) -> User:
    raw_token: str | None = None
    if credentials is not None and credentials.scheme.lower() == "bearer":
        raw_token = credentials.credentials
    elif isinstance(token, str) and token.strip():
        raw_token = token.strip()

    if not raw_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=tr_app(request.app, "auth.required"),
        )
    return await _get_current_user_from_token(request=request, db=db, token=raw_token)


async def _get_current_user_from_token(
    *,
    request: Request,
    db: AsyncSession,
    token: str,
) -> User:
    payload = decode_access_token(token)
    if payload is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=tr_app(request.app, "auth.invalid_access_token"),
        )

    user_id = payload.get("sub")
    if not isinstance(user_id, str) or not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=tr_app(request.app, "auth.invalid_access_payload"),
        )

    user = await db.get(User, user_id)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=tr_app(request.app, "auth.user_not_found"),
        )
    if user.is_blocked:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=tr_app(request.app, "auth.user_blocked"),
        )
    idle_timeout_seconds, touch_interval_seconds = _resolve_session_settings(request.app)
    is_session_active = await validate_and_touch_user_session(
        db=db,
        user=user,
        idle_timeout_seconds=idle_timeout_seconds,
        touch_interval_seconds=touch_interval_seconds,
    )
    if not is_session_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=tr_app(request.app, "auth.session_inactive"),
        )
    return user


async def get_current_admin_user(request: Request, user: User = Depends(get_current_user)) -> User:
    if not user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=tr_app(request.app, "auth.admin_required"),
        )
    return user


def extract_websocket_token(websocket: WebSocket) -> str | None:
    query_token = websocket.query_params.get("token")
    if isinstance(query_token, str) and query_token.strip():
        return query_token.strip()

    auth_header = websocket.headers.get("authorization")
    if isinstance(auth_header, str) and auth_header.lower().startswith("bearer "):
        return auth_header[7:].strip()
    return None


async def authenticate_websocket_user(websocket: WebSocket, db: AsyncSession) -> User | None:
    token = extract_websocket_token(websocket)
    if not token:
        return None
    payload = decode_access_token(token)
    if payload is None:
        return None
    user_id = payload.get("sub")
    if not isinstance(user_id, str) or not user_id:
        return None
    user = await db.get(User, user_id)
    if user is None or user.is_blocked:
        return None
    idle_timeout_seconds, touch_interval_seconds = _resolve_session_settings(websocket.app)
    if not await validate_and_touch_user_session(
        db=db,
        user=user,
        idle_timeout_seconds=idle_timeout_seconds,
        touch_interval_seconds=touch_interval_seconds,
    ):
        return None
    return user


async def find_conversation_for_user(
    *,
    db: AsyncSession,
    conversation_id: str,
    user_id: str,
):
    from app.orm_models import Conversation

    result = await db.execute(
        select(Conversation).where(
            Conversation.id == conversation_id,
            Conversation.user_id == user_id,
            Conversation.deleted_at.is_(None),
        )
    )
    return result.scalar_one_or_none()
