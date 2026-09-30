"""变换层测试。

这里钉的是「内容怎么改」的策略。与 ``test_parse_hexo.py`` 的分工：
那边管"读出什么"，这边管"写成什么"。

容易被"顺手优化"改坏的地方优先钉：标题下沉的**幅度**、图注归并的**边界**
（只认上一行、只认居中 p）、以及围栏内容绝不被这两步碰到。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from parse_hexo import ParsedPost, extract_images, parse_post, render_body
from transform import (
    MIGRATED_ON,
    ORIGIN,
    build_disclaimer,
    build_replacements,
    collect_missing,
    merge_captions_into_alt,
    normalize_headings,
    prepare_post,
    same_heading_as_title,
    upload_subdir,
)

# ---------------------------------------------------------------- 标题判定


@pytest.mark.parametrize(
    ("title", "heading", "expected"),
    [
        # 完全相同
        ("FastAPI框架", "FastAPI框架", True),
        # 只差空白与标点
        ("FastAPI快速入门自我总结", "FastAPI 快速入门自我总结", True),
        ("算法竞赛C++STL常用用法", "算法竞赛 C++ STL 常用用法", True),
        # 近似但确实是另一个名字 —— 必须保留
        ("FastAPI快速入门自我总结", "FastAPI 精通教程", False),
        ("SQLAlchemy快速入门自我总结", "SQLAlchemy 精通教程", False),
        ("GEO优化", "GEO 优化入门指南", False),
        ("培训", "职场礼仪与沟通技巧学习笔记", False),
        # 大小写不敏感
        ("SEO优化", "seo优化", True),
    ],
)
def test_same_heading_as_title(title, heading, expected):
    assert same_heading_as_title(title, heading) is expected


# ---------------------------------------------------------------- 标题归一化


def test_normalize_deletes_h1_equal_to_title():
    body = "# FastAPI框架\n\n正文开始。\n\n## 一章\n"
    out, notes = normalize_headings(body, "FastAPI框架")
    assert out == "\n正文开始。\n\n### 一章\n", "H1 该整行删除，其余标题下沉一档"
    assert any("删除" in note for note in notes)


def test_normalize_deletes_only_that_line_not_neighbours():
    """删除行最容易出的错是「把前后两行粘在一起」。

    这里**不断言最终层级**（H2 因整体下沉变 H3 是正确行为，由下一条测试管），
    只看行边界：被删行的前一行与后一行绝不能相连。
    """
    body = "# 标题\n\n第一段。\n\n## 一章\n\n第二段。\n"
    out, _ = normalize_headings(body, "标题")
    assert "第一段。第二段。" not in out
    assert "\n第一段。\n" in out
    assert "\n第二段。\n" in out
    # 行数守恒：7 行去掉 1 行 = 6 行
    assert len(out.splitlines()) == 6


def test_normalize_shifts_all_levels_down():
    """H1→H2、H2→H3、H3→H4：整条链降一级，相对层级不变。"""
    body = "# 甲\n\n## 乙\n\n### 丙\n"
    out, _ = normalize_headings(body, "别的标题")
    assert out == "## 甲\n\n### 乙\n\n#### 丙\n"


def test_normalize_after_deleting_h1_the_rest_still_shifts():
    """删掉与标题重复的 H1 之后，**其余标题仍然下沉一档**。

    这条容易搞错：既然 H1 没了，剩下的 H2 是不是就该保持 H2？
    不该——旧站的结构是 ``H1 标题 / H2 章 / H3 节``，新站是 ``（页头）/ H2 章 / H3 节``，
    所以 H2 章必须变成 H3，否则它会和"页头之下第一层"的语义冲突，
    整篇的层级也都会被抬高一档。
    """
    body = "# 标题\n\n## 章\n\n### 节\n"
    out, _ = normalize_headings(body, "标题")
    assert out == "\n### 章\n\n#### 节\n"


def test_normalize_caps_at_level_six():
    body = "# 甲\n\n###### 最深\n"
    out, _ = normalize_headings(body, "别的标题")
    assert "###### 最深" in out, "最深级别不能再往下加，否则会变成 ####### "


def test_normalize_no_h1_is_byte_identical():
    """没有 H1 的文章整体原样返回。

    14 篇本来就正常的文章不该被这套逻辑碰到——回归面越小越好。
    """
    body = "## 一章\n\n正文。\n\n### 一节\n"
    out, notes = normalize_headings(body, "标题")
    assert out == body
    assert notes == []


def test_normalize_ignores_headings_inside_fences():
    """``` 里的 ``# 注释`` 是代码，不是标题。

    断言用 ``splitlines()`` 精确比对，**不用 ``in``**：
    ``"# 这是代码注释" in "## 这是代码注释"`` 为真，
    用 ``in`` 会让这条用例在代码真的被改写时仍然通过
    （实测就这样放过了一次真实事故，见下一条用例的说明）。
    """
    body = "# 标题\n\n```python\n# 这是代码注释\n## 也是注释\n```\n"
    out, _ = normalize_headings(body, "标题")
    lines = out.splitlines()
    assert "# 这是代码注释" in lines, "代码里的注释被下沉成了标题"
    assert "## 也是注释" in lines, "本来双井号的注释不该被动"
    assert "## 这是代码注释" not in lines


