"""文件存储抽象。

当前只有本地磁盘实现；将来要换 OSS / COS / S3，只需要新增一个实现
``StorageBackend`` 协议的类，服务层代码一行都不用改。
"""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path
from typing import Protocol

from app.config import settings


class StorageBackend(Protocol):
    """存储后端协议。"""

    def url_for(self, *, subdir: str, stored_name: str) -> str:
        """返回可被浏览器直接访问的 URL。"""
        ...

    async def save(self, *, subdir: str, stored_name: str, data: bytes) -> None:
        """写入文件，重名则覆盖。"""
        ...

    async def delete(self, *, subdir: str, stored_name: str) -> bool:
        """删除文件，返回是否真的删掉了。"""
        ...

    async def exists(self, *, subdir: str, stored_name: str) -> bool: ...


class LocalStorage:
    """本地磁盘存储。

    文件按 ``storage/<subdir>/<stored_name>`` 落盘，URL 由 ``/media/<subdir>/<stored_name>``
    对外暴露（由 main.py 挂载 StaticFiles）。
    """

    def __init__(self, root: Path, url_prefix: str) -> None:
        self.root = root
        self.url_prefix = url_prefix.rstrip("/")

    def _path(self, subdir: str, stored_name: str) -> Path:
        # 二次防穿越：stored_name 由服务端生成，但这里仍然拦住任何 .. 或绝对路径
        safe_name = Path(stored_name).name
        if not safe_name or safe_name in {".", ".."}:
            raise ValueError(f"非法文件名：{stored_name!r}")
        return self.root / subdir / safe_name

    def url_for(self, *, subdir: str, stored_name: str) -> str:
        return f"{self.url_prefix}/{subdir}/{Path(stored_name).name}"

    async def save(self, *, subdir: str, stored_name: str, data: bytes) -> None:
        path = self._path(subdir, stored_name)
        # 同步磁盘 IO 丢到线程池，避免阻塞事件循环
        await asyncio.to_thread(self._write, path, data)

    @staticmethod
    def _write(path: Path, data: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    async def delete(self, *, subdir: str, stored_name: str) -> bool:
        path = self._path(subdir, stored_name)
        return await asyncio.to_thread(self._unlink, path)

    @staticmethod
    def _unlink(path: Path) -> bool:
        if path.is_file():
            path.unlink()
            return True
        return False

    async def exists(self, *, subdir: str, stored_name: str) -> bool:
        return await asyncio.to_thread(self._path(subdir, stored_name).is_file)

    def ensure_dirs(self) -> None:
        """启动时把目录建好，省得第一次上传才报错。"""
        for sub in ("uploads", "avatars"):
            (self.root / sub).mkdir(parents=True, exist_ok=True)

    def move_tree(self, target: Path) -> None:  # pragma: no cover - 迁移工具用
        """整体搬迁存储目录（运维场景）。"""
        shutil.move(str(self.root), str(target))


storage = LocalStorage(settings.storage_dir, settings.media_url_prefix)
