"""RSS 订阅与站点地图生成。

都放在后端生成而不是前端静态文件，原因有两个：
1. 内容是动态的——发文/改文后订阅源必须立即反映，静态文件需要额外的构建钩子；
2. 这两个端点是给机器（阅读器 / 搜索引擎）看的，不参与前端路由，
   由后端直出还能拿到正确的 Content-Type 与缓存头。
"""

from __future__ import annotations

from email.utils import format_datetime
from xml.etree.ElementTree import Element, SubElement, tostring
from xml.sax.saxutils import escape

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import Article, ArticleSort, ArticleStatus, SiteProfile
from app.repositories import ArticleFilter, ArticleQueryRepository, ArticleSorting, SiteRepository
from app.utils.text import strip_markdown

# RSS 只取最近 N 篇：全量输出会让 feed 随文章数无限膨胀，
# 主流阅读器本来也只关心增量。
FEED_ITEM_LIMIT = 20
# sitemap 有 5 万条上限的说法，但个人博客量级下取最近 1000 篇绰绰有余；
# 真正需要分页 sitemap 的网站不会是这个体量。
SITEMAP_ITEM_LIMIT = 1000

_PUBLISHED_ONLY: tuple[ArticleStatus, ...] = (ArticleStatus.PUBLISHED,)


def _article_url(article: Article) -> str:
    """拼文章链接。

    路径模板来自 ``SITE_ARTICLE_PATH``（默认 ``/article/{slug}``），
    而不是写死在前端或这里——后端不该硬编码前端的路由形状。
    ``tests/test_feed.py`` 里有一条用例会拿它与前端路由表比对。
    """
    return f"{settings.site_base_url}{settings.site_article_path.format(slug=article.slug)}"


def _iso_date(article: Article) -> str:
    moment = article.updated_at or article.published_at or article.created_at
    return moment.date().isoformat()


class FeedService:
    """生成 RSS 2.0 与 sitemap.xml 的文本。"""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.articles = ArticleQueryRepository(session)
        self.sites = SiteRepository(session)

    async def _latest(self, limit: int) -> list[Article]:
        rows = await self.articles.list_paged(
            flt=ArticleFilter(statuses=_PUBLISHED_ONLY),
            sorting=ArticleSorting(sort=ArticleSort.LATEST, top_first=False),
            offset=0,
            limit=limit,
        )
        return [row.article for row in rows]

    async def _profile(self) -> SiteProfile | None:
        return await self.sites.get_profile()

    async def rss(self) -> str:
        """RSS 2.0 订阅源。"""
        profile = await self._profile()
        owner = profile.owner_name if profile else "站长"
        description = (
            profile.headline if profile else None
        ) or "记录技术、生活，以及一切值得写下来的东西。"
        base = settings.site_base_url

        channel_items: list[Element] = []
        for article in await self._latest(FEED_ITEM_LIMIT):
            item = Element("item")
            SubElement(item, "title").text = article.title
            link = _article_url(article)
            SubElement(item, "link").text = link
            # guid 用链接即可；permalink 语义下阅读器能正确去重
            SubElement(item, "guid", attrib={"isPermaLink": "true"}).text = link
            published = article.published_at or article.created_at
            SubElement(item, "pubDate").text = format_datetime(published)
            summary = article.summary or strip_markdown(article.content_md, 200)
            if summary:
                SubElement(item, "description").text = summary
            for tag in article.tags:
                SubElement(item, "category").text = tag.name
            channel_items.append(item)

        rss = Element("rss", attrib={"version": "2.0"})
        channel = SubElement(rss, "channel")
        SubElement(channel, "title").text = f"{owner}的博客"
        SubElement(channel, "link").text = base
        SubElement(channel, "description").text = description
        SubElement(channel, "language").text = "zh-CN"
        for item in channel_items:
            channel.append(item)

        return '<?xml version="1.0" encoding="UTF-8"?>\n' + tostring(rss, encoding="unicode")

    async def sitemap(self) -> str:
        """XML 站点地图（仅公开页面 + 已发布文章）。"""
        base = settings.site_base_url
        urls: list[tuple[str, str | None, str]] = [
            (f"{base}/", None, "daily"),
            (f"{base}/tags", None, "weekly"),
            (f"{base}/categories", None, "weekly"),
            (f"{base}/archive", None, "weekly"),
            # /series 与 /links 都有真实内容且可被爬虫独立访问：
            # 前者是系列聚合页，后者是友链页（后台录入后才会出现在这里，
            # 但"可能为空"不是不收录的理由——空页面自己会显示空态）
            (f"{base}/series", None, "weekly"),
            (f"{base}/links", None, "monthly"),
            # 留言板同理：内容由访客产生，是真实页面而不是空壳
            # （changefreq 给 weekly：留言不像文章那样天天更新）
            (f"{base}/guestbook", None, "weekly"),
            (f"{base}/about", None, "monthly"),
        ]
        for article in await self._latest(SITEMAP_ITEM_LIMIT):
            urls.append((_article_url(article), _iso_date(article), "weekly"))

        lines = [
            '<?xml version="1.0" encoding="UTF-8"?>',
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">',
        ]
        for loc, lastmod, changefreq in urls:
            lines.append("  <url>")
            lines.append(f"    <loc>{escape(loc)}</loc>")
            if lastmod:
                lines.append(f"    <lastmod>{lastmod}</lastmod>")
            lines.append(f"    <changefreq>{changefreq}</changefreq>")
            lines.append("  </url>")
        lines.append("</urlset>")
        return "\n".join(lines)
