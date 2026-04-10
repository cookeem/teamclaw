from __future__ import annotations

import datetime as dt
import secrets
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile, status
from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.crud import add_audit_log, normalize_email, normalize_username, now_utc, user_to_public
from app.db import get_db
from app.deps import get_current_user
from app.i18n import tr_app
from app.mailer import send_password_reset_email
from app.orm_models import AuthRefreshToken, PasswordResetToken, User
from app.schemas import (
    AuthTokenResponse,
    ForgotPasswordRequest,
    ForgotPasswordResponse,
    LoginRequest,
    LogoutRequest,
    RefreshTokenRequest,
    ResetPasswordRequest,
    SignupRequest,
    UpdateMeRequest,
    UserPublic,
)
from app.security import (
    access_token_ttl_seconds,
    create_access_token,
    generate_refresh_token,
    hash_password,
    hash_token,
    refresh_expires_at,
    verify_password,
)

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])
me_router = APIRouter(prefix="/api/v1", tags=["users"])


def _to_aware_utc(value: dt.datetime) -> dt.datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=dt.timezone.utc)
    return value.astimezone(dt.timezone.utc)


def _is_user_session_inactive(*, user: User, now: dt.datetime, idle_timeout_seconds: int) -> bool:
    last_seen = user.last_active_at or user.last_login_at
    if last_seen is None:
        return False
    return (now - _to_aware_utc(last_seen)).total_seconds() > idle_timeout_seconds


def _generate_reset_code() -> str:
    return f"{secrets.randbelow(1_000_000):06d}"


async def _generate_unique_reset_code(
    *,
    db: AsyncSession,
    user_id: str,
    max_attempts: int = 50,
) -> str:
    for _ in range(max_attempts):
        code = _generate_reset_code()
        code_hash = hash_token(f"{user_id}:{code}")
        exists = await db.execute(
            select(PasswordResetToken.id).where(PasswordResetToken.token_hash == code_hash)
        )
        if exists.scalar_one_or_none() is None:
            return code
    raise RuntimeError("Unable to allocate unique reset code")


def _issue_refresh_token_row(
    *,
    db: AsyncSession,
    user: User,
    request: Request | None,
) -> tuple[str, AuthRefreshToken]:
    raw_refresh = generate_refresh_token()
    refresh_row = AuthRefreshToken(
        user_id=user.id,
        token_hash=hash_token(raw_refresh),
        expires_at=refresh_expires_at(),
        user_agent=(request.headers.get("user-agent") if request is not None else None),
        ip_address=(request.client.host if request is not None and request.client else None),
    )
    db.add(refresh_row)
    return raw_refresh, refresh_row


def _build_auth_response(*, user: User, refresh_token: str) -> AuthTokenResponse:
    access = create_access_token(
        user_id=user.id,
        username=user.username,
        is_admin=user.is_admin,
    )
    return AuthTokenResponse(
        access_token=access,
        refresh_token=refresh_token,
        expires_in=access_token_ttl_seconds(),
        user=user_to_public(user),
    )


@router.post("/signup", response_model=AuthTokenResponse)
async def signup(
    payload: SignupRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> AuthTokenResponse:
    email = normalize_email(payload.email)
    username = normalize_username(payload.username)

    existing = await db.execute(
        select(User).where(or_(User.email == email, User.username == username))
    )
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=tr_app(request.app, "auth.email_or_username_exists"),
        )

    user_count_result = await db.execute(select(func.count()).select_from(User))
    is_first_user = int(user_count_result.scalar_one() or 0) == 0

    now = now_utc()
    user = User(
        email=email,
        username=username,
        display_name=(payload.display_name.strip() if payload.display_name else None),
        password_hash=hash_password(payload.password),
        is_admin=is_first_user,
        is_blocked=False,
        last_login_at=now,
        last_active_at=now,
    )
    db.add(user)
    await db.flush()

    refresh_token, _ = _issue_refresh_token_row(db=db, user=user, request=request)
    await add_audit_log(
        db,
        actor_user_id=user.id,
        action="auth.signup",
        target_type="user",
        target_id=user.id,
        result="success",
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
        detail_json={"is_first_user": is_first_user},
    )
    await db.commit()
    await db.refresh(user)
    return _build_auth_response(user=user, refresh_token=refresh_token)


