"""服务层出口。

服务层是「业务规则唯一所在地」：路由只做参数解析与调用，仓储只做数据存取。
每个 Service 都接收一个 ``AsyncSession``，因此可以在一个请求里自由组合多个服务，
共享同一个事务。
"""

from app.services.article_service import ArticleService
from app.services.attachment_service import AttachmentService
from app.services.auth_service import AuthService
from app.services.comment_service import CommentService
from app.services.feed_service import FeedService
from app.services.series_service import SeriesService
from app.services.site_service import SiteService
from app.services.taxonomy_service import TaxonomyService
from app.services.visit_stats_service import VisitStatsService

__all__ = [
    "ArticleService",
    "AttachmentService",
    "AuthService",
    "CommentService",
    "FeedService",
    "SeriesService",
    "SiteService",
    "TaxonomyService",
    "VisitStatsService",
]