def test_normalize_does_not_touch_python_comments_in_fences():
    """**这条是真实 bug 的回归用例**（不是假想的边界）。

    Python 的 ``# 注释`` 与 Markdown 的 ``# 标题`` 在文本上完全一样，
    所以「整体下沉一档」会把代码里的注释改成 ``## 注释``——**代码被篡改**，
    而页面上看不出任何异常（那行在代码块里，渲染出来就是两个井号）。

    实测事故：`FastAPI框架` 一文 ```python 里的 ``# response_model_exclude``
    被下沉成了 ``## response_model_exclude``，连行尾两个空格也一起丢了。
    根因是逐行循环漏掉了围栏判断；**而当时的围栏用例用了 ``in`` 断言
    （``"# 注释" in out``），它是 ``"## 注释"`` 的子串，所以假通过。**
    """
    body = "# 文档标题\n\n```python\n# response_model_exclude  \n@app.get('/items')\n```\n"
    out, _ = normalize_headings(body, "别的标题")
    assert "# response_model_exclude  \n" in out, "代码注释被改写，或行尾空白被吃掉"
    assert "## response_model_exclude" not in out


def test_normalize_keeps_fenced_lines_byte_identical():
    """无论围栏里长什么样，逐字节不变。"""
    fenced_lines = [
        "# 看起来像标题",
        "## 也像",
        "###### 六级",
        "    # 缩进的",
        "#",
        "# ",
        "# 带尾随空白   ",
    ]
    body = "# 标题\n\n```\n" + "\n".join(fenced_lines) + "\n```\n"
    out, _ = normalize_headings(body, "标题")
    for line in fenced_lines:
        assert line + "\n" in out, f"围栏行被改动：{line!r}"


def test_normalize_two_h1_articles_both_become_h2():
    """「培训」一文有两份独立笔记，两个 H1 都该降为 H2。"""
    body = "# 子文章一\n\n## 一节\n\n# 子文章二\n\n## 二节\n"
    out, notes = normalize_headings(body, "培训")
    assert out == "## 子文章一\n\n### 一节\n\n## 子文章二\n\n### 二节\n"
    assert len(notes) == 2


def test_normalize_setext_heading_is_not_touched():
    """``标题\\n=====`` 是 setext H1，本函数不处理。

    旧站 23 篇一篇都没用 setext（全部是 # 形式），所以刻意不处理：
    为一个不存在的情况写解析分支，只会给真正要守的路径加风险。
    这条测试锁定"不处理"，避免以后有人以为漏了。
    """
    body = "标题\n=====\n\n正文。\n"
    out, _ = normalize_headings(body, "标题")
    assert out == body


# ---------------------------------------------------------------- 图注归并


def test_caption_line_becomes_italic_when_not_duplicate():
    body = '<img src="甲/1.jpg" alt="另一段文字" />\n<p style="text-align: center;">图说明</p>\n'
    out, converted, deduped = merge_captions_into_alt(body)
    assert out == '<img src="甲/1.jpg" alt="另一段文字" />\n*图说明*\n'
    assert (converted, deduped) == (1, 0)


def test_caption_dropped_when_already_in_previous_alt():
    """图注与上一张图的 alt 相同时整行删除。

    旧站「农大迷你马拉松」就是这样：``alt="最好的软件长跑队"`` 下面又有一行
    同样文字的居中说明。留着会让同一句话在页面出现两次。
    """
    body = (
        '<img src="甲/1.jpg" alt="最好的软件长跑队" />\n'
        '<p style="text-align:center">最好的软件长跑队</p>\n'
    )
    out, converted, deduped = merge_captions_into_alt(body)
    assert out == '<img src="甲/1.jpg" alt="最好的软件长跑队" />\n'
    assert (converted, deduped) == (0, 1)