@router.post("/login", response_model=AuthTokenResponse)
async def login(
    payload: LoginRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> AuthTokenResponse:
    account = payload.account.strip()
    normalized_email = normalize_email(account)
    user_result = await db.execute(
        select(User).where(or_(User.email == normalized_email, User.username == account))
    )
    user = user_result.scalar_one_or_none()

    if user is None or not verify_password(payload.password, user.password_hash):
        await add_audit_log(
            db,
            actor_user_id=None,
            action="auth.login",
            target_type="user",
            target_id=user.id if user is not None else None,
            result="failed",
            ip_address=request.client.host if request.client else None,
            user_agent=request.headers.get("user-agent"),
            detail_json={"reason": "invalid_credentials", "account": account},
        )
        await db.commit()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=tr_app(request.app, "auth.invalid_credentials"),
        )

    if user.is_blocked:
        await add_audit_log(
            db,
            actor_user_id=user.id,
            action="auth.login",
            target_type="user",
            target_id=user.id,
            result="failed",
            ip_address=request.client.host if request.client else None,
            user_agent=request.headers.get("user-agent"),
            detail_json={"reason": "blocked"},
        )
        await db.commit()
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=tr_app(request.app, "auth.user_blocked"),
        )

    now = now_utc()
    user.last_login_at = now
    user.last_active_at = now
    refresh_token, _ = _issue_refresh_token_row(db=db, user=user, request=request)
    await add_audit_log(
        db,
        actor_user_id=user.id,
        action="auth.login",
        target_type="user",
        target_id=user.id,
        result="success",
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )
    await db.commit()
    await db.refresh(user)
    return _build_auth_response(user=user, refresh_token=refresh_token)


@router.post("/refresh", response_model=AuthTokenResponse)
async def refresh_token(
    payload: RefreshTokenRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> AuthTokenResponse:
    token_hash_value = hash_token(payload.refresh_token)
    current_time = now_utc()
    result = await db.execute(
        select(AuthRefreshToken).where(
            AuthRefreshToken.token_hash == token_hash_value,
            AuthRefreshToken.revoked_at.is_(None),
            AuthRefreshToken.expires_at > current_time,
        )
    )
    token_row = result.scalar_one_or_none()
    if token_row is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=tr_app(request.app, "auth.invalid_refresh_token"),
        )

    user = await db.get(User, token_row.user_id)
    if user is None or user.is_blocked:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=tr_app(request.app, "auth.user_blocked_or_missing"),
        )
    session_cfg = request.app.state.config.session
    if _is_user_session_inactive(
        user=user,
        now=current_time,
        idle_timeout_seconds=session_cfg.idle_timeout_seconds,
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=tr_app(request.app, "auth.session_inactive"),
        )

    token_row.revoked_at = current_time
    user.last_active_at = current_time
    new_refresh, new_row = _issue_refresh_token_row(db=db, user=user, request=request)
    token_row.replaced_by_token_id = new_row.id

    await add_audit_log(
        db,
        actor_user_id=user.id,
        action="auth.refresh",
        target_type="user",
        target_id=user.id,
        result="success",
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )
    await db.commit()
    await db.refresh(user)
    return _build_auth_response(user=user, refresh_token=new_refresh)


@router.post("/logout")
async def logout(
    payload: LogoutRequest,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, bool]:
    if payload.revoke_all:
        await db.execute(
            update(AuthRefreshToken)
            .where(
                AuthRefreshToken.user_id == user.id,
                AuthRefreshToken.revoked_at.is_(None),
            )
            .values(revoked_at=now_utc())
        )
    elif payload.refresh_token:
        await db.execute(
            update(AuthRefreshToken)
            .where(
                AuthRefreshToken.user_id == user.id,
                AuthRefreshToken.token_hash == hash_token(payload.refresh_token),
                AuthRefreshToken.revoked_at.is_(None),
            )
            .values(revoked_at=now_utc())
        )

    await add_audit_log(
        db,
        actor_user_id=user.id,
        action="auth.logout",
        target_type="user",
        target_id=user.id,
        result="success",
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
        detail_json={"revoke_all": payload.revoke_all},
    )
    await db.commit()
    return {"ok": True}


