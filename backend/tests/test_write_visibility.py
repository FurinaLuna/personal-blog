"""写路由提交时机回归（read-your-writes 不变量）。

背景：FastAPI 对 yield 依赖的收尾代码在响应发送**之后**才运行。若事务提交
只存在于 ``get_session`` 的 teardown，真实部署里浏览器连接池的下一个请求
会在提交落地前开启读事务，偶发读到旧快照（双连接实测：create 后读不到 3/50、
delete 后仍可读 2/50）。修复后由写路由在返回前显式 commit，
本文件用「请求返回后立即用独立会话读」锁住这一不变量。

说明：ASGITransport 会在进程内跑完整 ASGI 栈（含依赖 teardown）才把响应
交给客户端，因此这些用例在旧实现下大概率也能通过——它们主要防的是
「有人把写路由里的显式 commit 误删」这类回归；真正的提交时序回归由
tools/ 下的真实浏览器与跨连接验证覆盖。
"""

from __future__ import annotations

from typing import Any

from httpx import AsyncClient

from app.db.session import async_session_factory
from app.models import Article, Comment, User, UserRole
from tests.factories import make_article_payload, make_user_payload


async def _fresh_get(model: type, id_: int) -> Any | None:
    """用一个全新的会话读取，模拟「另一条连接上的下一个请求」看到的库状态。"""
    async with async_session_factory() as session:
        return await session.get(model, id_)


class TestWriteVisibility:
    async def test_created_article_visible_in_fresh_session(
        self, client: AsyncClient, author_headers: dict[str, str]
    ) -> None:
        """创建文章的 201 一返回，新会话必须立刻能读到该行。"""
        response = await client.post(
            "/api/v1/articles", json=make_article_payload(), headers=author_headers
        )
        assert response.status_code == 201, response.text
        article = await _fresh_get(Article, response.json()["id"])
        assert article is not None, "写路由返回后数据尚未提交（read-your-writes 被破坏）"

    async def test_deleted_article_gone_in_fresh_session(
        self, client: AsyncClient, author_headers: dict[str, str], published_article: dict[str, Any]
    ) -> None:
        """删除文章的 204 一返回，新会话必须立刻读不到该行。"""
        article_id = int(published_article["id"])
        response = await client.delete(f"/api/v1/articles/{article_id}", headers=author_headers)
        assert response.status_code == 204, response.text
        assert await _fresh_get(Article, article_id) is None, "删除提交滞后：新会话仍能读到已删文章"

    async def test_created_comment_visible_in_fresh_session(
        self, client: AsyncClient, published_article: dict[str, Any]
    ) -> None:
        """匿名评论 201 一返回，新会话必须立刻能读到（含待审核状态）。"""
        response = await client.post(
            f"/api/v1/comments/article/{published_article['id']}",
            json={"author_name": "路人甲", "content": "写后立读回归用例"},
        )
        assert response.status_code == 201, response.text
        comment = await _fresh_get(Comment, response.json()["id"])
        assert comment is not None
        assert comment.is_approved is False

    async def test_created_user_ready_for_login(
        self, client: AsyncClient, admin_headers: dict[str, str]
    ) -> None:
        """站长建号的 201 一返回，用户行必须已提交——登录路径依赖这一点。"""
        payload = make_user_payload(username="visible_user")
        response = await client.post("/api/v1/auth/users", json=payload, headers=admin_headers)
        assert response.status_code == 201, response.text
        user = await _fresh_get(User, response.json()["id"])
        assert user is not None
        assert user.role is UserRole.AUTHOR
