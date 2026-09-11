"""附件（图片 / 文件）模型。"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin

if TYPE_CHECKING:
    from app.models.user import User


class Attachment(Base, TimestampMixin):
    """上传记录。

    库里只存元数据与相对 URL，物理文件落在对象存储 / 本地磁盘上——
    这样将来从本地磁盘切到 S3 / COS 只需要换一个 StorageBackend 实现。
    """

    __tablename__ = "attachments"

    id: Mapped[int] = mapped_column(primary_key=True)
    # 落到磁盘上的文件名（UUID + 扩展名），避免用户文件名带来的路径穿越与重名
    stored_name: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    # 用户原始文件名，仅用于展示与下载时还原
    original_name: Mapped[str] = mapped_column(String(255), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(120), nullable=False)
    size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    kind: Mapped[str] = mapped_column(String(20), default="image", nullable=False)  # image | file

    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    thumbnail_name: Mapped[str | None] = mapped_column(String(255))

    url: Mapped[str] = mapped_column(String(500), nullable=False)
    thumbnail_url: Mapped[str | None] = mapped_column(String(500))

    uploader_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    uploader: Mapped[User] = relationship(back_populates="attachments", lazy="joined")

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Attachment id={self.id} name={self.original_name!r} size={self.size}>"
