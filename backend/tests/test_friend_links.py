"""友情链接测试。

覆盖五块内容：

1. **公开读**：只含 ``is_active=true``、排序是 ``sort_order → id``；
2. **公开缓存**：``/api/v1/links`` 真的接进了 ``PublicCacheMiddleware``
   （带 ETag 与 ``Vary: Authorization``），而 ``/api/v1/links/manage`` 不在白名单内；
3. **权限**：写接口与 ``/manage`` 都是站长专属（未登录 401 / 作者 403）——
   ``/manage`` 只是"多看一眼"的读接口，漏掉门禁就是未启用条目的信息泄露；
4. **CRUD 的状态码与副作用**：不只断言响应码，也回到列表里核对真实状态变化；
5. **URL 安全边界**：``javascript:`` / ``data:`` 之类一律 422。
   这些值会被前端直接绑到 ``:href``，而 Vue **不清洗**动态 href ——
   放行一个就等于给出一条存储型 XSS 的路径。

风格与断言密度对齐 ``tests/test_taxonomy.py``。
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select

from app.api.cache import PUBLIC_MAX_AGE, is_cacheable_request
from app.config import settings
from app.models import FriendLink
from app.services.seed import DEMO_FRIEND_LINKS, ensure_seed
from tests.factories import unique_suffix

PUBLIC_LIST = "/api/v1/links"
MANAGED_LIST = "/api/v1/links/manage"


def make_link_payload(**overrides: object) -> dict[str, object]:
    """构造友链请求体。地址用唯一子域，避免同一次运行里互相撞唯一键。"""
    suffix = unique_suffix()
    payload: dict[str, object] = {
        "name": f"友站-{suffix}",
        "url": f"https://site-{suffix}.example.com/",
    }
    payload.update(overrides)
    return payload


async def create_link(
    client: AsyncClient, headers: dict[str, str], **overrides: object
) -> dict[str, object]:
    response = await client.post(PUBLIC_LIST, json=make_link_payload(**overrides), headers=headers)
    assert response.status_code == 201, response.text
    return response.json()


async def list_public(client: AsyncClient) -> list[dict[str, object]]:
    response = await client.get(PUBLIC_LIST)
    assert response.status_code == 200, response.text
    return response.json()


async def count_links() -> int:
    from app.db.session import async_session_factory

    async with async_session_factory() as session:
        result = await session.execute(select(func.count()).select_from(FriendLink))
        return int(result.scalar_one())


class TestPublicList:
    async def test_anonymous_can_read(self, client: AsyncClient) -> None:
        """公开接口不带任何凭证也必须能读。"""
        assert (await client.get(PUBLIC_LIST)).status_code == 200

    async def test_only_active_links_are_listed(
        self, client: AsyncClient, admin_headers: dict[str, str]
    ) -> None:
        visible = await create_link(client, admin_headers, name="可见友站")
        hidden = await create_link(client, admin_headers, name="已下线友站", is_active=False)

        names = [item["name"] for item in await list_public(client)]
        assert visible["name"] in names
        assert hidden["name"] not in names

    async def test_order_is_sort_order_then_id(
        self, client: AsyncClient, admin_headers: dict[str, str]
    ) -> None:
        """``sort_order`` 升序；相同则按 id —— 两条都为 0 时顺序仍然稳定。

        顺序不稳定的后果是"刷新一下友链顺序就变了"：默认 sort_order 全是 0，
        只按它排序时行序由数据库自行决定。
        """
        third = await create_link(client, admin_headers, name="第三个", sort_order=5)
        first = await create_link(client, admin_headers, name="第一个", sort_order=1)
        second = await create_link(client, admin_headers, name="第二个", sort_order=1)

        listed = await list_public(client)
        assert [item["id"] for item in listed] == [first["id"], second["id"], third["id"]]

    async def test_payload_shape(self, client: AsyncClient, admin_headers: dict[str, str]) -> None:
        """字段名是冻结契约（前端 types/link.ts 逐字对应），这里钉住。"""
        created = await create_link(client, admin_headers, description="一句话介绍")

        assert set(created) == {
            "id",
            "name",
            "url",
            "description",
            "avatar_url",
            "sort_order",
            "is_active",
            "created_at",
        }
        assert created["sort_order"] == 0
        assert created["is_active"] is True


class TestManagedList:
    async def test_manage_contains_inactive(
        self, client: AsyncClient, admin_headers: dict[str, str]
    ) -> None:
        hidden = await create_link(client, admin_headers, name="后台可见", is_active=False)

        response = await client.get(MANAGED_LIST, headers=admin_headers)
        assert response.status_code == 200
        ids = [item["id"] for item in response.json()]
        assert hidden["id"] in ids

    async def test_manage_requires_login(self, client: AsyncClient) -> None:
        assert (await client.get(MANAGED_LIST)).status_code == 401

    async def test_manage_forbidden_for_author(
        self, client: AsyncClient, author_headers: dict[str, str]
    ) -> None:
        """作者能发文章，但友链是站点级配置，只有站长能手改。"""
        assert (await client.get(MANAGED_LIST, headers=author_headers)).status_code == 403

    async def test_manage_is_not_publicly_cached(
        self, client: AsyncClient, admin_headers: dict[str, str]
    ) -> None:
        response = await client.get(MANAGED_LIST, headers=admin_headers)
        assert response.status_code == 200
        assert "etag" not in response.headers


class TestPublicCache:
    """公开列表必须吃到 ETag / Cache-Control / Vary（证明接进了缓存中间件）。"""

    async def test_public_list_carries_etag_and_vary(self, client: AsyncClient) -> None:
        response = await client.get(PUBLIC_LIST)

        assert response.status_code == 200
        assert response.headers["etag"].startswith('W/"')
        assert response.headers["cache-control"] == f"public, max-age={PUBLIC_MAX_AGE}"
        # 没有 Vary 时，站长登录后的请求会命中匿名那份缓存 ——
        # 表现为"后台刚加完友链，前台列表里没有"
        assert "Authorization" in response.headers.get("vary", "")

    async def test_etag_changes_after_content_changes(
        self, client: AsyncClient, admin_headers: dict[str, str]
    ) -> None:
        """内容变了 ETag 必须变，否则客户端会拿着 304 一直看旧列表。"""
        before = await client.get(PUBLIC_LIST)
        await create_link(client, admin_headers)

        after = await client.get(PUBLIC_LIST)
        assert after.headers["etag"] != before.headers["etag"]

    async def test_second_request_with_etag_gets_304(
        self, client: AsyncClient, admin_headers: dict[str, str]
    ) -> None:
        await create_link(client, admin_headers)
        etag = (await client.get(PUBLIC_LIST)).headers["etag"]

        cached = await client.get(PUBLIC_LIST, headers={"If-None-Match": etag})

        assert cached.status_code == 304
        assert cached.content == b""

    @pytest.mark.parametrize(
        ("path", "expected"),
        [(PUBLIC_LIST, True), (MANAGED_LIST, False)],
    )
    def test_cache_whitelist_covers_public_but_not_manage(self, path: str, expected: bool) -> None:
        """白名单是按前缀匹配的，``/manage`` 必须靠 ``_EXCLUDED_MARKERS`` 挡掉。

        这一条与上面几条是**两层**证明：HTTP 层看响应头，这里直接看判定函数 ——
        ``/manage`` 在带凭证时本来就不会被缓存，只在"匿名请求"这一侧
        才看得出标记有没有生效。
        """
        from starlette.requests import Request

        request = Request(
            {"type": "http", "method": "GET", "path": path, "headers": [], "query_string": b""}
        )
        assert is_cacheable_request(request) is expected


class TestCreate:
    async def test_create_returns_201(
        self, client: AsyncClient, admin_headers: dict[str, str]
    ) -> None:
        payload = make_link_payload(description="介绍", sort_order=2)

        response = await client.post(PUBLIC_LIST, json=payload, headers=admin_headers)

        assert response.status_code == 201
        body = response.json()
        assert body["id"] > 0
        assert body["name"] == payload["name"]
        assert body["url"] == payload["url"]
        assert body["sort_order"] == 2
        assert body["created_at"]
        # 副作用：真的落到了公开列表里
        assert body["id"] in [item["id"] for item in await list_public(client)]

    async def test_url_without_scheme_is_normalized(
        self, client: AsyncClient, admin_headers: dict[str, str]
    ) -> None:
        """没写协议的输入补 ``https://``（判定与补全都在 utils/url.py）。"""
        created = await create_link(client, admin_headers, url="docs.example.com/guide")

        assert created["url"] == "https://docs.example.com/guide"

    async def test_duplicate_url_conflicts(
        self, client: AsyncClient, admin_headers: dict[str, str]
    ) -> None:
        first = await create_link(client, admin_headers)
        again = await client.post(
            PUBLIC_LIST, json=make_link_payload(url=first["url"]), headers=admin_headers
        )

        assert again.status_code == 409
        assert again.json()["code"] == "conflict"

    async def test_duplicate_url_conflicts_for_different_spelling(
        self, client: AsyncClient, admin_headers: dict[str, str]
    ) -> None:
        """``example.com`` 与 ``https://example.com`` 归一后是同一个地址。"""
        await create_link(client, admin_headers, url="https://same-site.example.com")

        again = await client.post(
            PUBLIC_LIST, json=make_link_payload(url="same-site.example.com"), headers=admin_headers
        )
        assert again.status_code == 409

    async def test_inactive_link_still_occupies_the_url(
        self, client: AsyncClient, admin_headers: dict[str, str]
    ) -> None:
        """唯一性是全表口径，不是"启用的那些之间"。"""
        first = await create_link(client, admin_headers, is_active=False)

        again = await client.post(
            PUBLIC_LIST, json=make_link_payload(url=first["url"]), headers=admin_headers
        )
        assert again.status_code == 409

    async def test_guest_cannot_create(self, client: AsyncClient) -> None:
        response = await client.post(PUBLIC_LIST, json=make_link_payload())
        assert response.status_code == 401

    async def test_author_cannot_create(
        self, client: AsyncClient, author_headers: dict[str, str]
    ) -> None:
        response = await client.post(PUBLIC_LIST, json=make_link_payload(), headers=author_headers)
        assert response.status_code == 403

    @pytest.mark.parametrize(
        "bad_url",
        [
            "javascript:alert(1)",
            "JavaScript:alert(document.cookie)",
            "data:text/html;base64,PHNjcmlwdD5hbGVydCgxKTwvc2NyaXB0Pg==",
            "vbscript:msgbox(1)",
            "java\nscript:alert(1)",  # 浏览器会把换行剥掉，等价于 javascript:
        ],
    )
    async def test_dangerous_scheme_rejected(
        self, client: AsyncClient, admin_headers: dict[str, str], bad_url: str
    ) -> None:
        response = await client.post(
            PUBLIC_LIST, json=make_link_payload(url=bad_url), headers=admin_headers
        )

        assert response.status_code == 422
        # 拒绝而不是静默丢弃：库里的行数必须没变
        assert await count_links() == 0

    @pytest.mark.parametrize("bad_url", ["", "   ", None])
    async def test_url_is_required(
        self, client: AsyncClient, admin_headers: dict[str, str], bad_url: str | None
    ) -> None:
        response = await client.post(
            PUBLIC_LIST, json=make_link_payload(url=bad_url), headers=admin_headers
        )
        assert response.status_code == 422

    @pytest.mark.parametrize("bad_name", ["", "   "])
    async def test_blank_name_rejected(
        self, client: AsyncClient, admin_headers: dict[str, str], bad_name: str
    ) -> None:
        """空名与纯空白名都要 422：后者能通过 ``min_length=1``，
        所以名称是先 strip 再校验长度的（见 schemas/friend_link.py）。"""
        response = await client.post(
            PUBLIC_LIST, json=make_link_payload(name=bad_name), headers=admin_headers
        )
        assert response.status_code == 422

    async def test_name_is_trimmed(
        self, client: AsyncClient, admin_headers: dict[str, str]
    ) -> None:
        created = await create_link(client, admin_headers, name="  留白友站  ")
        assert created["name"] == "留白友站"

    async def test_avatar_blank_becomes_null(
        self, client: AsyncClient, admin_headers: dict[str, str]
    ) -> None:
        """空白头像统一成 ``None``：前端只需要判断一种"没填"。"""
        created = await create_link(client, admin_headers, avatar_url="   ")
        assert created["avatar_url"] is None

    async def test_avatar_follows_the_same_url_rules(
        self, client: AsyncClient, admin_headers: dict[str, str]
    ) -> None:
        """头像也是用户可控 URL，同样不能放行伪协议。"""
        response = await client.post(
            PUBLIC_LIST,
            json=make_link_payload(avatar_url="javascript:alert(1)"),
            headers=admin_headers,
        )
        assert response.status_code == 422

    async def test_negative_sort_order_rejected(
        self, client: AsyncClient, admin_headers: dict[str, str]
    ) -> None:
        response = await client.post(
            PUBLIC_LIST, json=make_link_payload(sort_order=-1), headers=admin_headers
        )
        assert response.status_code == 422


