"""API v1 路由聚合。"""

from fastapi import APIRouter

from app.api.v1 import (
    articles,
    attachments,
    auth,
    comments,
    notifications,
    series,
    site,
    stats,
    taxonomy,
)

api_router = APIRouter()
api_router.include_router(auth.router)
api_router.include_router(articles.router)
api_router.include_router(taxonomy.category_router)
api_router.include_router(taxonomy.tag_router)
api_router.include_router(series.router)
api_router.include_router(attachments.router)
api_router.include_router(comments.router)
api_router.include_router(site.router)
api_router.include_router(stats.router)
api_router.include_router(notifications.router)

__all__ = ["api_router"]
