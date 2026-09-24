"""留言板路由。

只做 HTTP：依赖注入、状态码、提交事务。规则全在 ``GuestbookService``。

两个接口共用 ``/guestbook`` 前缀，读法必须分清楚：

- ``GET /guestbook``：**匿名可读**，只返回已过审的留言，用于前台留言板页。
  它在 ``api/cache.py`` 的 ``CACHEABLE_PREFIXES`` 白名单里，会带上
  ETag / Cache-Control / ``Vary: Authorization``——因此这个响应**不能**掺入
  任何随用户变化的字段（邮箱、IP 都不在里面，见 ``schemas/guestbook.py``）。
- ``GET /guestbook/manage``：站长专属，含待审留言与邮箱/IP。路径里的 ``/manage``
  被 ``_EXCLUDED_MARKERS`` 排除，天生不会被公开缓存。

路由声明顺序：静态路径 ``/manage`` 写在前面。当前 ``/{message_id}`` 上只有
PATCH / PUT / DELETE，与 ``GET /manage`` 不会互相吃掉；但顺序一旦反过来，
将来加 ``GET /guestbook/{message_id}`` 详情接口时就会踩到 ``articles`` 那个
「把 '5/revisions' 当 slug 查」的坑，这里先按安全的一侧写。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query, Request, status

from app.api.deps import GUESTBOOK_RATE_LIMIT, AdminUser, OptionalUser, SessionDep, client_ip
from app.api.pagination import PageParamsDep
from app.schemas.common import Message, Page
from app.schemas.guestbook import (
    GuestbookMessageAdminRead,
    GuestbookMessageCreate,
    GuestbookMessageRead,
    GuestbookModerate,
    GuestbookReply,
)
from app.services import GuestbookService
from app.services.notification_service import (
    notify_guestbook_created,
    notify_guestbook_replied,
)

router = APIRouter(prefix="/guestbook", tags=["留言板"])


@router.get("", response_model=Page[GuestbookMessageRead], summary="留言列表（公开，仅已过审）")
async def list_messages(
    session: SessionDep, page_params: PageParamsDep
) -> Page[GuestbookMessageRead]:
    """前台留言板页。只含 ``is_approved=true``，最新的在前。"""
    return await GuestbookService(session).list_public(page_params)


@router.post(
    "",
    response_model=GuestbookMessageRead,
    status_code=status.HTTP_201_CREATED,
    summary="发表留言（支持匿名）",
)
async def create_message(
    payload: GuestbookMessageCreate,
    request: Request,
    session: SessionDep,
    viewer: OptionalUser,
    _: None = GUESTBOOK_RATE_LIMIT,
) -> GuestbookMessageRead:
    """发表留言。

    默认返回 201 + ``is_approved=false``（先审后发），前端据此提示「已提交，等待审核」；
    站长自己发的会直接过审，所以能在前台立刻看到。
    """
    created = await GuestbookService(session).create(
        payload,
        viewer=viewer,
        # 走统一的 client_ip：是否信任 X-Forwarded-For 由 TRUST_PROXY_HEADERS 决定，
        # 避免这里另写一套解析（盲信 XFF 等于把伪造的 IP 存进库）
        ip_address=client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    # 写路由显式提交（原因见 db/session.py get_session 说明）
    await session.commit()
    # 通知必须在 commit 之后触发：后台任务自建会话按 id 重查，
    # 未提交就触发会查不到这行（fire-and-forget，不阻塞响应）
    notify_guestbook_created(created.id)
    return created


# ------------------------------------------------------------------ 后台管理


@router.get(
    "/manage", response_model=Page[GuestbookMessageAdminRead], summary="留言列表（站长，含待审）"
)
async def list_managed_messages(
    _: AdminUser,
    session: SessionDep,
    page_params: PageParamsDep,
    approved: Annotated[bool | None, Query(description="按审核状态过滤，不传为全部")] = None,
) -> Page[GuestbookMessageAdminRead]:
    """后台列表。含待审留言与邮箱 / IP，可按 ``approved`` 过滤。"""
    return await GuestbookService(session).list_manage(page_params, approved=approved)


@router.patch("/{message_id}", response_model=GuestbookMessageAdminRead, summary="审核留言（站长）")
async def moderate_message(
    message_id: int, payload: GuestbookModerate, _: AdminUser, session: SessionDep
) -> GuestbookMessageAdminRead:
    """通过 / 撤回留言。``is_approved=True`` 即放行，不传视为放行（与评论同一口径）。

    审核本身**不发信**：对访客来说「我的留言过审了」不是他关心的事件，
    「站长回复了」才是（见 ``notification_service`` 的说明）。
    """
    approved = True if payload.is_approved is None else payload.is_approved
    message = await GuestbookService(session).set_approved(message_id, approved)
    await session.commit()
    return message


@router.put(
    "/{message_id}/reply",
    response_model=GuestbookMessageAdminRead,
    summary="回复留言（站长）",
)
async def reply_message(
    message_id: int, payload: GuestbookReply, user: AdminUser, session: SessionDep
) -> GuestbookMessageAdminRead:
    """写回复；``reply_content`` 传空字符串表示清除回复。

    只有**新写**的回复才通知留言者：这个 PUT 也是站长改错别字用的接口，
    以「接口被调用」为准会让同一条回复反复发信（判定见 ``set_reply`` 的返回值）。
    """
    result = await GuestbookService(session).set_reply(
        message_id, payload.reply_content, operator=user
    )
    await session.commit()
    # 与创建一致：必须在 commit 之后触发，否则后台任务按 id 查不到这行
    if result.is_new_reply:
        notify_guestbook_replied(result.message.id)
    return result.message


@router.delete("/{message_id}", response_model=Message, summary="删除留言（站长）")
async def delete_message(message_id: int, _: AdminUser, session: SessionDep) -> Message:
    """物理删除；这条留言的站长回复也随行一起消失（同一个行内字段）。"""
    await GuestbookService(session).delete(message_id)
    await session.commit()
    return Message(detail="留言已删除")
