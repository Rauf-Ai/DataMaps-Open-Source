from __future__ import annotations
from typing import Optional, Any, Dict, List, Literal, Union, Callable

from pydantic import BaseModel, EmailStr, Field


class RegisterIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)


class LoginIn(BaseModel):
    email: EmailStr
    password: str


class PasswordResetRequestIn(BaseModel):
    email: EmailStr


class PasswordResetConfirmIn(BaseModel):
    token: str
    password: str = Field(min_length=8, max_length=128)


class MeOut(BaseModel):
    id: int
    email: EmailStr
    is_admin: bool
    email_verified: bool
    credits_balance: int
    credits_spent: int
    total_paid_kop: int
    subscription: Optional[dict] = None
