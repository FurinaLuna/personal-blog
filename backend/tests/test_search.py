"""站内搜索（FTS5 + LIKE 混合）。

## 为什么要混合

实测结论（不是理论推导）：

- ``unicode61``（FTS5 默认分词器）**对中文完全无效**。它按 Unicode 类别切词，
  一整段连续汉字会变成一个 token：搜「数据层」在
  「我把博客的数据层重写了一遍」里返回 0 条，只有把整句原样贴进去才命中。
- ``trigram`` 分词器中文子串检索正常，但**要求查询词 ≥3 个字符**：
  「数据」「博客」这类常见两字词一律零结果。

所以 ≥3 字符走 FTS5（快 + bm25 相关度排序），1–2 字符退回 LIKE。
只上 FTS 会把中文搜索做残——这一类问题在英文语料上永远测不出来。
"""

from __future__ import annotations

from httpx import AsyncClient

from tests.factories import make_article_payload

SEARCH_URL = "/api/v1/articles/search"


async def _publish(client: AsyncClient, headers: dict[str, str], **overrides: object) -> dict:
    # 默认已发布，但 overrides 里可以覆盖（草稿 / 归档 / 排期各有用例）。
    # 必须用合并后的字典，不能写成 make_article_payload(status="published", **overrides)
    # —— 那样 overrides 里再出现 status 就是重复关键字参数，直接 TypeError。
    payload = make_article_payload(**{"status": "published", **overrides})
    response = await client.post("/api/v1/articles", json=payload, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()


class TestSearchBasics:
    async def test_empty_query_rejected(self, client: AsyncClient) -> None:
        """q 是必填的，空串直接 422，而不是返回全站文章。"""
        assert (await client.get(SEARCH_URL)).status_code == 422

    async def test_matches_title(
        self, client: AsyncClient, author_headers: dict[str, str]
    ) -> None:
        article = await _publish(
            client, author_headers, title="深入理解 PostgreSQL 的索引", content_md="正文内容"
        )
        body = (await client.get(f"{SEARCH_URL}?q=PostgreSQL")).json()
        assert article["id"] in [item["id"] for item in body["items"]]

    async def test_matches_body_only(
        self, client: AsyncClient, author_headers: dict[str, str]
    ) -> None:
        """只在正文里出现的词也要能搜到（标题、摘要都没提到）。"""
        article = await _publish(
            client,
            author_headers,
            title="一篇标题无关的文章",
            summary="摘要也无关",
            content_md="正文里提到了 Meilisearch 这个检索方案",
        )
        body = (await client.get(f"{SEARCH_URL}?q=Meilisearch")).json()
        assert article["id"] in [item["id"] for item in body["items"]]

    async def test_no_match_returns_empty(
        self, client: AsyncClient, published_article: dict
    ) -> None:
        body = (await client.get(f"{SEARCH_URL}?q=zzz绝不可能命中的词zzz")).json()
        assert body["items"] == []
        assert body["total"] == 0


class TestChineseSegmentation:
    """中文分词的边界——FTS5 默认分词器在这里是坏的。"""

    async def test_three_char_chinese_query_matches(
        self, client: AsyncClient, author_headers: dict[str, str]
    ) -> None:
        """三字中文查询（走 FTS5 trigram）必须命中。"""
        article = await _publish(
            client,
            author_headers,
            title="为什么我把博客的数据层重写了一遍",
            content_md="起因是老博客用了同步 ORM",
        )
        body = (await client.get(f"{SEARCH_URL}?q=数据层")).json()
        assert article["id"] in [item["id"] for item in body["items"]], (
            "三字中文查询没命中——很可能分词器退化成了 unicode61"
        )

    async def test_two_char_chinese_query_falls_back_to_like(
        self, client: AsyncClient, author_headers: dict[str, str]
    ) -> None:
        """两字中文查询（trigram 做不到）必须由 LIKE 兜住。

        这是混合策略存在的全部理由：trigram 按三字符滑窗建索引，
        「博客」只有两个字，FTS 一律零结果。退回 LIKE 才搜得到。
        """
        article = await _publish(
            client,
            author_headers,
            title="我的博客搭建记录",
            content_md="用 FastAPI 与 Vue 写的",
        )
        body = (await client.get(f"{SEARCH_URL}?q=博客")).json()
        assert article["id"] in [item["id"] for item in body["items"]], (
            "两字中文查询没命中——短查询没有退回 LIKE"
        )

    async def test_one_char_query_still_works(
        self, client: AsyncClient, author_headers: dict[str, str]
    ) -> None:
        """单字查询同样交给 LIKE。"""
        article = await _publish(
            client, author_headers, title="猫咪观察日记", content_md="记录一只橘猫"
        )
        body = (await client.get(f"{SEARCH_URL}?q=猫")).json()
        assert article["id"] in [item["id"] for item in body["items"]]


class TestVisibility:
    """搜索结果必须遵守与列表页相同的可见性口径。"""

    async def test_draft_not_searchable(
        self, client: AsyncClient, author_headers: dict[str, str]
    ) -> None:
        draft = await _publish(
            client,
            author_headers,
            title="草稿不该被搜到",
            content_md="独一无二的关键词 Xylophone",
            status="draft",
        )
        body = (await client.get(f"{SEARCH_URL}?q=Xylophone")).json()
        assert draft["id"] not in [item["id"] for item in body["items"]]

    async def test_archived_not_in_search_results(
        self, client: AsyncClient, author_headers: dict[str, str]
    ) -> None:
        """归档文章不进列表也不进搜索——搜索是列表的一种。"""
        archived = await _publish(
            client,
            author_headers,
            title="归档文章",
            content_md="关键词 Kettledrum",
            status="archived",
        )
        body = (await client.get(f"{SEARCH_URL}?q=Kettledrum")).json()
        assert archived["id"] not in [item["id"] for item in body["items"]]

    async def test_scheduled_not_searchable(
        self, client: AsyncClient, author_headers: dict[str, str]
    ) -> None:
        """未到发布时间的排期文章同样不能提前被搜出来。"""
        from datetime import UTC, datetime, timedelta

        future = (datetime.now(UTC) + timedelta(days=3)).isoformat()
        scheduled = await _publish(
            client,
            author_headers,
            title="排期文章",
            content_md="关键词 Glockenspiel",
            published_at=future,
        )
        body = (await client.get(f"{SEARCH_URL}?q=Glockenspiel")).json()
        assert scheduled["id"] not in [item["id"] for item in body["items"]]


class TestQuerySafety:
    """FTS5 查询语法里有 ``*`` ``(`` ``)`` ``:`` ``^`` ``-`` ``NOT`` 等操作符。"""

    async def test_fts_operators_are_treated_as_plain_text(
        self, client: AsyncClient, author_headers: dict[str, str]
    ) -> None:
        """把操作符拼进查询串不能报错，也不能改变语义。

        不转义的话轻则 500（"fts5: syntax error"），重则变成另一种查询。
        """
        await _publish(client, author_headers, title="关于 NOT 与 OR 的用法")
        for query in ["NOT", "a OR b", "foo*", "(bar)", "^start", "ne-1", 'say "hi"']:
            response = await client.get(f"{SEARCH_URL}", params={"q": query})
            assert response.status_code == 200, f"{query!r} 让接口报错了：{response.text}"

    async def test_quotes_do_not_break_the_query(
        self, client: AsyncClient, published_article: dict
    ) -> None:
        """引号是 FTS5 短语的定界符，用户输入里的引号必须被转义掉。"""
        response = await client.get(SEARCH_URL, params={"q": '带"引号"的查询'})
        assert response.status_code == 200

    async def test_long_query_rejected(self, client: AsyncClient) -> None:
        assert (await client.get(SEARCH_URL, params={"q": "x" * 200})).status_code == 422


class TestFallbackWithoutIndex:
    async def test_search_works_when_fts_index_missing(
        self, client: AsyncClient, author_headers: dict[str, str], monkeypatch
    ) -> None:
        """索引不存在时必须安静退回 LIKE，而不是抛 500。

        这个场景很真实：``DB_AUTO_CREATE=true``（开发默认）时表由
        ``create_all`` 建，而 FTS5 虚拟表不在 Base.metadata 里——
        只有跑过迁移才有。为它把整个搜索接口打挂是不可接受的。
        """
        from app.db import fulltext

        article = await _publish(
            client,
            author_headers,
            title="没有索引时也要能搜到",
            content_md="关键词 Trombone",
        )

        async def no_index(_session):  # noqa: ANN001, ANN202
            return False

        monkeypatch.setattr(fulltext, "fulltext_available", no_index)

        body = (await client.get(f"{SEARCH_URL}?q=Trombone")).json()
        assert article["id"] in [item["id"] for item in body["items"]]