def test_caption_dropped_when_alt_is_already_markdown():
    """替换成 Markdown 之后再跑一次也要认得（幂等性）。"""
    body = "![说明](/media/uploads/x.jpg)\n<p style='text-align:center'>说明</p>\n"
    _out, _converted, deduped = merge_captions_into_alt(body)
    assert deduped == 1


def test_caption_only_looks_one_line_back():
    """alt 匹配只看上一行。

    回溯更多行会让「与上文某张图 alt 恰好相同的正文段落」被误删——
    那是真正的内容损失，比多显示一行严重得多。
    """
    body = '<img src="甲/1.jpg" alt="相同文字" />\n\n<p style="text-align:center">相同文字</p>\n'
    out, converted, deduped = merge_captions_into_alt(body)
    assert deduped == 0, "隔了一行就不该判定为重复"
    assert converted == 1
    assert "*相同文字*" in out


def test_plain_paragraph_is_not_touched():
    """没有居中/图注标记的 <p> 是正文，不能动。"""
    body = '<p style="color:red">这是正文里的红字</p>\n'
    out, converted, deduped = merge_captions_into_alt(body)
    assert out == body
    assert (converted, deduped) == (0, 0)


def test_p_without_style_is_not_touched():
    body = "<p>普通段落</p>\n"
    out, _, _ = merge_captions_into_alt(body)
    assert out == body


def test_caption_inside_fence_is_not_touched():
    body = "```html\n<p style='text-align:center'>示例</p>\n```\n"
    out, converted, deduped = merge_captions_into_alt(body)
    assert out == body
    assert (converted, deduped) == (0, 0)


def test_caption_in_inline_code_is_not_touched():
    body = '写法是 `<p style="text-align:center">x</p>`\n'
    out, converted, _ = merge_captions_into_alt(body)
    assert out == body
    assert converted == 0


def test_caption_strips_nested_tags():
    body = '<p style="text-align:center"><font color="red">强调的图注</font></p>\n'
    out, converted, _ = merge_captions_into_alt(body)
    assert out == "*强调的图注*\n"
    assert converted == 1


def test_empty_caption_paragraph_is_kept():
    """空图注不产生 ``**`` 这种空斜体，也不删除原行（可能是有意的占位）。"""
    body = '<p style="text-align:center">   </p>\n'
    out, converted, deduped = merge_captions_into_alt(body)
    assert out == body
    assert (converted, deduped) == (0, 0)


# ---------------------------------------------------------------- 尾注


def test_disclaimer_contains_origin_and_abbrlink():
    text = build_disclaimer(abbrlink="86b9fb44", missing=[], status="published")
    assert ORIGIN in text
    assert f"{ORIGIN}/posts/86b9fb44.html" in text
    assert MIGRATED_ON in text
    assert "未迁移" not in text, "没有缺失图片时不该出现警告"
    assert "sticky" not in text, "没有 sticky 的文章不该出现权重留档"


def test_disclaimer_records_original_sticky_weight():
    """旧站的有序权重写进尾注留档。

    新站只有布尔置顶，名次无法保留；把原始值记下来，
    「哪几篇被保留为置顶」这个取舍才是可追溯、可反悔的。
    """
    text = build_disclaimer(abbrlink="e8eb82c9", missing=[], status="published", sticky=24)
    assert "sticky: 24" in text
    assert "本站无有序权重" in text


def test_disclaimer_lists_missing_images():
    text = build_disclaimer(
        abbrlink="fastapi-framework",
        missing=["FastAPI框架/截屏2022-08-29 16.31.21.png"],
        status="published",
    )
    assert "1 张图片未能迁移" in text
    assert "截屏2022-08-29 16.31.21.png" in text


def test_disclaimer_without_abbrlink_omits_link():
    text = build_disclaimer(abbrlink=None, missing=[], status="published")
    assert "/posts/" not in text


# ---------------------------------------------------------------- 集成


