"""
policy.py: your own rules.

  GET    /policy-rules          the rules plus what a rule can be about (catalog)
  POST   /policy-rules          add a rule
  PUT    /policy-rules/{id}     change a rule
  DELETE /policy-rules/{id}     remove a rule

Any change re-checks all your analysed contracts, and flags decided earlier keep their decision.
"""

import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field, StrictInt
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.db.database import get_db
from app.models.models import PolicyRule, User
from app.services import policy_service as ps

router = APIRouter(prefix="/policy-rules", tags=["Policy rules"])


class RuleIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    field: str = Field(max_length=40)
    operator: str = Field(max_length=12)
    value: Optional[StrictInt] = None
    target: Optional[str] = Field(default=None, max_length=40)
    severity: str = "MEDIUM"
    message: Optional[str] = Field(default=None, max_length=500)
    enabled: bool = True


class RuleOut(BaseModel):
    id: uuid.UUID
    name: str
    field: str
    operator: str
    value: Optional[int] = None
    target: Optional[str] = None
    severity: str
    message: Optional[str] = None
    enabled: bool
    created_at: datetime
    model_config = {"from_attributes": True}


class CatalogEntry(BaseModel):
    key: str
    label: str
    kind: str
    unit: Optional[str] = None
    operators: List[str]


class RulesResponse(BaseModel):
    rules: List[RuleOut]
    catalog: List[CatalogEntry]
    operator_labels: Dict[str, str]
    missing_targets: Dict[str, str]
    max_rules: int


OPERATOR_LABELS = {**{k: v for k, v in ps.NUMERIC_OPS.items()}, "is_true": "is on", "is_false": "is off", "missing": "is missing"}


async def _payload(db: AsyncSession, user: User) -> RulesResponse:
    rules = (await db.execute(select(PolicyRule).where(PolicyRule.user_id == user.id).order_by(PolicyRule.created_at))).scalars().all()
    return RulesResponse(
        rules=list(rules),
        catalog=[CatalogEntry(key=k, label=v["label"], kind=v["kind"], unit=v["unit"], operators=v["operators"]) for k, v in ps.CATALOG.items()],
        operator_labels=OPERATOR_LABELS, missing_targets=ps.MISSING_TARGETS, max_rules=ps.MAX_RULES_PER_USER,
    )


def _clean(body: RuleIn):
    try:
        value, target = ps.validate(body.field, body.operator, body.value, body.target, body.severity, body.name)
    except ps.RuleError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return value, target


@router.get("", response_model=RulesResponse)
async def list_rules(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    return await _payload(db, user)


@router.post("", response_model=RulesResponse, status_code=201)
async def add_rule(body: RuleIn, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    value, target = _clean(body)
    count = (await db.execute(select(func.count()).select_from(PolicyRule).where(PolicyRule.user_id == user.id))).scalar_one()
    if count >= ps.MAX_RULES_PER_USER:
        raise HTTPException(status_code=422, detail=f"You can have at most {ps.MAX_RULES_PER_USER} rules. Remove one first.")
    db.add(PolicyRule(user_id=user.id, name=" ".join(body.name.split()), field=body.field, operator=body.operator, value=value,
                      target=target, severity=body.severity, message=(body.message or "").strip() or None, enabled=body.enabled))
    await db.flush()
    await ps.reapply(db, user.id)
    await db.commit()
    return await _payload(db, user)


async def _mine(db: AsyncSession, user: User, rule_id: uuid.UUID) -> PolicyRule:
    rule = (await db.execute(select(PolicyRule).where(PolicyRule.id == rule_id, PolicyRule.user_id == user.id))).scalar_one_or_none()
    if rule is None:
        raise HTTPException(status_code=404, detail="Rule not found")
    return rule


@router.put("/{rule_id}", response_model=RulesResponse)
async def change_rule(rule_id: uuid.UUID, body: RuleIn, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    rule = await _mine(db, user, rule_id)
    value, target = _clean(body)
    rule.name, rule.field, rule.operator, rule.value, rule.target = " ".join(body.name.split()), body.field, body.operator, value, target
    rule.severity, rule.message, rule.enabled = body.severity, (body.message or "").strip() or None, body.enabled
    await db.flush()
    await ps.reapply(db, user.id)
    await db.commit()
    return await _payload(db, user)


@router.delete("/{rule_id}", status_code=204)
async def remove_rule(rule_id: uuid.UUID, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    rule = await _mine(db, user, rule_id)
    await db.delete(rule)
    await db.flush()
    await ps.reapply(db, user.id)
    await db.commit()
    return Response(status_code=204)
