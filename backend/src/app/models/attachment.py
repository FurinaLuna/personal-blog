"""附件（图片 / 文件）模型。"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import JSON, BigInteger, ForeignKey, Integer, String
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
    # 多尺寸变体：{"480": "a1b2c3d4-480.avif", "800": ...}。
    # 值是 stored_name（URL 读时计算）——将来从本地磁盘换对象存储时不用刷这一列。
    # 注意 1：JSON 列的原地修改不会触发 UPDATE，所有写路径必须整体赋值新 dict。
    # 注意 2：none_as_null 必须开——SQLAlchemy 默认把 Python None 序列化成
    # JSON 文本 'null' 而不是 SQL NULL，「没有变体」的筛选（IS NULL）会永远失配。
    variants: Mapped[dict[str, str] | None] = mapped_column(JSON(none_as_null=True))

    url: Mapped[str] = mapped_column(String(500), nullable=False)
    thumbnail_url: Mapped[str | None] = mapped_column(String(500))

    uploader_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    uploader: Mapped[User] = relationship(back_populates="attachments", lazy="joined")

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Attachment id={self.id} name={self.original_name!r} size={self.size}>"
