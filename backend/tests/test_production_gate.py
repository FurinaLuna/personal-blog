"""生产启动门禁。

存在意义：``is_production`` 原本只用来决定要不要关掉 ``/docs``，
于是一个只改了 ``APP_ENV=production`` 的实例会带着**写在开源仓库里的
JWT 密钥和管理员口令**正常启动并对外服务。这里把那条路径钉死。
"""

from __future__ import annotations

import pytest

from app.config import (
    _DEFAULT_ADMIN_PASSWORD,
    _DEFAULT_JWT_SECRET,
    Settings,
)

# 一份「改好了」的生产配置，各用例在这基础上只破坏一项，
# 保证失败一定是被测的那一项引起的。
SAFE_PRODUCTION = {
    "app_env": "production",
    "debug": False,
    "jwt_secret_key": "a" * 64,
    "admin_password": "a-strong-password",
    "db_auto_create": False,
    "seed_demo_data": False,
}


def _settings(**overrides: object) -> Settings:
    """构造独立的 Settings，不读 .env、不碰全局单例。"""
    return Settings(_env_file=None, **{**SAFE_PRODUCTION, **overrides})  # type: ignore[arg-type]


class TestProductionGate:
    def test_safe_production_passes(self) -> None:
        assert _settings().check_production_safety() == []

    def test_non_production_is_never_blocked(self) -> None:
        """开发/测试环境用默认值是常态，不能被门禁误伤。"""
        for env in ("development", "testing", "local"):
            problems = _settings(
                app_env=env,
                jwt_secret_key=_DEFAULT_JWT_SECRET,
                admin_password=_DEFAULT_ADMIN_PASSWORD,
                db_auto_create=True,
                seed_demo_data=True,
                debug=True,
            ).check_production_safety()
            assert problems == [], f"{env} 不该被门禁拦下"

    def test_default_jwt_secret_blocked(self) -> None:
        """默认密钥人人可见——任何人都能伪造出合法的登录态。"""
        problems = _settings(jwt_secret_key=_DEFAULT_JWT_SECRET).check_production_safety()
        assert any("JWT_SECRET_KEY" in text for text in problems)

    def test_short_jwt_secret_blocked(self) -> None:
        problems = _settings(jwt_secret_key="too-short").check_production_safety()
        assert any("JWT_SECRET_KEY" in text for text in problems)

    def test_default_admin_password_blocked(self) -> None:
        problems = _settings(admin_password=_DEFAULT_ADMIN_PASSWORD).check_production_safety()
        assert any("ADMIN_PASSWORD" in text for text in problems)

    def test_db_auto_create_blocked(self) -> None:
        """生产开着它 = 一次模型误改就悄悄改了生产表结构。"""
        problems = _settings(db_auto_create=True).check_production_safety()
        assert any("DB_AUTO_CREATE" in text for text in problems)

    def test_seed_demo_data_blocked(self) -> None:
        problems = _settings(seed_demo_data=True).check_production_safety()
        assert any("SEED_DEMO_DATA" in text for text in problems)

    def test_debug_blocked(self) -> None:
        problems = _settings(debug=True).check_production_safety()
        assert any("DEBUG" in text for text in problems)

    def test_all_problems_reported_at_once(self) -> None:
        """一次报全：运维改一轮就该过，而不是修一个报一个。"""
        problems = _settings(
            jwt_secret_key=_DEFAULT_JWT_SECRET,
            admin_password=_DEFAULT_ADMIN_PASSWORD,
            db_auto_create=True,
            seed_demo_data=True,
            debug=True,
        ).check_production_safety()
        assert len(problems) == 5


class TestStartupEnforcement:
    def test_create_app_refuses_insecure_production(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """门禁必须真的挂在启动路径上，而不是一个没人调用的方法。"""
        from app import main

        monkeypatch.setattr(main.settings, "app_env", "production")
        monkeypatch.setattr(main.settings, "jwt_secret_key", _DEFAULT_JWT_SECRET)
        monkeypatch.setattr(main.settings, "admin_password", "a-strong-password")
        monkeypatch.setattr(main.settings, "db_auto_create", False)
        monkeypatch.setattr(main.settings, "seed_demo_data", False)
        monkeypatch.setattr(main.settings, "debug", False)

        with pytest.raises(RuntimeError, match="拒绝启动"):
            main.create_app()
