"""刷新令牌会话仓储。

集中放"会话生命周期"的查询，让 service 只表达业务意图
（轮换 / 复用检测 / 单会话吊销 / 清理），不写 SQL。
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import RefreshSession
from app.repositories.base import BaseRepository


def hash_jti(jti: str) -> str:
    """jti → 落库用的哈希。

    jti 本身是 32 位随机 hex（128 bit 熵），不需要加盐：
    它的威胁模型是"数据库被拖走后能否据此伪造令牌"，而哈希单向即可。
    与"密码要加盐"的区别在于密码是低熵的人类输入。
    """
    return hashlib.sha256(jti.encode("utf-8")).hexdigest()


class RefreshSessionRepository(BaseRepository[RefreshSession]):
    """刷新令牌会话。"""

    model = RefreshSession

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session)

    async def get_by_jti(self, jti: str) -> RefreshSession | None:
        result = await self.session.execute(
            select(RefreshSession).where(RefreshSession.jti_hash == hash_jti(jti))
        )
        return result.scalar_one_or_none()

    async def open_session(self, *, user_id: int, jti: str, expires_at: datetime) -> RefreshSession:
        """登记一枚新签发的 refresh token。"""
        return await self.create(user_id=user_id, jti_hash=hash_jti(jti), expires_at=expires_at)

    async def mark_rotated(self, session_row: RefreshSession, *, replaced_by_jti: str) -> None:
        """把旧的标记为"已轮换"，并记下它换成了哪一枚（排障用）。"""
        session_row.rotated_at = datetime.now(UTC)
        session_row.replaced_by = hash_jti(replaced_by_jti)
        await self.session.flush()

    async def revoke_user_sessions(self, user_id: int) -> int:
        """吊销该用户的**全部**会话（登出 / 改密码 / 检测到复用）。

        Returns:
            受影响的会话数。
        """
        result = await self.session.execute(
            update(RefreshSession)
            .where(RefreshSession.user_id == user_id, RefreshSession.revoked_at.is_(None))
            .values(revoked_at=datetime.now(UTC))
        )
        return int(result.rowcount or 0)

    async def prune_expired(self, *, before: datetime) -> int:
        """删掉早就过期的会话行。

        与访问日志同理：这张表每次登录/刷新都会写一行，只增不减的话
        备份体积与迁移耗时都会慢慢变难看，而收益是零。
        """
        result = await self.session.execute(
            delete(RefreshSession).where(RefreshSession.expires_at < before)
        )
        return int(result.rowcount or 0)
