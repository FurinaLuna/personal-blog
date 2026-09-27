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
from sqlalchemy.exc import IntegrityError

from app.main import _is_unique_violation
from app.repositories import TagRepository


class TestUniqueViolationDiscriminator:
    """判据本身：只有唯一键冲突才该被答成 409。

    为什么值得单测：这个判断决定响应是 409 还是 500，两者在排障语义上完全不同
    （"名字重复了" vs "服务端有缺陷"）。而且它的判据是**方言相关**的——
    SQLite 只有文案、PostgreSQL 走 SQLSTATE——两条路都得覆盖，
    否则会在另一种方言上静默退化成"一律 500"或"一律 409"。
    """

    class _FakeOrig(Exception):
        """模拟 DBAPI 层原始异常；PostgreSQL 会把 sqlstate 挂在它上面。"""

        def __init__(self, message: str, sqlstate: str | None = None) -> None:
            super().__init__(message)
            if sqlstate is not None:
                self.sqlstate = sqlstate

    @staticmethod
    def _wrap(orig: Exception) -> IntegrityError:
        return IntegrityError("INSERT INTO t VALUES (?)", {}, orig)

    @pytest.mark.parametrize(
        ("message", "sqlstate", "expected"),
        [
            # SQLite：没有结构化错误码，只能看文案
            ("UNIQUE constraint failed: tags.name", None, True),
            ("NOT NULL constraint failed: articles.title", None, False),
            ("FOREIGN KEY constraint failed", None, False),
            ("CHECK constraint failed: view_count >= 0", None, False),
            # PostgreSQL：SQLSTATE 是标准值，比文案可靠
            ('duplicate key value violates unique constraint "uq_tags_name"', "23505", True),
            ("insert or update on table violates foreign key constraint", "23503", False),
            ("null value in column violates not-null constraint", "23502", False),
        ],
    )
    def test_classification(self, message: str, sqlstate: str | None, expected: bool) -> None:
        assert _is_unique_violation(self._wrap(self._FakeOrig(message, sqlstate))) is expected

    def test_missing_orig_is_not_treated_as_unique(self) -> None:
        """拿不到原始异常时**不要**猜成唯一键冲突：宁可报 500 也不要谎报 409。"""
        assert _is_unique_violation(IntegrityError("stmt", {}, Exception())) is False


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
