# 缺陷修复与回归验证报告

> 日期：2026-09-20 ｜ 前置：E2E 测试确认的 4 项 FAIL（`E2E-FINAL-REPORT-2026-09-19.md`）
> 结论：**4 项缺陷全部修复，回归全绿**（pytest 424/424、ruff 全过、E2E 62/62 PASS）

---

## 一、修复清单

| # | 缺陷 | 文件 | 改法 |
|---|---|---|---|
| 1 | 配置邮箱不校验 → 运行时 500 | `backend/src/app/config.py` | `admin_email: str` → `EmailStr`，非法邮箱在**配置解析期**即失败（与生产安全门禁同策略） |
| 2 | 无兜底 500 处理器 | `backend/src/app/main.py` | 三个既有 handler **之后**新增 `@app.exception_handler(Exception)`，返回 `{detail, code=internal_error, request_id}` 并记日志；更具体的 handler 仍优先 |
| 3 | 三级评论被接受却不可见 | `backend/src/app/services/comment_service.py` | `parent.parent_id is not None` → `BadRequestError("仅支持两级评论，请直接回复根评论")`，docstring Raises 同步 |
| 4 | 登出不吊销令牌 | `services/auth_service.py` + `api/v1/auth.py` | 新增 `AuthService.logout()` 复用既有 `token_version += 1` 机制；路由改接 `SessionDep` 并显式 commit；原「不值得做黑名单」注释按新行为重写 |

顺带：`utils/security.py` 注释（改密码/登出时 +1）、`CHANGELOG.md` 新增 4 条。

## 二、新增测试（406 → 424，+18）

| 文件 | 数量 | 覆盖 |
|---|---|---|
| `tests/test_config_email.py`（新） | 9 | 默认 `admin@example.com` 仍可用（防加固变开箱即崩）；参数化拒绝 `e2e.test`/`localhost`/`example.invalid`/无 @ 等；断言在 `Settings()` 构造期抛错 |
| `tests/test_error_envelope.py`（新） | 5 | 500 必须是 `{detail, code, request_id}`；不泄漏堆栈/表名；**404 不能因兜底处理器变成 500**；脏数据真实触发 500 仍带 request_id |
| `tests/test_auth.py::TestLogout`（新） | 3 | 登出后旧 access 401 / 旧 refresh 401 / 可重新登录 |
| `tests/test_comments.py`（新增用例） | 1 | 回复二级评论 400，且直连库断言无孤儿行 |

## 三、验证输出（真实执行）

```
pytest                 → 424 passed, 144 warnings in 281.16s
ruff check .           → All checks passed!
ruff format --check .  → 126 files already formatted
E2E (E2E_PORT=8123)    → 总计 62 条：PASS=62  FAIL=0  ERROR=0
项目真实库是否被污染    → 否
```

四个目标用例状态：

| 用例 | 修复前 | 修复后 |
|---|---|---|
| U05 登出后旧令牌失效 | FAIL（登出后 `/auth/me` 仍 200） | **PASS**（401） |
| C05 三级评论 | FAIL（落库但永不可见） | **PASS**（400，层级校验生效） |
| X01 保留域邮箱配置 | FAIL（运行时 500 击穿两页） | **PASS**（启动期即拒绝，日志命中 `admin_email` 校验） |
| X02 500 可定位性 | FAIL（纯文本，无 request_id） | **PASS**（500 响应含 request_id） |

## 四、对 E2E 执行器的改动（已授权，已复核）

修复 1 生效后，带保留域邮箱的实例**根本起不来**，原套件会走满 120s 超时并把整个套件打成 `E00 ERROR`，X01/X02 执行不到。因此阶段 6 拆为：

- **6a**：捕获启动失败，记为「配置校验生效」的证据；X01 判定改为「启动期拒绝 **或** 运行时结构化 500」，并强制断言启动日志确实含 `admin_email` 校验失败（防止变成永远通过的空壳）
- **6b**：另起**合法邮箱**实例，直连 sqlite 执行 `UPDATE site_profile SET email='broken@e2e.test'` 制造脏数据，让 X02 真在 500 上验证信封与 request_id

## 五、遗留事项（均为可选优化，不影响正确性）

1. `main.py` 中 500 用了整数字面量，同文件其他处惯用 `status.HTTP_500_INTERNAL_SERVER_ERROR`——功能一致，可统一
2. 兜底 500 的响应**不带 `X-Request-ID` 响应头**（`ServerErrorMiddleware` 在 `RequestContextMiddleware` 之外），request_id 仅在响应体；前端目前不读该头，无实际影响
3. 阶段 6a 需等满 `wait_ready` 120s 才判定启动失败，可改为「检测到子进程退出即返回」省约 2 分钟
4. **登出的取舍需知悉**：`token_version += 1` 会让该用户在**所有设备**上的令牌同时失效（个人博客规模下可接受，且优于「登出形同虚设」）

## 六、状态

**未做任何 git commit**。改动：`CHANGELOG.md`、`config.py`、`main.py`、`api/v1/auth.py`、`auth_service.py`、`comment_service.py`、`utils/security.py`、`tests/test_auth.py`、`tests/test_comments.py`，新增 `tests/test_config_email.py`、`tests/test_error_envelope.py`。