@router.post("/forgot-password", response_model=ForgotPasswordResponse)
async def forgot_password(
    payload: ForgotPasswordRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> ForgotPasswordResponse:
    cfg = request.app.state.config
    smtp_cfg = cfg.smtp
    reveal_error = bool(cfg.llm_message_debug)
    email = normalize_email(payload.email)
    result = await db.execute(select(User).where(User.email == email))
    user = result.scalar_one_or_none()

    if user is None:
        await add_audit_log(
            db,
            actor_user_id=None,
            action="auth.forgot_password",
            target_type="user",
            target_id=None,
            result="success",
            ip_address=request.client.host if request.client else None,
            user_agent=request.headers.get("user-agent"),
            detail_json={"email": email, "user_exists": False, "delivery": "none"},
        )
        await db.commit()
        return ForgotPasswordResponse(
            ok=True,
            delivery="none",
            message=tr_app(request.app, "auth.forgot.generic"),
        )

    try:
        raw_code = await _generate_unique_reset_code(db=db, user_id=user.id)
    except RuntimeError as exc:
        await add_audit_log(
            db,
            actor_user_id=user.id,
            action="auth.forgot_password",
            target_type="user",
            target_id=user.id,
            result="failed",
            ip_address=request.client.host if request.client else None,
            user_agent=request.headers.get("user-agent"),
            detail_json={"delivery": "failed", "reason": "reset_code_generation_failed", "error": str(exc)},
        )
        await db.commit()
        return ForgotPasswordResponse(
            ok=False,
            delivery="failed",
            message=tr_app(request.app, "auth.forgot.code_generation_failed"),
            error=str(exc) if reveal_error else None,
        )
    expires = now_utc() + dt.timedelta(seconds=smtp_cfg.reset_code_ttl_seconds)
    db.add(
        PasswordResetToken(
            user_id=user.id,
            token_hash=hash_token(f"{user.id}:{raw_code}"),
            expires_at=expires,
        )
    )
    response = ForgotPasswordResponse(
        ok=True,
        delivery="debug_token",
        reset_code=raw_code,
        reset_token=raw_code,
        expires_at=expires,
        message=tr_app(request.app, "auth.forgot.smtp_debug_mode"),
    )
    audit_result = "success"
    detail_json: dict[str, object] = {"delivery": "debug_token"}

    if smtp_cfg.enabled and not smtp_cfg.is_configured:
        audit_result = "failed"
        detail_json = {
            "delivery": "failed",
            "reason": "smtp_not_configured",
        }
        response = ForgotPasswordResponse(
            ok=True,
            delivery="failed",
            expires_at=expires,
            message=tr_app(request.app, "auth.forgot.smtp_not_configured"),
            error="smtp_not_configured" if reveal_error else None,
        )
    elif smtp_cfg.can_send:
        try:
            await send_password_reset_email(
                config=smtp_cfg,
                to_email=user.email,
                code=raw_code,
                expires_at=expires,
            )
            response = ForgotPasswordResponse(
                ok=True,
                delivery="email",
                expires_at=expires,
                message=tr_app(request.app, "auth.forgot.smtp_sent"),
            )
            detail_json["delivery"] = "email"
        except Exception as exc:
            audit_result = "failed"
            detail_json["delivery"] = "failed"
            detail_json["smtp_error"] = str(exc)
            response = ForgotPasswordResponse(
                ok=True,
                delivery="failed",
                expires_at=expires,
                message=tr_app(request.app, "auth.forgot.smtp_failed"),
                error=str(exc) if reveal_error else None,
            )

    await add_audit_log(
        db,
        actor_user_id=user.id,
        action="auth.forgot_password",
        target_type="user",
        target_id=user.id,
        result=audit_result,
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
        detail_json=detail_json,
    )
    await db.commit()
    return response


@router.post("/reset-password")
async def reset_password(
    payload: ResetPasswordRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> dict[str, bool]:
    email = normalize_email(payload.email)
    user_result = await db.execute(select(User).where(User.email == email))
    user = user_result.scalar_one_or_none()
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=tr_app(request.app, "auth.reset.invalid_email_or_code"),
        )

    token_hash_value = hash_token(f"{user.id}:{payload.code}")
    current_time = now_utc()
    result = await db.execute(
        select(PasswordResetToken).where(
            PasswordResetToken.user_id == user.id,
            PasswordResetToken.token_hash == token_hash_value,
            PasswordResetToken.used_at.is_(None),
            PasswordResetToken.expires_at > current_time,
        )
    )
    token_row = result.scalar_one_or_none()
    if token_row is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=tr_app(request.app, "auth.reset.invalid_or_expired_code"),
        )

    user.password_hash = hash_password(payload.new_password)
    token_row.used_at = current_time
    await db.execute(
        update(AuthRefreshToken)
        .where(
            AuthRefreshToken.user_id == user.id,
            AuthRefreshToken.revoked_at.is_(None),
        )
        .values(revoked_at=current_time)
    )

    await add_audit_log(
        db,
        actor_user_id=user.id,
        action="auth.reset_password",
        target_type="user",
        target_id=user.id,
        result="success",
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )
    await db.commit()
    return {"ok": True}


