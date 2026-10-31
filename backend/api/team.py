"""Staff accounts and roles."""
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from backend.api.deps import require, staff_required
from backend.database import session
from backend.models import Staff
from backend.services import team
from backend.services.permissions import PERMISSIONS

router = APIRouter(prefix="/api/admin/team", tags=["team"])


@router.get("")
def overview(staff=Depends(staff_required), db=Depends(session)):
    accounts = db.scalars(select(Staff).where(~Staff.username.contains(team.DELETED)).order_by(Staff.username)).all()
    return {"accounts": [team.public(db, s) for s in accounts], "roles": list(team.roles(db).values()),
            "permissions": [{"id": k, "description": v} for k, v in PERMISSIONS.items()], "you": staff.id}


class Account(BaseModel):
    username: str = Field(min_length=2, max_length=56)
    password: str = Field(min_length=1, max_length=256)
    role: str
    display_name: str = ""
    email: str = ""
    departments: list[int] = Field(default_factory=list)
    languages: list[str] = Field(default_factory=list)
    max_open_tickets: int = 0


@router.post("/accounts")
def create(data: Account, request: Request, staff=Depends(staff_required), db=Depends(session)):
    return team.public(db, team.create_account(db, staff, data.model_dump(), request.state.ip))


class AccountChange(BaseModel):
    role: str | None = None
    active: bool | None = None
    password: str | None = Field(default=None, max_length=256)
    display_name: str | None = None
    email: str | None = None
    departments: list[int] | None = None
    languages: list[str] | None = None
    max_open_tickets: int | None = None
    unlock: bool = False
    reset_totp: bool = False


@router.patch("/accounts/{staff_id}")
def update(staff_id: int, data: AccountChange, request: Request, staff=Depends(staff_required), db=Depends(session)):
    target = require(db, Staff, staff_id)
    return team.public(db, team.update_account(db, staff, target, data.model_dump(exclude_unset=True), request.state.ip))


@router.delete("/accounts/{staff_id}")
def delete(staff_id: int, request: Request, staff=Depends(staff_required), db=Depends(session)):
    team.delete_account(db, staff, require(db, Staff, staff_id), request.state.ip)
    return {"ok": True}


class RoleData(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    description: str = Field(default="", max_length=255)
    permissions: list[str]


@router.post("/roles")
def create_role(data: RoleData, request: Request, staff=Depends(staff_required), db=Depends(session)):
    return team.save_role(db, staff, None, data.name, data.permissions, data.description, request.state.ip)


@router.put("/roles/{role_id}")
def update_role(role_id: str, data: RoleData, request: Request, staff=Depends(staff_required), db=Depends(session)):
    return team.save_role(db, staff, role_id, data.name, data.permissions, data.description, request.state.ip)


@router.delete("/roles/{role_id}")
def delete_role(role_id: str, request: Request, staff=Depends(staff_required), db=Depends(session)):
    team.delete_role(db, staff, role_id, request.state.ip)
    return {"ok": True}
