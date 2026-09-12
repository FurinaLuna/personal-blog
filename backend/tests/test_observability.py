"""可观测性测试：请求 ID 透传、结构化日志、错误响应带 request_id。

这些是「线上排障的基础设施」——没有它们，用户报障时只能靠时间点猜日志。
"""

from __future__ import annotations

import json
import logging

from httpx import AsyncClient

from app.utils.logging import (
    REQUEST_ID_HEADER,
    JsonFormatter,
    get_request_id,
    sanitize_request_id,
)


class TestRequestId:
    async def test_generates_when_absent(self, client: AsyncClient) -> None:
        response = await client.get("/api/v1/articles")
        assert response.status_code == 200
        assert response.headers.get(REQUEST_ID_HEADER)

    async def test_echoes_valid_client_id(self, client: AsyncClient) -> None:
        """客户端自带 ID 时必须原样回显，否则跨服务串联会断。"""
        response = await client.get(
            "/api/v1/articles", headers={REQUEST_ID_HEADER: "trace-abc-123"}
        )
        assert response.headers[REQUEST_ID_HEADER] == "trace-abc-123"

    async def test_rejects_malicious_id(self, client: AsyncClient) -> None:
        """带换行/超长的 ID 必须被替换：否则就是日志注入的口子。"""
        response = await client.get(
            "/api/v1/articles", headers={REQUEST_ID_HEADER: "bad\ninjected=1"}
        )
        request_id = response.headers[REQUEST_ID_HEADER]
        assert "injected" not in request_id
        assert "\n" not in request_id

    async def test_error_body_contains_request_id(self, client: AsyncClient) -> None:
        """报错响应里带 request_id，用户截图即可定位。"""
        response = await client.get("/api/v1/articles/99999999")
        assert response.status_code == 404
        body = response.json()
        assert body["request_id"]
        assert body["request_id"] == response.headers[REQUEST_ID_HEADER]

    async def test_validation_error_contains_request_id(self, client: AsyncClient) -> None:
        # 缺字段才会触发 422；空字符串能过 schema（没有 min_length），走的是 401
        response = await client.post("/api/v1/auth/login", json={"username": "admin"})
        assert response.status_code == 422
        assert response.json()["request_id"]

    def test_sanitize_rules(self) -> None:
        assert sanitize_request_id("abc-123") == "abc-123"
        assert sanitize_request_id(None) != "-"
        # 超长输入被丢弃（换成新的随机 ID），而不是截断后继续用
        assert len(sanitize_request_id("x" * 100)) <= 64


class TestJsonFormatter:
    def test_emits_single_line_json(self) -> None:
        record = logging.LogRecord(
            name="blog",
            level=logging.INFO,
            pathname=__file__,
            lineno=1,
            msg="请求完成",
            args=(),
            exc_info=None,
        )
        record.extra_fields = {"path": "/api/v1/articles", "status": 200}

        payload = json.loads(JsonFormatter().format(record))
        assert payload["level"] == "info"
        assert payload["msg"] == "请求完成"
        assert payload["path"] == "/api/v1/articles"
        assert payload["status"] == 200
        assert "ts" in payload
        # 结构化日志必须能整行解析，中文不转义便于人读
        assert "\n" not in JsonFormatter().format(record)

    def test_includes_exception_text(self) -> None:
        try:
            raise ValueError("boom")
        except ValueError:
            import sys

            record = logging.LogRecord(
                name="blog",
                level=logging.ERROR,
                pathname=__file__,
                lineno=1,
                msg="请求处理异常",
                args=(),
                exc_info=sys.exc_info(),
            )
        payload = json.loads(JsonFormatter().format(record))
        assert "boom" in payload["exc"]

    def test_request_id_default_when_outside_request(self) -> None:
        """不在请求上下文（脚本/定时任务）时用占位符，而不是抛错。"""
        assert get_request_id() == "-"
