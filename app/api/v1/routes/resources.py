from datetime import date

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, require_admin
from app.core.database import get_db
from app.models import Resource
from app.schemas.resource import ResourceCreate, ResourceOut, ResourceUpdate

router = APIRouter(prefix="/resources", tags=["resources"])


@router.get("", response_model=list[ResourceOut], dependencies=[Depends(get_current_user)])
async def list_resources(db: AsyncSession = Depends(get_db)) -> list[Resource]:
    result = await db.execute(select(Resource).order_by(Resource.date.desc()))
    return list(result.scalars().all())


@router.post(
    "",
    response_model=ResourceOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_admin)],
)
async def create_resource(payload: ResourceCreate, db: AsyncSession = Depends(get_db)) -> Resource:
    data = payload.model_dump()
    if data.get("date") is None:
        data["date"] = date.today()

    resource = Resource(**data)
    db.add(resource)
    await db.commit()
    await db.refresh(resource)
    return resource


@router.patch(
    "/{resource_id}",
    response_model=ResourceOut,
    dependencies=[Depends(require_admin)],
)
async def update_resource(
    resource_id: int, payload: ResourceUpdate, db: AsyncSession = Depends(get_db)
) -> Resource:
    resource = await db.get(Resource, resource_id)
    if resource is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Resource not found")

    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(resource, field, value)
    await db.commit()
    await db.refresh(resource)
    return resource


@router.delete(
    "/{resource_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_admin)],
)
async def delete_resource(resource_id: int, db: AsyncSession = Depends(get_db)) -> None:
    resource = await db.get(Resource, resource_id)
    if resource is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Resource not found")

    await db.delete(resource)
    await db.commit()
