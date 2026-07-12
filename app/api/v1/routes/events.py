from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.deps import get_current_user, get_current_user_optional, require_admin
from app.core.database import get_db
from app.models import Event, Rsvp, User
from app.schemas.event import EventCreate, EventOut, EventUpdate

router = APIRouter(prefix="/events", tags=["events"])
me_router = APIRouter(prefix="/me", tags=["events"])


def _populate_event(event: Event, user: User | None) -> Event:
    event.rsvp_count = len(event.rsvps)
    event.is_rsvped = user is not None and any(r.user_id == user.id for r in event.rsvps)
    return event


@router.get("", response_model=list[EventOut])
async def list_events(
    user: User | None = Depends(get_current_user_optional),
    db: AsyncSession = Depends(get_db),
) -> list[Event]:
    query = select(Event).options(selectinload(Event.rsvps)).order_by(Event.date, Event.time)
    if user is None:
        query = query.where(Event.is_public.is_(True))

    result = await db.execute(query)
    events = list(result.scalars().all())
    for event in events:
        _populate_event(event, user)
    return events


@router.get("/{event_id}", response_model=EventOut)
async def get_event(
    event_id: int,
    user: User | None = Depends(get_current_user_optional),
    db: AsyncSession = Depends(get_db),
) -> Event:
    result = await db.execute(
        select(Event).options(selectinload(Event.rsvps)).where(Event.id == event_id)
    )
    event = result.scalar_one_or_none()
    if event is None or (not event.is_public and user is None):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Event not found")

    return _populate_event(event, user)


@router.post(
    "",
    response_model=EventOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_admin)],
)
async def create_event(payload: EventCreate, db: AsyncSession = Depends(get_db)) -> Event:
    event = Event(**payload.model_dump())
    db.add(event)
    await db.commit()
    await db.refresh(event)
    event.rsvp_count = 0
    event.is_rsvped = False
    return event


@router.patch(
    "/{event_id}",
    response_model=EventOut,
    dependencies=[Depends(require_admin)],
)
async def update_event(
    event_id: int, payload: EventUpdate, db: AsyncSession = Depends(get_db)
) -> Event:
    result = await db.execute(
        select(Event).options(selectinload(Event.rsvps)).where(Event.id == event_id)
    )
    event = result.scalar_one_or_none()
    if event is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Event not found")

    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(event, field, value)
    await db.commit()
    await db.refresh(event, attribute_names=["rsvps"])
    return _populate_event(event, None)


@router.delete(
    "/{event_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_admin)],
)
async def delete_event(event_id: int, db: AsyncSession = Depends(get_db)) -> None:
    event = await db.get(Event, event_id)
    if event is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Event not found")

    await db.delete(event)
    await db.commit()


@router.post("/{event_id}/rsvp", response_model=EventOut)
async def rsvp_event(
    event_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Event:
    result = await db.execute(
        select(Event).options(selectinload(Event.rsvps)).where(Event.id == event_id)
    )
    event = result.scalar_one_or_none()
    if event is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Event not found")

    existing = next((r for r in event.rsvps if r.user_id == user.id), None)
    if existing is not None:
        return _populate_event(event, user)

    if event.capacity is not None and len(event.rsvps) >= event.capacity:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Event is at full capacity"
        )

    rsvp = Rsvp(user_id=user.id, event_id=event.id)
    db.add(rsvp)
    await db.commit()
    await db.refresh(event)

    result = await db.execute(
        select(Event).options(selectinload(Event.rsvps)).where(Event.id == event_id)
    )
    event = result.scalar_one()
    return _populate_event(event, user)


@router.delete("/{event_id}/rsvp", status_code=status.HTTP_204_NO_CONTENT)
async def cancel_rsvp(
    event_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    event = await db.get(Event, event_id)
    if event is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Event not found")

    result = await db.execute(
        select(Rsvp).where(Rsvp.user_id == user.id, Rsvp.event_id == event_id)
    )
    rsvp = result.scalar_one_or_none()
    if rsvp is not None:
        await db.delete(rsvp)
        await db.commit()


@me_router.get("/rsvps", response_model=list[EventOut])
async def my_rsvps(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[Event]:
    result = await db.execute(
        select(Event)
        .join(Rsvp, Rsvp.event_id == Event.id)
        .options(selectinload(Event.rsvps))
        .where(Rsvp.user_id == user.id)
        .order_by(Event.date, Event.time)
    )
    events = list(result.scalars().all())
    for event in events:
        _populate_event(event, user)
    return events
