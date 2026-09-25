"""就绪探针：``/ready`` 必须把"检索是否真的走了索引"暴露出来。

存在意义：PG 分支依赖 ``CREATE EXTENSION pg_trgm``，而很多托管 PostgreSQL
**默认不给这个权限**。建不出来时搜索会退化成三列 ``ILIKE`` 全表扫——
结果依然正确，只是从毫秒级掉到全表扫，并且：

- 启动日志里只有一行 warning，会被迁移/建表的输出盖过去；
- 没有任何接口能看出这件事，"搜索变慢"只能靠用户抱怨才能发现。

所以把状态挂到就绪探针上：它不改变就绪判定（索引是增强项，为此摘流量
等于"搜索慢 → 整站下线"，比问题本身严重），但让退化**可观测**。
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from app.db.fulltext import get_search_index_status, set_search_index_status


@pytest.fixture(autouse=True)
def _restore_status() -> object:
    """用例结束后把进程级状态恢复成"未探测"，避免污染其它用例。"""
    yield
    set_search_index_status("", "")


class TestSearchIndexStatusPayload:
    """状态对象本身：取值与序列化都在这里，/ready 只负责拼进去。"""

    @pytest.mark.parametrize(
        ("kind", "expected"),
        [("fts5", True), ("pg_trgm", True), ("", False)],
    )
    def test_accelerated_follows_kind(self, kind: str, expected: bool) -> None:
        set_search_index_status(kind)
        assert get_search_index_status().accelerated is expected
        assert get_search_index_status().kind == kind

    def test_payload_always_has_the_two_core_fields(self) -> None:
        set_search_index_status("", "")
        payload = get_search_index_status().as_payload()
        assert payload["search_index"] == "none"
        assert payload["search_accelerated"] is False
        # 没有原因时不该出现空字段，免得探针输出里挂着一堆 null
        assert "search_detail" not in payload

    def test_detail_is_carried_when_degraded(self) -> None:
        """退化时必须带着原因：只有 "none" 的话运维仍然不知道该去申请什么权限。"""
        set_search_index_status("", "PG 没有 CREATE EXTENSION 权限")
        payload = get_search_index_status().as_payload()
        assert payload["search_index"] == "none"
        assert payload["search_accelerated"] is False
        assert "CREATE EXTENSION" in str(payload["search_detail"])


class TestReadyProbe:
    async def test_ready_exposes_search_index_state(self, client: AsyncClient) -> None:
        set_search_index_status("fts5")
        response = await client.get("/ready")
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "ready"
        assert body["database"] == "up"
        assert body["search_index"] == "fts5"
        assert body["search_accelerated"] is True

    async def test_degraded_search_still_ready(self, client: AsyncClient) -> None:
        """索引缺失不能让实例被摘掉。

        这是本条改动里最容易做错的取舍：把"搜索没走索引"报成 503，
        编排系统就会把实例摘出流量——而它唯一的影响只是搜索慢一点。
        增强功能的缺失要**可观测**，不要**可致命**。
        """
        set_search_index_status("", "PG 没有 CREATE EXTENSION 权限")
        response = await client.get("/ready")
        assert response.status_code == 200, "索引缺失不该让探针失败"
        body = response.json()
        assert body["search_accelerated"] is False
        assert "CREATE EXTENSION" in body["search_detail"]

    async def test_health_stays_minimal(self, client: AsyncClient) -> None:
        """/health 是存活探针，不该被这些细节污染。"""
        body = (await client.get("/health")).json()
        assert body["status"] == "ok"
        assert "search_index" not in body
