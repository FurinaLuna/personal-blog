"""留言板服务：留言板领域规则唯一所在地。

四条规则，每条都写了「不这么定会怎样」：

1. **始终先审后发，唯一例外是站长自己发的**（``is_approved=True``）。
   站长发的留言如果也进待审队列，就会出现「站长发了一条自己在前台看不到」的荒诞剧；
   而站长想先写一条欢迎留言时，也不该再回后台给自己点一次通过。
   作者（author 角色）**不**享受这个例外：留言板是站点级空间，作者身份不代表
   对它的背书，口径能少一个分支就少一个。
2. **留言板刻意不受 ``profile.allow_guest_comment`` 限制**。那个开关的语义是
   「文章评论区是否允许游客发言」（站点配置里它就叫 guest_comment）；而留言板
   存在的**唯一意义**就是让访客留言，把它也关掉等于把这个页面变成摆设。
   它的防线是另外两条：先审后发 + 限流（5 次/分，见 ``api/deps.py``）。
3. **匿名必须填昵称**（``BadRequestError`` → 400）；登录用户可以不填，自动取
   ``nickname or username``。留言板没有「回复某条留言」这种能把匿名者串起来的
   关系，一堆无名氏会让站长完全无法判断该回谁。
4. **一条留言只有一个回复**，且「清除回复」要把 ``replied_at`` / ``replied_by_id``
   一起清掉——只清正文会留下「有回复时间、没回复内容」的半截状态。

事务：只 ``flush`` 不 ``commit``，边界交给 ``api/v1/guestbook.py`` 与
``get_session`` 依赖收口——与本项目其余 service 一致。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import GuestbookMessage, User, UserRole
from app.repositories import GuestbookRepository
from app.schemas.common import Page, PageParams
from app.schemas.guestbook import (
    GuestbookMessageAdminRead,
    GuestbookMessageCreate,
    GuestbookMessageRead,
)
from app.utils.exceptions import BadRequestError, NotFoundError


@dataclass(frozen=True, slots=True)
class GuestbookReplyResult:
    """写回复的结果：响应体 + 「这一次是不是真的新写了一条回复」。

    为什么不让路由自己判断「设置前是空、设置后非空」：那要求路由为了发不发一封信
    再读一次同一行，并把「什么算新回复」这条规则复制到 HTTP 层。规则只写一份
    （见 ``set_reply``），路由只消费结论。

    这个标志同时是**通知去重**的闸门：只有「从没有回复变成有回复」才发信，
    站长改错别字、润色措辞不会重复打扰留言者（见 ``notify_guestbook_replied``）。
    """

    message: GuestbookMessageAdminRead
    is_new_reply: bool


class GuestbookService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.messages = GuestbookRepository(session)

    # ---------------------------------------------------------------- 读取

    async def list_public(self, page_params: PageParams) -> Page[GuestbookMessageRead]:
        """公开列表：只含已过审的留言，最新的在前。"""
        total = await self.messages.count(approved=True)
        rows = await self.messages.list_paged(
            offset=page_params.offset, limit=page_params.limit, approved=True
        )
        return Page.build(
            [self._to_read(row) for row in rows],
            total,
            page_params.page,
            page_params.page_size,
        )

    async def list_manage(
        self, page_params: PageParams, *, approved: bool | None = None
    ) -> Page[GuestbookMessageAdminRead]:
        """后台列表：全部留言，可按审核状态过滤（``None`` = 不过滤）。

        这里返回带邮箱与 IP 的 admin schema；它不会进公开缓存，因为
        ``/manage`` 被 ``api/cache.py`` 的 ``_EXCLUDED_MARKERS`` 排除在外。
        """
        total = await self.messages.count(approved=approved)
        rows = await self.messages.list_paged(
            offset=page_params.offset, limit=page_params.limit, approved=approved
        )
        return Page.build(
            [self._to_admin_read(row) for row in rows],
            total,
            page_params.page,
            page_params.page_size,
        )

    @staticmethod
    def _to_read(message: GuestbookMessage) -> GuestbookMessageRead:
        """公开响应：**逐字段构造**，而不是 ``model_validate`` 整个 ORM 对象。

        这样「邮箱 / IP / UA 不进公开响应」是这行代码显式表达的事实；
        哪天有人往模型上加一列敏感字段，公开响应也不会因为它存在就自动泄漏。
        """
        return GuestbookMessageRead(
            id=message.id,
            author_name=message.author_name,
            author_site=message.author_site,
            content=message.content,
            is_approved=message.is_approved,
            reply_content=message.reply_content,
            replied_at=message.replied_at,
            created_at=message.created_at,
        )

    @staticmethod
    def _to_admin_read(message: GuestbookMessage) -> GuestbookMessageAdminRead:
        """站长响应：在公开字段之上补邮箱与 IP（UA 仍然不给：审核用不上它）。"""
        return GuestbookMessageAdminRead(
            **GuestbookService._to_read(message).model_dump(),
            author_email=message.author_email,
            ip_address=message.ip_address,
        )

    # ---------------------------------------------------------------- 写入

    async def create(
        self,
        payload: GuestbookMessageCreate,
        *,
        viewer: User | None,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> GuestbookMessageRead:
        """发表留言。

        Raises:
            BadRequestError: 游客没填昵称。
        """
        # 只有站长直接过审（见模块 docstring 第 1 条）。作者角色仍走人工审核。
        is_admin = viewer is not None and viewer.role is UserRole.ADMIN

        if viewer is not None:
            display_name = viewer.nickname or viewer.username
        else:
            display_name = payload.author_name
            if not display_name:
                raise BadRequestError("请填写昵称")

        message = await self.messages.create(
            author_name=display_name,
            author_email=str(payload.author_email) if payload.author_email else None,
            author_site=payload.author_site,
            content=payload.content,
            is_approved=is_admin,
            ip_address=ip_address,
            # 与评论同一口径：UA 是反垃圾线索，不是内容，超长一律截断而不是拒收
            user_agent=user_agent[:500] if user_agent else None,
        )
        return self._to_read(message)

    async def set_approved(self, message_id: int, approved: bool) -> GuestbookMessageAdminRead:
        """放行 / 撤回一条留言。

        Raises:
            NotFoundError: 留言不存在。
        """
        message = await self._get_or_404(message_id)
        message.is_approved = approved
        await self.session.flush()
        return self._to_admin_read(message)

    async def set_reply(
        self, message_id: int, reply_content: str, *, operator: User
    ) -> GuestbookReplyResult:
        """写 / 清站长的回复。

        语义（也是 ``GuestbookReplyResult.is_new_reply`` 的判定依据）：

        - 正文 strip 后**非空** → 写入，``replied_at=now(UTC)``、``replied_by_id=operator.id``；
          **此前没有回复**时 ``is_new_reply=True``（路由据此发信），
          此前已有回复（改错别字、重写）时为 ``False``，不重复打扰留言者。
        - 正文 strip 后**为空** → 三列一起清空，``is_new_reply=False``（清除不是新回复）。

        注意这里用「此前这一行有没有回复内容」而不是「调用方传了什么」判断新回复：
        接口被调用 ≠ 留言者收到了新回复。

        Raises:
            NotFoundError: 留言不存在。
        """
        message = await self._get_or_404(message_id)
        cleaned = reply_content.strip()
        had_reply = bool((message.reply_content or "").strip())

        if cleaned:
            message.reply_content = cleaned
            message.replied_at = datetime.now(UTC)
            message.replied_by_id = operator.id
        else:
            message.reply_content = None
            message.replied_at = None
            message.replied_by_id = None

        await self.session.flush()
        return GuestbookReplyResult(
            message=self._to_admin_read(message),
            is_new_reply=bool(cleaned) and not had_reply,
        )

    async def delete(self, message_id: int) -> None:
        """删除留言（物理删除）。

        一行就是一条完整留言（含回复），没有需要级联的子表，也不存在「软删除后
        还要在别处过滤 is_deleted」的负担；误删的代价由站长自己承担。

        Raises:
            NotFoundError: 留言不存在。
        """
        message = await self._get_or_404(message_id)
        await self.messages.delete(message)

    # ---------------------------------------------------------------- 工具

    async def _get_or_404(self, message_id: int) -> GuestbookMessage:
        message = await self.messages.get(message_id)
        if message is None:
            raise NotFoundError("留言不存在")
        return message
