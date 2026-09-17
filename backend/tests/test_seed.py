"""演示数据灌入的幂等性。

存在意义：``SEED_DEMO_DATA`` 默认是 true，而 ``seed_demo_content`` 的守卫
只看「文章表是不是空的」。分类与标签的 name 都是唯一键，所以「文章数为 0」
并不蕴含「分类标签表是空的」——站长把演示文章删光后重启，
旧实现会再插一遍同名分类，``IntegrityError`` 冒到 lifespan，
**应用直接起不来**。

注意：测试环境默认把 ``SEED_DEMO_DATA`` 设成 false（见 conftest），
所以这些用例必须显式打开它，否则演示分支根本不执行，
断言会「因为什么都没发生」而假通过。
"""

from __future__ import annotations

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import Article, Category, Tag
from app.services.seed import DEMO_CATEGORIES, DEMO_TAGS, ensure_seed


async def _count(session: AsyncSession, model: type) -> int:
    result = await session.execute(select(func.count()).select_from(model))
    return int(result.scalar_one())


@pytest.fixture
def demo_seed_on(monkeypatch: pytest.MonkeyPatch) -> None:
    """打开演示数据开关——不开的话 seed_demo_content 会直接返回。"""
    monkeypatch.setattr(settings, "seed_demo_data", True)


class TestSeedIdempotency:
    async def test_reseed_after_deleting_all_articles_does_not_crash(
        self, db_reset: None, demo_seed_on: None
    ) -> None:
        """删光文章后重新灌数据，不能因为同名分类/标签撞唯一键而炸掉。"""
        from app.db.session import async_session_factory

        async with async_session_factory() as session:
            await ensure_seed(session)
            await session.commit()
            first_categories = await _count(session, Category)
            first_articles = await _count(session, Article)
            assert first_articles > 0, "演示数据没灌进来，用例前提不成立"

        # 模拟「站长把演示文章全删了」——分类和标签刻意留着
        async with async_session_factory() as session:
            await session.execute(delete(Article))
            await session.commit()
            assert await _count(session, Article) == 0
            assert await _count(session, Category) == first_categories

        # 这一步在修复前会抛 IntegrityError: UNIQUE constraint failed: categories.name
        async with async_session_factory() as session:
            await ensure_seed(session)
            await session.commit()

            # 分类没有翻倍：复用而不是重复插入
            assert await _count(session, Category) == first_categories
            # 演示文章确实被补回来了
            assert await _count(session, Article) == first_articles

    async def test_existing_user_category_with_demo_name_is_reused(
        self, db_reset: None, demo_seed_on: None
    ) -> None:
        """站长自己建过一个跟演示数据同名的分类时，也不该撞车。"""
        from app.db.session import async_session_factory

        name, slug, _description, _order = DEMO_CATEGORIES[0]

        async with async_session_factory() as session:
            session.add(Category(name=name, slug=slug))
            await session.commit()

        async with async_session_factory() as session:
            await ensure_seed(session)
            await session.commit()
            # 只有一个同名分类（复用了站长的那个），没有重复
            found = await session.execute(select(Category).where(Category.slug == slug))
            assert len(found.scalars().all()) == 1

    async def test_existing_demo_tag_is_reused(self, db_reset: None, demo_seed_on: None) -> None:
        """标签同理：name 唯一，已存在就必须复用。"""
        from app.db.session import async_session_factory

        tag_name = DEMO_TAGS[0]

        async with async_session_factory() as session:
            session.add(Tag(name=tag_name, slug=f"{tag_name}-user"))
            await session.commit()

        async with async_session_factory() as session:
            await ensure_seed(session)
            await session.commit()
            found = await session.execute(select(Tag).where(Tag.name == tag_name))
            assert len(found.scalars().all()) == 1

    async def test_second_seed_run_is_a_noop(self, db_reset: None, demo_seed_on: None) -> None:
        """已有文章时直接返回，不会重复插入。"""
        from app.db.session import async_session_factory

        async with async_session_factory() as session:
            await ensure_seed(session)
            await session.commit()
            articles = await _count(session, Article)
            tags = await _count(session, Tag)

        async with async_session_factory() as session:
            await ensure_seed(session)
            await session.commit()
            assert await _count(session, Article) == articles
            assert await _count(session, Tag) == tags


def test_demo_taxonomy_names_are_unique() -> None:
    """演示数据自己的名字不能有重复，否则第一次灌就会撞唯一键。"""
    category_names = [item[0] for item in DEMO_CATEGORIES]
    assert len(category_names) == len(set(category_names))
    assert len(DEMO_TAGS) == len(set(DEMO_TAGS))


def test_demo_article_taxonomy_refs_are_valid() -> None:
    """演示文章引用的分类 / 标签必须在演示数据里真实存在。

    引用写错的话，第一次灌数据就是 KeyError，同样是启动期崩溃。
    """
    from app.services.seed import DEMO_ARTICLES

    # DEMO_ARTICLES 的 category 字段引用的是 **slug**（category_map 以 slug 为键），
    # 不是分类名——这里写错的话用例会误报，注意别改成 name
    valid_category_slugs = {item[1] for item in DEMO_CATEGORIES}
    valid_tags = set(DEMO_TAGS)
    for item in DEMO_ARTICLES:
        assert str(item["category"]) in valid_category_slugs
        assert set(item["tags"]) <= valid_tags  # type: ignore[arg-type]
