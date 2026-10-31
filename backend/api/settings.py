"""Settings: general options and business hours, languages, departments and categories, ticket
statuses with their permitted transitions, SLA targets and routing rules — all without code changes."""
import re
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select
from backend.api.deps import require, staff_required
from backend.database import session
from backend.models import (AssignmentRule, Category, Department, Language, SlaPolicy, StatusTransition, Ticket, TicketStatus)
from backend.services import audit, content, options, tickets
from backend.services.i18n import KNOWN, normalize
from backend.services.security import DomainError

router = APIRouter(prefix="/api/admin/settings", tags=["settings"])
CODE = re.compile(r"[a-z]{2,3}(-[a-z0-9]{2,8})?\Z")
COLOR = re.compile(r"#[0-9a-fA-F]{6}\Z")


def names(value: dict | None) -> dict:
    result = {str(k)[:12]: str(v).strip()[:80] for k, v in (value or {}).items() if str(v or "").strip()}
    if not result:
        raise DomainError("name_required")
    return result


def changed(db, staff, request: Request, action: str, target="", details: dict | None = None) -> None:
    options.bump_content_version(db)  # the bot reloads languages, departments, statuses
    audit.record(db, staff, f"settings.{action}", "settings", target, details or {}, request.state.ip)
    db.commit()


@router.get("")
def everything(staff=Depends(staff_required), db=Depends(session)):
    return {"general": options.all_settings(db), "languages": languages(staff, db), "departments": departments(staff, db),
            "categories": categories(staff, db), "statuses": statuses(staff, db), "transitions": transitions(staff, db),
            "sla": sla(staff, db), "rules": rules(staff, db), "known_languages": {k: {"name": v[0], "native": v[1], "flag": v[2], "rtl": v[3]}
                                                                                     for k, v in KNOWN.items()}}


# ---------------------------------------------------------------- general
@router.get("/general")
def general(staff=Depends(staff_required), db=Depends(session)):
    return options.all_settings(db)


@router.patch("/general")
def update_general(data: dict, request: Request, staff=Depends(staff_required), db=Depends(session)):
    try:
        clean = options.validate(data)
    except ValueError as error:
        raise DomainError(f"bad_setting:{error}") from None
    for key, value in clean.items():
        options.put(db, key, value)
    if {"business_hours", "sla_business_hours", "sla_pause_pending_user"} & clean.keys():
        db.flush()
        tickets.recalculate_sla(db)
    changed(db, staff, request, "general", details={k: v for k, v in clean.items() if k != "business_hours"})
    return options.all_settings(db)


# ---------------------------------------------------------------- languages
def language_dict(lang: Language) -> dict:
    return {"code": lang.code, "name": lang.name, "native_name": lang.native_name, "flag": lang.flag, "rtl": lang.rtl,
            "enabled": lang.enabled, "is_default": lang.is_default, "position": lang.position}


@router.get("/languages")
def languages(staff=Depends(staff_required), db=Depends(session)):
    return [language_dict(lang) for lang in content.languages(db, enabled_only=False)]


class LanguageData(BaseModel):
    code: str = Field(default="", max_length=12)
    name: str | None = Field(default=None, max_length=64)
    native_name: str | None = Field(default=None, max_length=64)
    flag: str | None = Field(default=None, max_length=16)
    rtl: bool | None = None
    enabled: bool | None = None
    is_default: bool | None = None
    position: int | None = None


@router.post("/languages")
def add_language(data: LanguageData, request: Request, staff=Depends(staff_required), db=Depends(session)):
    """A new language: one click for the known ones, or any IETF code with its names."""
    code = normalize(data.code)
    if not CODE.match(code):
        raise DomainError("bad_language_code")
    if db.get(Language, code):
        raise DomainError("language_exists")
    name, native, flag, rtl = KNOWN.get(code, (data.name or code, data.native_name or code, data.flag or "", bool(data.rtl)))
    position = (db.scalar(select(func.max(Language.position))) or 0) + 1
    db.add(Language(code=code, name=data.name or name, native_name=data.native_name or native, flag=data.flag if data.flag is not None else flag,
                    rtl=rtl if data.rtl is None else data.rtl, enabled=data.enabled if data.enabled is not None else True, position=position))
    changed(db, staff, request, "language_add", code)
    return languages(staff, db)


