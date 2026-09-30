"""让测试能 import 同目录的解析器模块。

被测代码是「迁移工具」而不是应用代码，刻意不放进 ``backend/src/app``：
它不进生产镜像、不参与分层契约，也不该被业务模块 import。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
