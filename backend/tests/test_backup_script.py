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

from backup_db import (
    PostgresTarget,
    parse_postgres_url,
    pg_dump_docker_argv,
    pg_dump_local_argv,
    prune_old_backups,
    sqlite_path_from_url,
)


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

    def test_counts_both_sqlite_and_postgres_backups(self, tmp_path: Path) -> None:
        """两种后缀一起参与轮转。

        只认一种后缀的话，另一种会无限堆积——而这正是"从 SQLite 切到 PG"
        之后最容易发生的事（旧备份没人管，新备份又在加）。
        """
        self._make(
            tmp_path,
            [
                "blog-20260101-000000.db",
                "blog-20260102-000000.dump",
                "blog-20260103-000000.dump",
            ],
        )
        removed = prune_old_backups(tmp_path, keep=2)
        assert [p.name for p in removed] == ["blog-20260101-000000.db"]


class TestPostgresUrlParsing:
    """PG 备份的连接信息解析。

    这里错一个字符，症状是"认证失败"或"库不存在"——两个都容易让人往
    错误的方向排查（去查密码、去查库名），所以逐个字段钉住。
    """

    def test_asyncpg_url(self) -> None:
        target = parse_postgres_url("postgresql+asyncpg://blog:secret@db:5432/blog")
        assert target is not None
        assert (target.user, target.password, target.host, target.port, target.dbname) == (
            "blog",
            "secret",
            "db",
            "5432",
            "blog",
        )

    def test_defaults_port_and_host(self) -> None:
        target = parse_postgres_url("postgresql://blog@localhost/blog")
        assert target is not None
        assert target.port == "5432"
        assert target.host == "localhost"

    def test_percent_encoded_password_is_decoded(self) -> None:
        """URL 里的 p%40ss%20word 必须还原成 p@ss word 再交给 pg_dump。"""
        target = parse_postgres_url("postgresql+asyncpg://blog:p%40ss%20word@db:5432/blog")
        assert target is not None
        assert target.password == "p@ss word"

    def test_dsn_hides_password(self) -> None:
        """给错误提示用的连接串不能带密码（它会进日志）。"""
        target = parse_postgres_url("postgresql+asyncpg://blog:topsecret@db:5432/blog")
        assert target is not None
        assert "topsecret" not in target.dsn
        assert target.dsn == "postgresql://blog@db:5432/blog"

    def test_sqlite_url_is_not_postgres(self) -> None:
        assert parse_postgres_url("sqlite+aiosqlite:///./blog.db") is None

    def test_missing_database_name_is_rejected(self) -> None:
        """没有库名的 URL 无法备份，必须返回 None 而不是猜一个默认库。"""
        assert parse_postgres_url("postgresql+asyncpg://blog:pw@db:5432") is None


class TestPgDumpArgv:
    def _target(self) -> PostgresTarget:
        return PostgresTarget(
            user="blog", password="pw", host="127.0.0.1", port="5432", dbname="blog"
        )

    def test_local_argv_uses_custom_format(self) -> None:
        argv = pg_dump_local_argv(self._target())
        assert argv[0] == "pg_dump"
        # -Fc 是刻意的：custom 格式支持并行恢复与按表恢复，纯文本做不到
        assert "-Fc" in argv
        assert argv[-2:] == ["-d", "blog"]
        # 密码**不能**出现在命令行里（ps 能看到）
        assert "pw" not in argv

    def test_docker_argv_does_not_pass_host_or_port(self) -> None:
        """容器内连的是它自己的 5432，带上宿主端口反而连不上。"""
        argv = pg_dump_docker_argv("personal-blog-db-1", self._target())
        assert argv[:3] == ["docker", "exec", "personal-blog-db-1"]
        assert "-h" not in argv
        assert "-p" not in argv
        assert "-Fc" in argv
