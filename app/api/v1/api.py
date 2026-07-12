from fastapi import APIRouter

from app.api.v1.routes import (
    announcements,
    auth,
    blog,
    communications,
    dues,
    events,
    finance,
    leadership,
    members,
    org,
    payments,
    projects,
    resources,
)

api_router = APIRouter()
api_router.include_router(auth.router)
api_router.include_router(org.router)
api_router.include_router(org.contact_router)
api_router.include_router(projects.router)
api_router.include_router(blog.router)
api_router.include_router(leadership.router)
api_router.include_router(announcements.router)
api_router.include_router(resources.router)
api_router.include_router(events.router)
api_router.include_router(events.me_router)
api_router.include_router(dues.me_router)
api_router.include_router(dues.admin_router)
api_router.include_router(members.router)
api_router.include_router(finance.router)
api_router.include_router(payments.router)
api_router.include_router(communications.router)