class TestUpdate:
    async def test_partial_update_only_touches_sent_fields(
        self, client: AsyncClient, admin_headers: dict[str, str]
    ) -> None:
        created = await create_link(
            client, admin_headers, description="原介绍", sort_order=3, avatar_url=None
        )

        response = await client.patch(
            f"{PUBLIC_LIST}/{created['id']}", json={"name": "改名后"}, headers=admin_headers
        )

        assert response.status_code == 200
        body = response.json()
        assert body["name"] == "改名后"
        # 没传的字段一个都不能被覆盖
        assert body["url"] == created["url"]
        assert body["description"] == "原介绍"
        assert body["sort_order"] == 3
        assert body["is_active"] is True

    async def test_clearing_description(
        self, client: AsyncClient, admin_headers: dict[str, str]
    ) -> None:
        """显式传 null 才是"清空"；不传就是"不改"。"""
        created = await create_link(client, admin_headers, description="待清空")

        response = await client.patch(
            f"{PUBLIC_LIST}/{created['id']}", json={"description": None}, headers=admin_headers
        )
        assert response.status_code == 200
        assert response.json()["description"] is None

    async def test_null_on_required_field_is_ignored(
        self, client: AsyncClient, admin_headers: dict[str, str]
    ) -> None:
        """``name`` / ``sort_order`` 收到显式 null 视为"不修改"。

        它们在库里是 NOT NULL，硬写下去是 500；而"把名字清空"在语义上不存在。
        口径与 TaxonomyService 处理 description 的那段一致。
        """
        created = await create_link(client, admin_headers, sort_order=4)

        response = await client.patch(
            f"{PUBLIC_LIST}/{created['id']}",
            json={"name": None, "sort_order": None},
            headers=admin_headers,
        )
        assert response.status_code == 200
        assert response.json()["name"] == created["name"]
        assert response.json()["sort_order"] == 4

    async def test_toggling_active_hides_from_public_list(
        self, client: AsyncClient, admin_headers: dict[str, str]
    ) -> None:
        """临时下线的正确姿势：``is_active=false``，数据与排序位都留着。"""
        created = await create_link(client, admin_headers)
        assert created["id"] in [item["id"] for item in await list_public(client)]

        off = await client.patch(
            f"{PUBLIC_LIST}/{created['id']}", json={"is_active": False}, headers=admin_headers
        )
        assert off.status_code == 200
        assert off.json()["is_active"] is False
        assert created["id"] not in [item["id"] for item in await list_public(client)]

        managed = (await client.get(MANAGED_LIST, headers=admin_headers)).json()
        assert created["id"] in [item["id"] for item in managed]

        back = await client.patch(
            f"{PUBLIC_LIST}/{created['id']}", json={"is_active": True}, headers=admin_headers
        )
        assert back.json()["is_active"] is True
        assert created["id"] in [item["id"] for item in await list_public(client)]

    async def test_update_url_to_existing_conflicts(
        self, client: AsyncClient, admin_headers: dict[str, str]
    ) -> None:
        taken = await create_link(client, admin_headers)
        mine = await create_link(client, admin_headers)

        response = await client.patch(
            f"{PUBLIC_LIST}/{mine['id']}", json={"url": taken["url"]}, headers=admin_headers
        )
        assert response.status_code == 409

    async def test_keeping_own_url_is_not_a_conflict(
        self, client: AsyncClient, admin_headers: dict[str, str]
    ) -> None:
        """不改地址的 PATCH 不该判定成"和自己冲突"（查重要排除自己）。"""
        created = await create_link(client, admin_headers)

        response = await client.patch(
            f"{PUBLIC_LIST}/{created['id']}",
            json={"url": created["url"], "sort_order": 9},
            headers=admin_headers,
        )
        assert response.status_code == 200
        assert response.json()["sort_order"] == 9

    async def test_update_rejects_dangerous_scheme(
        self, client: AsyncClient, admin_headers: dict[str, str]
    ) -> None:
        created = await create_link(client, admin_headers)

        response = await client.patch(
            f"{PUBLIC_LIST}/{created['id']}",
            json={"url": "javascript:alert(1)"},
            headers=admin_headers,
        )
        assert response.status_code == 422

    async def test_missing_link_404(
        self, client: AsyncClient, admin_headers: dict[str, str]
    ) -> None:
        response = await client.patch(
            f"{PUBLIC_LIST}/999999", json={"sort_order": 1}, headers=admin_headers
        )
        assert response.status_code == 404

    async def test_guest_cannot_update(
        self, client: AsyncClient, admin_headers: dict[str, str]
    ) -> None:
        created = await create_link(client, admin_headers)
        response = await client.patch(f"{PUBLIC_LIST}/{created['id']}", json={"sort_order": 1})
        assert response.status_code == 401

    async def test_author_cannot_update(
        self, client: AsyncClient, admin_headers: dict[str, str], author_headers: dict[str, str]
    ) -> None:
        created = await create_link(client, admin_headers)
        response = await client.patch(
            f"{PUBLIC_LIST}/{created['id']}", json={"sort_order": 1}, headers=author_headers
        )
        assert response.status_code == 403


