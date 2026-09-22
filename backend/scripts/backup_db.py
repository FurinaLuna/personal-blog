#!/usr/bin/env python
"""数据库一致性备份（SQLite 与 PostgreSQL 都支持）。

## SQLite：为什么用 VACUUM INTO 而不是复制文件

本项目开启了 WAL。直接 ``cp blog.db backup.db`` 拿到的是**不一致**的快照：
已提交但还在 ``-wal`` 里的事务不会跟过去，恢复出来会丢最近的写入，
甚至可能因为主库与 WAL 不匹配而打不开。``VACUUM INTO`` 由 SQLite 自己
在事务里导出，产物天然一致，且顺带做了碎片整理（体积通常更小）。

## PostgreSQL：为什么现在必须支持

compose 里生产库就是 ``postgres:16``，而这个脚本此前明确拒绝 PG，只留一句
"请用 pg_dump" —— 于是**生产部署实际上没有任何备份路径**（pgdata 与
mediadata 两个卷都没有），而 SQLite 那条路径反倒做得挺完整。这是本末倒置：
越是生产库越需要备份。

两条实现路径，按可用性自动选：

1. 宿主有 ``pg_dump``（部署时把 postgresql-client 装进镜像/宿主）→ 直接调用；
2. 没有 ``pg_dump`` 但目标库跑在 Docker 里 → ``docker exec <容器> pg_dump``，
   把 stdout 写到本地文件（本项目 e2e_live 连 PG 用的就是同一招）。

两条路径都产出 **custom 格式**（``-Fc``）：支持并行恢复、可按表恢复、
自带校验。恢复用 ``pg_restore``，见 README「备份与恢复」。

## 用法

    python scripts/backup_db.py                      # 备份到 backend/backups/
    python scripts/backup_db.py --keep 30            # 只保留最近 30 份
    python scripts/backup_db.py --out D:/bak         # 指定目录
    python scripts/backup_db.py --database-url postgresql://blog:pw@127.0.0.1:5432/blog \
        --container personal-blog-db-1               # 指定 PG 与容器名

两条路径都不支持时**明确报错退出**，不会静默产出一个错的文件。
"""

from __future__ import annotations

import argparse
import os
import shutil
import sqlite3
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from urllib.parse import unquote, urlparse

# 中文 Windows 控制台默认是 GBK，而本脚本的输出里有 emoji 和中文。
# 不切换编码的话，备份本身成功了，却会在最后一句 print 上抛
# UnicodeEncodeError —— 用户看到的是「脚本报错」，于是合理地以为备份失败了。
# errors="replace" 保证即使某个字符真的编码不了，也只是一个问号，不会中断流程。
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

# 让脚本能 import app.config（与 alembic 一样，从 backend/ 目录解析）
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from app.config import BASE_DIR, settings  # noqa: E402

DEFAULT_KEEP = 14
DEFAULT_PG_CONTAINER = "personal-blog-db-1"

# 备份文件的两种后缀：SQLite 用 .db，PG 的 custom 格式惯例是 .dump
SQLITE_GLOB = "blog-*.db"
PG_GLOB = "blog-*.dump"


def sqlite_path_from_url(url: str) -> Path | None:
    """从 SQLAlchemy 的 DATABASE_URL 里取出 SQLite 文件路径。

    非 SQLite（或内存库）返回 None，由调用方给出明确提示。
    """
    prefix = "sqlite+aiosqlite:///"
    if not url.startswith(prefix):
        return None
    raw = url[len(prefix) :]
    if not raw or raw == ":memory:":
        return None
    path = Path(raw)
    return path if path.is_absolute() else (BASE_DIR / path).resolve()


@dataclass(frozen=True, slots=True)
class PostgresTarget:
    """pg_dump 需要的连接信息。"""

    user: str
    password: str
    host: str
    port: str
    dbname: str

    @property
    def dsn(self) -> str:
        """给错误提示用的、**不含密码**的连接串。"""
        return f"postgresql://{self.user}@{self.host}:{self.port}/{self.dbname}"


