#!/usr/bin/env python
"""SQLite 一致性备份。

## 为什么要用 VACUUM INTO 而不是复制文件

本项目开启了 WAL。直接 ``cp blog.db backup.db`` 拿到的是**不一致**的快照：
已提交但还在 ``-wal`` 里的事务不会跟过去，恢复出来会丢最近的写入，
甚至可能因为主库与 WAL 不匹配而打不开。``VACUUM INTO`` 由 SQLite 自己
在事务里导出，产物天然一致，且顺带做了碎片整理（体积通常更小）。

## 用法

    python scripts/backup_db.py                # 备份到 backend/backups/
    python scripts/backup_db.py --keep 30      # 只保留最近 30 份
    python scripts/backup_db.py --out D:/bak   # 指定目录

非 SQLite 的 ``DATABASE_URL``（PostgreSQL）会被明确拒绝并提示用 ``pg_dump``——
这个脚本不做能力之外的事，静默产出一个错的备份比不备份更危险。
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

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


def prune_old_backups(directory: Path, keep: int) -> list[Path]:
    """只保留最新的 ``keep`` 份备份，返回被删掉的列表。

    没有轮转的备份脚本等于没有备份脚本：磁盘迟早被塞满，
    而磁盘满之后**下一次备份会失败**，那时你恰好没有可用的备份。
    """
    backups = sorted(directory.glob("blog-*.db"), key=lambda p: p.name, reverse=True)
    removed: list[Path] = []
    for stale in backups[keep:]:
        stale.unlink(missing_ok=True)
        removed.append(stale)
    return removed


def backup(*, out_dir: Path, keep: int) -> int:
    source = sqlite_path_from_url(settings.database_url)
    if source is None:
        print(
            f"这个脚本只处理 SQLite，当前 DATABASE_URL 是：{settings.database_url}\n"
            'PostgreSQL 请用：pg_dump -Fc -f backup.dump "$DATABASE_URL"',
            file=sys.stderr,
        )
        return 2

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
    print(f"✅ 已备份：{target}  （{size_mb:.2f} MB）")

    removed = prune_old_backups(out_dir, keep)
    if removed:
        print(f"🧹 已清理 {len(removed)} 份旧备份，保留最近 {keep} 份")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="SQLite 一致性备份（VACUUM INTO）")
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
    args = parser.parse_args()

    if args.keep < 1:
        print("--keep 必须大于 0", file=sys.stderr)
        return 2

    return backup(out_dir=args.out, keep=args.keep)


if __name__ == "__main__":
    sys.exit(main())
