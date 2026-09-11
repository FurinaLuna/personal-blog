"""初始化数据：站长账号、站点档案、演示内容。

幂等：重复启动不会重复插入。演示内容只在「一篇文章都没有」时写入，
所以站长把自己写的文章删光后会重新出现演示数据——如果不想要，
把 ``SEED_DEMO_DATA`` 设为 false 即可。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import Article, ArticleStatus, Category, SiteProfile, Tag, User, UserRole
from app.utils.security import hash_password
from app.utils.text import estimate_reading_time, slugify, strip_markdown

DEMO_CATEGORIES: list[tuple[str, str, str, int]] = [
    ("技术笔记", "tech", "踩过的坑、读过的源码、验证过的方案", 1),
    ("生活随笔", "life", "记录生活里那些值得写下来的瞬间", 2),
    ("读书观影", "review", "书、影、音的长短评与摘抄", 3),
]

DEMO_TAGS: list[str] = ["Python", "FastAPI", "Vue", "SQL", "折腾记录", "效率工具", "随笔"]

DEMO_ARTICLES: list[dict[str, object]] = [
    {
        "title": "为什么我把博客的数据层重写了一遍",
        "category": "tech",
        "tags": ["Python", "SQL", "折腾记录"],
        "is_top": True,
        "content": """## 起因

老博客用的是同步 ORM，接口里到处都是 `session.query(...)`。单机跑没问题，
但只要并发一上来，连接池就会被同步 IO 卡住。

## 三个决定

### 1. 全链路异步

```python
async with async_session_factory() as session:
    result = await session.execute(select(Article).where(Article.slug == slug))
    article = result.scalars().first()
```

### 2. 仓储层只 flush，不 commit

事务边界统一收口在 FastAPI 依赖里，一个请求一个事务：

```python
async def get_session():
    async with async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
```

这样写的好处是：**不会再出现「文章存进去了但标签没存进去」的半成品数据**。

### 3. 列表接口绝不带正文

列表一次返回 20 条，每条如果都带上 `content_md`，响应体轻松上兆。
所以 `ArticleSummary` 里刻意没有正文字段。

## 效果

| 指标 | 改造前 | 改造后 |
| --- | --- | --- |
| 列表接口 P95 | 380ms | 46ms |
| 单页响应体 | 1.8MB | 62KB |

> 结论：性能问题往往不是「数据库慢」，而是「你查了不该查的字段」。
""",
    },
    {
        "title": "SQLite 外键约束默认是关的，这个坑我踩了两次",
        "category": "tech",
        "tags": ["SQL", "Python", "折腾记录"],
        "content": """SQLite 有个反直觉的默认值：**`PRAGMA foreign_keys` 默认是 OFF**。

这意味着你写了 `ON DELETE CASCADE`，数据库根本不会执行。删掉一篇文章，
评论会全部变成指向不存在文章的孤儿行。

## 正确做法

在每个连接建立时打开它：

```python
@event.listens_for(engine.sync_engine, "connect")
def _set_pragmas(dbapi_connection, _):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.close()
```

注意必须在 `connect` 事件里做，而不是启动时执行一次——连接池里的连接是复用的。

## 顺便说说 WAL

`journal_mode=WAL` 让读和写不再互相阻塞，对「读多写少」的博客场景收益很明显。
""",
    },
    {
        "title": "把博客当笔记本用之后，我改掉的四个习惯",
        "category": "life",
        "tags": ["随笔", "效率工具"],
        "content": """写博客这件事，坚持下来靠的不是毅力，而是**降低摩擦**。

## 一、不再追求「成体系」

以前总想写「XX 从入门到精通」，结果大纲列完就没了下文。
现在改成：今天解决了一个什么问题，就写这一个问题。

## 二、先写自己的话，再补代码

代码是证据，不是文章本身。先讲清楚「为什么要做这件事」，
代码只需要能跑通的最小片段。

## 三、随手建标签，不纠结分类

分类是骨架，标签是网络。一篇文章只能有一个分类，但可以有五个标签。
**分类用来导航，标签用来发现。**

## 四、允许自己写短

两百字的记录也是记录。发表按钮按下去，比存在草稿箱里第一百次改标题强。
""",
    },
    {
        "title": "《置身事内》读后：理解一件事的「钱从哪来」",
        "category": "review",
        "tags": ["随笔"],
        "content": """兰小欢这本书最厉害的地方，是它不急着评价对错，而是先把**机制**讲清楚。

## 一个让我印象很深的角度

看懂一个地方政府的决策，先问三个问题：

