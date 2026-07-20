"""Auth router — the only endpoints reachable without a token."""

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from api import activity
from api.auth import authenticate, create_access_token, get_caller, Principal

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginIn(BaseModel):
    email: str
    password: str


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    role: str
    tenant_id: str
    full_name: str


@router.post("/login", response_model=TokenOut)
def login(body: LoginIn):
    principal = authenticate(body.email, body.password)
    if principal is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid email or password")
    token = create_access_token(
        user_id=principal.user_id, tenant_id=principal.tenant_id, role=principal.role
    )
    activity.log(
        tenant_id=principal.tenant_id, actor_user_id=principal.user_id,
        action="auth.login", entity_type="user", entity_id=principal.user_id,
    )
    return TokenOut(
        access_token=token, role=principal.role,
        tenant_id=principal.tenant_id, full_name=principal["full_name"],
    )


@router.get("/me")
def me(user: Principal = Depends(get_caller)):
    return {
        "user_id": user.user_id, "email": user["email"], "full_name": user["full_name"],
        "tenant_id": user.tenant_id, "role": user.role, "kind": user["kind"],
        "assessment_id": user.get("assessment_id"),
    }