@router.patch("/languages/{code}")
def update_language(code: str, data: LanguageData, request: Request, staff=Depends(staff_required), db=Depends(session)):
    lang = require(db, Language, code)
    for key in ("name", "native_name", "flag", "rtl", "enabled", "position"):
        value = getattr(data, key)
        if value is not None:
            setattr(lang, key, value)
    if data.is_default:
        for other in db.scalars(select(Language)):
            other.is_default = other.code == code
        lang.enabled = True
    if lang.is_default and not lang.enabled:
        raise DomainError("default_language_disabled")
    db.flush()
    if not any(lang.enabled for lang in db.scalars(select(Language))):
        raise DomainError("no_language_enabled")
    changed(db, staff, request, "language", code, data.model_dump(exclude_unset=True))
    return languages(staff, db)


@router.delete("/languages/{code}")
def delete_language(code: str, request: Request, staff=Depends(staff_required), db=Depends(session)):
    """Removes a language from the bot; its translations stay in the content and come back when it is added again."""
    lang = require(db, Language, code)
    if lang.is_default:
        raise DomainError("default_language_disabled")
    db.delete(lang)
    changed(db, staff, request, "language_delete", code)
    return languages(staff, db)


# ---------------------------------------------------------------- departments and categories
@router.get("/departments")
def departments(staff=Depends(staff_required), db=Depends(session)):
    return [{"id": d.id, "name": d.name, "enabled": d.enabled, "position": d.position}
            for d in db.scalars(select(Department).order_by(Department.position, Department.id))]


class DepartmentData(BaseModel):
    name: dict[str, str] | None = None
    enabled: bool | None = None


@router.post("/departments")
def add_department(data: DepartmentData, request: Request, staff=Depends(staff_required), db=Depends(session)):
    position = (db.scalar(select(func.max(Department.position))) or 0) + 1
    db.add(Department(name=names(data.name), enabled=data.enabled is not False, position=position))
    changed(db, staff, request, "department_add")
    return departments(staff, db)


@router.patch("/departments/{department_id}")
def update_department(department_id: int, data: DepartmentData, request: Request, staff=Depends(staff_required), db=Depends(session)):
    department = require(db, Department, int(department_id))
    if data.name is not None:
        department.name = names(data.name)
    if data.enabled is not None:
        department.enabled = data.enabled
    changed(db, staff, request, "department", department_id)
    return departments(staff, db)


@router.delete("/departments/{department_id}")
def delete_department(department_id: int, request: Request, staff=Depends(staff_required), db=Depends(session)):
    department = require(db, Department, int(department_id))
    if db.scalar(select(func.count()).select_from(Ticket).where(Ticket.department_id == department.id)):
        raise DomainError("in_use")  # disable it instead: its tickets keep their history
    db.delete(department)
    changed(db, staff, request, "department_delete", department_id)
    return departments(staff, db)


class Reorder(BaseModel):
    ids: list[int]


@router.post("/departments/reorder")
def reorder_departments(data: Reorder, request: Request, staff=Depends(staff_required), db=Depends(session)):
    for position, item_id in enumerate(data.ids):
        require(db, Department, item_id).position = position
    changed(db, staff, request, "department_order")
    return departments(staff, db)


@router.get("/categories")
def categories(staff=Depends(staff_required), db=Depends(session)):
    return [{"id": c.id, "department_id": c.department_id, "name": c.name, "default_priority": c.default_priority,
             "enabled": c.enabled, "position": c.position} for c in db.scalars(select(Category).order_by(Category.position, Category.id))]


class CategoryData(BaseModel):
    department_id: int | None = None
    name: dict[str, str] | None = None
    default_priority: str | None = None
    enabled: bool | None = None


def _category(db, category: Category, data: CategoryData, partial: bool) -> None:
    fields = data.model_dump(exclude_unset=True)
    if "name" in fields or not partial:
        category.name = names(data.name)
    if "department_id" in fields:
        if data.department_id is not None:
            require(db, Department, data.department_id)
        category.department_id = data.department_id
    if data.default_priority is not None:
        if data.default_priority not in tickets.PRIORITIES:
            raise DomainError("unknown_priority")
        category.default_priority = data.default_priority
    if data.enabled is not None:
        category.enabled = data.enabled


