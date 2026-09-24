"""仓储层出口。

各 service 通过 ``from app.repositories import XxxRepository`` 使用，
统一出口方便将来替换实现（例如给高频只读查询加一层缓存仓储）。
"""

from app.repositories.article_query_repository import (
    ArticleFilter,
    ArticleListRow,
    ArticleQueryRepository,
    ArticleSorting,
)
from app.repositories.article_write_repository import ArticleWriteRepository
from app.repositories.attachment_repository import AttachmentRepository
from app.repositories.base import BaseRepository
from app.repositories.comment_repository import CommentRepository
from app.repositories.notification_opt_out_repository import NotificationOptOutRepository
from app.repositories.refresh_session_repository import RefreshSessionRepository
from app.repositories.revision_repository import ArticleRevisionRepository
from app.repositories.series_repository import SeriesRepository
from app.repositories.site_repository import SiteRepository
from app.repositories.taxonomy_repository import CategoryRepository, TagRepository
from app.repositories.user_repository import UserRepository
from app.repositories.visit_log_repository import VisitLogRepository

__all__ = [
    "ArticleFilter",
    "ArticleListRow",
    "ArticleQueryRepository",
    "ArticleRevisionRepository",
    "ArticleSorting",
    "ArticleWriteRepository",
    "AttachmentRepository",
    "BaseRepository",
    "CategoryRepository",
    "CommentRepository",
    "NotificationOptOutRepository",
    "RefreshSessionRepository",
    "SeriesRepository",
    "SiteRepository",
    "TagRepository",
    "UserRepository",
    "VisitLogRepository",
]
