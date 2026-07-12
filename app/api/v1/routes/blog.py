from datetime import date

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_admin
from app.core.database import get_db
from app.models import BlogPost
from app.schemas.blog import BlogPostCreate, BlogPostOut, BlogPostUpdate

router = APIRouter(prefix="/blog", tags=["blog"])


@router.get("", response_model=list[BlogPostOut])
async def list_blog_posts(db: AsyncSession = Depends(get_db)) -> list[BlogPost]:
    result = await db.execute(
        select(BlogPost).where(BlogPost.status == "published").order_by(BlogPost.date.desc())
    )
    return list(result.scalars().all())


@router.get("/admin/all", response_model=list[BlogPostOut], dependencies=[Depends(require_admin)])
async def list_all_blog_posts(db: AsyncSession = Depends(get_db)) -> list[BlogPost]:
    result = await db.execute(select(BlogPost).order_by(BlogPost.date.desc()))
    return list(result.scalars().all())


@router.get("/{slug}", response_model=BlogPostOut)
async def get_blog_post(slug: str, db: AsyncSession = Depends(get_db)) -> BlogPost:
    result = await db.execute(select(BlogPost).where(BlogPost.slug == slug))
    post = result.scalar_one_or_none()
    if post is None or post.status != "published":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Blog post not found")
    return post


@router.post(
    "",
    response_model=BlogPostOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_admin)],
)
async def create_blog_post(payload: BlogPostCreate, db: AsyncSession = Depends(get_db)) -> BlogPost:
    existing = await db.execute(select(BlogPost).where(BlogPost.slug == payload.slug))
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="A blog post with this slug already exists"
        )

    data = payload.model_dump()
    if data.get("date") is None:
        data["date"] = date.today()

    post = BlogPost(**data)
    db.add(post)
    await db.commit()
    await db.refresh(post)
    return post


@router.patch(
    "/{post_id}",
    response_model=BlogPostOut,
    dependencies=[Depends(require_admin)],
)
async def update_blog_post(
    post_id: int, payload: BlogPostUpdate, db: AsyncSession = Depends(get_db)
) -> BlogPost:
    post = await db.get(BlogPost, post_id)
    if post is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Blog post not found")

    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(post, field, value)
    await db.commit()
    await db.refresh(post)
    return post


@router.delete(
    "/{post_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_admin)],
)
async def delete_blog_post(post_id: int, db: AsyncSession = Depends(get_db)) -> None:
    post = await db.get(BlogPost, post_id)
    if post is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Blog post not found")

    await db.delete(post)
    await db.commit()