@router.post("/categories")
def add_category(data: CategoryData, request: Request, staff=Depends(staff_required), db=Depends(session)):
    category = Category(position=(db.scalar(select(func.max(Category.position))) or 0) + 1)
    _category(db, category, data, partial=False)
    db.add(category)
    changed(db, staff, request, "category_add")
    return categories(staff, db)


@router.patch("/categories/{category_id}")
def update_category(category_id: int, data: CategoryData, request: Request, staff=Depends(staff_required), db=Depends(session)):
    _category(db, require(db, Category, int(category_id)), data, partial=True)
    changed(db, staff, request, "category", category_id)
    return categories(staff, db)


@router.delete("/categories/{category_id}")
def delete_category(category_id: int, request: Request, staff=Depends(staff_required), db=Depends(session)):
    category = require(db, Category, int(category_id))
    if db.scalar(select(func.count()).select_from(Ticket).where(Ticket.category_id == category.id)):
        raise DomainError("in_use")
    db.delete(category)
    changed(db, staff, request, "category_delete", category_id)
    return categories(staff, db)


@router.post("/categories/reorder")
def reorder_categories(data: Reorder, request: Request, staff=Depends(staff_required), db=Depends(session)):
    for position, item_id in enumerate(data.ids):
        require(db, Category, item_id).position = position
    changed(db, staff, request, "category_order")
    return categories(staff, db)


# ---------------------------------------------------------------- statuses, transitions, SLA
@router.get("/statuses")
def statuses(staff=Depends(staff_required), db=Depends(session)):
    return [{"key": s.key, "name": s.name, "color": s.color, "stage": s.stage, "position": s.position, "enabled": s.enabled,
             "builtin": s.builtin, "notify_user": s.notify_user} for s in tickets.statuses(db).values()]


class StatusData(BaseModel):
    key: str = Field(default="", max_length=32)
    name: dict[str, str] | None = None
    color: str | None = None
    stage: str | None = None
    position: int | None = None
    enabled: bool | None = None
    notify_user: bool | None = None


def _status(status: TicketStatus, data: StatusData) -> None:
    if data.name is not None:
        status.name = names(data.name)
    if data.color is not None:
        if not COLOR.match(data.color):
            raise DomainError("bad_color")
        status.color = data.color
    for key in ("position", "enabled", "notify_user"):
        if getattr(data, key) is not None:
            setattr(status, key, getattr(data, key))


@router.post("/statuses")
def add_status(data: StatusData, request: Request, staff=Depends(staff_required), db=Depends(session)):
    """A custom status picks a stage, so reports and SLA keep understanding it."""
    key = data.key.strip().lower()
    if not re.fullmatch(r"[a-z][a-z0-9_]{1,31}", key) or db.get(TicketStatus, key):
        raise DomainError("bad_status_key")
    if data.stage not in tickets.STAGES:
        raise DomainError("bad_stage")
    status = TicketStatus(key=key, stage=data.stage, position=len(tickets.statuses(db)), builtin=False)
    _status(status, data)
    if not status.name:
        raise DomainError("name_required")
    db.add(status)
    changed(db, staff, request, "status_add", key)
    return statuses(staff, db)


@router.patch("/statuses/{key}")
def update_status(key: str, data: StatusData, request: Request, staff=Depends(staff_required), db=Depends(session)):
    status = require(db, TicketStatus, key)
    _status(status, data)
    if status.builtin and not status.enabled and status.stage in ("new", "open", "resolved", "closed"):
        raise DomainError("status_required")  # the automatic moves need these stages
    changed(db, staff, request, "status", key, data.model_dump(exclude_unset=True))
    return statuses(staff, db)


@router.delete("/statuses/{key}")
def delete_status(key: str, request: Request, staff=Depends(staff_required), db=Depends(session)):
    status = require(db, TicketStatus, key)
    if status.builtin:
        raise DomainError("builtin_status")
    if db.scalar(select(func.count()).select_from(Ticket).where(Ticket.status == key)):
        raise DomainError("in_use")
    db.delete(status)
    changed(db, staff, request, "status_delete", key)
    return statuses(staff, db)


@router.get("/transitions")
def transitions(staff=Depends(staff_required), db=Depends(session)):
    return {k: sorted(v) for k, v in tickets.transitions(db).items()}