def parse_postgres_url(url: str) -> PostgresTarget | None:
    """解析 PostgreSQL 的 SQLAlchemy URL；非 PG / 缺库名时返回 None。

    密码要 URL 解码：``p@ss word`` 在 URL 里是 ``p%40ss%20word``，
    直接拿去当 ``PGPASSWORD`` 会认证失败，而报错信息只会说"认证失败"，
    很容易怀疑到别的地方。库名同理（常见于 ``/blog%2Dprod`` 这种写法）。
    """
    if not url.startswith(("postgresql", "postgres://")):
        return None
    parsed = urlparse(url.replace("postgresql+asyncpg://", "postgresql://", 1))
    dbname = unquote(parsed.path.lstrip("/"))
    if not dbname:
        return None
    return PostgresTarget(
        user=unquote(parsed.username or "postgres"),
        password=unquote(parsed.password or ""),
        host=parsed.hostname or "127.0.0.1",
        port=str(parsed.port or 5432),
        dbname=dbname,
    )


def pg_dump_local_argv(target: PostgresTarget) -> list[str]:
    """宿主上直接调用 pg_dump 的参数（密码走环境变量，不出现在命令行里）。"""
    return [
        "pg_dump",
        "-Fc",
        "-h",
        target.host,
        "-p",
        target.port,
        "-U",
        target.user,
        "-d",
        target.dbname,
    ]


def pg_dump_docker_argv(container: str, target: PostgresTarget) -> list[str]:
    """在容器里执行 pg_dump，结果从 stdout 取回。

    刻意不带 ``-h/-p``：容器内部连的是它自己的 5432，用宿主映射出来的端口
    反而会连不上（容器网络与宿主网络不是一回事）。
    """
    return [
        "docker",
        "exec",
        container,
        "pg_dump",
        "-Fc",
        "-U",
        target.user,
        "-d",
        target.dbname,
    ]


def prune_old_backups(directory: Path, keep: int) -> list[Path]:
    """只保留最新的 ``keep`` 份备份（两种后缀一起算），返回被删掉的列表。

    没有轮转的备份脚本等于没有备份脚本：磁盘迟早被塞满，
    而磁盘满之后**下一次备份会失败**，那时你恰好没有可用的备份。

    两种后缀一起排序：同一台机器上先备份 SQLite、后切到 PG 时，
    只认其中一种后缀会让另一种无限堆积。
    """
    backups = sorted(
        list(directory.glob(SQLITE_GLOB)) + list(directory.glob(PG_GLOB)),
        key=lambda p: p.name,
        reverse=True,
    )
    removed: list[Path] = []
    for stale in backups[keep:]:
        stale.unlink(missing_ok=True)
        removed.append(stale)
    return removed


def _report(target: Path, keep: int) -> None:
    out_dir = target.parent
    removed = prune_old_backups(out_dir, keep)
    if removed:
        print(f"🧹 已清理 {len(removed)} 份旧备份，保留最近 {keep} 份")


def backup_sqlite(*, source: Path, out_dir: Path, keep: int) -> int:
    if not source.exists():
        print(f"数据库文件不存在：{source}", file=sys.stderr)
        return 2

    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    target = out_dir / f"blog-{stamp}.db"

    # VACUUM INTO 要求目标文件不存在，同名（同一秒内跑两次）时先让路
    target.unlink(missing_ok=True)

    # 用普通 sqlite3 连接即可：VACUUM 需要的是文件级访问，与应用的异步引擎无关。
    # 目标路径用参数占位符拼不了（DDL/PRAGMA 类语句不支持），
    # 这里路径来自本机配置而非外部输入，且已做 str() 归一，无注入面。
    connection = sqlite3.connect(str(source))
    try:
        connection.execute(f"VACUUM INTO '{target.as_posix()}'")
    finally:
        connection.close()

    size_mb = target.stat().st_size / 1024 / 1024
    print(f"✅ 已备份（SQLite / VACUUM INTO）：{target}  （{size_mb:.2f} MB）")
    _report(target, keep)
    return 0


