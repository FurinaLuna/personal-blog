"""生产启动门禁。

存在意义：``is_production`` 原本只用来决定要不要关掉 ``/docs``，
于是一个只改了 ``APP_ENV=production`` 的实例会带着**写在开源仓库里的
JWT 密钥和管理员口令**正常启动并对外服务。这里把那条路径钉死。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

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
    # 必须显式给一个非 SQLite 的地址：字段默认值是 SQLite（开发零依赖），
    # 而「SQLite 上生产」现在本身是一条门禁项。不写这一项，
    # 这个"安全配置"夹具其实是**不安全的**，会掩盖门禁本身的问题。
    "database_url": "postgresql+asyncpg://blog:blog@localhost:5432/blog",
}


def _settings(**overrides: object) -> Settings:
    """构造独立的 Settings，不读 .env、不碰全局单例。"""
    return Settings(_env_file=None, **{**SAFE_PRODUCTION, **overrides})  # type: ignore[arg-type]


class TestProductionGate:
    def test_safe_production_passes(self) -> None:
        assert _settings().check_production_safety() == []

    def test_non_production_is_never_blocked(self) -> None:
        """开发/测试环境用默认值是常态，不能被门禁误伤。

        注意这里只剩 development / testing：``local`` 这类自造值现在会被
        **拒绝启动**（见 TestAppEnvWhitelist）——因为"未知环境名"正是让
        整个门禁静默失效的那条路。
        """
        for env in ("development", "testing"):
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

    def test_wildcard_cors_blocked(self) -> None:
        """通配源 + allow_credentials=True = 任意站点都能带凭据读响应。

        这条此前完全没被门禁覆盖：CORS 中间件（main.py）开着
        ``allow_credentials=True``，靠人工 review 保证没人填 ``*`` 是不可靠的。
        """
        problems = _settings(cors_origins=["*"]).check_production_safety()
        assert any("CORS_ORIGINS" in text for text in problems)

    def test_empty_cors_blocked(self) -> None:
        """留空同样要拦：它会让前端请求全被浏览器拦掉，
        进而诱导运维"先改成 * 让它跑起来"——那正是上一条要防的形态。"""
        problems = _settings(cors_origins=[]).check_production_safety()
        assert any("CORS_ORIGINS" in text for text in problems)

    @pytest.mark.parametrize(
        "url",
        [
            "sqlite+aiosqlite:///./blog.db",
            "sqlite:///blog.db",
            "sqlite://",
        ],
    )
    def test_sqlite_on_production_blocked(self, url: str) -> None:
        """SQLite 上生产此前是能通过全部门禁的。

        它在容器多副本下必然数据分裂，且没有并发写能力——
        这是"配置错却表现为一切正常"的典型：应用起得来、读写也正常，
        直到某天发现两个副本的数据对不上。
        """
        problems = _settings(database_url=url).check_production_safety()
        assert any("SQLite" in text for text in problems)

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


class TestAppEnvWhitelist:
    """APP_ENV 必须是白名单里的值 —— 这是整个门禁的判据本身。

    存在意义：`is_production` 的实现是 ``app_env == "production"``，
    判据是一个用户手打的字符串。写成 ``prod`` / ``prd`` / 末尾带空格，
    门禁就**整体静默失效**：默认 JWT 密钥、默认管理员口令、DEBUG、
    DB_AUTO_CREATE 全都不会被拦下，而服务照常启动、日志里一句警告都没有。
    """

    @pytest.mark.parametrize("raw", ["prod", "prd", "staging", "local", "PRODUCTION2", ""])
    def test_unknown_env_is_rejected_at_startup(self, raw: str) -> None:
        with pytest.raises(ValidationError):
            _settings(app_env=raw)

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [("Production", "production"), (" production ", "production"), ("TESTING", "testing")],
    )
    def test_case_and_whitespace_are_normalized(self, raw: str, expected: str) -> None:
        """大小写与空格写错不该降级成"开发环境"，而应归一化后照常生效。"""
        assert _settings(app_env=raw).app_env == expected

    def test_normalized_production_still_triggers_the_gate(self) -> None:
        """归一化之后必须真的走门禁（这是这条修复的全部意义）。"""
        problems = _settings(
            app_env=" Production ",
            jwt_secret_key=_DEFAULT_JWT_SECRET,
            admin_password=_DEFAULT_ADMIN_PASSWORD,
        ).check_production_safety()
        assert problems, "带空格/大写的 production 绕过了门禁"


class TestMigrationEnforcement:
    """Alembic 也必须过门禁。

    ``check_production_safety()`` 只在 ``create_app()`` 里被调用过，
    于是「带着默认密钥的库，迁移照跑、应用起不来」这种形态没人拦。
    这里做源码级断言（直接执行 env.py 会真的连库跑迁移，不适合单测），
    保证那条调用一直在——它是唯一能让迁移入口也 fail fast 的东西。
    """

    _ENV_PY = Path(__file__).resolve().parents[1] / "alembic" / "env.py"

    def test_env_py_enforces_the_gate(self) -> None:
        source = self._ENV_PY.read_text(encoding="utf-8")
        assert "check_production_safety()" in source, (
            "alembic/env.py 没有调用生产门禁：迁移会绕开 JWT/口令/DB 那几条检查，"
            "表现为「库建好了但服务拒绝启动」，且日志里看不到原因"
        )

    def test_env_py_refuses_instead_of_warning(self) -> None:
        """必须是拒绝执行，而不是只记一条 warning。

        迁移是**写操作**：waring 会被刷屏的迁移输出盖过去，
        而它已经把表建在一个不该建的环境里了。
        """
        source = self._ENV_PY.read_text(encoding="utf-8")
        assert "raise RuntimeError" in source

    def test_env_py_does_not_duplicate_the_rules(self) -> None:
        """门禁规则只能有一份实现，env.py 必须复用 Settings 的方法。

        一旦在 env.py 里重写一遍判断，两边迟早漂移：
        「应用说配置不合法、迁移说合法」比两边都错更难查。
        """
        source = self._ENV_PY.read_text(encoding="utf-8")
        assert "from app.config import settings" in source
        assert "app_env" not in source.replace("# ", ""), (
            "env.py 里出现了 app_env 的直接判断，应该复用 check_production_safety()"
        )


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


class TestCookieConfigTolerance:
    """空串按「没配」处理。

    为什么值得单列：``.env`` 里写 ``KEY=``（保留键但留空）是很常见的运维写法，
    而 docker-compose 用 ``${COOKIE_SECURE:-}`` 透传时**必然**产生空串。
    pydantic-settings 没开 ``env_ignore_empty``，空串会原样当值传进来 ——
    对 ``bool`` 字段直接解析失败让应用起不来，对 ``COOKIE_DOMAIN``
    则写出一个 ``Domain=`` 为空的 Cookie（浏览器不认）。
    """

    def test_empty_cookie_secure_is_unset(self) -> None:
        assert _settings(cookie_secure="").cookie_secure is None

    def test_empty_cookie_domain_is_unset(self) -> None:
        assert _settings(cookie_domain="").cookie_domain is None

    def test_blank_padded_domain_is_unset(self) -> None:
        assert _settings(cookie_domain="   ").cookie_domain is None

    def test_explicit_values_still_win(self) -> None:
        """容错不能把正常配置吃掉：显式值必须原样生效。"""
        assert _settings(cookie_secure=False).cookie_secure is False
        assert _settings(cookie_domain="example.com").cookie_domain == "example.com"

    def test_secure_flag_falls_back_to_env_when_unset(self) -> None:
        """空串等于没配，于是 cookie_secure_flag 应当回到 is_production 推断。

        这条是上面几条的"合起来仍然对"的检查：单看 cookie_secure 是 None
        不能说明最终 Secure 属性正确。
        """
        assert (
            _settings(cookie_secure="").cookie_secure_flag is True
        )  # SAFE_PRODUCTION 是 production
        assert _settings(app_env="development", cookie_secure="").cookie_secure_flag is False


class TestWorkerCountRuntimeCheck:
    """``--workers 1`` 的**运行时**检查。

    存在意义：限流器是进程内固定窗口（``utils/ratelimit.py``），多 worker 会让
    登录 / 评论 / 点赞的配额按 worker 数线性放大，且没有任何告警。
    此前只有 ``tests/test_deploy_config.py`` 静态断言 Dockerfile / compose 写了
    ``--workers 1``，挡不住"有人裸机用 ``uvicorn --workers 4`` 起"。
    这里守 ``Settings.check_worker_count()`` 与 ``main._enforce_worker_invariant``。

    注意 ``sys.argv`` 一律用 monkeypatch 替换，绝不改真实进程的 argv
    （真实 argv 属于 pytest，改了会干扰其它用例）。
    """

    @pytest.fixture
    def clean_worker_env(self, monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
        """清空两个 worker 环境变量、并把 argv 收成一个正常启动形态。"""
        for name in ("WEB_CONCURRENCY", "UVICORN_WORKERS"):
            monkeypatch.delenv(name, raising=False)
        monkeypatch.setattr(sys, "argv", ["uvicorn", "app.main:app"])
        return monkeypatch

    def test_clean_environment_reports_nothing(self, clean_worker_env: pytest.MonkeyPatch) -> None:
        """正常启动（无环境变量、无 --workers）不该报任何问题。"""
        assert _settings().check_worker_count() == []

    @pytest.mark.parametrize("name", ["WEB_CONCURRENCY", "UVICORN_WORKERS"])
    def test_env_var_with_multiple_workers_is_flagged(
        self, clean_worker_env: pytest.MonkeyPatch, name: str
    ) -> None:
        clean_worker_env.setenv(name, "4")
        problems = _settings().check_worker_count()
        assert any(name in text for text in problems), problems

    def test_blank_env_var_is_treated_as_unset(self, clean_worker_env: pytest.MonkeyPatch) -> None:
        """``.env`` 里写 ``WEB_CONCURRENCY=``（留空但保留键）不该被误判。

        与 COOKIE_SECURE / COOKIE_DOMAIN 的空串容错同一约定。
        """
        clean_worker_env.setenv("WEB_CONCURRENCY", "  ")
        assert _settings().check_worker_count() == []

    @pytest.mark.parametrize(
        "argv",
        [
            ["uvicorn", "app.main:app", "--workers", "4"],
            ["uvicorn", "app.main:app", "-w", "4"],
            ["uvicorn", "app.main:app", "--workers=4"],
        ],
    )
    def test_argv_with_multiple_workers_is_flagged(
        self, clean_worker_env: pytest.MonkeyPatch, argv: list[str]
    ) -> None:
        """三种写法都要覆盖：带空格的 ``--workers N`` / ``-w N`` 与 ``--workers=N``。"""
        clean_worker_env.setattr(sys, "argv", argv)
        problems = _settings().check_worker_count()
        assert any("workers" in text for text in problems), problems

    @pytest.mark.parametrize(
        "argv",
        [
            ["uvicorn", "app.main:app", "--workers", "1"],
            ["uvicorn", "app.main:app", "-w", "1"],
            ["uvicorn", "app.main:app", "--workers=1"],
        ],
    )
    def test_explicit_single_worker_is_fine(
        self, clean_worker_env: pytest.MonkeyPatch, argv: list[str]
    ) -> None:
        """显式写 1 是**正确**用法（Dockerfile 就是这么写的），不能被误报。"""
        clean_worker_env.setattr(sys, "argv", argv)
        assert _settings().check_worker_count() == []

    def test_env_var_of_one_is_fine(self, clean_worker_env: pytest.MonkeyPatch) -> None:
        clean_worker_env.setenv("WEB_CONCURRENCY", "1")
        assert _settings().check_worker_count() == []

    def test_trailing_workers_flag_without_value_is_ignored(
        self, clean_worker_env: pytest.MonkeyPatch
    ) -> None:
        """``--workers`` 后面没有值时不越界、不误报（uvicorn 自己会报参数错）。"""
        clean_worker_env.setattr(sys, "argv", ["uvicorn", "app.main:app", "--workers"])
        assert _settings().check_worker_count() == []

    def test_production_refuses_to_start(
        self, monkeypatch: pytest.MonkeyPatch, clean_worker_env: pytest.MonkeyPatch
    ) -> None:
        """生产下多 worker 必须**拒绝启动**，而不是只打条警告。

        警告在容器日志里没人看，而配额被静默放大是不可逆的安全削弱
        （攻击者当天就能用上放大后的配额）。
        """
        from app import main

        # 先把生产门禁的其它项都置为"已修好"，保证失败一定是 worker 这一条引起的
        for key, value in SAFE_PRODUCTION.items():
            monkeypatch.setattr(main.settings, key, value)
        clean_worker_env.setenv("WEB_CONCURRENCY", "4")

        with pytest.raises(RuntimeError, match="多 worker"):
            main.create_app()

    def test_non_production_only_warns(
        self, monkeypatch: pytest.MonkeyPatch, clean_worker_env: pytest.MonkeyPatch
    ) -> None:
        """非生产只记 warning：开发时临时多开 worker 不该起不来。

        这里直接替换 ``main.logger.warning`` 而不是用 ``caplog``：``setup_logging``
        把 ``blog`` 这个 logger 的 ``propagate`` 设成了 False，caplog 挂在 root 上，
        捕获不到它的记录（照写会得到一条永远为空的假绿用例）。
        """
        from app import main

        calls: list[str] = []
        monkeypatch.setattr(
            main.logger, "warning", lambda msg, *args, **_kw: calls.append(msg % args)
        )
        monkeypatch.setattr(main.settings, "app_env", "development")
        clean_worker_env.setenv("WEB_CONCURRENCY", "4")

        main._enforce_worker_invariant()  # 不抛异常

        assert any("多 worker" in text for text in calls), calls
        assert any("非生产环境" in text for text in calls), calls

    def test_non_production_clean_env_stays_silent(
        self, monkeypatch: pytest.MonkeyPatch, clean_worker_env: pytest.MonkeyPatch
    ) -> None:
        """没有多 worker 时连警告都不该有（免得天天刷屏、把人训练成忽略告警）。"""
        from app import main

        calls: list[str] = []
        monkeypatch.setattr(
            main.logger, "warning", lambda msg, *args, **_kw: calls.append(msg % args)
        )
        monkeypatch.setattr(main.settings, "app_env", "development")

        main._enforce_worker_invariant()

        assert calls == []
