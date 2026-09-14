"""通知退订仓储。"""

from __future__ import annotations

from app.models import NotificationOptOut
from app.repositories.base import BaseRepository


class NotificationOptOutRepository(BaseRepository[NotificationOptOut]):
    """只用到基类的 ``exists_by`` / ``create``，无领域特化查询。"""

    model = NotificationOptOut