class TestDelete:
    async def test_delete_returns_message_and_removes_row(
        self, client: AsyncClient, admin_headers: dict[str, str]
    ) -> None:
        created = await create_link(client, admin_headers)

        response = await client.delete(f"{PUBLIC_LIST}/{created['id']}", headers=admin_headers)

        assert response.status_code == 200
        assert response.json()["detail"]
        # 副作用两处都要核：公开列表与后台列表
        assert created["id"] not in [item["id"] for item in await list_public(client)]
        managed = (await client.get(MANAGED_LIST, headers=admin_headers)).json()
        assert created["id"] not in [item["id"] for item in managed]

    async def test_delete_frees_the_url(
        self, client: AsyncClient, admin_headers: dict[str, str]
    ) -> None:
        """删除之后同一个地址可以被重新收录（唯一索引不会留下占位）。"""
        created = await create_link(client, admin_headers)
        await client.delete(f"{PUBLIC_LIST}/{created['id']}", headers=admin_headers)

        again = await client.post(
            PUBLIC_LIST, json=make_link_payload(url=created["url"]), headers=admin_headers
        )
        assert again.status_code == 201

    async def test_delete_missing_404(
        self, client: AsyncClient, admin_headers: dict[str, str]
    ) -> None:
        assert (
            await client.delete(f"{PUBLIC_LIST}/999999", headers=admin_headers)
        ).status_code == 404

    async def test_guest_cannot_delete(
        self, client: AsyncClient, admin_headers: dict[str, str]
    ) -> None:
        created = await create_link(client, admin_headers)
        response = await client.delete(f"{PUBLIC_LIST}/{created['id']}")
        assert response.status_code == 401

    async def test_author_cannot_delete(
        self, client: AsyncClient, admin_headers: dict[str, str], author_headers: dict[str, str]
    ) -> None:
        created = await create_link(client, admin_headers)
        response = await client.delete(f"{PUBLIC_LIST}/{created['id']}", headers=author_headers)
        assert response.status_code == 403


