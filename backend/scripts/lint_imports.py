#!/usr/bin/env python
"""跨平台的 ``lint-imports`` 包装。

## 为什么需要它

``.importlinter`` 里的契约名与注释是中文的，而 import-linter 用
**系统默认编码**去读这个文件。在中文 Windows 上默认编码是 GBK，
于是直接跑 ``lint-imports`` 会在解析阶段就炸：

    'gbk' codec can't decode byte 0xaf in position 60: illegal multibyte sequence

退出码 1，看起来像"分层契约被破坏了"，实际只是文件读不出来。
CI 跑在 Ubuntu（默认 UTF-8）上一切正常，所以这个问题只在本地 Windows 开发时
出现——而 Makefile 与 README 都明确支持 Windows，``make check`` 在那里必挂。

## 做法

不能只靠 ``PYTHONUTF8=1`` 环境变量：那要求每个调用方都记得加，
而直接敲 ``lint-imports`` 的人一定不会加。这里检测 UTF-8 模式，
没开就用子进程重新拉起自己并**转发退出码**——
重新执行之后解释器在启动阶段就启用 UTF-8，读配置文件不再依赖系统 locale。

用 ``subprocess`` 而不是 ``os.execv``：Windows 上 execv 会丢掉子进程的退出码
（实测：子进程 exit(1)，父 shell 收到的是 0），而退出码正是这个门禁的全部意义。

用法（与 lint-imports 完全一致）::

    python scripts/lint_imports.py
"""

from __future__ import annotations

import os
import subprocess
import sys


def main() -> int:
    # sys.flags.utf8_mode 由解释器在启动时根据 -X utf8 / PYTHONUTF8 决定，
    # 进程内改 os.environ 是无效的，必须重新拉起一个进程。
    if not sys.flags.utf8_mode:
        completed = subprocess.run(
            [sys.executable, "-X", "utf8", os.path.abspath(__file__), *sys.argv[1:]],
            check=False,
        )
        return completed.returncode

    from importlinter.cli import lint_imports

    # 注意：lint_imports() 是**返回**状态码，不是 sys.exit——
    # 真正做 sys.exit 的是它外面的 click 命令包装。直接调用却忽略返回值的话，
    # 契约被破坏时本脚本仍会以 0 退出，等于把 make lint / CI 的门禁悄悄关掉。
    return int(lint_imports())


if __name__ == "__main__":
    sys.exit(main())