1. 这件事的钱从哪来？
2. 谁承担成本，谁获得收益？
3. 这个激励结构是短期的还是长期的？

这套问法用在读新闻、看政策、甚至理解公司内部决策上，都异常好用。

## 摘抄

> 现实世界中，没有完美的制度，只有在特定约束下相对可行的制度。

读完最大的收获不是知道了什么结论，而是多了一把可以反复用的尺子。
""",
    },
]


async def seed_admin(session: AsyncSession) -> User:
    """保证站长账号存在（幂等）。"""
    from sqlalchemy import select

    result = await session.execute(select(User).where(User.username == settings.admin_username))
    admin = result.scalars().first()
    if admin is not None:
        return admin

    admin = User(
        username=settings.admin_username,
        email=settings.admin_email,
        hashed_password=hash_password(settings.admin_password),
        nickname="站长",
        role=UserRole.ADMIN,
        bio="在这里记录技术、生活，以及一切值得写下来的东西。",
        is_active=True,
    )
    session.add(admin)
    await session.flush()
    return admin


async def seed_site_profile(session: AsyncSession) -> SiteProfile:
    """保证站点档案存在（幂等）。"""
    from sqlalchemy import select

    result = await session.execute(select(SiteProfile).where(SiteProfile.id == 1))
    profile = result.scalars().first()
    if profile is not None:
        return profile

    profile = SiteProfile(
        id=1,
        owner_name="站长",
        headline="记录技术、生活，以及一切值得写下来的东西",
        bio_md="这里是我的数字花园。不追求体系完整，只求每篇都解决一个真实问题。",
        about_md="""## 关于我

一个喜欢把事情做到底的开发者。

## 关于这个博客

这里什么都会记一点——踩过的坑、读过的书、想明白的道理。
写作对我来说首先是**整理思路的工具**，其次才是分享。

## 技术栈

本站是前后端分离架构：

- **后端**：FastAPI + SQLAlchemy 2.0（异步）+ SQLite / PostgreSQL
- **前端**：Vue 3 + TypeScript + Vite + Pinia
- **渲染**：Markdown 原文存储，前端用 marked 渲染 + DOMPurify 消毒

## 联系方式

欢迎通过评论或邮件交流。
""",
        email=settings.admin_email,
        location="中国",
        social_links=[
            {"label": "GitHub", "url": "https://github.com/", "icon": "github"},
            {"label": "RSS", "url": "/api/v1/articles", "icon": "rss"},
        ],
        skills=["Python", "FastAPI", "Vue", "PostgreSQL", "Docker"],
        comment_need_approval=True,
        allow_guest_comment=True,
    )
    session.add(profile)
    await session.flush()
    return profile


async def seed_demo_content(session: AsyncSession, admin: User) -> None:
    """写入演示文章。已有文章时直接跳过。"""
    from sqlalchemy import func, select

    existing = await session.execute(select(func.count()).select_from(Article))
    if int(existing.scalar_one()) > 0:
        return

    # 分类
    category_map: dict[str, Category] = {}
    for name, slug, description, order in DEMO_CATEGORIES:
        category_map[slug] = Category(
            name=name, slug=slug, description=description, sort_order=order
        )
        session.add(category_map[slug])

    # 标签
    tag_map: dict[str, Tag] = {}
    for name in DEMO_TAGS:
        tag_map[name] = Tag(name=name, slug=slugify(name, fallback_prefix="tag"))
        session.add(tag_map[name])

    await session.flush()

    now = datetime.now(UTC)
    for index, item in enumerate(DEMO_ARTICLES):
        content = str(item["content"])
        title = str(item["title"])
        article = Article(
            title=title,
            slug=slugify(title, fallback_prefix="article"),
            summary=strip_markdown(content, 160),
            content_md=content,
            status=ArticleStatus.PUBLISHED,
            is_top=bool(item.get("is_top", False)),
            allow_comment=True,
            view_count=(index + 1) * 37,
            like_count=(index + 1) * 5,
            reading_time=estimate_reading_time(content),
            # 让演示数据有先后顺序，方便验证「上一篇/下一篇」和归档
            published_at=now - timedelta(days=index * 7, hours=index),
            author_id=admin.id,
            category_id=category_map[str(item["category"])].id,
            tags=[tag_map[name] for name in item["tags"]],  # type: ignore[index]
        )
        session.add(article)

    await session.flush()


async def ensure_seed(session: AsyncSession) -> None:
    """统一入口：启动时调用一次即可。"""
    admin = await seed_admin(session)
    await seed_site_profile(session)
    if settings.seed_demo_data:
        await seed_demo_content(session, admin)
