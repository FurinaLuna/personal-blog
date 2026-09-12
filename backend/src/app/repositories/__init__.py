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
from app.repositories.series_repository import SeriesRepository
from app.repositories.site_repository import SiteRepository
from app.repositories.taxonomy_repository import CategoryRepository, TagRepository
from app.repositories.user_repository import UserRepository

__all__ = [
    "ArticleFilter",
    "ArticleListRow",
    "ArticleRepository",
    "ArticleSorting",
    "AttachmentRepository",
    "BaseRepository",
    "CategoryRepository",
    "CommentRepository",
    "SeriesRepository",
    "SiteRepository",
    "TagRepository",
    "UserRepository",
]
