from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.v1.routes.org import get_stats
from app.core.database import get_db
from app.core.templates import templates
from app.models.models import (
    AboutPillar,
    BlogPost,
    Committee,
    Event,
    LeadershipBoard,
    Milestone,
    OrgSettings,
    PageContentBlock,
    PrayerTimes,
    Project,
)

router = APIRouter()


def _scale_for_counter(amount: float) -> tuple[float, int, str]:
    """(scaled value, decimal places, suffix) for the animated home-page
    counter — full precision under 1,000, K+/M+ above that, matching the
    abbreviate_money template filter used in the admin finance module."""
    if amount >= 1_000_000:
        return round(amount / 1_000_000, 1), 1, "M+"
    if amount >= 1_000:
        return round(amount / 1_000, 1), 1, "K+"
    return round(amount, 0), 0, ""


async def _get_content_blocks(db: AsyncSession) -> dict[str, PageContentBlock]:
    result = await db.execute(select(PageContentBlock))
    return {b.key: b for b in result.scalars().all()}


async def _get_org(db: AsyncSession) -> OrgSettings | None:
    result = await db.execute(select(OrgSettings))
    return result.scalar_one_or_none()


def _serialize_event(e: Event) -> dict:
    return {
        "id": e.id,
        "title": e.title,
        "category": e.category,
        "date": e.date.strftime("%Y-%m-%d") if e.date else None,
        "time": e.time,
        "end_time": e.end_time,
        "location": e.location,
        "description": e.description,
        "is_public": e.is_public,
        "capacity": e.capacity,
        "rsvp_required": e.rsvp_required,
        "icon": e.icon,
    }


def _serialize_blog_post(p: BlogPost) -> dict:
    return {
        "id": p.id,
        "slug": p.slug,
        "title": p.title,
        "excerpt": p.excerpt,
        "category": p.category,
        "icon": p.icon,
        "author_name": p.author_name,
        "date": p.date.strftime("%Y-%m-%d") if p.date else None,
        "read_time": p.read_time,
    }


def _serialize_project(p: Project) -> dict:
    progress_percent = 0
    if p.target and p.target > 0:
        progress_percent = min(100, round((float(p.current) / float(p.target)) * 100))
    elif p.current > 0 and p.target == 0:
        progress_percent = 100  # Or handle as appropriate for your business logic

    return {
        "id": p.id,
        "title": p.title,
        "slug": p.slug,
        "category": p.category,
        "icon": p.icon,
        "summary": p.summary,
        "target": float(p.target),
        "current": float(p.current),
        "progress_percent": progress_percent,
        "status": p.status,
        "deadline": p.deadline.strftime("%Y-%m-%d") if p.deadline else None,
    }


@router.get("/", name="home")
async def home(request: Request, db: AsyncSession = Depends(get_db)):
    org = await _get_org(db)

    # Open projects — limit 3
    proj_result = await db.execute(
        select(Project).where(Project.status == "open").limit(3)
    )
    projects = [_serialize_project(p) for p in proj_result.scalars().all()]

    # Public events — ordered by date, limit 4
    evt_result = await db.execute(
        select(Event)
        .where(Event.is_public == True)  # noqa: E712
        .order_by(Event.date)
        .limit(4)
    )
    events = evt_result.scalars().all()

    # Published blog posts — limit 3
    blog_result = await db.execute(
        select(BlogPost)
        .where(BlogPost.status == "published")
        .order_by(BlogPost.date.desc())
        .limit(3)
    )
    posts = blog_result.scalars().all()

    # Current board with members
    board_result = await db.execute(
        select(LeadershipBoard)
        .where(LeadershipBoard.is_current == True)  # noqa: E712
        .options(selectinload(LeadershipBoard.members))
    )
    current_board = board_result.scalar_one_or_none()

    stats = await get_stats(db)
    raised_value, raised_decimals, raised_suffix = _scale_for_counter(float(stats.total_raised))

    prayer_times = await db.get(PrayerTimes, 1)
    blocks = await _get_content_blocks(db)

    return templates.TemplateResponse(
        request=request,
        name="public/index.html",
        context={
            "org": org,
            "active_nav": "home",
            "projects": projects,
            "events": events,
            "posts": posts,
            "current_board": current_board,
            "active_members": stats.active_members,
            "events_per_year": stats.events_per_year,
            "years_active": stats.years_active,
            "raised_value": raised_value,
            "raised_decimals": raised_decimals,
            "raised_suffix": raised_suffix,
            "prayer_times": prayer_times,
            "blocks": blocks,
        },
    )


