"""附件上传与媒体库路由。"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, File, Query, UploadFile, status

from app.api.deps import AdminUser, AuthorUser, SessionDep
from app.api.pagination import PageParamsDep
from app.schemas.attachment import BackfillResult, UploadResult
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
    """删除附件会同时删除磁盘文件、缩略图与多尺寸变体。"""
    await AttachmentService(session).delete(attachment_id, operator=user)
    await session.commit()
    return Message(detail="附件已删除")


@router.post(
    "/backfill-variants",
    response_model=BackfillResult,
    summary="为存量图片补生成多尺寸变体（站长）",
)
async def backfill_variants(
    user: AdminUser,
    session: SessionDep,
    limit: Annotated[int, Query(ge=1, le=100, description="本轮最多处理的图片数")] = 100,
) -> BackfillResult:
    """幂等运维接口：只处理 ``variants`` 还为空的图片记录。

    存量图片是功能上线前上传的，没有变体；调用本接口按批补生成。
    可反复调用直到 ``processed`` 为 0。单条失败（原文件已被手动清理 /
    图片损坏）只跳过不中断。
    """
    result = await AttachmentService(session).backfill(limit=limit)
    await session.commit()
    return result
