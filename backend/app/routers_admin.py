from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi import File, UploadFile
from sqlalchemy import and_, delete, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.crud import (
    add_audit_log,
    audit_to_public,
    normalize_email,
    normalize_username,
    now_utc,
    user_to_public,
)
from app.db import get_db
from app.deps import get_current_admin_user
from app.i18n import tr_app
from app.orm_models import AuditLog, AuthRefreshToken, User
from app.schemas import AdminCreateUserRequest, AdminUpdateUserRequest, AuditLogPublic, UserPublic
from app.security import hash_password

router = APIRouter(prefix="/api/v1/admin", tags=["admin"])


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


@router.get("/users")
async def list_users(
    search: str | None = Query(default=None, max_length=128),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    _: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, object]:
    where_clause = []
    if isinstance(search, str) and search.strip():
        q = f"%{search.strip().lower()}%"
        where_clause.append(
            or_(
                func.lower(User.email).like(q),
                func.lower(User.username).like(q),
                func.lower(func.coalesce(User.display_name, "")).like(q),
            )
        )

    total_query = select(func.count()).select_from(User)
    if where_clause:
        total_query = total_query.where(and_(*where_clause))
    total = int((await db.execute(total_query)).scalar_one() or 0)

    query = select(User).order_by(User.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
    if where_clause:
        query = query.where(and_(*where_clause))

    items = (await db.execute(query)).scalars().all()
    return {
        "items": [user_to_public(user).model_dump() for user in items],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.patch("/users/{user_id}", response_model=UserPublic)
async def update_user(
    user_id: str,
    payload: AdminUpdateUserRequest,
    request: Request,
    admin_user: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
) -> UserPublic:
    user = await db.get(User, user_id)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=tr_app(request.app, "admin.user_not_found"),
        )

    if payload.display_name is not None:
        user.display_name = payload.display_name.strip() or None

    if payload.email is not None:
        new_email = normalize_email(payload.email)
        dup = await db.execute(select(User).where(User.email == new_email, User.id != user.id))
        if dup.scalar_one_or_none() is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=tr_app(request.app, "user.email_exists"),
            )
        user.email = new_email

    if payload.new_password is not None:
        user.password_hash = hash_password(payload.new_password)
        await db.execute(
            update(AuthRefreshToken)
            .where(
                AuthRefreshToken.user_id == user.id,
                AuthRefreshToken.revoked_at.is_(None),
            )
            .values(revoked_at=now_utc())
        )

    is_self_update = user.id == admin_user.id
    if is_self_update and (
        (payload.is_admin is not None and payload.is_admin != user.is_admin)
        or (payload.is_blocked is not None and payload.is_blocked != user.is_blocked)
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=tr_app(request.app, "admin.self_admin_block_update_forbidden"),
        )

    should_disable_admin = payload.is_admin is False and user.is_admin
    should_block_admin = payload.is_blocked is True and user.is_admin and not user.is_blocked
    if should_disable_admin or should_block_admin:
        active_admin_count = int(
            (
                await db.execute(
                    select(func.count()).select_from(User).where(
                        User.is_admin.is_(True),
                        User.is_blocked.is_(False),
                    )
                )
            ).scalar_one()
            or 0
        )
        if active_admin_count <= 1:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=tr_app(request.app, "admin.last_active_admin_required"),
            )

    if payload.is_admin is not None:
        user.is_admin = payload.is_admin
    if payload.is_blocked is not None:
        user.is_blocked = payload.is_blocked
    if "conversation_limit" in payload.model_fields_set:
        user.conversation_limit = payload.conversation_limit

    audit_detail = payload.model_dump(exclude_unset=True)
    if "new_password" in audit_detail:
        audit_detail["new_password"] = "***"

    await add_audit_log(
        db,
        actor_user_id=admin_user.id,
        action="admin.update_user",
        target_type="user",
        target_id=user.id,
        result="success",
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
        detail_json=audit_detail,
    )
    await db.commit()
    await db.refresh(user)
    return user_to_public(user)