@router.put("/transitions")
def save_transitions(data: dict[str, list[str]], request: Request, staff=Depends(staff_required), db=Depends(session)):
    """The permitted-transitions matrix: {from status: [to statuses]}."""
    known = set(tickets.statuses(db))
    if not set(data) <= known or not all(set(v) <= known for v in data.values()):
        raise DomainError("unknown_status")
    db.execute(delete(StatusTransition))
    for source, targets in data.items():
        for target in sorted(set(targets) - {source}):
            db.add(StatusTransition(from_key=source, to_key=target))
    changed(db, staff, request, "transitions")
    return transitions(staff, db)


@router.get("/sla")
def sla(staff=Depends(staff_required), db=Depends(session)):
    return [{"priority": p.priority, "first_response_minutes": p.first_response_minutes, "resolution_minutes": p.resolution_minutes}
            for p in db.scalars(select(SlaPolicy).order_by(SlaPolicy.position))]


class SlaRow(BaseModel):
    priority: str
    first_response_minutes: int = Field(ge=1, le=60 * 24 * 30)
    resolution_minutes: int = Field(ge=1, le=60 * 24 * 90)


@router.put("/sla")
def save_sla(rows: list[SlaRow], request: Request, staff=Depends(staff_required), db=Depends(session)):
    """Response and resolution targets per priority; open tickets get the new targets at once."""
    for row in rows:
        if row.priority not in tickets.PRIORITIES:
            raise DomainError("unknown_priority")
        policy = db.get(SlaPolicy, row.priority) or SlaPolicy(priority=row.priority, position=tickets.PRIORITIES.index(row.priority))
        policy.first_response_minutes, policy.resolution_minutes = row.first_response_minutes, row.resolution_minutes
        db.merge(policy)
    db.flush()
    tickets.recalculate_sla(db)
    changed(db, staff, request, "sla", details={r.priority: [r.first_response_minutes, r.resolution_minutes] for r in rows})
    return sla(staff, db)


# ---------------------------------------------------------------- routing rules
def rule_dict(r: AssignmentRule) -> dict:
    return {k: getattr(r, k) for k in ("id", "name", "position", "enabled", "department_id", "category_id", "language", "priority",
                                       "set_department_id", "set_priority", "assign_to", "strategy")}


@router.get("/rules")
def rules(staff=Depends(staff_required), db=Depends(session)):
    return [rule_dict(r) for r in db.scalars(select(AssignmentRule).order_by(AssignmentRule.position, AssignmentRule.id))]


class RuleData(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    enabled: bool = True
    department_id: int | None = None
    category_id: int | None = None
    language: str | None = None
    priority: str | None = None
    set_department_id: int | None = None
    set_priority: str | None = None
    assign_to: int | None = None
    strategy: str = "none"


def _rule(rule: AssignmentRule, data: RuleData) -> None:
    if data.strategy not in ("none", "least_loaded", "round_robin"):
        raise DomainError("bad_strategy")
    for key in ("priority", "set_priority"):
        if getattr(data, key) and getattr(data, key) not in tickets.PRIORITIES:
            raise DomainError("unknown_priority")
    for key, value in data.model_dump().items():
        setattr(rule, key, value or (None if key not in ("name", "enabled", "strategy") else value))


@router.post("/rules")
def add_rule(data: RuleData, request: Request, staff=Depends(staff_required), db=Depends(session)):
    rule = AssignmentRule(position=(db.scalar(select(func.max(AssignmentRule.position))) or 0) + 1)
    _rule(rule, data)
    db.add(rule)
    changed(db, staff, request, "rule_add", data.name)
    return rules(staff, db)


@router.patch("/rules/{rule_id}")
def update_rule(rule_id: int, data: RuleData, request: Request, staff=Depends(staff_required), db=Depends(session)):
    _rule(require(db, AssignmentRule, int(rule_id)), data)
    changed(db, staff, request, "rule", rule_id)
    return rules(staff, db)


@router.delete("/rules/{rule_id}")
def delete_rule(rule_id: int, request: Request, staff=Depends(staff_required), db=Depends(session)):
    db.delete(require(db, AssignmentRule, int(rule_id)))
    changed(db, staff, request, "rule_delete", rule_id)
    return rules(staff, db)


@router.post("/rules/reorder")
def reorder_rules(data: Reorder, request: Request, staff=Depends(staff_required), db=Depends(session)):
    for position, item_id in enumerate(data.ids):
        require(db, AssignmentRule, item_id).position = position
    changed(db, staff, request, "rule_order")
    return rules(staff, db)
