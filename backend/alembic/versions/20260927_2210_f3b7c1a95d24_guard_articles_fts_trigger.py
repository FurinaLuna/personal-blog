"""guard articles_fts_au trigger against non-content updates

Revision ID: f3b7c1a95d24
Revises: ec88a7148baa
Create Date: 2026-09-27 22:10:00.000000

## 为什么需要这个迁移

``articles_fts_au`` 是「UPDATE 后同步全文索引」的触发器，原定义**没有 WHEN 守卫**，
于是 ``articles`` 上任何 UPDATE 都会触发「delete + insert」一对 FTS 写操作。
而详情页每次访问都会

    UPDATE articles SET view_count = view_count + 1 WHERE id = ?

（见 ``repositories/article_write_repository.py`` 的 ``increment_view``，点赞同理）。
结果是**读一次文章就重新分词一次整篇正文**，实测（30 次迭代、内存库）
2KB 正文 0.11ms、30KB 1.42ms、100KB 4.73ms —— 与正文长度线性相关，
且每次浏览都多一次索引写。

## 为什么不能只改代码里的 DDL（这是本次真正的坑）

三条触发器都用 ``CREATE TRIGGER IF NOT EXISTS`` 建（``db/fulltext.py`` 与上一个
FTS 迁移都是），而 ``IF NOT EXISTS`` **不会更新已存在的触发器**。
所以只改 ``CREATE_TRIGGER_SQL`` 的后果是：

- 新装的库拿到带守卫的新定义；
- 已经建过触发器的库（也就是所有现存部署）**永远留着旧定义**，
  没有任何报错、没有任何日志 —— 两种库行为不同。

这类「改了却对存量库不生效」的 DDL 漂移只能靠迁移修，因此这里显式
``DROP TRIGGER`` + ``CREATE TRIGGER`` 重建，并且是幂等的（重跑无害）。
同时 ``db/fulltext.py`` 的启动钩子也加了「定义漂移就重建」的自愈逻辑
（``_refresh_update_trigger``），两条路径都覆盖：跑迁移的部署和
「自动建表不跑迁移」的开发库。

守卫的写法有两个坑，都写在 ``db/fulltext.py`` 的注释里：
触发器体里不能写括号条件（只能挂 WHEN）；且必须用 ``coalesce(...) IS NOT
coalesce(...)`` 而不是 ``new.x IS NOT old.x`` —— ``summary`` 是可空列，
一旦旧值是 NULL，``IS NOT`` 得到 NULL，整个 AND 短路，触发器就不跑了，
而「summary 从 NULL 改成有值」恰恰是需要重建索引的情况。

## 方言边界

只在 SQLite 上做（FTS5 是 SQLite 专有；PostgreSQL 走 pg_trgm，没有这组触发器）。

## 回滚

``downgrade`` 把触发器还原成无守卫的旧定义。**注意**：回滚后写放大问题会回来，
所以只在必须回到旧行为时才用。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f3b7c1a95d24"
down_revision: str | None = "ec88a7148baa"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TRIGGER = "articles_fts_au"

# 与 ``app.db.fulltext.CREATE_TRIGGER_SQL`` 里那条 *_au 保持一致。
# 刻意在这里写一份字面量而不是 import 应用代码：迁移必须能在任意历史版本的
# 代码上重放，import 应用常量会让「以后改了应用代码」反过来改掉历史迁移的行为。
_GUARDED_SQL = """
CREATE TRIGGER articles_fts_au AFTER UPDATE ON articles
WHEN coalesce(new.title, '') IS NOT coalesce(old.title, '')
  OR coalesce(new.summary, '') IS NOT coalesce(old.summary, '')
  OR coalesce(new.content_md, '') IS NOT coalesce(old.content_md, '')
BEGIN
    INSERT INTO articles_fts(articles_fts, rowid, title, summary, content_md)
    VALUES ('delete', old.id, old.title, old.summary, old.content_md);
    INSERT INTO articles_fts(rowid, title, summary, content_md)
    VALUES (new.id, new.title, new.summary, new.content_md);
END
"""

# 迁移前的旧定义（无守卫），用于 downgrade
_LEGACY_SQL = """
CREATE TRIGGER articles_fts_au AFTER UPDATE ON articles BEGIN
    INSERT INTO articles_fts(articles_fts, rowid, title, summary, content_md)
    VALUES ('delete', old.id, old.title, old.summary, old.content_md);
    INSERT INTO articles_fts(rowid, title, summary, content_md)
    VALUES (new.id, new.title, new.summary, new.content_md);
END
"""


def _is_sqlite() -> bool:
    return op.get_bind().dialect.name == "sqlite"


def _has_fts_table() -> bool:
    return sa.inspect(op.get_bind()).has_table("articles_fts")


def upgrade() -> None:
    if not _is_sqlite():
        return
    # 没建过 FTS 表的库（例如从没跑过搜索索引那条迁移）不需要也不该建触发器：
    # 触发器引用的 articles_fts 不存在的话，CREATE TRIGGER 会直接失败。
    if not _has_fts_table():
        return

    op.execute(f"DROP TRIGGER IF EXISTS {_TRIGGER}")
    op.execute(_GUARDED_SQL)


def downgrade() -> None:
    if not _is_sqlite():
        return
    if not _has_fts_table():
        return

    op.execute(f"DROP TRIGGER IF EXISTS {_TRIGGER}")
    op.execute(_LEGACY_SQL)
