"""文章写仓储：计数自增与标签集合替换。

读写拆分自 ``article_repository.py``：这里只放会写库的方法，方法体逐字搬移。
新增 / 修改 / 删除走 ``BaseRepository`` 的通用 CRUD（调用方是
``ArticleCommandService``）；只读查询见 ``article_query_repository``。
"""

from __future__ import annotations

from sqlalchemy import update
from sqlalchemy.orm.attributes import set_committed_value

from app.models import Article, Tag
from app.repositories.base import BaseRepository


class ArticleWriteRepository(BaseRepository[Article]):
    model = Article

    # ------------------------------------------------------------------ 写入

    async def increment_view(self, article: Article) -> int:
        """原子自增阅读量，并把新值同步回会话里的对象。

        绝不用「读出来 +1 再存回去」——并发下会丢计数。这里交给数据库做
        ``SET view_count = view_count + 1``，一条语句搞定。

        两个必须加的执行选项：

        - ``synchronize_session=False``：默认的自动同步策略在遇到
          ``view_count + 1`` 这种数据库表达式时无法求值，会把这行对象上的
          ``updated_at``（带 onupdate）标记为过期。异步会话里再去访问它就会抛
          ``MissingGreenlet``。
        - 配合 ``set_committed_value``：把新值直接告诉 ORM 并标记为「已提交」，
          这样响应里能拿到新计数，也不会反过来生成一条多余的 UPDATE。

        ``updated_at`` 必须显式钉住（``updated_at=Article.updated_at``）：
        列上的 ``onupdate`` 对 Core ``update()`` 同样生效，不钉住的话**读一次文章
        就会改一次 updated_at**。后果是后台默认排序 ``sort=updated`` 变成
        「最近被看过的」，热门旧文会压过刚编辑过的；同时 sitemap 的 ``<lastmod>``
        每次访问都变，等于告诉爬虫全站天天在改。计数变化不是内容修改。

        Returns:
            自增后的阅读量。
        """
        await self.session.execute(
            update(Article)
            .where(Article.id == article.id)
            .values(view_count=Article.view_count + 1, updated_at=Article.updated_at)
            .execution_options(synchronize_session=False)
        )
        new_value = (article.view_count or 0) + 1
        set_committed_value(article, "view_count", new_value)
        return new_value

    async def increment_like(self, article: Article) -> int:
        """原子自增点赞数，说明同 ``increment_view``（含 updated_at 必须钉住的理由）。"""
        await self.session.execute(
            update(Article)
            .where(Article.id == article.id)
            .values(like_count=Article.like_count + 1, updated_at=Article.updated_at)
            .execution_options(synchronize_session=False)
        )
        new_value = (article.like_count or 0) + 1
        set_committed_value(article, "like_count", new_value)
        return new_value

    async def set_tags(self, article: Article, tags: list[Tag]) -> None:
        """整体替换文章的标签集合（先清后加，幂等）。

        必须先 ``refresh`` 把现有集合真正加载进来，再赋值。原因是：
        直接写 ``article.tags = tags`` 时，SQLAlchemy 为了算出「增删了哪些」
        会去**惰性加载**原有的 collection —— 而异步会话里的惰性加载会在事件
        循环里发起同步 IO，直接抛 ``MissingGreenlet``。

        这个坑只在真正跑起请求时才会暴露，静态检查完全看不出来。
        """
        await self.session.refresh(article, ["tags"])
        article.tags = list(tags)
        await self.session.flush()
