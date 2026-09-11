"""临时调试脚本：复现 POST /articles 的 500。用完即删。"""

import asyncio
import os
import tempfile
import traceback
from pathlib import Path

DB = Path(tempfile.gettempdir()) / "dbg.db"
DB.unlink(missing_ok=True)
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{DB.as_posix()}"

from httpx import ASGITransport, AsyncClient  # noqa: E402

from app.db.base import Base  # noqa: E402
from app.db.seed import ensure_seed  # noqa: E402
from app.db.session import async_session_factory, engine  # noqa: E402
from app.main import app  # noqa: E402


async def main() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    async with async_session_factory() as session:
        await ensure_seed(session)
        await session.commit()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as client:
        resp = await client.post(
            "/api/v1/auth/login", json={"username": "admin", "password": "admin123456"}
        )
        admin_headers = {"Authorization": "Bearer " + resp.json()["access_token"]}
        resp = await client.post(
            "/api/v1/auth/users",
            json={
                "username": "writer",
                "email": "w@e.com",
                "password": "author123456",
                "nickname": "x",
                "role": "author",
            },
            headers=admin_headers,
        )
        print("create user:", resp.status_code)
        resp = await client.post(
            "/api/v1/auth/login", json={"username": "writer", "password": "author123456"}
        )
        writer_headers = {"Authorization": "Bearer " + resp.json()["access_token"]}

        for label, headers in (("writer", writer_headers), ("admin", admin_headers)):
            try:
                resp = await client.post(
                    "/api/v1/articles",
                    json={
                        "title": f"t-{label}",
                        "content_md": "body",
                        "status": "published",
                        "tags": ["x"],
                    },
                    headers=headers,
                )
                print(f"POST as {label}: {resp.status_code} {resp.text[:200]}")
            except Exception:
                lines = traceback.format_exc().splitlines()
                print(f"=== POST as {label} raised ===")
                print("\n".join(line for line in lines if "app\\" in line or "app/" in line))
                print("--- tail ---")
                print("\n".join(lines[-10:]))


asyncio.run(main())
