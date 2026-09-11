"""订阅源与站点地图端点（挂在根路径，不走 /api/v1）。"""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import Response

from app.api.deps import SessionDep
from app.services import FeedService

router = APIRouter(tags=["feed"])


@router.get("/feed.xml", summary="RSS 2.0 订阅源", include_in_schema=True)
async def rss_feed(session: SessionDep) -> Response:
    xml = await FeedService(session).rss()
    return Response(
        content=xml,
        media_type="application/rss+xml; charset=utf-8",
        # 订阅源允许缓存一小时：阅读器本身也是定时轮询，没必要每次都实时生成
        headers={"Cache-Control": "public, max-age=3600"},
    )


@router.get("/sitemap.xml", summary="站点地图", include_in_schema=True)
async def sitemap(session: SessionDep) -> Response:
    xml = await FeedService(session).sitemap()
    return Response(
        content=xml,
        media_type="application/xml; charset=utf-8",
        headers={"Cache-Control": "public, max-age=3600"},
    )
