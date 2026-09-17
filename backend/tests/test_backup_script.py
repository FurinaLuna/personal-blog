"""备份脚本的纯逻辑测试。

只测不碰真实数据库的部分：URL 解析与轮转删除。
轮转尤其值得测——**删错文件是备份脚本最危险的失败模式**：
它会在你最需要备份的时候，把备份删掉。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# scripts/ 不是包，按文件路径导入
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from backup_db import prune_old_backups, sqlite_path_from_url


class TestSqlitePathFromUrl:
    def test_relative_sqlite_url(self) -> None:
        path = sqlite_path_from_url("sqlite+aiosqlite:///./blog.db")
        assert path is not None
        assert path.name == "blog.db"

    def test_absolute_sqlite_url(self, tmp_path: Path) -> None:
        target = tmp_path / "custom.db"
        path = sqlite_path_from_url(f"sqlite+aiosqlite:///{target.as_posix()}")
        assert path == target

    def test_postgres_is_rejected(self) -> None:
        """非 SQLite 必须返回 None，让调用方给出「请用 pg_dump」的明确提示。

        静默产出一个错的备份，比明确拒绝要危险得多。
        """
        assert sqlite_path_from_url("postgresql+asyncpg://u:p@db:5432/blog") is None

    def test_memory_db_is_rejected(self) -> None:
        """内存库没有可备份的文件。"""
        assert sqlite_path_from_url("sqlite+aiosqlite:///:memory:") is None


class TestPruneOldBackups:
    def _make(self, directory: Path, names: list[str]) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        for name in names:
            (directory / name).write_text("x", encoding="utf-8")

    def test_keeps_newest_and_deletes_rest(self, tmp_path: Path) -> None:
        self._make(
            tmp_path,
            [
                "blog-20260101-000000.db",
                "blog-20260102-000000.db",
                "blog-20260103-000000.db",
                "blog-20260104-000000.db",
            ],
        )
        removed = prune_old_backups(tmp_path, keep=2)

        assert [p.name for p in removed] == [
            "blog-20260102-000000.db",
            "blog-20260101-000000.db",
        ]
        assert sorted(p.name for p in tmp_path.glob("*.db")) == [
            "blog-20260103-000000.db",
            "blog-20260104-000000.db",
        ]

    def test_noop_when_under_limit(self, tmp_path: Path) -> None:
        self._make(tmp_path, ["blog-20260101-000000.db"])
        assert prune_old_backups(tmp_path, keep=5) == []

    def test_ignores_unrelated_files(self, tmp_path: Path) -> None:
        """只认自己的命名模式，不能把用户放在同目录的其它文件删掉。"""
        self._make(tmp_path, ["blog-20260101-000000.db"])
        (tmp_path / "important-notes.txt").write_text("别删我", encoding="utf-8")
        (tmp_path / "blog.db").write_text("原始库，不是备份", encoding="utf-8")

        prune_old_backups(tmp_path, keep=0)

        assert (tmp_path / "important-notes.txt").exists()
        # "blog.db" 不匹配 blog-*.db（没有连字符 + 时间戳），必须保留
        assert (tmp_path / "blog.db").exists()

    def test_empty_directory(self, tmp_path: Path) -> None:
        assert prune_old_backups(tmp_path, keep=3) == []

    @pytest.mark.parametrize("keep", [1, 3])
    def test_never_deletes_more_than_needed(self, tmp_path: Path, keep: int) -> None:
        names = [f"blog-2026010{i}-000000.db" for i in range(1, 6)]
        self._make(tmp_path, names)
        prune_old_backups(tmp_path, keep=keep)
        assert len(list(tmp_path.glob("blog-*.db"))) == keep
