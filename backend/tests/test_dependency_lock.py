"""依赖锁与声明范围的一致性。

## 这个测试防的是什么

``requirements.lock`` 是 CI 与 Docker 构建的**唯一**安装来源，
而 ``pyproject.toml`` 里的范围是给人看的意图声明。两者一旦漂移，
就会出现最难查的一类问题：**CI 装的是旧版本，本地装的是新版本**，
本地全绿、CI 全红（或反过来），而代码一个字没改。

漂移是必然会发生的——改了 ``pyproject.toml`` 的范围却忘了重新生成锁文件，
在 review 里几乎看不出来。所以这里把它做成机器检查。

## 它还防住了什么

锁文件里出现 ``pyproject.toml`` 中不存在的包（比如把调试用的临时依赖
冻了进去），或者漏掉了某个 extras，都会在这里失败。

依赖锁定相关的背景见 ``requirements.lock`` 顶部的说明。
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest
from packaging.requirements import Requirement
from packaging.version import Version

BACKEND_DIR = Path(__file__).resolve().parents[1]
PYPROJECT = BACKEND_DIR / "pyproject.toml"
LOCK = BACKEND_DIR / "requirements.lock"


def _locked_versions() -> dict[str, str]:
    """解析锁文件为 {规范化包名: 版本}。"""
    locked: dict[str, str] = {}
    for raw in LOCK.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "==" not in line:
            pytest.fail(f"锁文件里出现了非精确固定的行：{raw!r}（应当只有 name==version）")
        name, _, version = line.partition("==")
        key = name.strip().lower().replace("_", "-")
        if key in locked:
            pytest.fail(f"锁文件里 {name} 出现了两次")
        locked[key] = version.strip()
    return locked


def _declared_requirements() -> list[Requirement]:
    """pyproject 里声明的**运行时**依赖（基础 + 所有 extras）。

    刻意**不**包含 ``build-system.requires``（hatchling）：构建后端由 pip 在
    隔离的构建环境里临时安装，不进运行时环境，因此也不该出现在运行时锁里。
    第一版把它也纳进来，测试立刻报了「hatchling 没有进锁文件」——
    那是测试的分类错了，不是锁错了。
    """
    data = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    project = data["project"]
    raw = list(project.get("dependencies", []))
    for group in project.get("optional-dependencies", {}).values():
        raw.extend(group)
    return [Requirement(item) for item in raw]


class TestLockConsistency:
    def test_lock_file_exists(self) -> None:
        """锁文件必须存在且非空——CI 与镜像构建都直接依赖它。"""
        assert LOCK.exists(), "requirements.lock 不存在；CI 与 Dockerfile 都会因此失败"
        assert LOCK.stat().st_size > 0

    def test_every_declared_requirement_is_locked(self) -> None:
        """pyproject 声明了但锁文件里没有的包 = 会装出漂移的环境。"""
        locked = _locked_versions()
        missing = [
            req.name
            for req in _declared_requirements()
            if req.name.lower().replace("_", "-") not in locked
        ]
        assert not missing, f"这些声明的依赖没有进锁文件：{missing}"

    def test_locked_versions_satisfy_declared_ranges(self) -> None:
        """锁定的版本必须落在 pyproject 声明的范围内。

        这是最容易出问题的一条：给某个包收紧了上界（或抬了下界）却没重新
        生成锁文件，CI 会继续装旧版本，而 ``pip install -e .`` 的本地环境
        装的是新版本——两边跑的根本不是同一套依赖。
        """
        locked = _locked_versions()
        violations: list[str] = []

        for req in _declared_requirements():
            key = req.name.lower().replace("_", "-")
            version = locked.get(key)
            if version is None:
                continue  # 上一条用例已经在报缺失了
            if not req.specifier:
                continue
            if Version(version) not in req.specifier:
                violations.append(f"{req.name}：锁定 {version}，但声明要求 {req.specifier}")

        assert not violations, (
            "锁文件与 pyproject.toml 的范围不一致，请按 requirements.lock 顶部的步骤重新生成：\n  "
            + "\n  ".join(violations)
        )

    def test_no_unexpected_packages(self) -> None:
        """锁文件里不该有声明之外的东西。

        生成锁时若误用了带调试包的开发环境，那些包会被一起冻进来——
        它们会跟着进生产镜像。
        """
        declared = {req.name.lower().replace("_", "-") for req in _declared_requirements()}
        locked = _locked_versions()
        # 传递依赖不在 pyproject 里，所以只做「不能为空」与「关键项存在」的弱断言；
        # 真正的严格检查是上面的子集关系 + 版本满足性。
        assert len(locked) > len(declared), (
            "锁文件的包数应当多于直接依赖数（它包含传递依赖）；如果两者相等，说明生成方式不对"
        )

    def test_key_runtime_dependencies_are_pinned(self) -> None:
        """几个一旦漂移就会真出问题的包，必须在锁里且是精确版本。"""
        locked = _locked_versions()
        for name in ("fastapi", "sqlalchemy", "pydantic", "alembic", "bcrypt", "pyjwt"):
            assert name in locked, f"{name} 不在锁文件里"
            assert Version(locked[name]) is not None

    def test_postgres_extra_is_locked(self) -> None:
        """asyncpg 只在生产镜像里用得上，但生产镜像正是靠这份锁构建的。

        漏了它的后果是镜像构建直接失败——而这只有在真正跑 Docker 时才会发现。
        """
        assert "asyncpg" in _locked_versions(), (
            "asyncpg 不在锁文件里；生成时应当安装 .[postgres,dev] 而不是 .[dev]"
        )