@router.post("/users", response_model=UserPublic, status_code=status.HTTP_201_CREATED)
async def create_user(
    payload: AdminCreateUserRequest,
    request: Request,
    admin_user: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
) -> UserPublic:
    email = normalize_email(payload.email)
    username = normalize_username(payload.username)

    duplicate = await db.execute(
        select(User).where(
            or_(User.email == email, User.username == username),
        )
    )
    existing = duplicate.scalar_one_or_none()
    if existing is not None:
        key = "user.email_exists" if existing.email == email else "user.username_exists"
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=tr_app(request.app, key),
        )

    user = User(
        email=email,
        username=username,
        display_name=(payload.display_name.strip() if payload.display_name else None),
        password_hash=hash_password(payload.password),
        is_admin=payload.is_admin,
        is_blocked=payload.is_blocked,
        conversation_limit=payload.conversation_limit,
    )
    db.add(user)
    await db.flush()

    await add_audit_log(
        db,
        actor_user_id=admin_user.id,
        action="admin.create_user",
        target_type="user",
        target_id=user.id,
        result="success",
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
        detail_json={
            "email": user.email,
            "username": user.username,
            "display_name": user.display_name,
            "is_admin": user.is_admin,
            "is_blocked": user.is_blocked,
            "conversation_limit": user.conversation_limit,
        },
    )
    await db.commit()
    await db.refresh(user)
    return user_to_public(user)


@router.delete("/users/{user_id}")
async def delete_user(
    user_id: str,
    request: Request,
    admin_user: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, bool]:
    user = await db.get(User, user_id)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=tr_app(request.app, "admin.user_not_found"),
        )

    if user.id == admin_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=tr_app(request.app, "admin.self_delete_forbidden"),
        )

    if user.is_admin and not user.is_blocked:
        active_admin_count = int(
            (
                await db.execute(
                    select(func.count()).select_from(User).where(
                        User.is_admin.is_(True),
                        User.is_blocked.is_(False),
                    )
                )
            ).scalar_one()
            or 0
        )
        if active_admin_count <= 1:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=tr_app(request.app, "admin.last_active_admin_required"),
            )

    await db.execute(
        update(AuthRefreshToken)
        .where(
            AuthRefreshToken.user_id == user.id,
            AuthRefreshToken.revoked_at.is_(None),
        )
        .values(revoked_at=now_utc())
    )
    await db.execute(delete(User).where(User.id == user.id))

    await add_audit_log(
        db,
        actor_user_id=admin_user.id,
        action="admin.delete_user",
        target_type="user",
        target_id=user.id,
        result="success",
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
        detail_json={"email": user.email, "username": user.username},
    )
    await db.commit()
    return {"ok": True}


@router.post("/users/{user_id}/avatar", response_model=UserPublic)
async def admin_upload_user_avatar(
    user_id: str,
    request: Request,
    file: UploadFile = File(...),
    admin_user: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
) -> UserPublic:
    user = await db.get(User, user_id)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=tr_app(request.app, "admin.user_not_found"),
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
    if isinstance(user.avatar_url, str) and user.avatar_url.strip():
        old_name = Path(user.avatar_url.strip()).name
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

    user.avatar_url = f"/api/v1/avatars/{safe_name}"

    await add_audit_log(
        db,
        actor_user_id=admin_user.id,
        action="admin.upload_user_avatar",
        target_type="user",
        target_id=user.id,
        result="success",
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )
    await db.commit()
    await db.refresh(user)

    if old_path and old_path.exists() and old_path != target_path:
        try:
            old_path.unlink()
        except OSError:
            pass

    return user_to_public(user)


@router.get("/audit-logs")
async def list_audit_logs(
    action: str | None = Query(default=None, max_length=128),
    result: str | None = Query(default=None, max_length=32),
    actor_user_id: str | None = Query(default=None, max_length=36),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    _: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, object]:
    where_clause = []
    if action:
        where_clause.append(AuditLog.action == action)
    if result:
        where_clause.append(AuditLog.result == result)
    if actor_user_id:
        where_clause.append(AuditLog.actor_user_id == actor_user_id)

    total_query = select(func.count()).select_from(AuditLog)
    if where_clause:
        total_query = total_query.where(and_(*where_clause))
    total = int((await db.execute(total_query)).scalar_one() or 0)

    query = (
        select(AuditLog)
        .order_by(AuditLog.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    if where_clause:
        query = query.where(and_(*where_clause))
    items = (await db.execute(query)).scalars().all()
    return {
        "items": [audit_to_public(item).model_dump() for item in items],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.get("/audit-logs/{log_id}", response_model=AuditLogPublic)
async def get_audit_log(
    log_id: str,
    request: Request,
    _: User = Depends(get_current_admin_user),
    db: AsyncSession = Depends(get_db),
) -> AuditLogPublic:
    log_item = await db.get(AuditLog, log_id)
    if log_item is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=tr_app(request.app, "admin.audit_log_not_found"),
        )
    return audit_to_public(log_item)
