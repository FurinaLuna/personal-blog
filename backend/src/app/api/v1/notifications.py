"""评论邮件通知路由。

只有一个公开端点：退订。发信本身不在这里——通知是评论路由
commit 后触发的后台任务（见 ``services/notification_service.py``），
没有「查通知列表」这类需求，不为凑文件而造 API。
"""

from __future__ import annotations

from fastapi import APIRouter, status

from app.api.deps import SessionDep
from app.schemas.common import Message
from app.schemas.notification import UnsubscribeRequest
from app.services.notification_service import unsubscribe

router = APIRouter(prefix="/notifications", tags=["通知"])


@router.post(
    "/unsubscribe",
    response_model=Message,
    status_code=status.HTTP_200_OK,
    summary="退订评论回复邮件",
)
async def unsubscribe_notifications(payload: UnsubscribeRequest, session: SessionDep) -> Message:
    """凭邮件里的签名 token 退订。幂等：重复点、再发一次都返回成功。"""
    email = await unsubscribe(session, payload.token)
    # 写路由显式提交（原因见 db/session.py get_session 说明）
    await session.commit()
    return Message(detail=f"{email} 已退订评论回复通知")
