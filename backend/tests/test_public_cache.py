"""公开读接口的 HTTP 缓存（ETag / Cache-Control）与中间件顺序契约。

两块内容放在一个文件里，是因为它们由同一次改动引入、且互相依赖：
**只有 RequestContext 在最外层，"被 304 挡掉的请求"才会出现在访问日志里**；
顺序错了不会报错，只会让观测数据静默失真。
"""

from __future__ import annotations

import json
import logging

import pytest
from httpx import AsyncClient

from app.api.cache import PUBLIC_MAX_AGE, if_none_match_hits, is_cacheable_request, make_etag
from tests.factories import make_article_payload

PUBLIC_LIST = "/api/v1/articles"


class TestCacheableRequestRules:
    """哪些请求允许被公开缓存 —— 判错的方向只有一个：越权读。"""

    def _request(self, method: str, path: str, headers: dict[str, str] | None = None):
        from starlette.requests import Request

        raw = [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
        scope = {
            "type": "http",
            "method": method,
            "path": path,
            "headers": raw,
            "query_string": b"",
        }
        return Request(scope)

    @pytest.mark.parametrize(
        "path",
        [
            "/api/v1/articles",
            "/api/v1/articles/search",
            "/api/v1/articles/hello-world",
            "/api/v1/categories",
            "/api/v1/tags",
            "/api/v1/series",
            "/api/v1/site/profile",
            "/api/v1/comments/article/1",
        ],
    )
    def test_public_read_paths_are_cacheable(self, path: str) -> None:
        assert is_cacheable_request(self._request("GET", path)) is True

    @pytest.mark.parametrize(
        "path", ["/api/v1/articles/manage/list", "/api/v1/articles/1/revisions"]
    )
    def test_admin_and_revision_paths_are_excluded(self, path: str) -> None:
        """后台列表与版本历史与前台共用前缀，必须显式排除。"""
        assert is_cacheable_request(self._request("GET", path)) is False

    def test_authenticated_request_is_never_cached(self) -> None:
        """同一 URL 对不同用户可能不同（草稿 / 待审评论），共享缓存命中即越权。"""
        request = self._request("GET", PUBLIC_LIST, {"Authorization": "Bearer x"})
        assert is_cacheable_request(request) is False

    @pytest.mark.parametrize("method", ["POST", "PATCH", "PUT", "DELETE", "OPTIONS"])
    def test_non_read_methods_are_never_cached(self, method: str) -> None:
        assert is_cacheable_request(self._request(method, PUBLIC_LIST)) is False

    def test_unrelated_paths_are_not_cached(self) -> None:
        assert is_cacheable_request(self._request("GET", "/health")) is False


class TestEtagHelpers:
    def test_same_body_same_etag(self) -> None:
        assert make_etag(b"abc") == make_etag(b"abc")

    def test_different_body_different_etag(self) -> None:
        assert make_etag(b"abc") != make_etag(b"abd")

    def test_etag_is_weak_and_quoted(self) -> None:
        etag = make_etag(b"abc")
        assert etag.startswith('W/"') and etag.endswith('"')

    @pytest.mark.parametrize(
        ("header", "expected"),
        [
            (None, False),
            ("", False),
            ('W/"abc"', True),
            ('"abc"', True),  # 弱比较：W/ 前缀不参与
            ('"other", W/"abc"', True),
            ('"other"', False),
            ("*", True),
            (" * ", True),
        ],
    )
    def test_if_none_match_matching(self, header: str | None, expected: bool) -> None:
        assert if_none_match_hits(header, 'W/"abc"') is expected


class TestPublicCacheHeaders:
    async def test_public_list_carries_etag_and_cache_control(self, client: AsyncClient) -> None:
        response = await client.get(PUBLIC_LIST)

        assert response.status_code == 200
        assert response.headers["etag"].startswith('W/"')
        assert response.headers["cache-control"] == f"public, max-age={PUBLIC_MAX_AGE}"

    async def test_second_request_with_etag_gets_304(self, client: AsyncClient) -> None:
        """这是缓存真正省下流量的那条路径：命中条件请求时响应体为空。"""
        first = await client.get(PUBLIC_LIST)
        etag = first.headers["etag"]

        second = await client.get(PUBLIC_LIST, headers={"If-None-Match": etag})

        assert second.status_code == 304
        assert second.content == b""
        # 304 必须带上同样的 ETag 与缓存指令，否则客户端无法更新自己的元信息
        assert second.headers["etag"] == etag
        assert second.headers["cache-control"] == f"public, max-age={PUBLIC_MAX_AGE}"

    async def test_stale_etag_gets_full_body(self, client: AsyncClient, admin_headers) -> None:
        """内容变了 ETag 必须变 —— 否则客户端会拿着 304 一直看旧内容。"""
        before = await client.get(PUBLIC_LIST)
        created = await client.post(
            "/api/v1/articles",
            json=make_article_payload(title="缓存失效验证", status="published", content_md="正文"),
            headers=admin_headers,
        )
        assert created.status_code == 201, created.text

        after = await client.get(PUBLIC_LIST, headers={"If-None-Match": before.headers["etag"]})

        assert after.status_code == 200
        assert after.headers["etag"] != before.headers["etag"]

    async def test_wildcard_if_none_match_gets_304(self, client: AsyncClient) -> None:
        response = await client.get(PUBLIC_LIST, headers={"If-None-Match": "*"})
        assert response.status_code == 304

    async def test_authenticated_request_has_no_cache_headers(
        self, client: AsyncClient, admin_headers
    ) -> None:
        response = await client.get(PUBLIC_LIST, headers=admin_headers)

        assert response.status_code == 200
        assert "etag" not in response.headers
        assert "cache-control" not in response.headers

    async def test_error_response_is_not_cached(self, client: AsyncClient) -> None:
        """错误页被缓存住会很难自救：修好了服务端，客户端还在看旧的失败结果。"""
        response = await client.get("/api/v1/articles/this-does-not-exist-xyz")

        assert response.status_code == 404
        assert "etag" not in response.headers
        assert "cache-control" not in response.headers

    async def test_admin_listing_is_not_cached(self, client: AsyncClient, admin_headers) -> None:
        response = await client.get("/api/v1/articles/manage/list", headers=admin_headers)

        assert response.status_code == 200
        assert "etag" not in response.headers

    async def test_health_check_is_not_cached(self, client: AsyncClient) -> None:
        response = await client.get("/health")

        assert response.status_code == 200
        assert "etag" not in response.headers

    async def test_body_survives_the_cache_middleware(self, client: AsyncClient) -> None:
        """中间件读完 body 后要原样交回去，不能把响应变成空壳。"""
        response = await client.get(PUBLIC_LIST)

        payload = json.loads(response.content)
        assert "items" in payload and "total" in payload

    async def test_content_length_matches_body(self, client: AsyncClient) -> None:
        """重建响应时如果照抄了旧的 content-length，长度会对不上。"""
        response = await client.get(PUBLIC_LIST)

        assert int(response.headers["content-length"]) == len(response.content)


class TestMiddlewareOrder:
    """顺序契约：**后注册的在外层**。

    顺序写错不会报错，只会静默改变行为，所以必须用测试钉住。
    """

    async def test_cors_rejected_preflight_still_has_request_id(self, client: AsyncClient) -> None:
        """被 CORS 拒绝的预检也要留下 request_id。

        这正是原实现的缺陷：RequestContext 注册在 CORS 之前（= 内层），
        于是被 CORS 截住的请求在日志里完全消失、响应里也没有 request_id。
        """
        response = await client.options(
            PUBLIC_LIST,
            headers={
                "Origin": "https://evil.example.com",
                "Access-Control-Request-Method": "GET",
            },
        )

        assert response.status_code == 400  # CORS 确实拒绝了
        assert response.headers.get("x-request-id"), "被 CORS 拒绝的请求没有 request_id"

    async def test_allowed_preflight_echoes_cors_headers(self, client: AsyncClient) -> None:
        response = await client.options(
            PUBLIC_LIST,
            headers={
                "Origin": "http://localhost:5173",
                "Access-Control-Request-Method": "GET",
            },
        )

        assert response.status_code == 200
        assert response.headers["access-control-allow-origin"] == "http://localhost:5173"

    async def test_log_records_304_not_200(
        self, client: AsyncClient, caplog: pytest.LogCaptureFixture
    ) -> None:
        """访问日志里记的必须是**最终**状态码。

        RequestContext 在 PublicCache 外层时，它看到的是折叠后的 304；
        反过来则会把每个 304 都记成 200 —— "缓存到底省了多少"就永远算不出来。
        """
        first = await client.get(PUBLIC_LIST)
        etag = first.headers["etag"]

        with caplog.at_level(logging.INFO, logger="blog"):
            second = await client.get(PUBLIC_LIST, headers={"If-None-Match": etag})
        assert second.status_code == 304

        records = [
            record
            for record in caplog.records
            if getattr(record, "extra_fields", {}).get("path") == PUBLIC_LIST
        ]
        assert records, "没有捕获到该路径的访问日志"
        assert records[-1].extra_fields["status"] == 304