def _prepared(tmp_path: Path, body: str, title: str = "甲"):
    """把一篇最小文章跑完整条变换链，返回最终正文与解析器实例。"""
    posts = tmp_path / "_posts"
    (posts / "甲").mkdir(parents=True, exist_ok=True)
    (posts / "甲" / "1.jpg").write_bytes(b"aaa")
    path = posts / "甲.md"
    path.write_text(
        f"---\ntitle: {title}\ndate: 2025-11-14 10:36:19\n---\n{body}", encoding="utf-8"
    )
    post = parse_post(path)
    post.images = extract_images(post, posts)
    # URL 用与真实导入一致的形状（含服务端的月份子目录），
    # 这样"少拼一层目录"那类错误在这层就能被抓到
    url = f"/media/uploads/{upload_subdir()}/{'a' * 16}.jpg"
    _origin, out = prepare_post(post, {"甲/1.jpg": url})
    return post, out


def test_prepare_post_end_to_end(tmp_path):
    body = (
        "# 甲\n"  # 与标题相同 -> 删除
        "\n"
        "正文。\n"
        "\n"
        "## 一章\n"
        "\n"
        '<img src="甲/1.jpg" alt="配图" style="zoom:50%;" />\n'
        '<p style="text-align:center">配图</p>\n'
    )
    _post, out = _prepared(tmp_path, body, title="甲")

    assert "# 甲\n" not in out, "与标题重复的 H1 该被删除"
    # 用 splitlines 精确比对，不用 `in`：`"## 一章" in "### 一章"` 也为真，
    # 那样写会"永远通过"，把层级回归放过去（这条断言原本就是这么错的）
    lines = out.splitlines()
    assert "### 一章" in lines, "H1 被删后，其余标题仍应整体下沉一档"
    assert "## 一章" not in lines
    assert f"![配图](/media/uploads/{upload_subdir()}/{'a' * 16}.jpg)" in out
    assert "<img" not in out, "HTML img 应全部改写为 Markdown"
    assert "text-align" not in out, "内联样式不该留下（新站会剥掉，留着是死属性）"
    assert "<p " not in out, "居中 p 图注应已归并"
    assert f"{ORIGIN}/posts/" not in out, "该篇 frontmatter 没有 abbrlink，尾注不该编一个原文链接"
    assert f"[furinaluna.top]({ORIGIN})" in out


def test_prepare_post_keeps_fenced_code_untouched(tmp_path):
    fenced = '<img src="甲/1.jpg" style="zoom:50%;">\n<p style="text-align:center">示例</p>\n'
    body = f'<img src="甲/1.jpg" alt="真的" />\n\n```html\n{fenced}```\n'
    _post, out = _prepared(tmp_path, body)
    assert fenced in out, "围栏内的示例必须逐字节保留"
    assert out.count("/media/uploads/") == 1


def test_prepare_post_is_idempotent_on_second_pass(tmp_path):
    """对已经是新站形态的正文再跑一次，除尾注外不该继续变化。

    幂等性的实际价值：迁移中途失败要重跑、或者事后补跑几篇时，
    不会把已经处理好的正文再改一遍。
    """
    body = '<img src="甲/1.jpg" alt="配图" />\n\n## 一章\n'
    post, first = _prepared(tmp_path, body)
    stripped = first.split("\n---\n")[0]

    again = ParsedPost(
        source=post.source,
        frontmatter=post.frontmatter,
        body=stripped,
        title=post.title,
        date_raw=post.date_raw,
        tags=post.tags,
        categories=post.categories,
        cover_raw=post.cover_raw,
        abbrlink=post.abbrlink,
        hidden=post.hidden,
        sticky=post.sticky,
    )
    second_body, _ = normalize_headings(again.body, again.title)
    second_body, _c, _d = merge_captions_into_alt(second_body)
    assert second_body == stripped, "第二次变换与第一次结果不一致（不幂等）"
    assert render_body(again, build_replacements(again, {"甲/1.jpg": "a" * 64})) == stripped


def test_collect_missing_excludes_remote(tmp_path):
    posts = tmp_path / "_posts"
    posts.mkdir()
    path = posts / "甲.md"
    path.write_text(
        "---\ntitle: 甲\n---\n"
        '<img src="甲/丢.png" />\n<img src="https://cdn.example.com/a.png" />\n',
        encoding="utf-8",
    )
    post = parse_post(path)
    post.images = extract_images(post, posts)
    assert collect_missing(post) == ["甲/丢.png"]
