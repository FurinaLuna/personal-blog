"""``.env.example`` 与 ``config.py`` 的同步。

## 防的是什么

``config.py`` 是配置项的**唯一真相**，``.env.example`` 是运维唯一会读的文档。
两者漂移的后果不是"文档旧了"这么轻：

- ``ALLOWED_IMAGE_TYPES`` / ``ALLOWED_FILE_TYPES`` 是**上传白名单**，
  也是「默认禁 SVG」这条安全决定的落点。运维不知道它们可以配，
  就既不会去收紧也不会去确认；
- 反过来，``.env.example`` 里出现代码里已删掉的变量，会让人以为配置生效了，
  实际被 ``extra="ignore"`` 静默丢弃 —— 这类"配了但没用"最难查。

所以这条约定要机器检查。真正新增配置项时，这个测试会失败并告诉你加了哪个。
"""

from __future__ import annotations

import re
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
CONFIG = BACKEND_DIR / "src" / "app" / "config.py"
ENV_EXAMPLE = BACKEND_DIR / ".env.example"

# Settings 类字段：缩进 4 空格 + 小写名 + 冒号
_FIELD_RE = re.compile(r"^    ([a-z][a-z0-9_]*):\s", re.MULTILINE)
# .env.example：行首（可带 # 注释前缀）的大写下划线变量名
_KEY_RE = re.compile(r"^#?\s*([A-Z][A-Z0-9_]*)=", re.MULTILINE)


def _config_fields() -> set[str]:
    return {name.upper() for name in _FIELD_RE.findall(CONFIG.read_text(encoding="utf-8"))}


def _documented_keys() -> set[str]:
    return set(_KEY_RE.findall(ENV_EXAMPLE.read_text(encoding="utf-8")))


class TestEnvExampleCoverage:
    def test_every_setting_is_documented(self) -> None:
        """每个配置项都要在 .env.example 里出现。

        新增配置项却忘了写文档时，这个用例会失败并直接点名是哪一个。
        """
        missing = sorted(_config_fields() - _documented_keys())
        assert not missing, (
            "这些配置项没有写进 backend/.env.example：\n  "
            + "\n  ".join(missing)
            + "\n（它们是运维唯一会读的文档；安全相关的项漏了后果更重）"
        )

    def test_no_dangling_keys(self) -> None:
        """反向：.env.example 里不该有代码里不存在的变量。

        写了但代码不认的变量会被 pydantic-settings 的 extra="ignore" 静默丢弃，
        表现为"我明明配了却不生效"，极难排查。
        """
        dangling = sorted(_documented_keys() - _config_fields())
        assert not dangling, (
            "这些变量写在 backend/.env.example 里，但 config.py 里没有对应字段"
            "（配了也不会生效，会被静默忽略）：\n  " + "\n  ".join(dangling)
        )