def backup_postgres(
    *, target: PostgresTarget, out_dir: Path, keep: int, container: str | None
) -> int:
    """PostgreSQL 备份：优先用宿主 pg_dump，其次 docker exec。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = out_dir / f"blog-{stamp}.dump"
    path.unlink(missing_ok=True)

    env = dict(os.environ)
    local_dumper = shutil.which("pg_dump")
    if local_dumper:
        argv = pg_dump_local_argv(target)
        # 密码走环境变量：写在命令行上会出现在 ps / 进程列表里
        env["PGPASSWORD"] = target.password
        how = f"宿主 pg_dump（{local_dumper}）"
    else:
        container = container or os.environ.get("PG_BACKUP_CONTAINER") or DEFAULT_PG_CONTAINER
        if not shutil.which("docker"):
            print(
                "既没有 pg_dump，也没有 docker，无法备份 PostgreSQL。\n"
                "  方案一：安装 postgresql-client（Debian: apt-get install postgresql-client）\n"
                "  方案二：让数据库跑在 Docker 里，并用 --container 指定容器名",
                file=sys.stderr,
            )
            return 2
        argv = pg_dump_docker_argv(container, target)
        how = f"docker exec（容器 {container}）"

    # 两条路径都让 pg_dump 把 custom 格式写到 stdout，由这里落盘：
    # 用同一段代码，行为一致；'wb' 不做换行转换，二进制不会坏。
    with path.open("wb") as handle:
        result = subprocess.run(argv, env=env, stdout=handle, check=False)

    if result.returncode != 0:
        path.unlink(missing_ok=True)
        print(
            f"pg_dump 失败（退出码 {result.returncode}），目标 {target.dsn}。\n"
            "未生成备份文件 —— 宁可什么都没有，也不要一个不完整的 dump。",
            file=sys.stderr,
        )
        return 2

    size_mb = path.stat().st_size / 1024 / 1024
    print(f"✅ 已备份（PostgreSQL / {how}）：{path}  （{size_mb:.2f} MB）")
    print(f"   恢复：pg_restore --clean --if-exists -d <目标库> {path.name}")
    _report(path, keep)
    return 0


def backup(
    *, out_dir: Path, keep: int, database_url: str | None = None, container: str | None = None
) -> int:
    url = database_url or settings.database_url

    sqlite_path = sqlite_path_from_url(url)
    if sqlite_path is not None:
        return backup_sqlite(source=sqlite_path, out_dir=out_dir, keep=keep)

    pg_target = parse_postgres_url(url)
    if pg_target is not None:
        return backup_postgres(target=pg_target, out_dir=out_dir, keep=keep, container=container)

    print(
        f"无法识别的 DATABASE_URL：{url}\n"
        "支持 sqlite+aiosqlite:/// 与 postgresql+asyncpg:// 两种。",
        file=sys.stderr,
    )
    return 2


def main() -> int:
    parser = argparse.ArgumentParser(description="数据库一致性备份（SQLite / PostgreSQL）")
    parser.add_argument(
        "--out",
        type=Path,
        default=BASE_DIR / "backups",
        help="备份输出目录（默认 backend/backups/）",
    )
    parser.add_argument(
        "--keep",
        type=int,
        default=DEFAULT_KEEP,
        help=f"保留最近多少份（默认 {DEFAULT_KEEP}）",
    )
    parser.add_argument(
        "--database-url",
        default=None,
        help="覆盖 DATABASE_URL（默认读环境 / backend/.env）",
    )
    parser.add_argument(
        "--container",
        default=None,
        help=f"没有 pg_dump 时用哪个容器执行（默认 {DEFAULT_PG_CONTAINER}）",
    )
    args = parser.parse_args()

    if args.keep < 1:
        print("--keep 必须大于 0", file=sys.stderr)
        return 2

    return backup(
        out_dir=args.out, keep=args.keep, database_url=args.database_url, container=args.container
    )


if __name__ == "__main__":
    sys.exit(main())