@router.get("/about", name="about")
async def about(request: Request, db: AsyncSession = Depends(get_db)):
    org = await _get_org(db)
    blocks = await _get_content_blocks(db)
    pillars = (
        (await db.execute(select(AboutPillar).order_by(AboutPillar.order_index)))
        .scalars()
        .all()
    )
    milestones = (
        (await db.execute(select(Milestone).order_by(Milestone.order_index)))
        .scalars()
        .all()
    )
    return templates.TemplateResponse(
        request=request,
        name="public/about.html",
        context={
            "org": org,
            "active_nav": "about",
            "blocks": blocks,
            "pillars": pillars,
            "milestones": milestones,
        },
    )


@router.get("/blog", name="blog")
async def blog(request: Request, db: AsyncSession = Depends(get_db)):
    org = await _get_org(db)
    result = await db.execute(
        select(BlogPost)
        .where(BlogPost.status == "published")
        .order_by(BlogPost.date.desc())
    )
    posts = [_serialize_blog_post(p) for p in result.scalars().all()]
    return templates.TemplateResponse(
        request=request,
        name="public/blog.html",
        context={"org": org, "active_nav": "blog", "posts": posts},
    )


@router.get("/blog/{slug}", name="blog_post")
async def blog_post(slug: str, request: Request, db: AsyncSession = Depends(get_db)):
    org = await _get_org(db)

    result = await db.execute(
        select(BlogPost).where(BlogPost.slug == slug, BlogPost.status == "published")
    )
    post = result.scalar_one_or_none()

    if post is None:
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail="Post not found")

    # Other posts for sidebar (exclude current)
    others_result = await db.execute(
        select(BlogPost)
        .where(BlogPost.status == "published", BlogPost.slug != slug)
        .order_by(BlogPost.date.desc())
        .limit(3)
    )
    other_posts = others_result.scalars().all()

    return templates.TemplateResponse(
        request=request,
        name="public/blog_post.html",
        context={
            "org": org,
            "active_nav": "blog",
            "post": post,
            "other_posts": other_posts,
        },
    )


@router.get("/events", name="events")
async def events(request: Request, db: AsyncSession = Depends(get_db)):
    org = await _get_org(db)
    result = await db.execute(
        select(Event).where(Event.is_public == True).order_by(Event.date)
    )
    events_list = [_serialize_event(e) for e in result.scalars().all()]
    return templates.TemplateResponse(
        request=request,
        name="public/events.html",
        context={"org": org, "active_nav": "events", "events": events_list},
    )


@router.get("/projects", name="projects")
async def projects(request: Request, db: AsyncSession = Depends(get_db)):
    org = await _get_org(db)
    projects_orm = await db.execute(select(Project).order_by(Project.id.desc()))
    projects_data = [_serialize_project(p) for p in projects_orm.scalars().all()]
    return templates.TemplateResponse(
        request=request,
        name="public/projects.html",
        context={"org": org, "active_nav": "projects", "projects": projects_data},
    )


@router.get("/leadership", name="leadership")
async def leadership(request: Request, db: AsyncSession = Depends(get_db)):
    org = await _get_org(db)
    result = await db.execute(
        select(LeadershipBoard)
        .order_by(LeadershipBoard.term.desc())
        .options(
            selectinload(LeadershipBoard.members),
            selectinload(LeadershipBoard.committees).selectinload(Committee.members),
        )
    )
    boards = result.scalars().all()
    current_board = next((b for b in boards if b.is_current), None)
    past_boards = [b for b in boards if not b.is_current]
    return templates.TemplateResponse(
        request=request,
        name="public/leadership.html",
        context={
            "org": org,
            "active_nav": "leadership",
            "current_board": current_board,
            "past_boards": past_boards,
        },
    )


@router.get("/contact", name="contact")
async def contact(request: Request, db: AsyncSession = Depends(get_db)):
    org = await _get_org(db)
    flash = request.query_params.get("flash")
    return templates.TemplateResponse(
        request=request,
        name="public/contact.html",
        context={"org": org, "active_nav": "contact", "flash": flash},
    )


@router.post("/contact", name="contact_submit")
async def contact_submit(
    request: Request,
    name: str = Form(...),
    email: str = Form(...),
    subject: str = Form(...),
    message: str = Form(...),
    db: AsyncSession = Depends(get_db),
):
    # Log or email the message — for now we just redirect with a flash message.
    # A real implementation would send an email via e.g. the org.email setting.
    return RedirectResponse(
        url="/contact?flash=Thanks+for+reaching+out%21+We%27ll+get+back+to+you+soon%2C+in+shaa+Allah.",
        status_code=303,
    )


@router.get("/donate", name="donate")
async def donate(request: Request, db: AsyncSession = Depends(get_db)):
    org = await _get_org(db)
    result = await db.execute(
        select(Project).where(Project.status == "open").order_by(Project.id)
    )
    open_projects = result.scalars().all()
    selected_project_slug = request.query_params.get("project")
    return templates.TemplateResponse(
        request=request,
        name="public/donate.html",
        context={
            "org": org,
            "active_nav": "donate",
            "open_projects": open_projects,
            "selected_project_slug": selected_project_slug,
        },
    )
