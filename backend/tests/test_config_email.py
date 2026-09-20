"""``ADMIN_EMAIL`` 必须在**配置层**被校验。

## 防的是什么

``admin_email`` 的去向是 seed：站点首次启动时它被原样写进 ``users.email`` 与
``site_profile.email``，**写入路径没有任何校验**（Pydantic 只管 schema 层）。
要等到读取时，``UserRead`` / ``SiteProfileRead`` 里的 ``EmailStr`` 才会把它拦下。

于是「一个填错的环境变量」的表现不是启动失败，而是：

- 服务正常起来，启动日志里没有任何告警；
- ``GET /site/profile``（关于页）与 ``GET /auth/me`` 一律 500；
- 前者让整页不可用，后者让站长登录后任何依赖 ``/auth/me`` 的页面都挂。

典型的触发方式是 RFC 保留域名（``admin@e2e.test``、``admin@localhost``、
``admin@example.invalid``）——看起来是个正常邮箱，但 email-validator 会拒绝。

把它写成 ``EmailStr`` 之后，非法值在**配置解析时**（也就是进程启动那一刻）
就失败，与 ``check_production_safety()`` 是同一个取舍：启动失败一定会被处理，
运行时 500 只会变成一条没人看的日志。
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.config import Settings


def _settings(**overrides: object) -> Settings:
    """独立的 Settings：不读 .env、不碰全局单例，断言只反映传入值。"""
    return Settings(_env_file=None, **{"app_env": "testing", **overrides})  # type: ignore[arg-type]


class TestAdminEmailIsValidatedAtLoadTime:
    def test_default_email_is_accepted(self) -> None:
        """硬约束：仓库里的默认值必须仍可启动。

        ``example.com`` 是 IANA 保留域名，但 email-validator 对**交付能力**
        不作判断（不做 DNS 查询），它是允许 example.com 的——默认值不能因为
        这次加固反而变成「开箱即崩」。
        """
        assert _settings().admin_email == "admin@example.com"

    @pytest.mark.parametrize(
        "bad_email",
        [
            "admin@e2e.test",  # RFC 6761 保留顶级域
            "admin@localhost",  # 没有点号、且是保留名
            "admin@example.invalid",  # RFC 2606 保留域
            "not-an-email",  # 连 @ 都没有
            "admin@",  # 空域名
            "@example.com",  # 空本地部分
        ],
    )
    def test_invalid_email_is_rejected(self, bad_email: str) -> None:
        """非法邮箱必须在构造 Settings 时就炸，而不是等到读取接口。"""
        with pytest.raises(ValidationError) as exc_info:
            _settings(admin_email=bad_email)

        fields = {tuple(str(part) for part in err["loc"]) for err in exc_info.value.errors()}
        assert ("admin_email",) in fields, f"{bad_email} 未命中 admin_email 字段的校验"

    def test_rejection_happens_before_anything_is_written(self) -> None:
        """失败点的位置才是这次修复的关键。

        字段类型错了（比如退化回 ``str``）时，下面这条断言会失败——
        因为它不会抛 ValidationError，而会一路静默走到 HTTP 层变成 500。
        """
        with pytest.raises(ValidationError, match="admin_email"):
            _settings(admin_email="admin@e2e.test")

    def test_valid_custom_email_is_accepted(self) -> None:
        assert _settings(admin_email="me@myblog.dev").admin_email == "me@myblog.dev"
