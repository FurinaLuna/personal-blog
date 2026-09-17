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

    async def test_matches_title(self, client: AsyncClient, author_headers: dict[str, str]) -> None:
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

        async def no_index(_session):
            return False

        monkeypatch.setattr(fulltext, "fulltext_available", no_index)

        body = (await client.get(f"{SEARCH_URL}?q=Trombone")).json()
        assert article["id"] in [item["id"] for item in body["items"]]


class TestPaginationHonesty:
    """分页与总数必须诚实——这是最容易被"看起来能用"掩盖的一类缺陷。"""

    async def test_total_reflects_all_matches_not_just_this_page(
        self, client: AsyncClient, author_headers: dict[str, str]
    ) -> None:
        """total 必须是命中总数，而不是"这一页取回了几条"。

        实现里 ids 是按 offset+limit 取的，如果 total 直接取 len(ids)，
        第一页的上限就是 page_size —— 命中 15 条时 total 会显示 10，
        前端据此判断"没有下一页"，第 11 条之后**永远翻不到**。
        """
        for index in range(12):
            response = await client.post(
                "/api/v1/articles",
                json=make_article_payload(
                    title=f"Paginated {index}",
                    content_md="共同关键词 Zebra",
                    status="published",
                ),
                headers=author_headers,
            )
            assert response.status_code == 201

        first = (await client.get(f"{SEARCH_URL}?q=Zebra&page=1&page_size=5")).json()
        assert first["total"] == 12, f"total 应当是 12，实际 {first['total']}"
        assert len(first["items"]) == 5

        third = (await client.get(f"{SEARCH_URL}?q=Zebra&page=3&page_size=5")).json()
        assert len(third["items"]) == 2, "第 3 页应当还有 2 条，否则说明后段结果取不到"

    async def test_pages_do_not_overlap(
        self, client: AsyncClient, author_headers: dict[str, str]
    ) -> None:
        """翻页不能重复返回同一批结果。"""
        for index in range(8):
            await client.post(
                "/api/v1/articles",
                json=make_article_payload(
                    title=f"Overlap {index}", content_md="关键词 Yak", status="published"
                ),
                headers=author_headers,
            )

        page1 = (await client.get(f"{SEARCH_URL}?q=Yak&page=1&page_size=4")).json()
        page2 = (await client.get(f"{SEARCH_URL}?q=Yak&page=2&page_size=4")).json()
        ids1 = {item["id"] for item in page1["items"]}
        ids2 = {item["id"] for item in page2["items"]}
        assert not (ids1 & ids2), "第 1、2 页出现了重复结果"
        assert len(ids1 | ids2) == 8


class TestRankingConsistency:
    """2 字与 3 字查询必须给出**一致**的排序。

    这是修复过的一个真实缺陷：长查询走 FTS5 + bm25，短查询走
    ``list_public(sort=LATEST)``，而后者**置顶优先**——于是搜「数据」
    第一篇是只在正文里顺带提了一句的置顶文章，搜「数据层」第一篇却是
    标题命中。同一个搜索框，排序随字数变化，用户无法形成预期。
    """

    async def test_both_paths_put_title_match_first(
        self, client: AsyncClient, author_headers: dict[str, str]
    ) -> None:
        """置顶不能影响搜索排序；两条路径都把标题命中排在最前。"""
        # 置顶，但只在正文提到
        await client.post(
            "/api/v1/articles",
            json=make_article_payload(
                title="置顶但只是顺带一提",
                content_md="正文里出现了 数据层 三个字",
                status="published",
                is_top=True,
            ),
            headers=author_headers,
        )
        # 普通，但标题就是关键词
        await client.post(
            "/api/v1/articles",
            json=make_article_payload(
                title="数据层 全解析", content_md="正文无关", status="published"
            ),
            headers=author_headers,
        )

        long_q = (await client.get(f"{SEARCH_URL}?q=数据层")).json()  # FTS5
        short_q = (await client.get(f"{SEARCH_URL}?q=数据")).json()  # LIKE

        assert long_q["items"][0]["title"] == "数据层 全解析"
        assert short_q["items"][0]["title"] == "数据层 全解析", (
            "短查询把置顶文章排到了最前——搜索排序不该受置顶影响"
        )

    async def test_title_beats_body_on_short_query(
        self, client: AsyncClient, author_headers: dict[str, str]
    ) -> None:
        """短查询也要遵守「标题 > 摘要 > 正文」的相关度顺序。"""
        await client.post(
            "/api/v1/articles",
            json=make_article_payload(
                title="无关标题", content_md="正文里出现了 蝴蝶 两个字", status="published"
            ),
            headers=author_headers,
        )
        await client.post(
            "/api/v1/articles",
            json=make_article_payload(
                title="蝴蝶 观察笔记", content_md="正文无关", status="published"
            ),
            headers=author_headers,
        )

        body = (await client.get(f"{SEARCH_URL}?q=蝴蝶")).json()
        assert body["items"][0]["title"] == "蝴蝶 观察笔记"


