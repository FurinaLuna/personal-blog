"""仓储层出口。

各 service 通过 ``from app.repositories import XxxRepository`` 使用，
统一出口方便将来替换实现（例如给高频只读查询加一层缓存仓储）。
"""

from app.repositories.article_repository import (
    ArticleFilter,
    ArticleListRow,
    ArticleRepository,
    ArticleSorting,
)
from app.repositories.attachment_repository import AttachmentRepository
from app.repositories.base import BaseRepository
from app.repositories.comment_repository import CommentRepository
from app.repositories.notification_opt_out_repository import NotificationOptOutRepository
from app.repositories.series_repository import SeriesRepository
from app.repositories.site_repository import SiteRepository
from app.repositories.taxonomy_repository import CategoryRepository, TagRepository
from app.repositories.user_repository import UserRepository
from app.repositories.visit_log_repository import VisitLogRepository

__all__ = [
    "ArticleFilter",
    "ArticleListRow",
    "ArticleRepository",
    "ArticleSorting",
    "AttachmentRepository",
    "BaseRepository",
    "CategoryRepository",
    "CommentRepository",
    "NotificationOptOutRepository",
    "SeriesRepository",
    "SiteRepository",
    "TagRepository",
    "UserRepository",
    "VisitLogRepository",
]
