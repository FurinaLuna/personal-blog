"""API v1 路由聚合。"""

from fastapi import APIRouter

from app.api.v1 import (
    articles,
    attachments,
    auth,
    comments,
    guestbook,
    links,
    notifications,
    revisions,
    series,
    site,
    stats,
    taxonomy,
)

api_router = APIRouter()
api_router.include_router(auth.router)
# 版本路由必须**先于** articles 注册：它的路径是 /articles/{id}/revisions，
# 而 articles 里有 /articles/{slug_or_id}。反过来的话 FastAPI 会先用通配的
# 那个匹配上，把 "5/revisions" 当成 slug 去查文章，得到 404。
api_router.include_router(revisions.router)
api_router.include_router(articles.router)
api_router.include_router(taxonomy.category_router)
api_router.include_router(taxonomy.tag_router)
api_router.include_router(series.router)
api_router.include_router(attachments.router)
api_router.include_router(comments.router)
api_router.include_router(guestbook.router)
api_router.include_router(links.router)
api_router.include_router(site.router)
api_router.include_router(stats.router)
api_router.include_router(notifications.router)

__all__ = ["api_router"]
