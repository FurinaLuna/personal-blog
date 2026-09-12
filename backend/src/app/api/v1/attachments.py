"""附件上传与媒体库路由。"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, File, Query, UploadFile, status

from app.api.deps import AuthorUser, SessionDep
from app.api.pagination import PageParamsDep
from app.schemas.attachment import UploadResult
from app.schemas.common import Message, Page
from app.services import AttachmentService

router = APIRouter(prefix="/attachments", tags=["附件"])


@router.post(
    "/upload",
    response_model=UploadResult,
    status_code=status.HTTP_201_CREATED,
    summary="上传图片或附件（作者）",
)
async def upload(
    user: AuthorUser,
    session: SessionDep,
    file: Annotated[UploadFile, File(description="图片或附件，大小上限见配置 MAX_UPLOAD_SIZE")],
) -> UploadResult:
    """上传文件。

    返回体里的 ``markdown`` 字段可以直接粘进文章正文，这是编辑器「上传即插入」
    体验的服务端支撑。

    校验要点：真实类型靠 Pillow 解码判定而非客户端声明的 Content-Type；
    超限在读取过程中就中断；文件名由服务端生成。
    """
    result = await AttachmentService(session).upload(upload=file, uploader=user)
    # 写路由显式提交（原因见 db/session.py get_session 说明）
    await session.commit()
    return result


@router.get("", response_model=Page[UploadResult], summary="媒体库列表（作者）")
async def list_attachments(
    user: AuthorUser,
    session: SessionDep,
    page_params: PageParamsDep,
    kind: Annotated[str | None, Query(pattern="^(image|file)$", description="筛选类型")] = None,
) -> Page[UploadResult]:
    """作者只看自己的上传，站长看全站。"""
    return await AttachmentService(session).list_paged(
        page_params=page_params, kind=kind, viewer=user
    )


@router.delete("/{attachment_id}", response_model=Message, summary="删除附件（作者）")
async def delete_attachment(attachment_id: int, user: AuthorUser, session: SessionDep) -> Message:
    """删除附件会同时删除磁盘文件与缩略图。"""
    await AttachmentService(session).delete(attachment_id, operator=user)
    await session.commit()
    return Message(detail="附件已删除")
