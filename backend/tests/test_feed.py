"""RSS / sitemap / 相关文章 的接口测试。"""

from __future__ import annotations

import xml.etree.ElementTree as ET

import pytest
from httpx import AsyncClient

from tests.conftest import login
from tests.factories import make_article_payload, unique_suffix

pytestmark = pytest.mark.asyncio


async def _publish(client: AsyncClient, headers: dict[str, str], **overrides) -> dict:
    payload = make_article_payload(**overrides)
    resp = await client.post("/api/v1/articles", json=payload, headers=headers)
    assert resp.status_code == 201, resp.text
    return resp.json()


class TestRssFeed:
    async def test_feed_is_valid_rss(self, client: AsyncClient, published_article: dict) -> None:
        resp = await client.get("/feed.xml")
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("application/rss+xml")

        root = ET.fromstring(resp.text)
        assert root.tag == "rss"
        channel = root.find("channel")
        assert channel is not None
        assert channel.findtext("title")
        assert channel.findtext("link")

        titles = [item.findtext("title") for item in channel.findall("item")]
        assert published_article["title"] in titles

    async def test_feed_excludes_drafts(
        self, client: AsyncClient, author_headers: dict[str, str]
    ) -> None:
        draft = await _publish(
            client, author_headers, title=f"草稿{unique_suffix()}", status="draft"
        )
        resp = await client.get("/feed.xml")
        assert draft["title"] not in resp.text

    async def test_feed_links_are_absolute(
        self, client: AsyncClient, published_article: dict
    ) -> None:
        resp = await client.get("/feed.xml")
        channel = ET.fromstring(resp.text).find("channel")
        assert channel is not None
        for item in channel.findall("item"):
            link = item.findtext("link") or ""
            assert link.startswith("http"), f"RSS 链接必须是绝对地址：{link}"
            assert "/article/" in link


class TestSitemap:
    async def test_sitemap_contains_static_pages_and_articles(
        self, client: AsyncClient, published_article: dict
    ) -> None:
        resp = await client.get("/sitemap.xml")
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("application/xml")

        root = ET.fromstring(resp.text)
        ns = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
        locs = [url.findtext("sm:loc", namespaces=ns) for url in root.findall("sm:url", ns)]
        assert any(loc and loc.endswith("/about") for loc in locs)
        assert any(loc and published_article["slug"] in loc for loc in locs)


class TestRelatedArticles:
    async def test_same_category_is_related(
        self, client: AsyncClient, author_headers: dict[str, str]
    ) -> None:
        category = (
            await client.post(
                "/api/v1/categories",
                json={"name": f"相关分类{unique_suffix()}"},
                headers=author_headers,
            )
        ).json()
        first = await _publish(client, author_headers, category_id=category["id"])
        second = await _publish(client, author_headers, category_id=category["id"])

        resp = await client.get(f"/api/v1/articles/{first['id']}/related")
        assert resp.status_code == 200
        ids = [item["id"] for item in resp.json()]
        assert second["id"] in ids
        assert first["id"] not in ids  # 不能把自己推荐给自己

    async def test_shared_tag_is_related(
        self, client: AsyncClient, author_headers: dict[str, str]
    ) -> None:
        tag = f"共同标签{unique_suffix()}"
        first = await _publish(client, author_headers, tags=[tag])
        second = await _publish(client, author_headers, tags=[tag])

        resp = await client.get(f"/api/v1/articles/{first['id']}/related")
        ids = [item["id"] for item in resp.json()]
        assert second["id"] in ids

    async def test_unrelated_article_not_included(
        self, client: AsyncClient, author_headers: dict[str, str]
    ) -> None:
        first = await _publish(client, author_headers, tags=[f"甲{unique_suffix()}"])
        other = await _publish(client, author_headers, tags=[f"乙{unique_suffix()}"])

        resp = await client.get(f"/api/v1/articles/{first['id']}/related")
        ids = [item["id"] for item in resp.json()]
        assert other["id"] not in ids

    async def test_draft_not_related_and_not_found_for_draft_source(
        self, client: AsyncClient, author_headers: dict[str, str]
    ) -> None:
        tag = f"草稿标签{unique_suffix()}"
        draft = await _publish(client, author_headers, status="draft", tags=[tag])
        published = await _publish(client, author_headers, tags=[tag])

        # 草稿源 → 404
        assert (await client.get(f"/api/v1/articles/{draft['id']}/related")).status_code == 404
        # 草稿不出现在别人的相关列表里
        resp = await client.get(f"/api/v1/articles/{published['id']}/related")
        ids = [item["id"] for item in resp.json()]
        assert draft["id"] not in ids

    async def test_login_helper_importable(self) -> None:
        """conftest 的登录辅助函数签名回归（防止重构后测试悄悄失效）。"""
        assert callable(login)
