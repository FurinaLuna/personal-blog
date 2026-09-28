"""文章模型。"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    event,
    text,
)
from sqlalchemy import (
    Enum as SQLEnum,
)
from sqlalchemy import (
    inspect as sa_inspect,
)
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Mapped, Mapper, mapped_column, relationship

from app.db.base import Base, TimestampMixin
from app.db.types import UTCDateTime
from app.models.associations import article_tags
from app.models.enums import ArticleStatus, enum_values

if TYPE_CHECKING:
    from app.models.comment import Comment
    from app.models.revision import ArticleRevision
    from app.models.series import Series
    from app.models.taxonomy import Category, Tag
    from app.models.user import User


class Article(Base, TimestampMixin):
    """博客文章。

    正文以 Markdown 原文存储（``content_md``），渲染交给前端（marked + DOMPurify），
    这样后端保持纯粹的「内容源」角色，将来换渲染器不用动数据库。
    """

    __tablename__ = "articles"
    __table_args__ = (
        # 列表页最常见的组合条件：已发布 + 按发布时间倒序
        Index("ix_articles_status_published_at", "status", "published_at"),
        Index("ix_articles_is_top_published_at", "is_top", "published_at"),
        # 按月归档（`list_archive` / `list_by_month`）走 `published_ym`。
        # 前导列 status 是这两条查询里恒定出现的等值条件，第二列才是月份；
        # 反过来的话 `status IN (...)` 就用不上索引了。
        Index("ix_articles_status_published_ym", "status", "published_ym"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    slug: Mapped[str] = mapped_column(String(220), unique=True, index=True, nullable=False)
    summary: Mapped[str | None] = mapped_column(String(500))
    content_md: Mapped[str] = mapped_column(Text, default="", nullable=False)
    cover_image: Mapped[str | None] = mapped_column(String(500))

    status: Mapped[ArticleStatus] = mapped_column(
        SQLEnum(ArticleStatus, native_enum=False, length=20, values_callable=enum_values),
        default=ArticleStatus.DRAFT,
        nullable=False,
        index=True,
    )
    is_top: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    allow_comment: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    view_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    like_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    reading_time: Mapped[int] = mapped_column(
        Integer, default=1, nullable=False, comment="预估阅读时长（分钟）"
    )
    published_at: Mapped[datetime | None] = mapped_column(UTCDateTime, index=True)
    # 「有效发布时间的 YYYY-MM」，materialised 出来的归档键。
    #
    # 为什么要有这一列：归档谓词原来是 ``substr(cast(coalesce(published_at,
    # created_at), text), 1, 7) == 'YYYY-MM'`` ——**列的函数**，任何索引都服务不了，
    # 于是每个月份一次全表扫描、且必须回表取整行。换成裸列比较之后
    # ``(status, published_ym)`` 这条索引才用得上（见 tests/test_indexes.py 的
    # 查询计划断言）。
    #
    # 可空：DDL 上只是「加一列 + 回填」，SQLite 也加不了 NOT NULL 约束；
    # 语义上 NULL 表示「这个对象还没有任何可用的时间」（未 flush 的瞬时状态），
    # 落库的行总是有值（created_at 非空）。写入侧的维护逻辑见文件末尾的两个事件。
    published_ym: Mapped[str | None] = mapped_column(
        String(7), comment="有效发布时间的 YYYY-MM（归档按月筛选用，由 ORM 事件维护）"
    )

    author_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    category_id: Mapped[int | None] = mapped_column(
        ForeignKey("categories.id", ondelete="SET NULL"), index=True
    )
    # 系列（可选，一对多）：系列内顺序由 series_order 决定，
    # 详情页系列导航与 prev/next 优先用它
    series_id: Mapped[int | None] = mapped_column(
        ForeignKey("series.id", ondelete="SET NULL"), index=True
    )
    # server_default 不能省：Alembic 迁移（add_series_collection_to_articles）建出来
    # 的这一列带 DEFAULT 0，而只写 ORM 的 ``default=0`` 时 create_all 建出的列没有
    # 默认值——同一份模型两条建表路径结构不一致，任何直接往表里 INSERT 的脚本
    # （不含该列的 INSERT）在 create_all 库上都会 NOT NULL 失败。
    # SQLite 的 ALTER COLUMN 改不了默认值，所以不为此新增迁移，只在模型侧对齐。
    series_order: Mapped[int] = mapped_column(
        Integer, default=0, server_default=text("0"), nullable=False
    )

    author: Mapped[User] = relationship(back_populates="articles", lazy="joined")
    category: Mapped[Category | None] = relationship(back_populates="articles", lazy="joined")
    series: Mapped[Series | None] = relationship(back_populates="articles", lazy="joined")
    tags: Mapped[list[Tag]] = relationship(
        secondary=article_tags, back_populates="articles", lazy="selectin"
    )
    comments: Mapped[list[Comment]] = relationship(
        back_populates="article", cascade="all, delete-orphan", passive_deletes=True
    )
    # 版本历史。刻意**不设 lazy="selectin"**：文章详情/列表根本用不到版本，
    # 预加载它等于每次读文章都多查一次全部历史——列表页会直接被打爆。
    # 需要时由版本仓储显式查询。
    revisions: Mapped[list[ArticleRevision]] = relationship(
        back_populates="article", cascade="all, delete-orphan", passive_deletes=True
    )

    @property
    def is_public(self) -> bool:
        """访客是否可见（草稿只对作者/管理员可见）。"""
        return self.status is not ArticleStatus.DRAFT

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Article id={self.id} slug={self.slug!r} status={self.status.value}>"


# --------------------------------------------------------------- published_ym 维护
#
# 这一列是**派生值**，必须在每条写路径上跟着 published_at / created_at 走。
# 做法选在**模型层的 ORM 事件**而不是服务层，原因是写入点分散且不在同一层：
# ``ArticleCommandService.create`` / ``update``（新建、改标题、改发布时间、发布草稿、
# 定时发布）与 ``services/seed.py`` 的演示数据都直接给 ORM 对象赋值，模型事件能
# 一次全覆盖；写进服务层则每加一个写入口都要记得补一行，漏了不会报错——只会让
# 文章从归档页上静静消失。
#
# 已知边界（写在代码里，免得以后被当成 bug 排查）：
#
# - **裸 SQL 写入**（诸如 tests/test_fts_trigger_guard.py 的探针 INSERT、
#   以及运维手工 UPDATE）绕过 ORM 事件，需要自己带上这一列；
# - Core 层的 ``update(Article).values(view_count=...)``（浏览/点赞自增）**不应**
#   触发重算，也确实不会：它不经过 mapper 事件，而月份本来也只由
#   published_at / created_at 决定。

# 决定 published_ym 的两个输入。status 不在其中——旧的 substr 谓词同样不看状态，
# 归档查询自己按状态过滤，多一个输入只会让「什么时候该重算」变复杂。
_YM_SOURCE_FIELDS = ("published_at", "created_at")


def derive_published_ym(published_at: datetime | None, created_at: datetime | None) -> str | None:
    """有效发布时间的 ``YYYY-MM``。

    与旧的归档谓词 ``substr(cast(coalesce(published_at, created_at), text), 1, 7)``、
    以及与迁移里的回填语句必须是**同一个规则**：已发布用 ``published_at``
    （含未来的定时发布时间），草稿回退到 ``created_at``。
    """
    moment = published_at or created_at
    return moment.strftime("%Y-%m") if moment is not None else None


@event.listens_for(Article, "before_insert")
def _fill_published_ym(mapper: Mapper[Article], connection: Connection, target: Article) -> None:
    """INSERT 前落定 ``published_ym``。

    ``before_insert`` **早于** SQLAlchemy 求值列的 Python 侧默认值
    （``orm/persistence.py`` 的 ``_organize_states_for_save`` 先派发事件，
    之后才 ``_collect_insert_commands``），所以此刻 ``target.created_at`` 还是空的。
    草稿（``published_at`` 为空）的月份只能来自 ``created_at``，因此这里按
    ``TimestampMixin`` 的同一个表达式先把它落定 —— 否则草稿的 ``published_ym``
    会算成 NULL，而迁移回填出来的老草稿却有值，两条路径直接漂移。

    好处是两列由**同一次** ``datetime.now(UTC)`` 算出，不会出现「跨月零点插入时
    published_ym 与 created_at 差一个月」这种一年才可能撞上一次、撞上了也查不出的错。
    """
    if target.created_at is None:
        target.created_at = datetime.now(UTC)
    target.published_ym = derive_published_ym(target.published_at, target.created_at)


@event.listens_for(Article, "before_update")
def _refresh_published_ym(mapper: Mapper[Article], connection: Connection, target: Article) -> None:
    """UPDATE 前重算 ``published_ym``——只在这两列**真的变了**时才写。

    不加这层判断的代价不是正确性而是噪声：任何 ORM UPDATE（改标题、改置顶、
    改状态）都会把同一个值再 SET 一次。判断条件与列的定义域严格对齐，
    所以「跳过」永远是安全的。
    """
    state = sa_inspect(target)
    if any(state.attrs[field].history.has_changes() for field in _YM_SOURCE_FIELDS):
        target.published_ym = derive_published_ym(target.published_at, target.created_at)
