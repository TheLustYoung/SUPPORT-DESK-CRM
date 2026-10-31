from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from backend.api.deps import COOKIE, check_origin, client_ip, staff_required
from backend.config import settings
from backend.database import session
from backend.services import audit
from backend.services.auth import authenticate, token_for
from backend.services.limits import rate_limit

router = APIRouter(prefix="/api/auth", tags=["auth"])


class Login(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)
    code: str = Field(default="", max_length=12)  # two-factor code when the account has it


@router.post("/login")
def login(data: Login, request: Request, response: Response, db=Depends(session)):
    check_origin(request)
    rate_limit(f"login:{client_ip(request)}", 10, 300)
    staff, reason = authenticate(db, data.username, data.password, data.code)
    if not staff:
        if reason != "code_required":
            audit.record(db, None, "auth.failed", "staff", data.username[:64], {"reason": reason}, client_ip(request))
            db.commit()
        raise HTTPException(401, reason)
    audit.record(db, staff, "auth.login", "staff", staff.username, {}, client_ip(request))
    db.commit()
    response.set_cookie(COOKIE, token_for(staff), httponly=True, secure=settings().cookie_secure, samesite="strict",
                        max_age=settings().session_hours * 3600, path="/api")
    return {"username": staff.username}


@router.post("/logout")
def logout(response: Response, staff=Depends(staff_required)):
    response.delete_cookie(COOKIE, path="/api", secure=settings().cookie_secure, httponly=True, samesite="strict")
    return {"ok": True}
