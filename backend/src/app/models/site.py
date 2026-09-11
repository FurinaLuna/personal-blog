"""站点级配置（单行表）。「关于页」的数据源。"""

from __future__ import annotations

from typing import Any

from sqlalchemy import JSON, Boolean, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class SiteProfile(Base, TimestampMixin):
    """站点主人档案，全站只有一行（id=1），由启动初始化逻辑保证存在。"""

    __tablename__ = "site_profile"

    id: Mapped[int] = mapped_column(primary_key=True, default=1)

    owner_name: Mapped[str] = mapped_column(String(50), default="站长", nullable=False)
    headline: Mapped[str | None] = mapped_column(String(200))
    avatar_url: Mapped[str | None] = mapped_column(String(500))
    bio_md: Mapped[str | None] = mapped_column(Text)
    about_md: Mapped[str | None] = mapped_column(Text)

    email: Mapped[str | None] = mapped_column(String(255))
    location: Mapped[str | None] = mapped_column(String(100))
    icp: Mapped[str | None] = mapped_column(String(100))

    # 结构化字段用 JSON 存：这些是「展示用配置」，不值得为它们各开一张表
    social_links: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON)
    skills: Mapped[list[str] | None] = mapped_column(JSON)

    # 站点策略开关
    comment_need_approval: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    allow_guest_comment: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<SiteProfile owner={self.owner_name!r}>"
