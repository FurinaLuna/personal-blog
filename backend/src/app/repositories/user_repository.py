"""用户仓储。"""

from __future__ import annotations

from sqlalchemy import func, or_, select

from app.models import User, UserRole
from app.repositories.base import BaseRepository


class UserRepository(BaseRepository[User]):
    model = User

    async def get_by_username(self, username: str) -> User | None:
        return await self.get_by("username", username)

    async def get_by_email(self, email: str) -> User | None:
        return await self.get_by("email", email)

    async def get_by_login(self, login: str) -> User | None:
        """登录入口：用户名或邮箱都能登。"""
        result = await self.session.execute(
            select(User).where(or_(User.username == login, User.email == login))
        )
        return result.scalars().first()

    async def username_taken(self, username: str, *, exclude_id: int | None = None) -> bool:
        return await self.exists_by("username", username, exclude_id=exclude_id)

    async def email_taken(self, email: str, *, exclude_id: int | None = None) -> bool:
        return await self.exists_by("email", email, exclude_id=exclude_id)

    async def list_all(self) -> list[User]:
        result = await self.session.execute(select(User).order_by(User.id))
        return list(result.scalars().all())

    async def count_active_admins(self, *, exclude_id: int | None = None) -> int:
        """用于「不能删掉最后一个管理员」这类自锁保护。"""
        stmt = (
            select(func.count())
            .select_from(User)
            .where(User.role == UserRole.ADMIN, User.is_active.is_(True))
        )
        if exclude_id is not None:
            stmt = stmt.where(User.id != exclude_id)
        return int((await self.session.execute(stmt)).scalar_one())

    async def first_admin_email(self) -> str | None:
        """站长邮箱（新评论通知收件人）。多个站长时取 id 最小的那个。"""
        result = await self.session.execute(
            select(User.email)
            .where(User.role == UserRole.ADMIN, User.is_active.is_(True))
            .order_by(User.id)
            .limit(1)
        )
        row = result.first()
        return str(row[0]) if row else None
