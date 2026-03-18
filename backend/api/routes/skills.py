from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import shutil
import re
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
import yaml
from sqlalchemy import desc, func, or_, select
from sqlalchemy.orm import Session

from backend.api.deps import get_current_user, require_admin
from backend.core.config import get_settings
from backend.core.database import get_db
from backend.core.models import (
    Conversation,
    ConversationMessage,
    Skill,
    SkillAuditLog,
    SkillGroup,
    SkillGroupSkill,
    SkillGroupUser,
    User,
)

router = APIRouter(prefix="/skills", tags=["skills"])

SKILL_NAME_RE = re.compile(r"^[A-Za-z0-9-]+$")

ROOT_DIR = Path.cwd()


def _resolve_storage_path(path_str: str) -> Path:
    path = Path(str(path_str)).expanduser()
    if not path.is_absolute():
        path = (ROOT_DIR / path).resolve()
    return path


def _builtin_skill_names() -> set[str]:
    settings = get_settings()
    extra = settings.model_extra or {}
    skills_cfg = extra.get("skills", {}) or {}
    if not skills_cfg.get("enabled", True):
        return set()
    source_dirs = skills_cfg.get("directories", []) or []
    resolved: list[Path] = []
    for entry in source_dirs:
        path = Path(str(entry)).expanduser()
        if not path.is_absolute():
            path = (ROOT_DIR / path).resolve()
        resolved.append(path)
    valid = [p for p in resolved if p.exists() and p.is_dir()]
    if not valid:
        return set()
    chosen = valid[-1]
    names: set[str] = set()
    for folder in chosen.iterdir():
        if folder.is_dir() and (folder / "SKILL.md").exists():
            names.add(folder.name)
    return names


def _builtin_skills() -> list[dict]:
    settings = get_settings()
    extra = settings.model_extra or {}
    skills_cfg = extra.get("skills", {}) or {}
    if not skills_cfg.get("enabled", True):
        return []
    source_dirs = skills_cfg.get("directories", []) or []
    resolved: list[Path] = []
    for entry in source_dirs:
        path = Path(str(entry)).expanduser()
        if not path.is_absolute():
            path = (ROOT_DIR / path).resolve()
        resolved.append(path)
    valid = [p for p in resolved if p.exists() and p.is_dir()]
    if not valid:
        return []
    chosen = valid[-1]
    items: list[dict] = []
    for folder in sorted(chosen.iterdir(), key=lambda p: p.name):
        if not folder.is_dir():
            continue
        skill_md = folder / "SKILL.md"
        if not skill_md.exists():
            continue
        meta = _read_skill_frontmatter(skill_md)
        name = meta.get("name") or folder.name
        description = meta.get("description") or ""
        display_name = meta.get("display_name") or meta.get("title") or ""
        items.append(
            {
                "name": str(name),
                "display_name": str(display_name) if display_name else None,
                "description": str(description) if description else None,
                "source_type": "builtin",
            }
        )
    return items


_storage = get_settings().skill_storage
USERSKILLS_DIR = _resolve_storage_path(_storage.userskills_dir)
PRESKILLS_DIR = _resolve_storage_path(_storage.preskills_dir)
PUBLISHED_DIR = _resolve_storage_path(_storage.skills_dir)
AGENTSKILLS_DIR = _resolve_storage_path(_storage.agentskills_dir)


