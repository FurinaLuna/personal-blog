"""ORM 模型统一出口。

Alembic 的 autogenerate 依赖「所有模型都被 import 过」才能看到完整 metadata，
因此这里必须导出全部模型类，不要按需裁剪。
"""

from app.db.base import Base
from app.models.article import Article
from app.models.associations import article_tags
from app.models.attachment import Attachment
from app.models.comment import Comment
from app.models.enums import ArticleSort, ArticleStatus, UserRole
from app.models.series import Series
from app.models.site import SiteProfile
from app.models.taxonomy import Category, Tag
from app.models.user import User
from app.models.visit_log import VisitLog

__all__ = [
    "Article",
    "ArticleSort",
    "ArticleStatus",
    "Attachment",
    "Base",
    "Category",
    "Comment",
    "Series",
    "SiteProfile",
    "Tag",
    "User",
    "UserRole",
    "VisitLog",
    "article_tags",
]
