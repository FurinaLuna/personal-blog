"""文章版本历史服务。

## 什么时候产生一版

这是本模块唯一需要判断的事，也是最容易做错的地方：

- **只在标题 / 摘要 / 正文真的变了时**才快照。改「是否置顶」「所属系列」
  这类元数据不产生版本——否则版本列表会被噪音淹没，真出事时反而找不到
  想看的那一版。
- 拿**当前库里的内容**与即将写入的内容比对，而不是看「请求里传了什么」：
  PATCH 带了 content_md 但内容与库里一致（编辑器整段重发）时不该产生版本。
- 快照存的是**改动前的样子**（旧值）。这样「恢复到某一版」= 把那一版的内容
  写回去，语义直观；如果存新值，用户会看到列表第一条永远等于当前内容，
  第一反应是「这不是没变吗」，反而不敢用。

## 恢复为什么也要产生一版

恢复本身是一次修改。如果不为它留痕，用户恢复错了就再也回不到恢复前——
而「恢复错了」恰恰是这个功能最可能被触发的场景。
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Article, ArticleRevision, User, UserRole
from app.repositories import ArticleRepository, ArticleRevisionRepository
from app.schemas.revision import RevisionAuthorBrief, RevisionRead
from app.utils.exceptions import NotFoundError, PermissionDeniedError

# 单篇文章保留的版本上限。不设上限的话，一篇长期维护的文章会无限累积——
# Markdown 单篇几 KB，一年几百次编辑就是几 MB，而「三个月前那一版」
# 几乎不会被用到。超限时从最旧的开始删。
MAX_REVISIONS_PER_ARTICLE = 100

# 版本来源。用短字符串而不是枚举：这张表是审计性质的，
# 将来加来源时不该再动一次数据库枚举定义。
REASON_SAVE = "save"
REASON_RESTORE = "restore"


class RevisionService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.revisions = ArticleRevisionRepository(session)
        self.articles = ArticleRepository(session)

    async def snapshot_if_content_changed(
        self,
        article: Article,
        *,
        new_title: str,
        new_summary: str | None,
        new_content_md: str,
        author: User | None,
    ) -> ArticleRevision | None:
        """内容真的变了才存一版（存**改动前**的旧值）。

        Returns:
            新建的版本；内容没变时返回 None。
        """
        unchanged = (
            article.title == new_title
            and (article.summary or None) == (new_summary or None)
            and article.content_md == new_content_md
        )
        if unchanged:
            return None

        return await self._create(
            article,
            title=article.title,
            summary=article.summary,
            content_md=article.content_md,
            author_id=author.id if author is not None else None,
            reason=REASON_SAVE,
        )

    async def list_for_article(self, article_id: int, *, viewer: User) -> list[RevisionRead]:
        """文章版本列表（**不含正文**，避免列表响应过大）。

        Raises:
            NotFoundError: 文章不存在。
            PermissionDeniedError: 不是作者本人也不是站长。
        """
        article = await self.articles.get(article_id)
        if article is None:
            raise NotFoundError("文章不存在")
        self._assert_can_read(article, viewer)

        rows = await self.revisions.list_for_article(article_id, limit=MAX_REVISIONS_PER_ARTICLE)
        return [self._to_read(row, include_content=False) for row in rows]

    async def get_revision(
        self, article_id: int, revision_id: int, *, viewer: User
    ) -> RevisionRead:
        """单个版本的完整内容（含正文）。"""
        article = await self.articles.get(article_id)
        if article is None:
            raise NotFoundError("文章不存在")
        self._assert_can_read(article, viewer)

        revision = await self.revisions.get(revision_id)
        # 必须校验归属：只查 revision_id 的话，换个数字就能读到别人文章的
        # 正文历史（IDOR）。历史版本可能包含作者已删掉的内容，属于草稿性质。
        if revision is None or revision.article_id != article_id:
            raise NotFoundError("版本不存在")
        return self._to_read(revision, include_content=True)

    async def restore(self, article_id: int, revision_id: int, *, viewer: User) -> Article:
        """把某一版的内容写回文章。

        恢复前先给**当前内容**存一版（reason=restore，并用 restored_from_id
        记下「这是要恢复到哪一版之前的样子」），这样恢复错了还能再退回来。

        Raises:
            NotFoundError: 文章或版本不存在。
            PermissionDeniedError: 不是作者本人也不是站长。
        """
        article = await self.articles.get(article_id)
        if article is None:
            raise NotFoundError("文章不存在")
        self._assert_can_read(article, viewer)

        target = await self.revisions.get(revision_id)
        if target is None or target.article_id != article_id:
            raise NotFoundError("版本不存在")

        # 先留退路
        await self._create(
            article,
            title=article.title,
            summary=article.summary,
            content_md=article.content_md,
            author_id=viewer.id,
            reason=REASON_RESTORE,
            restored_from_id=target.id,
        )

        article.title = target.title
        article.summary = target.summary
        article.content_md = target.content_md
        await self.session.flush()
        return article

    # ---------------------------------------------------------------- 内部

    async def _create(
        self,
        article: Article,
        *,
        title: str,
        summary: str | None,
        content_md: str,
        author_id: int | None,
        reason: str,
        restored_from_id: int | None = None,
    ) -> ArticleRevision:
        revision = await self.revisions.create(
            article_id=article.id,
            author_id=author_id,
            title=title,
            summary=summary,
            content_md=content_md,
            content_length=len(content_md or ""),
            reason=reason,
            restored_from_id=restored_from_id,
        )
        await self._trim(article.id)
        return revision

    async def _trim(self, article_id: int) -> None:
        """超过上限时删掉最旧的若干版。"""
        total = await self.revisions.count_for_article(article_id)
        overflow = total - MAX_REVISIONS_PER_ARTICLE
        if overflow <= 0:
            return
        # 按时间正序取前 N 条 = 删掉最老的那几条。
        # 不用 offset 分页：这里的语义就是「最老的 N 条」，
        # 不依赖总数在两次查询之间有没有变化。
        stmt = (
            select(ArticleRevision)
            .where(ArticleRevision.article_id == article_id)
            .order_by(ArticleRevision.created_at.asc(), ArticleRevision.id.asc())
            .limit(overflow)
        )
        result = await self.session.execute(stmt)
        for stale in result.scalars().all():
            await self.revisions.delete(stale)

    @staticmethod
    def _assert_can_read(article: Article, viewer: User) -> None:
        """版本历史只对作者本人和站长开放。

        与文章详情的口径**刻意不同**（那边访客能看已发布的）：这里读的是
        历史版本，可能包含作者删掉的内容，属于草稿性质，
        不能跟着「文章已发布」一起放行。
        """
        if viewer.role is UserRole.ADMIN or article.author_id == viewer.id:
            return
        raise PermissionDeniedError("只能查看自己文章的版本历史")

    @staticmethod
    def _to_read(revision: ArticleRevision, *, include_content: bool) -> RevisionRead:
        author = revision.author
        return RevisionRead(
            id=revision.id,
            article_id=revision.article_id,
            title=revision.title,
            summary=revision.summary,
            content_md=revision.content_md if include_content else None,
            content_length=revision.content_length,
            reason=revision.reason,
            restored_from_id=revision.restored_from_id,
            author=(
                RevisionAuthorBrief(
                    id=author.id,
                    username=author.username,
                    nickname=author.nickname,
                )
                if author is not None
                else None
            ),
            created_at=revision.created_at,
        )