class CreateSkillRequest(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    display_name: str | None = Field(default=None, max_length=128)
    description: str | None = None


class UpdateSkillRequest(BaseModel):
    name: str | None = Field(default=None, max_length=64)
    display_name: str | None = Field(default=None, max_length=128)
    description: str | None = None
    is_public: bool | None = None
    is_public_edit: bool | None = None


class PublishSkillRequest(BaseModel):
    comment: str | None = None


class RejectSkillRequest(BaseModel):
    comment: str | None = None


class CreateGroupRequest(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    description: str | None = None


class AddSkillToGroupRequest(BaseModel):
    skill_id: str


class AddUserToGroupRequest(BaseModel):
    user_id: str


class FileWriteRequest(BaseModel):
    path: str
    content: str | None = ""


class DirCreateRequest(BaseModel):
    path: str


class RenameRequest(BaseModel):
    from_path: str
    to_path: str


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _serialize_skill(skill: Skill) -> dict:
    return {
        "id": skill.id,
        "owner_user_id": skill.owner_user_id,
        "source_type": skill.source_type,
        "status": skill.status,
        "name": skill.name,
        "display_name": skill.display_name,
        "description": skill.description,
        "is_public": bool(skill.is_public),
        "is_public_edit": bool(skill.is_public_edit),
        "usage_count": int(skill.usage_count or 0),
        "cloned_from_skill_id": skill.cloned_from_skill_id,
        "pending_comment": skill.pending_comment,
        "published_at": skill.published_at.isoformat() if skill.published_at else None,
        "published_by": skill.published_by,
        "rejected_at": skill.rejected_at.isoformat() if skill.rejected_at else None,
        "rejected_by": skill.rejected_by,
        "rejected_reason": skill.rejected_reason,
        "created_at": skill.created_at.isoformat() if skill.created_at else None,
        "updated_at": skill.updated_at.isoformat() if skill.updated_at else None,
    }


def _serialize_group(group: SkillGroup) -> dict:
    return {
        "id": group.id,
        "name": group.name,
        "description": group.description,
        "created_by": group.created_by,
        "created_at": group.created_at.isoformat() if group.created_at else None,
        "updated_at": group.updated_at.isoformat() if group.updated_at else None,
    }


def _skill_dir(skill: Skill) -> Path:
    if skill.status == "published":
        return PUBLISHED_DIR / skill.name
    if skill.status == "pending":
        return PRESKILLS_DIR / skill.owner_user_id / skill.name
    if skill.source_type == "agent":
        return AGENTSKILLS_DIR / skill.owner_user_id / skill.name
    return USERSKILLS_DIR / skill.owner_user_id / skill.name


def _ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def _ensure_skill_scaffold(skill: Skill) -> None:
    skill_dir = _skill_dir(skill)
    _ensure_dir(skill_dir)
    skill_md = skill_dir / "SKILL.md"
    if not skill_md.exists():
        title = skill.display_name or skill.name
        content = f"---\nname: {skill.name}\ndescription: \"\"\nlicense: MIT\ncompatibility: designed for deepagents-cli\n---\n\n# {title}\n"
        skill_md.write_text(content, encoding="utf-8")


def _read_skill_frontmatter(skill_md: Path) -> dict:
    if not skill_md.exists():
        return {}
    try:
        text = skill_md.read_text(encoding="utf-8")
    except Exception:
        return {}
    if not text.startswith("---"):
        return {}
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}
    raw = parts[1]
    try:
        data = yaml.safe_load(raw) or {}
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _update_skill_frontmatter_name(skill_md: Path, new_name: str) -> None:
    if not skill_md.exists():
        return
    try:
        text = skill_md.read_text(encoding="utf-8")
    except Exception:
        return
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            meta = yaml.safe_load(parts[1]) or {}
            if not isinstance(meta, dict):
                meta = {}
            meta["name"] = new_name
            front = yaml.safe_dump(meta, allow_unicode=True, sort_keys=False).strip()
            rebuilt = f"---\n{front}\n---{parts[2]}"
            skill_md.write_text(rebuilt, encoding="utf-8")
            return
    # Fallback: prepend frontmatter if missing
    front = f"---\nname: {new_name}\ndescription: \"\"\nlicense: MIT\ncompatibility: designed for deepagents-cli\n---\n\n"
    skill_md.write_text(front + text, encoding="utf-8")


def _normalize_name(name: str) -> str:
    trimmed = name.strip()
    if not trimmed or not SKILL_NAME_RE.fullmatch(trimmed):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Skill name must be english letters, numbers, or hyphen")
    return trimmed


def _name_exists_for_owner(db: Session, owner_id: str, name: str, exclude_id: str | None = None) -> bool:
    stmt = select(Skill.id).where(Skill.owner_user_id == owner_id, Skill.name == name)
    if exclude_id:
        stmt = stmt.where(Skill.id != exclude_id)
    return db.scalar(stmt) is not None


def _userskill_dir_exists(owner_id: str, name: str) -> bool:
    return (USERSKILLS_DIR / owner_id / name).exists()


def _name_exists_globally(db: Session, name: str, statuses: tuple[str, ...] = ("pending", "published"), exclude_id: str | None = None) -> bool:
    stmt = select(Skill.id).where(Skill.name == name, Skill.status.in_(statuses))
    if exclude_id:
        stmt = stmt.where(Skill.id != exclude_id)
    return db.scalar(stmt) is not None


def _unique_name(base: str, exists_fn) -> str:
    if not exists_fn(base):
        return base
    for _ in range(50):
        suffix = uuid4().hex[:6]
        candidate = f"{base}-{suffix}"
        if not exists_fn(candidate):
            return candidate
    raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Unable to generate unique skill name")


def _move_dir(src: Path, dest: Path) -> None:
    if not src.exists():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Skill directory missing")
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Target skill directory already exists")
    shutil.move(str(src), str(dest))


def _copy_dir(src: Path, dest: Path) -> None:
    if not src.exists():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Skill directory missing")
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Target skill directory already exists")
    shutil.copytree(src, dest)


def _resolve_path(root: Path, rel_path: str) -> Path:
    if not rel_path:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Path is required")
    rel = Path(rel_path)
    if rel.is_absolute():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Absolute path is not allowed")
    target = (root / rel).resolve()
    root_resolved = root.resolve()
    if root_resolved != target and root_resolved not in target.parents:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid path")
    return target


def _can_view_skill(db: Session, user: User, skill: Skill) -> bool:
    if user.is_admin:
        return True
    if skill.owner_user_id == user.id:
        return True
    if skill.status != "published":
        return False
    if skill.is_public:
        return True
    subq = select(SkillGroupSkill.skill_id).join(
        SkillGroupUser, SkillGroupSkill.group_id == SkillGroupUser.group_id
    ).where(SkillGroupUser.user_id == user.id)
    return db.scalar(select(Skill.id).where(Skill.id == skill.id, Skill.id.in_(subq))) is not None


def _require_view_skill(db: Session, user: User, skill: Skill) -> None:
    if not _can_view_skill(db, user, skill):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not allowed")


def _can_edit_skill(user: User, skill: Skill) -> bool:
    if user.is_admin:
        return True
    if skill.owner_user_id != user.id:
        return False
    if skill.source_type == "agent":
        return False
    return skill.status in {"draft", "rejected"}


def _require_edit_skill(user: User, skill: Skill) -> None:
    if not _can_edit_skill(user, skill):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not allowed")


def _log(db: Session, skill_id: str, actor_id: str, action: str, detail: dict | None = None) -> None:
    log = SkillAuditLog(
        id=str(uuid4()),
        skill_id=skill_id,
        action=action,
        actor_user_id=actor_id,
        detail=detail or {},
        created_at=_now(),
    )
    db.add(log)


@router.get("/mine")
def list_my_skills(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict:
    skills = db.scalars(
        select(Skill)
        .where(Skill.owner_user_id == current_user.id, Skill.source_type != "agent")
        .order_by(Skill.created_at.desc())
    ).all()
    return {"items": [_serialize_skill(s) for s in skills]}


@router.get("/agents")
def list_agent_skills(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict:
    skills = db.scalars(
        select(Skill)
        .where(Skill.owner_user_id == current_user.id, Skill.source_type == "agent")
        .order_by(Skill.created_at.desc())
    ).all()
    return {"items": [_serialize_skill(s) for s in skills]}


@router.get("/agent_skills")
def list_agent_skill_dirs(current_user: User = Depends(get_current_user)) -> dict:
    root = AGENTSKILLS_DIR / current_user.id
    if not root.exists() or not root.is_dir():
        return {"items": []}
    items: list[dict] = []
    for child in sorted(root.iterdir()):
        if not child.is_dir():
            continue
        skill_md = child / "SKILL.md"
        meta = _read_skill_frontmatter(skill_md)
        name = str(meta.get("name") or child.name)
        items.append(
            {
                "name": name,
                "dir_name": child.name,
                "display_name": meta.get("display_name") or meta.get("title") or None,
                "description": meta.get("description") or None,
            }
        )
    return {"items": items}


@router.post("/agent_skills/{skill_name}/move_to_user")
def move_agent_skill_to_user(
    skill_name: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    name = _normalize_name(skill_name)
    source_dir = AGENTSKILLS_DIR / current_user.id / name
    if not source_dir.exists():
        raise HTTPException(status_code=404, detail="Agent skill not found")
    new_name = _unique_name(
        name,
        lambda n: _name_exists_for_owner(db, current_user.id, n) or _userskill_dir_exists(current_user.id, n),
    )
    target_dir = USERSKILLS_DIR / current_user.id / new_name
    _ensure_dir(target_dir.parent)
    if new_name != name:
        _update_skill_frontmatter_name(source_dir / "SKILL.md", new_name)
    shutil.move(str(source_dir), str(target_dir))
    skill = Skill(
        id=str(uuid4()),
        owner_user_id=current_user.id,
        source_type="user",
        status="draft",
        name=new_name,
        display_name=None,
        description=None,
    )
    db.add(skill)
    db.commit()
    db.refresh(skill)
    return {"item": _serialize_skill(skill)}


@router.post("/{skill_id}/move_to_agent")
def move_user_skill_to_agent(
    skill_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    skill = db.get(Skill, skill_id)
    if not skill or skill.owner_user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Skill not found")
    if skill.status != "draft":
        raise HTTPException(status_code=400, detail="Only draft skills can be moved")
    if skill.source_type == "agent":
        return {"item": _serialize_skill(skill)}
    source_dir = _skill_dir(skill)
    if not source_dir.exists():
        raise HTTPException(status_code=404, detail="Skill directory not found")
    target_dir = AGENTSKILLS_DIR / current_user.id / skill.name
    _ensure_dir(target_dir.parent)
    new_name = skill.name
    if target_dir.exists():
        new_name = _unique_name(
            skill.name,
            lambda n: (AGENTSKILLS_DIR / current_user.id / n).exists(),
        )
        target_dir = AGENTSKILLS_DIR / current_user.id / new_name
        _update_skill_frontmatter_name(source_dir / "SKILL.md", new_name)
        skill.name = new_name
    shutil.move(str(source_dir), str(target_dir))
    skill.source_type = "agent"
    db.commit()
    db.refresh(skill)
    return {"item": _serialize_skill(skill)}


@router.get("/all")
def list_all_skills(_: User = Depends(require_admin), db: Session = Depends(get_db)) -> dict:
    skills = db.scalars(select(Skill).order_by(Skill.created_at.desc())).all()
    return {"items": [_serialize_skill(s) for s in skills]}


@router.get("/pending")
def list_pending_skills(_: User = Depends(require_admin), db: Session = Depends(get_db)) -> dict:
    skills = db.scalars(select(Skill).where(Skill.status == "pending").order_by(Skill.created_at.desc())).all()
    return {"items": [_serialize_skill(s) for s in skills]}


@router.get("/published")
def list_published_skills(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict:
    if current_user.is_admin:
        skills = db.scalars(select(Skill).where(Skill.status == "published")).all()
        return {"items": [_serialize_skill(s) for s in skills]}
    subq = (
        select(SkillGroupSkill.skill_id)
        .join(SkillGroupUser, SkillGroupSkill.group_id == SkillGroupUser.group_id)
        .where(SkillGroupUser.user_id == current_user.id)
    )
    skills = db.scalars(
        select(Skill).where(
            Skill.status == "published",
            or_(Skill.is_public.is_(True), Skill.id.in_(subq)),
        )
    ).all()
    return {"items": [_serialize_skill(s) for s in skills]}


@router.get("/publish_requests")
def list_publish_requests(
    status: str = "pending",
    _: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict:
    if status != "pending":
        return {"items": []}
    skills = db.scalars(select(Skill).where(Skill.status == "pending").order_by(Skill.created_at.desc())).all()
    items = [
        {
            "id": s.id,
            "skill_id": s.id,
            "requester_user_id": s.owner_user_id,
            "comment": s.pending_comment,
        }
        for s in skills
    ]
    return {"items": items}


@router.get("/builtin")
def list_builtin_skills(current_user: User = Depends(get_current_user)) -> dict:
    return {"items": _builtin_skills()}


@router.post("")
def create_skill(
    payload: CreateSkillRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    name = _normalize_name(payload.name)
    if _name_exists_for_owner(db, current_user.id, name) or _userskill_dir_exists(current_user.id, name):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Skill name already exists")
    skill = Skill(
        id=str(uuid4()),
        owner_user_id=current_user.id,
        source_type="user",
        status="draft",
        name=name,
        display_name=payload.display_name or None,
        description=payload.description or None,
        is_public=False,
        is_public_edit=False,
        created_at=_now(),
        updated_at=_now(),
    )
    db.add(skill)
    _log(db, skill.id, current_user.id, "create")
    db.commit()
    _ensure_skill_scaffold(skill)
    return {"item": _serialize_skill(skill)}


@router.get("/usage")
def list_skill_usage(
    conversation_id: str | None = None,
    include_non_skills: bool = False,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    builtin_names = _builtin_skill_names()
    skill_rows = db.scalars(select(Skill)).all()
    skill_by_name: dict[str, Skill] = {}
    for skill in skill_rows:
        if skill.name not in skill_by_name:
            skill_by_name[skill.name] = skill
    skill_name_set = set(skill_by_name.keys()) | builtin_names
    non_skill_tools = {
        "terminal",
        "read_file",
        "write_file",
        "edit_file",
        "ls",
        "glob",
        "web_search",
        "internet_search",
        "fetch_url",
    }

    if conversation_id:
        conversation = db.get(Conversation, conversation_id)
        if not conversation or conversation.user_id != current_user.id:
            raise HTTPException(status_code=404, detail="Conversation not found")

    items: list[dict] = []
    if conversation_id:
        query = (
            select(ConversationMessage.tool_name, func.count(ConversationMessage.id))
            .join(Conversation, Conversation.id == ConversationMessage.conversation_id)
            .where(Conversation.user_id == current_user.id)
            .where(ConversationMessage.message_type == "ToolMessage")
            .where(ConversationMessage.tool_name.isnot(None))
            .where(ConversationMessage.conversation_id == conversation_id)
        )
        if not include_non_skills:
            query = query.where(~ConversationMessage.tool_name.in_(sorted(non_skill_tools)))
        query = query.group_by(ConversationMessage.tool_name).order_by(desc(func.count(ConversationMessage.id)))
        rows = db.execute(query).all()
        for tool_name, count in rows:
            name = str(tool_name)
            item = {"tool_name": name, "count": int(count)}
            if name in builtin_names:
                item["source"] = "builtin"
            elif name in skill_by_name:
                skill = skill_by_name[name]
                item["source"] = skill.source_type
                item["status"] = skill.status
                if skill.display_name:
                    item["display_name"] = skill.display_name
            else:
                item["source"] = "tool"
            items.append(item)
        return {"items": items}

    for skill in skill_rows:
        item = {"tool_name": skill.name, "count": int(skill.usage_count or 0), "source": skill.source_type}
        if skill.status:
            item["status"] = skill.status
        if skill.display_name:
            item["display_name"] = skill.display_name
        items.append(item)

    if builtin_names:
        builtin_query = (
            select(ConversationMessage.tool_name, func.count(ConversationMessage.id))
            .where(ConversationMessage.message_type == "ToolMessage")
            .where(ConversationMessage.tool_name.in_(sorted(builtin_names)))
            .group_by(ConversationMessage.tool_name)
        )
        for tool_name, count in db.execute(builtin_query).all():
            items.append({"tool_name": str(tool_name), "count": int(count), "source": "builtin"})

    if include_non_skills:
        extra_query = (
            select(ConversationMessage.tool_name, func.count(ConversationMessage.id))
            .where(ConversationMessage.message_type == "ToolMessage")
            .where(ConversationMessage.tool_name.isnot(None))
            .where(~ConversationMessage.tool_name.in_(sorted(skill_name_set)))
            .group_by(ConversationMessage.tool_name)
        )
        for tool_name, count in db.execute(extra_query).all():
            items.append({"tool_name": str(tool_name), "count": int(count), "source": "tool"})

    return {"items": items}


@router.get("/{skill_id}")
def get_skill(
    skill_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    skill = db.get(Skill, skill_id)
    if not skill:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Skill not found")
    _require_view_skill(db, current_user, skill)
    return {"item": _serialize_skill(skill)}


@router.patch("/{skill_id}")
def update_skill(
    skill_id: str,
    payload: UpdateSkillRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    skill = db.get(Skill, skill_id)
    if not skill:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Skill not found")
    if not _can_edit_skill(current_user, skill) and not current_user.is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not allowed")

    updates = payload.model_dump(exclude_none=True)
    renamed = False
    if "name" in updates:
        if skill.status == "published":
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Published skill cannot be renamed")
        name = _normalize_name(updates["name"])
        if skill.status in {"pending", "published"}:
            if _name_exists_globally(db, name, exclude_id=skill.id):
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Skill name already exists")
        else:
            if _name_exists_for_owner(db, skill.owner_user_id, name, exclude_id=skill.id) or _userskill_dir_exists(
                skill.owner_user_id, name
            ):
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Skill name already exists")
        if name != skill.name:
            old_dir = _skill_dir(skill)
            skill.name = name
            renamed = True
            new_dir = _skill_dir(skill)
            _move_dir(old_dir, new_dir)

    if "display_name" in updates:
        skill.display_name = updates["display_name"]
    if "description" in updates:
        skill.description = updates["description"]

    is_public_requested = "is_public" in updates
    if is_public_requested:
        if not current_user.is_admin or skill.status != "published":
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not allowed")
        skill.is_public = bool(updates["is_public"])
        if not skill.is_public:
            skill.is_public_edit = False
    if "is_public_edit" in updates:
        if not current_user.is_admin or skill.status != "published" or not skill.is_public:
            # Allow turning off public edit when public is being disabled in the same request.
            if not (is_public_requested and updates.get("is_public") is False):
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not allowed")
        skill.is_public_edit = bool(updates["is_public_edit"])

    skill.updated_at = _now()
    db.add(skill)
    _log(db, skill.id, current_user.id, "edit", {"renamed": renamed})
    db.commit()
    db.refresh(skill)
    return {"item": _serialize_skill(skill)}


@router.delete("/{skill_id}")
def delete_skill(
    skill_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    skill = db.get(Skill, skill_id)
    if not skill:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Skill not found")
    if not current_user.is_admin:
        if skill.owner_user_id != current_user.id or skill.status not in {"draft", "rejected"}:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not allowed")
    skill_dir = _skill_dir(skill)
    if skill_dir.exists():
        shutil.rmtree(skill_dir)
    db.delete(skill)
    _log(db, skill_id, current_user.id, "delete")
    db.commit()
    return {"message": "deleted"}


@router.post("/{skill_id}/publish")
def request_publish(
    skill_id: str,
    payload: PublishSkillRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    skill = db.get(Skill, skill_id)
    if not skill:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Skill not found")
    if skill.owner_user_id != current_user.id or skill.source_type == "agent":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not allowed")
    if skill.status not in {"draft", "rejected"}:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Skill cannot be published")
    skill_dir = _skill_dir(skill)
    if not (skill_dir / "SKILL.md").exists():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="SKILL.md is required")

    def exists_fn(candidate: str) -> bool:
        return _name_exists_globally(db, candidate, exclude_id=skill.id)

    if exists_fn(skill.name):
        new_name = _unique_name(skill.name, exists_fn)
        old_dir = _skill_dir(skill)
        skill.name = new_name
        new_dir = _skill_dir(skill)
        _move_dir(old_dir, new_dir)

    old_dir = _skill_dir(skill)
    skill.status = "pending"
    skill.pending_comment = payload.comment
    skill.rejected_reason = None
    skill.rejected_at = None
    skill.rejected_by = None
    skill.updated_at = _now()
    new_dir = _skill_dir(skill)
    _move_dir(old_dir, new_dir)
    _log(db, skill.id, current_user.id, "submit", {"comment": payload.comment})
    db.commit()
    db.refresh(skill)
    return {"item": _serialize_skill(skill)}


@router.post("/{skill_id}/withdraw")
def withdraw_publish(
    skill_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    skill = db.get(Skill, skill_id)
    if not skill:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Skill not found")
    if skill.owner_user_id != current_user.id or skill.status != "pending":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not allowed")
    old_dir = _skill_dir(skill)
    skill.status = "draft"
    skill.pending_comment = None
    skill.updated_at = _now()
    new_dir = _skill_dir(skill)
    _move_dir(old_dir, new_dir)
    _log(db, skill.id, current_user.id, "withdraw")
    db.commit()
    db.refresh(skill)
    return {"item": _serialize_skill(skill)}


@router.post("/publish_requests/{skill_id}/approve")
def approve_publish(
    skill_id: str,
    _: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict:
    skill = db.get(Skill, skill_id)
    if not skill:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Skill not found")
    if skill.status != "pending":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Skill not pending")

    def exists_fn(candidate: str) -> bool:
        return _name_exists_globally(db, candidate, statuses=("published",), exclude_id=skill.id) or (
            PUBLISHED_DIR / candidate
        ).exists()

    if exists_fn(skill.name):
        new_name = _unique_name(skill.name, exists_fn)
        old_dir = _skill_dir(skill)
        skill.name = new_name
        new_dir = _skill_dir(skill)
        _move_dir(old_dir, new_dir)

    old_dir = _skill_dir(skill)
    skill.status = "published"
    skill.pending_comment = None
    skill.published_at = _now()
    skill.published_by = _.id
    skill.updated_at = _now()
    new_dir = _skill_dir(skill)
    _move_dir(old_dir, new_dir)
    _log(db, skill.id, _.id, "approve")
    db.commit()
    db.refresh(skill)
    return {"item": _serialize_skill(skill)}


@router.post("/publish_requests/{skill_id}/reject")
def reject_publish(
    skill_id: str,
    payload: RejectSkillRequest,
    admin_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict:
    skill = db.get(Skill, skill_id)
    if not skill:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Skill not found")
    if skill.status != "pending":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Skill not pending")
    old_dir = _skill_dir(skill)
    skill.status = "rejected"
    skill.rejected_at = _now()
    skill.rejected_by = admin_user.id
    skill.rejected_reason = payload.comment
    skill.pending_comment = None
    skill.updated_at = _now()
    new_dir = _skill_dir(skill)
    _move_dir(old_dir, new_dir)
    _log(db, skill.id, admin_user.id, "reject", {"comment": payload.comment})
    db.commit()
    db.refresh(skill)
    return {"item": _serialize_skill(skill)}


@router.post("/{skill_id}/save_to_mine")
def save_agent_skill(
    skill_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    skill = db.get(Skill, skill_id)
    if not skill:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Skill not found")
    if skill.owner_user_id != current_user.id or skill.source_type != "agent":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not allowed")
    def exists_fn(candidate: str) -> bool:
        return (
            db.scalar(
                select(Skill.id).where(
                    Skill.owner_user_id == current_user.id,
                    Skill.name == candidate,
                    Skill.id != skill.id,
                )
            )
            is not None
            or _userskill_dir_exists(current_user.id, candidate)
        )

    if exists_fn(skill.name):
        new_name = _unique_name(skill.name, exists_fn)
        old_dir = _skill_dir(skill)
        skill.name = new_name
        new_dir = _skill_dir(skill)
        _move_dir(old_dir, new_dir)

    old_dir = _skill_dir(skill)
    skill.source_type = "user"
    skill.status = "draft"
    skill.updated_at = _now()
    new_dir = _skill_dir(skill)
    _move_dir(old_dir, new_dir)
    _log(db, skill.id, current_user.id, "save")
    db.commit()
    db.refresh(skill)
    return {"item": _serialize_skill(skill)}


@router.post("/{skill_id}/copy")
def copy_public_skill(
    skill_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    skill = db.get(Skill, skill_id)
    if not skill:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Skill not found")
    if skill.status != "published" or not skill.is_public_edit:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not allowed")

    base_name = skill.name

    def exists_fn(candidate: str) -> bool:
        return _name_exists_for_owner(db, current_user.id, candidate) or _userskill_dir_exists(current_user.id, candidate)

    new_name = _unique_name(base_name, exists_fn)
    new_skill = Skill(
        id=str(uuid4()),
        owner_user_id=current_user.id,
        source_type="user",
        status="draft",
        name=new_name,
        display_name=skill.display_name,
        description=skill.description,
        is_public=False,
        is_public_edit=False,
        cloned_from_skill_id=skill.id,
        created_at=_now(),
        updated_at=_now(),
    )
    db.add(new_skill)
    _log(db, new_skill.id, current_user.id, "copy", {"source_skill_id": skill.id})
    db.commit()
    try:
        _copy_dir(_skill_dir(skill), _skill_dir(new_skill))
    except Exception as exc:  # noqa: BLE001
        db.delete(new_skill)
        db.commit()
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)) from exc
    return {"item": _serialize_skill(new_skill)}


@router.get("/{skill_id}/tree")
def get_skill_tree(
    skill_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    skill = db.get(Skill, skill_id)
    if not skill:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Skill not found")
    _require_view_skill(db, current_user, skill)
    root = _skill_dir(skill)
    if not root.exists():
        return {"items": []}
    items: list[dict] = []
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root).as_posix()
        items.append({"path": rel, "is_dir": path.is_dir()})
    return {"items": items}


@router.get("/{skill_id}/file")
def read_skill_file(
    skill_id: str,
    path: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    skill = db.get(Skill, skill_id)
    if not skill:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Skill not found")
    _require_view_skill(db, current_user, skill)
    root = _skill_dir(skill)
    file_path = _resolve_path(root, path)
    if not file_path.exists() or not file_path.is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found")
    content = file_path.read_text(encoding="utf-8", errors="replace")
    return {"content": content}


@router.put("/{skill_id}/file")
def write_skill_file(
    skill_id: str,
    payload: FileWriteRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    skill = db.get(Skill, skill_id)
    if not skill:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Skill not found")
    if not _can_edit_skill(current_user, skill) and not current_user.is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not allowed")
    root = _skill_dir(skill)
    file_path = _resolve_path(root, payload.path)
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(payload.content or "", encoding="utf-8")
    _log(db, skill.id, current_user.id, "edit_file", {"path": payload.path})
    db.commit()
    return {"message": "ok"}


@router.post("/{skill_id}/dir")
def create_skill_dir(
    skill_id: str,
    payload: DirCreateRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    skill = db.get(Skill, skill_id)
    if not skill:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Skill not found")
    if not _can_edit_skill(current_user, skill) and not current_user.is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not allowed")
    root = _skill_dir(skill)
    dir_path = _resolve_path(root, payload.path)
    dir_path.mkdir(parents=True, exist_ok=True)
    _log(db, skill.id, current_user.id, "create_dir", {"path": payload.path})
    db.commit()
    return {"message": "ok"}


@router.post("/{skill_id}/rename")
def rename_skill_path(
    skill_id: str,
    payload: RenameRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    skill = db.get(Skill, skill_id)
    if not skill:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Skill not found")
    if not _can_edit_skill(current_user, skill) and not current_user.is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not allowed")
    if payload.from_path == "SKILL.md" or payload.to_path == "SKILL.md":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="SKILL.md cannot be renamed")
    root = _skill_dir(skill)
    src = _resolve_path(root, payload.from_path)
    dest = _resolve_path(root, payload.to_path)
    if not src.exists():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Path not found")
    dest.parent.mkdir(parents=True, exist_ok=True)
    src.rename(dest)
    _log(db, skill.id, current_user.id, "rename", {"from": payload.from_path, "to": payload.to_path})
    db.commit()
    return {"message": "ok"}


@router.delete("/{skill_id}/path")
def delete_skill_path(
    skill_id: str,
    path: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    skill = db.get(Skill, skill_id)
    if not skill:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Skill not found")
    if not _can_edit_skill(current_user, skill) and not current_user.is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not allowed")
    if path == "SKILL.md":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="SKILL.md cannot be deleted")
    root = _skill_dir(skill)
    target = _resolve_path(root, path)
    if not target.exists():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Path not found")
    if target.is_dir():
        shutil.rmtree(target)
    else:
        target.unlink()
    _log(db, skill.id, current_user.id, "delete_path", {"path": path})
    db.commit()
    return {"message": "ok"}


@router.get("/groups/list")
def list_groups(_: User = Depends(require_admin), db: Session = Depends(get_db)) -> dict:
    groups = db.scalars(select(SkillGroup).order_by(SkillGroup.created_at.desc())).all()
    if not groups:
        return {"items": []}
    group_ids = [g.id for g in groups]
    group_skill_rows = db.execute(
        select(SkillGroupSkill.group_id, Skill.id, Skill.name, Skill.display_name)
        .join(Skill, Skill.id == SkillGroupSkill.skill_id)
        .where(SkillGroupSkill.group_id.in_(group_ids))
        .order_by(Skill.created_at.desc())
    ).all()
    skills_by_group: dict[str, list[dict]] = {gid: [] for gid in group_ids}
    for row in group_skill_rows:
        skills_by_group[row.group_id].append(
            {"id": row.id, "name": row.name, "display_name": row.display_name}
        )
    return {
        "items": [
            {**_serialize_group(g), "skills": skills_by_group.get(g.id, [])}
            for g in groups
        ]
    }


@router.get("/groups/options")
def list_group_options(_: User = Depends(require_admin), db: Session = Depends(get_db)) -> dict:
    skills = db.scalars(
        select(Skill)
        .where(Skill.status == "published", Skill.is_public.is_(False))
        .order_by(Skill.created_at.desc())
    ).all()
    users = db.scalars(select(User).order_by(User.created_at.desc())).all()
    return {
        "skills": [_serialize_skill(s) for s in skills],
        "users": [
            {
                "id": u.id,
                "username": u.username,
                "display_name": u.display_name,
                "email": u.email,
                "is_admin": u.is_admin,
            }
            for u in users
        ],
    }


@router.post("/groups")
def create_group(
    payload: CreateGroupRequest,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict:
    existing = db.scalar(select(SkillGroup).where(SkillGroup.name == payload.name))
    if existing:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Group name already exists")
    group = SkillGroup(
        id=str(uuid4()),
        name=payload.name,
        description=payload.description,
        created_by=current_user.id,
        created_at=_now(),
        updated_at=_now(),
    )
    db.add(group)
    db.commit()
    db.refresh(group)
    return {"item": _serialize_group(group)}


@router.patch("/groups/{group_id}")
def update_group(
    group_id: str,
    payload: CreateGroupRequest,
    _: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict:
    group = db.get(SkillGroup, group_id)
    if not group:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Group not found")
    existing = db.scalar(select(SkillGroup).where(SkillGroup.name == payload.name, SkillGroup.id != group_id))
    if existing:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Group name already exists")
    group.name = payload.name
    group.description = payload.description
    group.updated_at = _now()
    db.add(group)
    db.commit()
    db.refresh(group)
    return {"item": _serialize_group(group)}


@router.delete("/groups/{group_id}")
def delete_group(group_id: str, _: User = Depends(require_admin), db: Session = Depends(get_db)) -> dict:
    group = db.get(SkillGroup, group_id)
    if not group:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Group not found")
    db.query(SkillGroupSkill).filter(SkillGroupSkill.group_id == group_id).delete()
    db.query(SkillGroupUser).filter(SkillGroupUser.group_id == group_id).delete()
    db.delete(group)
    db.commit()
    return {"message": "deleted"}


@router.get("/groups/{group_id}/skills")
def list_group_skills(group_id: str, _: User = Depends(require_admin), db: Session = Depends(get_db)) -> dict:
    skill_ids = db.scalars(select(SkillGroupSkill.skill_id).where(SkillGroupSkill.group_id == group_id)).all()
    if not skill_ids:
        return {"items": []}
    skills = db.scalars(select(Skill).where(Skill.id.in_(skill_ids))).all()
    return {"items": [_serialize_skill(s) for s in skills]}


@router.post("/groups/{group_id}/skills")
def add_group_skill(
    group_id: str,
    payload: AddSkillToGroupRequest,
    _: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict:
    group = db.get(SkillGroup, group_id)
    if not group:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Group not found")
    skill = db.get(Skill, payload.skill_id)
    if not skill:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Skill not found")
    if skill.status != "published" or skill.is_public:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Skill must be published and non-public")
    exists = db.get(SkillGroupSkill, {"group_id": group_id, "skill_id": payload.skill_id})
    if not exists:
        db.add(SkillGroupSkill(group_id=group_id, skill_id=payload.skill_id))
        db.commit()
    return {"message": "ok"}


@router.delete("/groups/{group_id}/skills/{skill_id}")
def remove_group_skill(
    group_id: str,
    skill_id: str,
    _: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict:
    db.query(SkillGroupSkill).filter(
        SkillGroupSkill.group_id == group_id, SkillGroupSkill.skill_id == skill_id
    ).delete()
    db.commit()
    return {"message": "ok"}


@router.get("/groups/{group_id}/users")
def list_group_users(group_id: str, _: User = Depends(require_admin), db: Session = Depends(get_db)) -> dict:
    user_ids = db.scalars(select(SkillGroupUser.user_id).where(SkillGroupUser.group_id == group_id)).all()
    if not user_ids:
        return {"items": []}
    users = db.scalars(select(User).where(User.id.in_(user_ids))).all()
    return {
        "items": [
            {
                "id": u.id,
                "username": u.username,
                "display_name": u.display_name,
                "email": u.email,
                "is_admin": u.is_admin,
            }
            for u in users
        ]
    }


@router.get("/groups/for_user/{user_id}")
def list_groups_for_user(user_id: str, _: User = Depends(require_admin), db: Session = Depends(get_db)) -> dict:
    group_ids = db.scalars(select(SkillGroupUser.group_id).where(SkillGroupUser.user_id == user_id)).all()
    if not group_ids:
        return {"items": []}
    groups = db.scalars(select(SkillGroup).where(SkillGroup.id.in_(group_ids))).all()
    return {"items": [_serialize_group(g) for g in groups]}


@router.get("/groups/users_map")
def list_group_users_map(_: User = Depends(require_admin), db: Session = Depends(get_db)) -> dict:
    rows = db.execute(
        select(SkillGroupUser.user_id, SkillGroup.id, SkillGroup.name)
        .join(SkillGroup, SkillGroup.id == SkillGroupUser.group_id)
        .order_by(SkillGroup.name.asc())
    ).all()
    mapping: dict[str, list[dict]] = {}
    for row in rows:
        mapping.setdefault(row.user_id, []).append({"id": row.id, "name": row.name})
    items = [{"user_id": user_id, "groups": groups} for user_id, groups in mapping.items()]
    return {"items": items}


@router.post("/groups/{group_id}/users")
def add_group_user(
    group_id: str,
    payload: AddUserToGroupRequest,
    _: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict:
    group = db.get(SkillGroup, group_id)
    if not group:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Group not found")
    user = db.get(User, payload.user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    exists = db.get(SkillGroupUser, {"group_id": group_id, "user_id": payload.user_id})
    if not exists:
        db.add(SkillGroupUser(group_id=group_id, user_id=payload.user_id))
        db.commit()
    return {"message": "ok"}


@router.delete("/groups/{group_id}/users/{user_id}")
def remove_group_user(
    group_id: str,
    user_id: str,
    _: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict:
    db.query(SkillGroupUser).filter(
        SkillGroupUser.group_id == group_id, SkillGroupUser.user_id == user_id
    ).delete()
    db.commit()
    return {"message": "ok"}