class TestDemoSeed:
    """演示友链的幂等性。

    测试环境默认把 ``SEED_DEMO_DATA`` 关掉（见 conftest），所以这些用例
    必须显式打开；不开的话演示分支根本不执行，断言会因为"什么都没发生"而假通过。
    """

    @pytest.fixture
    def demo_seed_on(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(settings, "seed_demo_data", True)

    async def test_seed_writes_demo_links_once(
        self, client: AsyncClient, db_reset: None, demo_seed_on: None
    ) -> None:
        from app.db.session import async_session_factory

        async with async_session_factory() as session:
            await ensure_seed(session)
            await session.commit()
        assert await count_links() == len(DEMO_FRIEND_LINKS)

        # 再灌一次：表里已有友链，直接跳过（不重复插入、也不撞唯一键）
        async with async_session_factory() as session:
            await ensure_seed(session)
            await session.commit()
        assert await count_links() == len(DEMO_FRIEND_LINKS)

        listed = await list_public(client)
        assert [item["url"] for item in listed] == [item["url"] for item in DEMO_FRIEND_LINKS]

    async def test_seed_uses_real_http_sites(self) -> None:
        """演示数据必须是真实存在的站点，且地址本身合法。

        编造域名（example.com 之类）在演示里看着没问题，实际点不开——
        而站长第一件事就是点一遍自己的友链页。
        """
        assert len(DEMO_FRIEND_LINKS) == 3
        for item in DEMO_FRIEND_LINKS:
            url = str(item["url"])
            assert url.startswith("https://")
            assert "example.com" not in url

    async def test_seed_skips_when_site_owner_already_has_links(
        self, client: AsyncClient, admin_headers: dict[str, str], demo_seed_on: None
    ) -> None:
        """站长自己先加了友链时，演示数据一条都不能进来。"""
        from app.db.session import async_session_factory

        mine = await create_link(client, admin_headers, name="我自己的友链")

        async with async_session_factory() as session:
            await ensure_seed(session)
            await session.commit()

        listed = await list_public(client)
        assert [item["id"] for item in listed] == [mine["id"]]
