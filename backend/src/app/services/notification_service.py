"""评论与留言板邮件通知服务（域事件风格：路由在 commit 后触发，fire-and-forget）。

通知规则（与 ROADMAP 1.5 一致）：

| 事件                  | 收件人     | 条件                                       |
|-----------------------|------------|--------------------------------------------|
| 评论创建（无条件触发）| 被回复者   | 是回复且**已过审**，父评论有 email、非站长所写、未退订 |
| 评论创建              | 站长       | 非站长所写（**待审也要通知**——那正是需要提醒的时刻） |
| 审核通过（approved）  | 被回复者   | 是回复且父评论有 email、未退订（过审时刻才通知） |
| 留言创建（无条件触发）| 站长       | 无（待审也要通知，同上）                    |
| 站长回复留言          | 留言者     | 留言留了 email、未退订，且是**新写的**回复（见下） |

留言板**刻意不做「过审通知」**：对留言板来说「过审」不是访客关心的事件——
访客不会因为「我的留言现在公开了」而收到一封信，那只是站长的后台动作；
「站长回复了」才是他真正在等的事件。多发的每一封信都在消耗收件人对这个地址的
信任（以及邮件服务商的投诉率），所以只留两封：给站长的新留言提醒、给留言者的回复。

留言板的回复通知还有一道**去重闸门**：只有「从没有回复变成有回复」才发信
（判定在 ``GuestbookService.set_reply`` 的 ``is_new_reply``）。站长在后台改错别字、
润色措辞会再次走同一个 PUT 接口——若以「接口被调用」为准，留言者就会被同一件事
反复打扰。

两个关键工程决策：
1. **自建会话**：请求 session 在响应后被 ``get_session`` 关闭，后台任务不能
   复用它——任务里用 ``async_session_factory()`` 开自己的会话，按 id 重查，
   不持有任何请求传来的 ORM 对象；
2. **持强引用**：``asyncio.create_task`` 只持弱引用，没有引用的任务可能被
   GC 掉无声消失——模块级集合持有强引用，完成回调摘除。
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Coroutine

import jwt
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.session import async_session_factory
from app.models import Comment, GuestbookMessage
from app.repositories import (
    CommentRepository,
    GuestbookRepository,
    NotificationOptOutRepository,
    UserRepository,
)
from app.utils.email import EmailSender
from app.utils.exceptions import BadRequestError
from app.utils.security import create_unsubscribe_token, decode_unsubscribe_token
from app.utils.text import truncate

logger = logging.getLogger(__name__)

email_sender = EmailSender()

_pending_tasks: set[asyncio.Task[None]] = set()


def notify_comment_created(comment_id: int) -> None:
    """评论创建后的通知（在路由 commit 之后调用）。"""
    _spawn(_notify_created(comment_id))


def notify_comment_approved(comment_id: int) -> None:
    """评论审核通过后的通知（仅在 approved=True 时调用）。"""
    _spawn(_notify_approved(comment_id))


def notify_guestbook_created(message_id: int) -> None:
    """新留言的通知（在路由 commit 之后调用，给站长）。

    这里**不**判断「是不是站长自己写的」：留言表没有 ``user_id``，而
    ``is_approved`` 不能当判据（站长完全可能在后台先手动过审了别人的留言，
    任务才跑到）。宁可站长偶尔收到一封自己写的留言提醒，也不要漏掉真实留言。
    """
    _spawn(_notify_guestbook_created(message_id))


def notify_guestbook_replied(message_id: int) -> None:
    """站长回复留言后的通知（仅在「新写了回复」时调用，给留言者）。"""
    _spawn(_notify_guestbook_replied(message_id))


async def drain_notifications() -> None:
    """等所有未完成的通知任务跑完（测试同步用，生产不调用）。"""
    tasks = list(_pending_tasks)
    _pending_tasks.clear()
    for task in tasks:
        await task


def _spawn(coro: Coroutine[None, None, None]) -> None:
    task = asyncio.create_task(_guard(coro))
    _pending_tasks.add(task)
    task.add_done_callback(_pending_tasks.discard)


async def _guard(coro: Coroutine[None, None, None]) -> None:
    """兜住任务里的任何异常。

    发信器自己吞异常，但任务里还有查库、拼模板等步骤。后台任务抛出的异常
    没有任何调用方接得住——只会变成 asyncio「Task exception was never
    retrieved」的启动噪音，真出问题时反而看不见。这里统一记日志收口。
    """
    try:
        await coro
    except Exception:
        logger.exception("通知任务异常")


def _article_link(slug: str) -> str:
    return settings.site_base_url + settings.site_article_path.format(slug=slug)


def _unsubscribe_link(email: str) -> str:
    """退订链接。

    token 放在 **URL fragment**（``#`` 之后）而不是 query string，是刻意的：

    - fragment 由浏览器保留在本地，**不会随请求发给服务器**，因此不会出现在
      nginx 的 access log、Referer 头、以及任何中间代理的日志里。
      query string 则会——而这是一个有效期 10 年、凭它就能为任意邮箱退订的凭证；
    - 顺带也不进浏览器历史记录的请求部分与 CDN 日志。

    代价是前端必须从 ``location.hash`` 读，不能用 ``route.query``（见
    ``frontend/src/views/UnsubscribeView.vue``）。
    """
    return f"{settings.site_base_url}/unsubscribe#token={create_unsubscribe_token(email)}"


async def _reply_recipient(comment: Comment, session: AsyncSession) -> str | None:
    """计算被回复者收件地址；不满足通知条件（含已退订）返回 None。

    条件：父评论有 email、非站长所写（站长发信通知自己很荒谬）、
    不是回复者本人（自己接自己的话不写信）、未退订。
    """
    parent = comment.parent
    if parent is None or not parent.author_email or parent.is_admin_reply:
        return None
    recipient = parent.author_email.lower()
    if comment.author_email and comment.author_email.lower() == recipient:
        return None
    if await NotificationOptOutRepository(session).exists_by("email", recipient):
        return None
    return recipient


async def _send_reply_notification(comment: Comment, recipient: str) -> None:
    """「你的评论收到了回复」邮件（含退订链接）。"""
    article = comment.article
    subject = "您的评论收到了新回复"
    text = (
        f"{comment.author_name} 回复了您在《{article.title}》下的评论：\n\n"
        f"{comment.content}\n\n"
        f"查看与回复：{_article_link(article.slug)}\n\n"
        "———\n"
        "不想再收到这类邮件？点击退订：\n"
        f"{_unsubscribe_link(recipient)}\n"
        "（之后重新评论即可恢复订阅）"
    )
    await email_sender.send(to=recipient, subject=subject, text=text)


async def _send_admin_notification(comment: Comment, admin_email: str) -> None:
    """「新评论」站长提醒邮件（含后台审核链接；待审恰恰最需要提醒）。"""
    article = comment.article
    state = "已过审" if comment.is_approved else "待审核"
    subject = f"新评论提醒：{article.title}"
    text = (
        f"{comment.author_name} 在《{article.title}》发表了评论（{state}）：\n\n"
        f"{comment.content}\n\n"
        f"前往处理：{settings.site_base_url}/admin/comments?approved=false"
    )
    await email_sender.send(to=admin_email, subject=subject, text=text)


async def _notify_created(comment_id: int) -> None:
    if not settings.smtp_enabled:
        return
    async with async_session_factory() as session:
        comment = await CommentRepository(session).get_with_relations(comment_id)
        if comment is None:
            return  # 极端时序下评论已被删，通知无事可做

        recipient = await _reply_recipient(comment, session) if comment.is_approved else None
        if recipient:
            await _send_reply_notification(comment, recipient)

        if not comment.is_admin_reply:
            admin_email = await UserRepository(session).first_admin_email()
            if admin_email:
                await _send_admin_notification(comment, admin_email)


async def _notify_approved(comment_id: int) -> None:
    if not settings.smtp_enabled:
        return
    async with async_session_factory() as session:
        comment = await CommentRepository(session).get_with_relations(comment_id)
        if comment is None:
            return
        recipient = await _reply_recipient(comment, session)
        if recipient:
            await _send_reply_notification(comment, recipient)


# ---------------------------------------------------------------- 留言板


def _guestbook_link() -> str:
    """留言板公开页地址（邮件里给留言者看的那个）。"""
    return f"{settings.site_base_url}/guestbook"


async def _send_guestbook_admin_notification(message: GuestbookMessage, admin_email: str) -> None:
    """「新留言」站长提醒邮件。

    正文用 ``truncate`` 截成摘要而不是全文：留言上限 2000 字，全文塞进邮件
    在手机上要滑好几屏，真正要看完整内容时点后台链接更合适。
    链接指向后台待审列表——与评论通知的 ``/admin/comments?approved=false``
    同一形状（``/admin/guestbook`` 这个后台页面由前端后续补，路径先按同一命名约定定下）。
    """
    state = "已过审" if message.is_approved else "待审核"
    subject = "新留言提醒：留言板"
    text = (
        f"{message.author_name} 在留言板留言（{state}）：\n\n"
        f"{truncate(message.content, 200)}\n\n"
        f"前往处理：{settings.site_base_url}/admin/guestbook?approved=false"
    )
    await email_sender.send(to=admin_email, subject=subject, text=text)


async def _send_guestbook_reply_notification(message: GuestbookMessage, recipient: str) -> None:
    """「站长回复了你的留言」邮件（含退订链接）。

    同时带上留言原文：留言者可能几个月前留的言，只给回复内容他无从对应。
    """
    subject = "您的留言收到了回复"
    text = (
        f"站长回复了您在留言板的留言：\n\n"
        f"您的留言：{truncate(message.content, 200)}\n\n"
        f"回复：{message.reply_content}\n\n"
        f"查看：{_guestbook_link()}\n\n"
        "———\n"
        "不想再收到这类邮件？点击退订：\n"
        f"{_unsubscribe_link(recipient)}\n"
        "（之后重新留言即可恢复订阅）"
    )
    await email_sender.send(to=recipient, subject=subject, text=text)


async def _notify_guestbook_created(message_id: int) -> None:
    if not settings.smtp_enabled:
        return
    async with async_session_factory() as session:
        message = await GuestbookRepository(session).get(message_id)
        if message is None:
            return  # 极端时序下留言已被删，通知无事可做
        admin_email = await UserRepository(session).first_admin_email()
        if not admin_email:
            return
        await _send_guestbook_admin_notification(message, admin_email)


async def _notify_guestbook_replied(message_id: int) -> None:
    if not settings.smtp_enabled:
        return
    async with async_session_factory() as session:
        message = await GuestbookRepository(session).get(message_id)
        # 任务跑到时回复可能已经被清掉（站长手快），没有回复内容就没有信可发
        if message is None or not message.reply_content:
            return
        if not message.author_email:
            return  # 没留邮箱的留言者收不到信（这是留言板通知的唯一前提）
        recipient = message.author_email.lower()
        if await NotificationOptOutRepository(session).exists_by("email", recipient):
            return
        await _send_guestbook_reply_notification(message, recipient)


# ---------------------------------------------------------------- 退订


async def unsubscribe(session: AsyncSession, token: str) -> str:
    """把 token 里的邮箱加入退订名单（幂等），返回归一后的邮箱。

    Raises:
        BadRequestError: token 无效 / 过期 / 类型不符。链接坏了就让用户
            重新评论一篇——不用区分错误细节，也不给攻击者探测口子。
    """
    try:
        email = decode_unsubscribe_token(token)
    except jwt.InvalidTokenError as exc:
        raise BadRequestError("退订链接无效或已过期") from exc
    if not await NotificationOptOutRepository(session).exists_by("email", email):
        await NotificationOptOutRepository(session).create(email=email)
    return email
