"""部署配置的机器检查：compose ↔ 根 .env.example ↔ Dockerfile ↔ 入口脚本。

## 防的是什么

这批 docker 部署的问题有一个共同特征：**静态读代码看不出来，只有真跑一次才暴露**，
而"没跑过"恰恰是它们能一直躺在仓库里的原因。

1. ``deploy/Dockerfile.frontend`` 里写着 ``COPY deploy/nginx.conf``，但该镜像的
   构建上下文是 ``frontend/`` —— 上下文之外的文件 COPY 不到，
   ``docker compose build`` 会以「failed to compute cache key: not found」失败。
2. compose 头部让人 ``cp backend/.env.example backend/.env``，可变量插值只读
   **项目根目录**的 .env：照着文档配完，``docker compose up`` 报
   「required variable POSTGRES_PASSWORD is missing」。
3. 根 .env.example 里写一个 compose 和 config.py 都不认的变量名：
   compose 会把它塞进容器，而 pydantic 的 ``extra="ignore"`` 静默丢弃，
   运维会以为配上了。
4. 入口脚本一旦带 CRLF 检入，进容器执行时会报「no such file or directory」，
   报错方向完全是错的。

这四条都不该靠"下次记得"来维持，所以钉成用例。

> PyYAML 不是新依赖：它来自 ``uvicorn[standard]``，已在 requirements.lock 里。
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
COMPOSE_PATH = REPO_ROOT / "docker-compose.yml"
ROOT_ENV_EXAMPLE = REPO_ROOT / ".env.example"
BACKEND_DOCKERFILE = REPO_ROOT / "deploy" / "Dockerfile.backend"
ENTRYPOINT_SCRIPT = REPO_ROOT / "backend" / "scripts" / "docker-entrypoint.sh"
CONFIG_PY = REPO_ROOT / "backend" / "src" / "app" / "config.py"
GITATTRIBUTES = REPO_ROOT / ".gitattributes"

# ``${VAR}`` / ``${VAR:-默认}`` / ``${VAR:?报错}`` 都算「compose 会去读的变量」
_INTERPOLATION_RE = re.compile(r"\$\{([A-Z][A-Z0-9_]*)")
# .env 行首（可带 # 注释前缀）的大写下划线变量名
_KEY_RE = re.compile(r"^#?\s*([A-Z][A-Z0-9_]*)=", re.MULTILINE)
# config.py 里 Settings 的字段（与 tests/test_env_example.py 同一套约定，
# 那边检查 backend/.env.example，这边检查根 .env.example）
_FIELD_RE = re.compile(r"^    ([a-z][a-z0-9_]*):\s", re.MULTILINE)
_COPY_RE = re.compile(r"^(?:COPY|ADD)\s+(.*)$", re.IGNORECASE | re.MULTILINE)
_STAGE_RE = re.compile(r"^FROM\s+\S+\s+AS\s+(\S+)", re.IGNORECASE | re.MULTILINE)
_DEFAULT_RE = re.compile(r"^\$\{[A-Z0-9_]+:-([^}]*)\}$")

# 这三个必须写成 ``:?``（缺了就拒绝启动）：它们缺失的后果分别是
# 空口令数据库、人人可伪造的登录态、公开的管理员账号。
REQUIRED_SECRETS = {"POSTGRES_PASSWORD", "JWT_SECRET_KEY", "ADMIN_PASSWORD"}


def _without_comments(text: str) -> str:
    """去掉整行注释。

    必要性：说明文字里会拿 ``${VAR}`` 当占位符举例（比如解释 ``:?`` 的语法），
    不剔掉注释就会把这些举例当成"compose 真的引用了 VAR"。
    """
    return "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))


def _compose() -> dict[str, Any]:
    return yaml.safe_load(COMPOSE_PATH.read_text(encoding="utf-8"))


def _interpolated_vars() -> set[str]:
    """所有 compose 文件里 ``${VAR}`` 形式的变量。

    **必须扫全部 compose 文件，不能只扫主文件**：``docker-compose.tls.yml``
    是自管证书那条部署路径的入口，它引用的 ``TLS_CERTS_DIR`` 同样属于
    "运维得知道它存在"的变量。只扫主文件的话，覆盖文件里新增的变量会
    同时绕过两条测试（既不用写进 .env.example，也不会被判为"没人读"）。
    """
    text = "\n".join(
        _without_comments(path.read_text(encoding="utf-8"))
        for path in sorted(REPO_ROOT.glob("docker-compose*.yml"))
    )
    return set(_INTERPOLATION_RE.findall(text))


def _documented_keys() -> set[str]:
    return set(_KEY_RE.findall(ROOT_ENV_EXAMPLE.read_text(encoding="utf-8")))


def _config_fields() -> set[str]:
    return {name.upper() for name in _FIELD_RE.findall(CONFIG_PY.read_text(encoding="utf-8"))}


def _read_by_script() -> set[str]:
    """入口脚本自己读的变量（它不在 config.py 里，但确实有人读）。"""
    text = _without_comments(ENTRYPOINT_SCRIPT.read_text(encoding="utf-8"))
    return set(_INTERPOLATION_RE.findall(text)) | set(re.findall(r"\$([A-Z][A-Z0-9_]*)", text))


def _env_map(service: dict[str, Any]) -> dict[str, str]:
    env = service.get("environment") or {}
    if isinstance(env, list):  # "KEY=value" 列表写法
        return dict(item.split("=", 1) for item in env)
    return {key: str(value) for key, value in env.items()}


def _default_of(value: str) -> str | None:
    """取出 ``${VAR:-默认值}`` 里的默认值；没有默认值就返回 None，字面量原样返回。"""
    match = _DEFAULT_RE.match(value)
    if match:
        return match.group(1)
    return None if value.startswith("${") else value


def _builds() -> list[tuple[str, Path, Path]]:
    """compose 里每个 build 服务的 (服务名, 构建上下文目录, Dockerfile 路径)。"""
    builds: list[tuple[str, Path, Path]] = []
    for name, service in _compose()["services"].items():
        build = service.get("build")
        if not build:
            continue
        if isinstance(build, str):
            context, dockerfile = REPO_ROOT / build, "Dockerfile"
        else:
            context = REPO_ROOT / build["context"]
            dockerfile = build.get("dockerfile", "Dockerfile")
        builds.append((name, context.resolve(), (context / dockerfile).resolve()))
    return builds


def _copy_instructions(dockerfile: Path) -> list[tuple[str | None, list[str]]]:
    """解析 Dockerfile 里的 COPY/ADD，返回 [(--from 的阶段名或 None, 源路径列表)]。"""
    # 先把续行折叠，避免 ``COPY a \\\n  b dest`` 被当成两条指令
    text = dockerfile.read_text(encoding="utf-8").replace("\\\n", " ")
    instructions: list[tuple[str | None, list[str]]] = []
    for raw in _COPY_RE.findall(text):
        parts = raw.split()
        flags: list[str] = []
        while parts and parts[0].startswith("--"):
            flags.append(parts.pop(0))
        stage = next((f.split("=", 1)[1] for f in flags if f.startswith("--from=")), None)
        rest = " ".join(parts)
        # JSON 数组写法：COPY ["a", "b", "dest"]
        items = json.loads(rest) if rest.startswith("[") else rest.split()
        instructions.append((stage, items[:-1]))
    return instructions


class TestComposeEnvWiring:
    """配置只有一个来源：根 .env.example。"""

    def test_backend_reads_the_same_env_file_compose_interpolates(self) -> None:
        """backend 的 env_file 必须就是插值读的那个根 .env。

        否则会出现最难查的分裂状态：运维改了 .env，插值（数据库密码）跟着变，
        容器里的应用配置却还是旧的。
        """
        files = _compose()["services"]["backend"]["env_file"]
        normalized = {Path(entry).as_posix() for entry in files}
        assert normalized == {".env"}, (
            "backend 服务的 env_file 应该只指向项目根目录的 .env（compose 插值读的就是它）："
            f"当前是 {sorted(normalized)}"
        )

    def test_every_interpolated_var_is_documented(self) -> None:
        """compose 会去读的变量，都必须写在根 .env.example 里。

        ``:?`` 的变量缺了会直接拒绝启动，``:-`` 的变量缺了会悄悄用默认值 ——
        后者更危险：上线时忘了改 SITE_BASE_URL，RSS 里的链接就全指向 localhost。
        """
        missing = sorted(_interpolated_vars() - _documented_keys())
        assert not missing, (
            "docker-compose.yml 引用了这些变量，但根 .env.example 里没有：\n  "
            + "\n  ".join(missing)
            + "\n（运维只会读 .env.example，没写等于不存在）"
        )

    def test_no_var_that_nobody_reads(self) -> None:
        """反向：根 .env.example 里的变量必须真的有人读。

        没人读的变量有两种：拼错的（本意是别的名字），
        以及代码里已经删掉的（pydantic 的 extra="ignore" 会静默丢弃）。
        两种都表现为"我明明配了却不生效"。
        """
        known = _interpolated_vars() | _read_by_script() | _config_fields()
        dangling = sorted(_documented_keys() - known)
        assert not dangling, (
            "这些变量写在根 .env.example 里，但 compose、入口脚本、config.py 都不读：\n  "
            + "\n  ".join(dangling)
        )

    def test_secrets_fail_fast_when_missing(self) -> None:
        """密钥类变量必须用 ``:?``：缺失时在 up 阶段报错，而不是用空值/公开默认值起来。"""
        text = COMPOSE_PATH.read_text(encoding="utf-8")
        guarded = set(re.findall(r"\$\{([A-Z][A-Z0-9_]*):\?", text))
        assert guarded >= REQUIRED_SECRETS, (
            "以下变量缺少 `${VAR:?...}` 形式的必填校验，缺失时会静默留空或用仓库里的公开默认值：\n  "
            + "\n  ".join(sorted(REQUIRED_SECRETS - guarded))
        )

    def test_container_defaults_to_production(self) -> None:
        """容器默认按生产跑。

        backend/.env 是给裸机开发用的（APP_ENV=development、DEBUG=true、
        DB_AUTO_CREATE=true）。这些值一旦被带进容器，就是"以开发模式对外服务"：
        /docs 敞开、异常带堆栈、改错模型会在生产库上静默改表。
        """
        env = _env_map(_compose()["services"]["backend"])
        expected = {
            "APP_ENV": "production",
            "DEBUG": "false",
            "DB_AUTO_CREATE": "false",
            "SEED_DEMO_DATA": "false",
        }
        wrong = {
            key: _default_of(env.get(key, ""))
            for key, want in expected.items()
            if (_default_of(env.get(key, "")) or "").lower() != want
        }
        assert not wrong, f"容器里的默认值必须落在安全侧，实际是：{wrong}"


class TestDockerfileCopySources:
    """COPY 的源必须真的在构建上下文里 —— 这条就是被真实 bug 逼出来的。"""

    def test_copy_sources_stay_inside_the_build_context(self) -> None:
        problems: list[str] = []
        for name, context, dockerfile in _builds():
            for stage, sources in _copy_instructions(dockerfile):
                if stage is not None:
                    continue  # 跨阶段/额外上下文的 COPY 由下一个用例负责
                for source in sources:
                    if source in (".", "./"):
                        continue
                    if any(char in source for char in "*?["):
                        found = list(context.glob(source))
                    else:
                        found = [context / source]
                    if not any(path.exists() for path in found):
                        problems.append(
                            f"{name}: {dockerfile.name} 的 COPY 源 {source!r} "
                            f"不在构建上下文 {context.name}/ 里"
                        )
        assert not problems, (
            "构建上下文之外的文件 COPY 不到，构建会直接失败：\n  "
            + "\n  ".join(problems)
            + "\n（要么把它挪进上下文，要么在 compose 的 additional_contexts 里挂进来）"
        )

    def test_every_copy_from_target_is_declared(self) -> None:
        """``COPY --from=X`` 里的 X 必须是已声明的构建阶段，或 compose 挂的额外上下文。"""
        problems: list[str] = []
        services = _compose()["services"]
        for name, _context, dockerfile in _builds():
            build = services[name]["build"]
            extra = set((build.get("additional_contexts") or {}) if isinstance(build, dict) else {})
            declared = set(_STAGE_RE.findall(dockerfile.read_text(encoding="utf-8"))) | extra
            for stage, _sources in _copy_instructions(dockerfile):
                if stage is not None and stage not in declared:
                    problems.append(
                        f"{name}: {dockerfile.name} 引用了 --from={stage}，"
                        f"但它既不是构建阶段，也不在 additional_contexts 里"
                    )
        assert not problems, "\n  ".join(problems)


class TestEntrypointScript:
    """入口脚本要先迁移再启动，且必须是 LF。"""

    def test_backend_image_migrates_before_start(self) -> None:
        """镜像入口必须跑迁移。

        compose 里 DB_AUTO_CREATE 默认 false，于是"忘了跑迁移"的后果是应用起不来。
        而原来的文档让人在 ``up -d`` 之后再 exec 进去补迁移 —— 那时容器正在
        重启循环里，exec 根本进不去。迁移交给入口，才是真的一条命令可用。
        """
        dockerfile = BACKEND_DOCKERFILE.read_text(encoding="utf-8")
        assert "docker-entrypoint.sh" in dockerfile, "后端镜像没有使用入口脚本"
        assert "ENTRYPOINT" in dockerfile, "入口脚本要挂在 ENTRYPOINT 上（CMD 会被 exec 传进去）"
        assert "alembic upgrade head" in ENTRYPOINT_SCRIPT.read_text(encoding="utf-8")

    def test_entrypoint_script_is_lf(self) -> None:
        """CRLF 的 shell 脚本进容器会报「no such file or directory」——方向全错的报错。"""
        assert b"\r\n" not in ENTRYPOINT_SCRIPT.read_bytes(), (
            "backend/scripts/docker-entrypoint.sh 里出现了 CRLF；"
            "带 \\r 的脚本在 Linux 容器里无法执行，且报错看起来像文件不存在"
        )

    def test_gitattributes_pins_shell_scripts_to_lf(self) -> None:
        """光靠"下次注意"守不住：要在 .gitattributes 里钉死。"""
        attrs = GITATTRIBUTES.read_text(encoding="utf-8")
        assert re.search(r"^\*\.sh\s+text eol=lf", attrs, re.MULTILINE), (
            ".gitattributes 缺少 `*.sh text eol=lf`：Windows 上检出会带 CRLF，"
            "而容器里的 CRLF 脚本执行必失败"
        )

    def test_hsts_is_conditional_on_forwarded_proto(self) -> None:
        """HSTS 必须是条件式的：只有外层真的在用 HTTPS 时才发。

        为什么值得钉住：无条件发 `Strict-Transport-Security` 的后果**不可逆**——
        浏览器会把"这个站点只能用 HTTPS"记住 max-age 那么久，而本仓库的 nginx
        只监听 80（TLS 由外层终止）。一个纯 HTTP 部署会因此把自己锁死，
        且服务端撤不掉这个记忆，只能等它过期。

        实现方式是 `map $http_x_forwarded_proto $hsts_header`：
        `https` → 有值，其余 → 空串（nginx 对空值的 add_header 不会发出该头）。
        这里同时钉住 map 的两个分支与 add_header 用的是变量而不是字面量。
        """
        # 配置拆成了「两套外壳 + 一份共享 server 体」：
        # map 在外壳（nginx.conf / nginx.https.conf，两套的条件不同），
        # 而真正发头的 add_header 在共享的 security-headers.inc 里。
        # 所以这条断言要跨两个文件读 —— 只读 nginx.conf 会在重构后误报。
        conf = (REPO_ROOT / "deploy" / "nginx.conf").read_text(encoding="utf-8")
        headers = (REPO_ROOT / "deploy" / "security-headers.inc").read_text(encoding="utf-8")

        map_block = re.search(
            r"map\s+\$http_x_forwarded_proto\s+\$hsts_header\s*\{(?P<body>[^}]*)\}",
            conf,
        )
        assert map_block, "缺少条件式 HSTS 的 map（直接写字面量会把纯 HTTP 部署锁死）"
        body = map_block.group("body")
        assert re.search(r'default\s+""\s*;', body), "default 必须是空串（非 HTTPS 不发 HSTS）"
        assert re.search(r"https\s+\"max-age=\d+", body), "https 分支缺少 max-age"

        assert re.search(
            r"add_header\s+Strict-Transport-Security\s+\$hsts_header\s+always\s*;", headers
        ), "add_header 必须用 $hsts_header 变量，而不是写死的字面量"

        # 另一半：自管证书那套**自己就是 TLS 终点**，HSTS 必须是**无条件**的。
        # 把这条也钉住，是因为反过来（HTTPS 版也靠 X-Forwarded-Proto 判断）意味着
        # 直连 443 的访客永远收不到 HSTS —— 一个不会报错的静默失效。
        https_conf = (REPO_ROOT / "deploy" / "nginx.https.conf").read_text(encoding="utf-8")
        https_map = re.search(
            r"map\s+\$http_x_forwarded_proto\s+\$hsts_header\s*\{(?P<body>[^}]*)\}",
            https_conf,
        )
        assert https_map, "nginx.https.conf 缺少 HSTS 的 map"
        assert re.search(r"default\s+\"max-age=\d+", https_map.group("body")), (
            "自管证书那套的 HSTS default 必须是非空值（它是 TLS 终点，不依赖 X-Forwarded-Proto）"
        )

    def test_nginx_conf_is_included_into_http_context(self) -> None:
        """`map` / `upstream` 都是 http 级指令，这个文件必须被 include 进 http{}。

        如果哪天有人把它挪成 nginx 主体配置（带 `events{}` 的那一层），
        `map` 会直接让 nginx 起不来（`"map" directive is not allowed here`）。
        落到 `conf.d/` 才是被 http{} include 的关键，所以这里把目标路径钉住。
        注意来源是构建阶段（构建上下文是 frontend/，配置来自 deploy/ 上下文），
        直接 `COPY deploy/nginx.conf` 会因跨上下文而构建失败。
        """
        dockerfile = (REPO_ROOT / "deploy" / "Dockerfile.frontend").read_text(encoding="utf-8")
        # 主配置由构建参数 NGINX_CONF 选（默认 nginx.conf，TLS 部署传 nginx.https.conf），
        # 所以这里要同时认「写死的 nginx.conf」与「${NGINX_CONF}」两种写法 ——
        # 钉住的是**目标路径**这个不变量，不是某个具体文件名。
        assert re.search(
            r"COPY\s+(?:--from=\S+\s+)?(?:nginx\.conf|\$\{NGINX_CONF\})\s+/etc/nginx/conf\.d/",
            dockerfile,
        ), (
            "nginx 主配置必须落到 /etc/nginx/conf.d/（那里被 include 进 http{}）；"
            "落成 /etc/nginx/nginx.conf 会让 map/upstream 变成非法指令"
        )

        # 片段（.inc）被主配置用**绝对路径** include，所以它们也必须进 conf.d/，
        # 而且引用的名字与拷进去的名字必须对得上。这两处一旦不一致，
        # 症状是 nginx 起不来（unknown directive / open() failed），只在容器里可见。
        copied = set(re.findall(r"([\w.-]+\.inc)", dockerfile))
        assert {"nginx-common.inc", "nginx-server.inc", "security-headers.inc"} <= copied, (
            f"nginx 主配置 include 的三个片段必须一起拷进镜像：实际只看到 {sorted(copied)}"
        )

        referenced: set[str] = set()
        for name in ("nginx.conf", "nginx.https.conf", "nginx-server.inc", "security-headers.inc"):
            text = (REPO_ROOT / "deploy" / name).read_text(encoding="utf-8")
            referenced |= set(re.findall(r"include\s+/etc/nginx/conf\.d/([\w.-]+\.inc)\s*;", text))
        assert referenced <= copied, "配置里 include 了镜像里不存在的片段：" + ", ".join(
            sorted(referenced - copied)
        )
