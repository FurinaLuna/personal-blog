"""数据库完整性约束冲突 → 409。

背景：本项目到处是「先查重、再插入」（用户名 / 邮箱 / slug / 分类名 / 标签名）。
两步之间永远存在竞态窗口：两个请求同时通过查重，其中一个必然在 INSERT 时撞唯一键。
**这不是 bug，是并发下的正常结果**，正确的回答是 409「已存在」而不是
500「服务器开小差了」——后者会让用户以为是自己操作错了或者站点坏了。

用例通过打桩把「查重」这一步短路，从而确定性地复现那个竞态窗口
（真起两个并发请求来撞唯一键既慢又不稳定）。
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from app.repositories import TagRepository


class TestIntegrityErrorTranslation:
    async def test_unique_violation_becomes_409_not_500(
        self,
        client: AsyncClient,
        author_headers: dict[str, str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """绕开查重后插入同名标签，应当得到 409 + 可读文案，而不是 500。"""
        payload = {"name": "并发敏感标签"}

        created = await client.post("/api/v1/tags", json=payload, headers=author_headers)
        assert created.status_code == 201, created.text

        # 模拟「另一个请求在本次查重之后、插入之前完成了同名插入」：
        # 让查重永远看不到已存在的行，于是 INSERT 必然撞唯一键
        async def always_missing(_self: object, _name: str):
            return None

        monkeypatch.setattr(TagRepository, "get_by_name", always_missing)

        response = await client.post("/api/v1/tags", json=payload, headers=author_headers)

        assert response.status_code == 409, response.text
        body = response.json()
        assert body["code"] == "conflict"
        # 必须带 request_id：用户报障时靠它在服务端日志里定位这次请求
        assert body["request_id"]
        # 文案要可读，且不能泄露表名 / 列名这类内部结构
        assert "tags" not in body["detail"]
        assert "UNIQUE" not in body["detail"].upper()

    async def test_handler_does_not_break_normal_conflict_path(
        self, client: AsyncClient, author_headers: dict[str, str]
    ) -> None:
        """正常的查重路径仍然由 ConflictError 给出 409，行为不变。"""
        payload = {"name": "普通重复标签"}
        assert (
            await client.post("/api/v1/tags", json=payload, headers=author_headers)
        ).status_code == 201
        duplicate = await client.post("/api/v1/tags", json=payload, headers=author_headers)
        assert duplicate.status_code == 409
        assert duplicate.json()["code"] == "conflict"

    async def test_other_requests_still_work_after_a_conflict(
        self, client: AsyncClient, author_headers: dict[str, str]
    ) -> None:
        """冲突后事务必须干净回滚：后续请求不能被污染。"""
        payload = {"name": "回滚验证标签"}
        await client.post("/api/v1/tags", json=payload, headers=author_headers)
        await client.post("/api/v1/tags", json=payload, headers=author_headers)

        # 换一个没冲突的名字，应当正常成功
        ok = await client.post(
            "/api/v1/tags", json={"name": "回滚验证标签-2"}, headers=author_headers
        )
        assert ok.status_code == 201, ok.text
