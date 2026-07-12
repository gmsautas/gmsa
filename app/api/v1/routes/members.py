from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_admin
from app.core.database import get_db
from app.models import DuesRecord, User
from app.models.models import ROLES, USER_STATUSES
from app.schemas.user import BulkMemberAction, UserAdminOut, UserAdminUpdate
from app.services import academic
from app.services.audience import current_semester_label

router = APIRouter(prefix="/admin/members", tags=["members"])


async def _dues_status_for(db: AsyncSession, user_id: int) -> str:
    semester = current_semester_label()
    result = await db.execute(
        select(DuesRecord).where(
            DuesRecord.user_id == user_id, DuesRecord.semester == semester
        )
    )
    record = result.scalar_one_or_none()
    return record.status if record is not None else "unpaid"


@router.get("", response_model=list[UserAdminOut])
async def list_members(
    q: str | None = None,
    role: str | None = None,
    status_: str | None = Query(default=None, alias="status"),
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(require_admin),
) -> list[UserAdminOut]:
    query = select(User)

    if q:
        pattern = f"%{q}%"
        query = query.where((User.name.ilike(pattern)) | (User.email.ilike(pattern)))
    if role:
        query = query.where(User.role == role)
    if status_:
        query = query.where(User.status == status_)

    query = query.order_by(User.name)
    result = await db.execute(query)
    users = result.scalars().all()

    semester = current_semester_label()
    records_result = await db.execute(
        select(DuesRecord).where(DuesRecord.semester == semester)
    )
    records_by_user = {r.user_id: r for r in records_result.scalars().all()}

    out: list[UserAdminOut] = []
    for user in users:
        record = records_by_user.get(user.id)
        user.dues_status = record.status if record is not None else "unpaid"
        user.level = academic.effective_level(user)
        out.append(UserAdminOut.model_validate(user))
    return out


@router.get("/{user_id}", response_model=UserAdminOut)
async def get_member(
    user_id: int,
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(require_admin),
) -> UserAdminOut:
    user = await db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Member not found")

    user.dues_status = await _dues_status_for(db, user.id)
    user.level = academic.effective_level(user)
    return UserAdminOut.model_validate(user)


@router.patch("/{user_id}", response_model=UserAdminOut)
async def update_member(
    user_id: int,
    payload: UserAdminUpdate,
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(require_admin),
) -> UserAdminOut:
    user = await db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Member not found")

    data = payload.model_dump(exclude_unset=True)

    if "role" in data and data["role"] not in ROLES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Invalid role. Must be one of: {', '.join(ROLES)}",
        )
    if "role" in data and data["role"] != user.role and _admin.role != "superadmin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Changing a member's role requires a super admin",
        )
    if "status" in data and data["status"] not in USER_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Invalid status. Must be one of: {', '.join(USER_STATUSES)}",
        )

    for field, value in data.items():
        setattr(user, field, value)

    await db.commit()
    await db.refresh(user)

    user.dues_status = await _dues_status_for(db, user.id)
    user.level = academic.effective_level(user)
    return UserAdminOut.model_validate(user)


@router.delete("/{user_id}", response_model=UserAdminOut)
async def deactivate_member(
    user_id: int,
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(require_admin),
) -> UserAdminOut:
    user = await db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Member not found")

    user.status = "inactive"
    await db.commit()
    await db.refresh(user)

    user.dues_status = await _dues_status_for(db, user.id)
    user.level = academic.effective_level(user)
    return UserAdminOut.model_validate(user)


# Bulk actions requiring a super admin — role changes and mass deactivation are
# a bigger blast radius than doing either one member at a time.
_SUPERADMIN_BULK_ACTIONS = {"role_member", "role_admin", "delete"}


@router.post("/bulk")
async def bulk_update_members(
    payload: BulkMemberAction,
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(require_admin),
) -> dict:
    if payload.action in _SUPERADMIN_BULK_ACTIONS and _admin.role != "superadmin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This bulk action requires a super admin",
        )
    if not payload.user_ids:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="No members selected")

    members = (
        (await db.execute(select(User).where(User.id.in_(payload.user_ids)))).scalars().all()
    )

    if payload.action == "activate":
        for m in members:
            m.status = "active"
    elif payload.action in ("deactivate", "delete"):
        # No hard-delete for users: dues/transaction/RSVP history hangs off
        # this row, so "delete" here is the same soft-deactivate as a
        # single-member delete, just gated more strictly given the blast radius.
        for m in members:
            m.status = "inactive"
    elif payload.action == "role_member":
        for m in members:
            m.role = "member"
    elif payload.action == "role_admin":
        for m in members:
            m.role = "admin"
    else:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Unknown bulk action"
        )

    await db.commit()
    return {"updated": len(members)}