class TestRecencyDecay:
    """时间衰减：同等相关度下新文优先。

    实现用**除法**而不是减法，这是实测决定的：bm25 的量纲随语料规模与词频
    变化几个数量级（3 篇语料下稀有词约 -1.0，202 篇下约 -8.8，高频词只有
    -0.000001）。任何固定的加减值都会在某个区间里彻底压倒相关度。
    """

    async def test_newer_wins_when_relevance_is_equal(
        self, client: AsyncClient, author_headers: dict[str, str]
    ) -> None:
        from datetime import UTC, datetime, timedelta

        # 两篇同样是「只改正文、标题无关」，相关度相同，用发布时间拉开差距
        old = (
            await client.post(
                "/api/v1/articles",
                json=make_article_payload(
                    title="旧文章",
                    content_md="正文提到 猫头鹰",
                    status="published",
                    published_at=(datetime.now(UTC) - timedelta(days=365 * 5)).isoformat(),
                ),
                headers=author_headers,
            )
        ).json()
        new = (
            await client.post(
                "/api/v1/articles",
                json=make_article_payload(
                    title="新文章",
                    content_md="正文提到 猫头鹰",
                    status="published",
                    published_at=(datetime.now(UTC) - timedelta(days=1)).isoformat(),
                ),
                headers=author_headers,
            )
        ).json()

        body = (await client.get(f"{SEARCH_URL}?q=猫头鹰")).json()
        ids = [item["id"] for item in body["items"]]
        assert ids.index(new["id"]) < ids.index(old["id"]), "同等相关度下新文应当靠前"

    async def test_relevance_still_beats_recency(
        self, client: AsyncClient, author_headers: dict[str, str]
    ) -> None:
        """衰减不能反过来压倒相关度：旧文的标题命中仍应胜过新文的正文命中。"""
        from datetime import UTC, datetime, timedelta

        old_title = (
            await client.post(
                "/api/v1/articles",
                json=make_article_payload(
                    title="变色龙 完全指南",
                    content_md="正文无关",
                    status="published",
                    published_at=(datetime.now(UTC) - timedelta(days=365 * 6)).isoformat(),
                ),
                headers=author_headers,
            )
        ).json()
        new_body = (
            await client.post(
                "/api/v1/articles",
                json=make_article_payload(
                    title="新写的随笔",
                    content_md="随手提到 变色龙",
                    status="published",
                    published_at=(datetime.now(UTC) - timedelta(days=1)).isoformat(),
                ),
                headers=author_headers,
            )
        ).json()

        body = (await client.get(f"{SEARCH_URL}?q=变色龙")).json()
        ids = [item["id"] for item in body["items"]]
        assert ids.index(old_title["id"]) < ids.index(new_body["id"]), (
            "时间衰减把 6 年前的标题命中压到了新文的正文命中之后——衰减过头了"
        )


class TestSnippet:
    """命中片段：回答「这条为什么会出现」。"""

    async def test_snippet_shows_body_context(
        self, client: AsyncClient, author_headers: dict[str, str]
    ) -> None:
        await client.post(
            "/api/v1/articles",
            json=make_article_payload(
                title="标题里没有那个词",
                summary="摘要里也没有",
                content_md="前面一堆无关的话。"
                + "铺垫" * 40
                + "关键内容是 犰狳 的习性。"
                + "收尾" * 40,
                status="published",
            ),
            headers=author_headers,
        )
        body = (await client.get(f"{SEARCH_URL}?q=犰狳")).json()
        item = body["items"][0]
        assert item["snippet"] is not None
        assert "犰狳" in item["snippet"]
        # 片段应当带省略号，表明是截取出来的
        assert item["snippet"].startswith("…")

    async def test_no_snippet_when_only_title_matched(
        self, client: AsyncClient, author_headers: dict[str, str]
    ) -> None:
        """正文里没有这个词时不给片段——给了反而是误导。

        （这时标题高亮已经说明了一切，前端会自己处理。）
        """
        await client.post(
            "/api/v1/articles",
            json=make_article_payload(
                title="穿山甲 研究报告", content_md="正文完全没有那两个字", status="published"
            ),
            headers=author_headers,
        )
        body = (await client.get(f"{SEARCH_URL}?q=穿山甲")).json()
        assert body["items"][0]["snippet"] is None

    async def test_snippet_is_plain_text(
        self, client: AsyncClient, author_headers: dict[str, str]
    ) -> None:
        """片段必须是纯文本：后端塞 HTML 标记会把 XSS 面重新引进来。"""
        await client.post(
            "/api/v1/articles",
            json=make_article_payload(
                title="无关",
                content_md="正文提到 水獭 <script>alert(1)</script> 这个词",
                status="published",
            ),
            headers=author_headers,
        )
        body = (await client.get(f"{SEARCH_URL}?q=水獭")).json()
        snippet = body["items"][0]["snippet"] or ""
        assert "<mark>" not in snippet and "<b>" not in snippet

    async def test_snippet_survives_regex_metacharacters(
        self, client: AsyncClient, author_headers: dict[str, str]
    ) -> None:
        """搜索词里带正则元字符时不能炸（实现用的是 find 而不是正则）。"""
        await client.post(
            "/api/v1/articles",
            json=make_article_payload(
                title="无关", content_md="正文里有 a(b)*c 这样的写法", status="published"
            ),
            headers=author_headers,
        )
        body = (await client.get(SEARCH_URL, params={"q": "a(b)*c"})).json()
        assert body["items"][0]["snippet"] is not None
