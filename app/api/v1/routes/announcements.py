from datetime import date

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, require_admin
from app.core.database import get_db
from app.models import Announcement
from app.schemas.announcement import AnnouncementCreate, AnnouncementOut, AnnouncementUpdate

router = APIRouter(prefix="/announcements", tags=["announcements"])


@router.get("", response_model=list[AnnouncementOut], dependencies=[Depends(get_current_user)])
async def list_announcements(db: AsyncSession = Depends(get_db)) -> list[Announcement]:
    result = await db.execute(select(Announcement).order_by(Announcement.date.desc()))
    return list(result.scalars().all())


@router.post(
    "",
    response_model=AnnouncementOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_admin)],
)
async def create_announcement(
    payload: AnnouncementCreate, db: AsyncSession = Depends(get_db)
) -> Announcement:
    data = payload.model_dump()
    if data.get("date") is None:
        data["date"] = date.today()

    announcement = Announcement(**data)
    db.add(announcement)
    await db.commit()
    await db.refresh(announcement)
    return announcement


@router.patch(
    "/{announcement_id}",
    response_model=AnnouncementOut,
    dependencies=[Depends(require_admin)],
)
async def update_announcement(
    announcement_id: int, payload: AnnouncementUpdate, db: AsyncSession = Depends(get_db)
) -> Announcement:
    announcement = await db.get(Announcement, announcement_id)
    if announcement is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Announcement not found")

    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(announcement, field, value)
    await db.commit()
    await db.refresh(announcement)
    return announcement


@router.delete(
    "/{announcement_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_admin)],
)
async def delete_announcement(announcement_id: int, db: AsyncSession = Depends(get_db)) -> None:
    announcement = await db.get(Announcement, announcement_id)
    if announcement is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Announcement not found")

    await db.delete(announcement)
    await db.commit()
