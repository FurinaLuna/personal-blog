"""友情链接路由。

只做 HTTP：依赖注入、状态码、提交事务。规则全在 ``FriendLinkService``。

两个接口共用 ``/links`` 前缀，读法必须分清楚：

- ``GET /links``：**匿名可读**，只返回已启用的条目，用于前台友链页。
  它在 ``api/cache.py`` 的 ``CACHEABLE_PREFIXES`` 白名单里，会带上
  ETag / Cache-Control / ``Vary: Authorization``——因此这个响应**不能**
  掺入任何随用户变化的字段，否则会污染共享缓存。
- ``GET /links/manage``：站长专属，含未启用的条目。路径里的 ``/manage``
  被 ``_EXCLUDED_MARKERS`` 排除，天生不会被公开缓存。

路由声明顺序：静态路径 ``/manage`` 写在前面。当前没有 ``/links/{id}`` 的 GET，
两者不会互相吃掉；但顺序一旦反过来，将来加详情接口时就会踩到
``articles`` 那个「把 '5/revisions' 当 slug 查」的坑，这里先按安全的一侧写。
"""

from __future__ import annotations

from fastapi import APIRouter, status

from app.api.deps import AdminUser, SessionDep
from app.schemas.common import Message
from app.schemas.friend_link import FriendLinkCreate, FriendLinkRead, FriendLinkUpdate
from app.services import FriendLinkService

router = APIRouter(prefix="/links", tags=["友链"])


@router.get("", response_model=list[FriendLinkRead], summary="友链列表（公开，仅启用的）")
async def list_links(session: SessionDep) -> list[FriendLinkRead]:
    """前台友链页。只含 ``is_active=true``，按展示顺序升序。"""
    links = await FriendLinkService(session).list_active()
    return [FriendLinkRead.model_validate(link) for link in links]


@router.get("/manage", response_model=list[FriendLinkRead], summary="友链列表（站长，含未启用）")
async def list_managed_links(_: AdminUser, session: SessionDep) -> list[FriendLinkRead]:
    """后台列表。含未启用条目，排序口径与前台一致。"""
    links = await FriendLinkService(session).list_all()
    return [FriendLinkRead.model_validate(link) for link in links]


@router.post(
    "",
    response_model=FriendLinkRead,
    status_code=status.HTTP_201_CREATED,
    summary="新建友链（站长）",
)
async def create_link(
    payload: FriendLinkCreate, _: AdminUser, session: SessionDep
) -> FriendLinkRead:
    link = await FriendLinkService(session).create(payload)
    # 写路由显式提交（原因见 db/session.py get_session 说明）
    await session.commit()
    return FriendLinkRead.model_validate(link)


@router.patch("/{link_id}", response_model=FriendLinkRead, summary="修改友链（站长）")
async def update_link(
    link_id: int, payload: FriendLinkUpdate, _: AdminUser, session: SessionDep
) -> FriendLinkRead:
    """部分更新：只改请求体里真的带了的字段。"""
    link = await FriendLinkService(session).update(link_id, payload)
    await session.commit()
    return FriendLinkRead.model_validate(link)


@router.delete("/{link_id}", response_model=Message, summary="删除友链（站长）")
async def delete_link(link_id: int, _: AdminUser, session: SessionDep) -> Message:
    """删除是物理删除；临时下线请用 ``PATCH {"is_active": false}``。"""
    await FriendLinkService(session).delete(link_id)
    await session.commit()
    return Message(detail="友链已删除")