@me_router.get("/me", response_model=UserPublic)
async def get_me(user: User = Depends(get_current_user)) -> UserPublic:
    return user_to_public(user)


def _avatar_extension(upload: UploadFile) -> str | None:
    allowed_by_content_type = {
        "image/png": ".png",
        "image/jpeg": ".jpg",
        "image/webp": ".webp",
        "image/gif": ".gif",
    }
    if isinstance(upload.content_type, str):
        mapped = allowed_by_content_type.get(upload.content_type.strip().lower())
        if mapped:
            return mapped

    suffix = Path(upload.filename or "").suffix.lower()
    if suffix in {".png", ".jpg", ".jpeg", ".webp", ".gif"}:
        return ".jpg" if suffix == ".jpeg" else suffix
    return None


@me_router.post("/me/avatar", response_model=UserPublic)
async def upload_my_avatar(
    request: Request,
    file: UploadFile = File(...),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> UserPublic:
    current_user = await db.get(User, user.id)
    if current_user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=tr_app(request.app, "auth.user_not_found"),
        )

    extension = _avatar_extension(file)
    if extension is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=tr_app(request.app, "user.avatar_type_invalid"),
        )

    avatars_dir = Path(getattr(request.app.state, "avatars_dir", "avatars")).resolve()
    avatars_dir.mkdir(parents=True, exist_ok=True)

    old_path = None
    if isinstance(current_user.avatar_url, str) and current_user.avatar_url.strip():
        old_name = Path(current_user.avatar_url.strip()).name
        old_path = (avatars_dir / old_name).resolve()

    safe_name = f"{user.id}-{int(now_utc().timestamp())}{extension}"
    target_path = (avatars_dir / safe_name).resolve()
    if avatars_dir not in target_path.parents:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=tr_app(request.app, "user.avatar_upload_failed"),
        )

    data = await file.read()
    if not data:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=tr_app(request.app, "user.avatar_file_required"),
        )

    try:
        target_path.write_bytes(data)
    except OSError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=tr_app(request.app, "user.avatar_upload_failed"),
        ) from exc

    current_user.avatar_url = f"/api/v1/avatars/{safe_name}"
    await add_audit_log(
        db,
        actor_user_id=current_user.id,
        action="user.upload_avatar",
        target_type="user",
        target_id=current_user.id,
        result="success",
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )
    await db.commit()
    await db.refresh(current_user)

    if old_path and old_path.exists() and old_path != target_path:
        try:
            old_path.unlink()
        except OSError:
            pass

    return user_to_public(current_user)


@me_router.patch("/me", response_model=UserPublic)
async def update_me(
    payload: UpdateMeRequest,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> UserPublic:
    current_user = await db.get(User, user.id)
    if current_user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=tr_app(request.app, "auth.user_not_found"),
        )

    if payload.display_name is not None:
        current_user.display_name = payload.display_name.strip() or None

    if payload.email is not None:
        new_email = normalize_email(payload.email)
        existing = await db.execute(
            select(User).where(User.email == new_email, User.id != current_user.id)
        )
        if existing.scalar_one_or_none() is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=tr_app(request.app, "user.email_exists"),
            )
        current_user.email = new_email

    if payload.new_password is not None:
        if not payload.current_password:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=tr_app(request.app, "user.current_password_required"),
            )
        if not verify_password(payload.current_password, current_user.password_hash):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=tr_app(request.app, "user.current_password_incorrect"),
            )
        current_user.password_hash = hash_password(payload.new_password)
        await db.execute(
            update(AuthRefreshToken)
            .where(
                AuthRefreshToken.user_id == current_user.id,
                AuthRefreshToken.revoked_at.is_(None),
            )
            .values(revoked_at=now_utc())
        )

    await add_audit_log(
        db,
        actor_user_id=current_user.id,
        action="user.update_profile",
        target_type="user",
        target_id=current_user.id,
        result="success",
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )
    await db.commit()
    await db.refresh(current_user)
    return user_to_public(current_user)
