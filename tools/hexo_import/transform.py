"""正文变换：把旧站文章变成新站能正确渲染的样子。

这是**唯一**改正文内容的地方，预检（``preflight.py``）与导入（``import_hexo.py``）
都调它，所以「预检报告里看到的字节数」就是「导入后落库的字节数」，
不存在两套变换慢慢漂移的可能。

## 图片 URL 从哪来（这里踩过一个致命的坑）

图片的最终 URL **一律由调用方传入**（``asset_urls``），本模块不自己拼。
原因是服务端按 ``uploads/<YYYYMM>/`` 分目录落盘（见 ``AttachmentService.upload``），
而月份取决于**上传那一刻**的时间——自己拼 ``/media/uploads/{name}`` 会少一层月份目录，
结果是数据库里 14 张图全部 404，而导入过程没有任何报错。

所以正确做法是：导入器把 ``POST /attachments/upload`` 响应里的 ``url`` 原样拿来用。
预检（不联网）则用同一个月份规则构造，保证两边一致。

## 三类变换，逐条说明为什么必须做

### 1. 图片引用改写成 Markdown

旧站用 ``<img src="..." style="...">``。新站的 Markdown 渲染器（``frontend/src/utils/markdown.ts``）
出于安全刻意禁掉了 ``style`` 属性——内联样式能在正文里伪造一个全屏"登录框"
（配个密码输入框就是站内钓鱼页），这是它被禁的唯一原因，不是洁癖。
``class`` 也只放行 ``language-*``（代码块高亮用），所以
``class="rounded-lg shadow"`` 之类同样会被剥掉。

留着 ``<img>`` 标签的结局是「标签还在、样式没了」，不如统一改成 Markdown 图片，
让图片完全走渲染器的正常路径（顺带拿到懒加载与 10px 圆角）。

### 2. 标题层级归一化

新站现有文章的顶层标题是 ``##``，文章标题由页面页头承担。旧站 9 篇用了 ``#``：

- H1 文本**与标题完全相同**（如「FastAPI框架」一文里的 ``# FastAPI框架``）
  → 删掉，纯重复；
- H1 是文章内部的小标题（如「培训」一文里的 ``# 职场礼仪与沟通技巧学习笔记``）
  → 降为 ``##``，并把全文所有标题**整体下沉一档**，保持相对层级不变。

判定只用「去空白与标点后是否相等」，不做模糊匹配：
近似标题（``# FastAPI 精通教程`` vs 文章标题 ``FastAPI快速入门自我总结``）
保留并降级——删掉它属于替作者做内容决定，而迁移不该有这种自由。

### 3. 迁移尾注

如实写明来源与原文地址。旧站的 ``abbrlink`` 是它唯一的稳定 ID，
不记下来就再也回不到原文。丢失的图片也在这里列出——正文里留个静态占位符
只会污染阅读，而尾注是读者与未来的自己都会看到的地方。
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path

from parse_hexo import (
    ParsedPost,
    content_hash,
    extract_images,
    mask_inline_code,
    render_body,
    scan_lines,
)

#: 旧站线上地址（写进尾注，读者可回去看原文）。
ORIGIN = "https://www.furinaluna.top"

#: 迁移日期。常量而不是 ``date.today()``：导入跑几次、哪天补跑，
#: 尾注都应该写同一天，否则同一批内容会带上不同日期。
MIGRATED_ON = "2026-09-29"

_H1_RE = re.compile(r"^(\s{0,3})#(\s+)(.*?)\s*$")
_HEADING_RE = re.compile(r"^(\s{0,3})(#{1,6})(\s+)(.*?)\s*$")
#: 归一化标题用于比较时去掉的字符：空白 + 常见中英文标点
_PUNCT_RE = re.compile(
    r"[\s\u3000\-_—–·・、，。！？：；「」『』（）()\[\]【】<>《》“”\"'`~!@#$%^&*+=|\\/,.?:;]+"
)

#: 居中的 `<p>`——旧站用它当图片说明文字。只匹配**单行内**的成对标签：
#: 允许跨行会把「一个开标签 ... 中间正文 ... 一个闭标签」并成一处，吞掉正常段落。
_CAPTION_P_RE = re.compile(r"""<p\b(?P<attrs>[^>]*)>(?P<inner>.*?)</p\s*>""", re.IGNORECASE)
_CAPTION_ATTR_RE = re.compile(
    r"""(?:text-align\s*:\s*center)|(?:class\s*=\s*["'][^"']*\bcaption\b)""",
    re.IGNORECASE,
)
_TAG_RE = re.compile(r"<[^>]+>")
_MD_IMG_ALT_RE = re.compile(r"!\[([^\]]*)\]\([^)]*\)")


def merge_captions_into_alt(body: str) -> tuple[str, int, int]:
    """把「图片下面那行居中说明文字」并入图片的 alt。

    旧站有作者用 ``<p style="text-align:center;font-size:30px">说明</p>`` 当图注
    （「农大迷你马拉松」那篇）。新站禁用内联样式，这段文字会退化成一行和正文
    一样的普通小字——看不出是图注，读者很容易当成正文的一部分。

    处理：整行变成 ``*说明*``（斜体行，仍收在图片下方，但明确不是正文）。
    若它已经作为上一张图的 ``alt`` 存在，则**删除该行**——文本已经在图里了，
    再显示一遍纯属重复。

    返回 ``(新正文, 转换为斜体的行数, 因重复而删除的行数)``。只处理围栏外的行。

    **换行符必须从 ``raw_body.splitlines(keepends=True)`` 取**，不能靠
    ``scan_lines`` 返回的行——它内部用 ``splitlines()``，行尾是剥掉的。
    拿它拼回去会把整篇正文粘成一行（这个坑已经踩过一次，有测试守着）。
    """
    converted = 0
    deduped = 0
    out: list[str] = []
    raw_lines = body.splitlines(keepends=True)
    for number, raw, in_fence in scan_lines(body):
        if in_fence:
            out.append(raw_lines[number - 1])
            continue

        stripped = raw
        masked = mask_inline_code(stripped)
        match = _CAPTION_P_RE.search(masked)
        if not match or not _CAPTION_ATTR_RE.search(match.group("attrs")):
            out.append(raw_lines[number - 1])
            continue

        inner = _TAG_RE.sub("", stripped[match.start("inner") : match.end("inner")]).strip()
        if not inner:
            out.append(raw_lines[number - 1])
            continue

        if _caption_already_used_as_alt(out, inner):
            deduped += 1
            continue  # 整行丢弃

        out.append(f"*{inner}*\n")
        converted += 1

    return "".join(out), converted, deduped


def _caption_already_used_as_alt(previous_lines: list[str], caption: str) -> bool:
    """说明文字是否已经写在上一张图的 alt 里。

    只回溯一行（图注总是紧跟在图片下一行）。回溯更多行会让「与上文某张图 alt
    恰好相同的正文段落」被误删——那是真正的内容损失，比多显示一行严重得多。
    两种形态都要认：已替换成 Markdown 的 ``![说明](url)``，和还没替换的 HTML alt。
    """
    if not previous_lines:
        return False
    previous = previous_lines[-1]
    if any(
        match.group(1).strip() == caption
        for match in _MD_IMG_ALT_RE.finditer(mask_inline_code(previous))
    ):
        return True
    return f'alt="{caption}"' in previous or f"alt='{caption}'" in previous


def same_heading_as_title(title: str, heading: str) -> bool:
    """标题行是否与文章标题是同一句话（忽略空白与标点）。

    ``# FastAPI框架`` 与标题 ``FastAPI框架`` → True（该删）
    ``# FastAPI 精通教程`` 与 ``FastAPI快速入门自我总结`` → False（该留）

    也用于预检报告里的「删除/降级」判定，所以两个地方的行为天然一致。
    """
    return _PUNCT_RE.sub("", title).lower() == _PUNCT_RE.sub("", heading).lower()


def normalize_headings(body: str, title: str) -> tuple[str, list[str]]:
    """归一化正文标题层级。

    返回 ``(新正文, 变更说明)``；变更说明供预检报告与导入日志展示。

    规则只有两条，且都**只看 H1**：

    1. H1 文本与文章标题相同 → 删除该行（页面页头已经显示了同一个大标题）；
    2. 其余 H1 → 降为 H2，并把全文标题**整体下沉一档**。

    为什么是「整体下沉」而不是「只把 H1 改成 H2」：旧站的结构是
    ``H1 文档标题 / H2 章 / H3 节 / H4 细节``，新站的结构是
    ``（页头） / H2 章 / H3 节 / H4 细节``——正确对应是整条链降一级，
    只改 H1 会让 H3 细节越过 H2 直接挂在章下面。

    实测反例（「培训」一文）：它是两份独立笔记合在一个文件里，
    有两个 H1 段落。若只改 H1 而不下沉，会得到
    ``H2 子文章 → H3 着装原则 → H4 十条细节``，
    而目录只收录 H2~H4，于是 H3 被过滤、10 个 H4 **悬空**挂在边上；
    整体下沉后是 H2/H3/H4 完整三级，目录正常。

    代价：``FastAPI框架`` 与 ``langchain与rag`` 里原本最深的 H4 会变成 H5，
    超出目录收录范围。这是**正确**的——它们在旧站同样是"章下面的三级小节"，
    旧站目录自 H2 起算，本来也不显示它们。层级是有语义的，不为目录好看来压缩。

    只处理**围栏之外**的标题行：围栏代码块里写着 ``# 这是注释`` 时，
    它必须原样保留——把代码里的注释放到 H2 上就是篡改代码。
    """
    notes: list[str] = []
    # 先判定这篇文章需不需要动：没有任何 H1 时整体原样返回，
    # 避免对 14 篇正常文章做无谓的逐行重建（回归面越小越好）
    h1_lines: list[tuple[int, str]] = []
    for number, line, in_fence in scan_lines(body):
        if in_fence:
            continue
        match = _H1_RE.match(line)
        if match:
            h1_lines.append((number, line))

    dropped: set[int] = set()
    for number, line in h1_lines:
        heading = line.lstrip().lstrip("#").strip()
        if same_heading_as_title(title, heading):
            dropped.add(number)
            notes.append(f"删除与标题重复的 H1：{heading}")
        else:
            notes.append(f"H1 降为 H2：{heading}")

    if not h1_lines:
        return body, notes

    # 围栏内的行号集合。**必须显式排除**，不能指望"代码里的注释不会长成标题样"：
    # Python 的 `# 注释` 与 Markdown 的 `# 标题` 在文本上一模一样，
    # 实测 ```` ```python ```` 里的 `# response_model_exclude` 就被下沉成了
    # `## response_model_exclude`——代码被篡改，而页面上看不出任何异常。
    fenced = {number for number, _line, in_fence in scan_lines(body) if in_fence}

    # 逐行处理但**保留每行自己的换行符**：用 splitlines() 会把它丢掉，
    # 于是"删掉被判定为重复的那一行"就变成"把它前后两行粘在一起"。
    lines = body.splitlines(keepends=True)
    out: list[str] = []
    for number, raw_line in enumerate(lines, start=1):
        if number in dropped:
            continue
        if number in fenced:
            # 原样透传（连行尾空白都不能动）
            out.append(raw_line)
            continue
        stripped = raw_line.rstrip("\r\n")
        ending = raw_line[len(stripped) :]
        match = _HEADING_RE.match(stripped)
        if match:
            indent, hashes, space, text = match.groups()
            new_level = min(len(hashes) + 1, 6)
            stripped = f"{indent}{'#' * new_level}{space}{text}"
        out.append(stripped + ending)
    return "".join(out), notes


def build_disclaimer(
    *,
    abbrlink: str | None,
    missing: list[str],
    status: str,
    sticky: int | None = None,
) -> str:
    """生成迁移尾注。

    ``status`` 为 ``draft`` 时尾注会额外说明「本文尚未发布」——
    否则读者（以及将来的自己）看到一篇标着草稿的文章会以为导入失败了。

    ``sticky``：旧站的有序权重。新站只有布尔置顶，**名次无法保留**，
    所以把原始值写进尾注留档——这样「是否置顶」的取舍是可追溯、可反悔的，
    而不是一个说不清来历的布尔值。
    """
    lines = ["", "---", ""]
    if sticky is not None:
        lines.append(f"> 旧站排序权重 `sticky: {sticky}`（本站无有序权重，故仅保留「是否置顶」）。")
        lines.append(">")
    lines.append(
        f"> **关于本文**：本文于 {MIGRATED_ON} 从旧站 [furinaluna.top]({ORIGIN}) 迁移至本站。"
    )
    if abbrlink:
        lines.append("> ")
        lines.append(f"> 旧站原文地址：<{ORIGIN}/posts/{abbrlink}.html>")
    if missing:
        lines.append("> ")
        lines.append(
            f"> ⚠️ **有 {len(missing)} 张图片未能迁移**——它们在迁移前就已经从旧站丢失"
            f"（旧站仓库与线上均不存在）。原引用位置："
        )
        for raw in missing:
            lines.append(f"> - `{raw}`")
    lines.append("")
    return "\n".join(lines)


def collect_missing(post: ParsedPost) -> list[str]:
    """本篇里「找不到本地文件」的图片引用（外链不算）。"""
    seen: list[str] = []
    for ref in post.images:
        if not ref.exists and not ref.is_remote and ref.raw not in seen:
            seen.append(ref.raw)
    return seen


def build_replacements(
    post: ParsedPost,
    asset_urls: dict[str, str],
) -> dict[str, str]:
    """把本篇的 ``asset_key`` 映射到最终 URL。

    ``asset_urls`` 是**全局**的 ``asset_key -> url``（一个 asset_key 只有一个 URL，
    因为是按内容去重的），这里只挑出本篇用到的那些。

    刻意不自己拼 URL：服务端按 ``uploads/<YYYYMM>/`` 分目录落盘，月份取决于
    上传那一刻的时间，自己拼会少一层目录而**全站图片 404 且不报错**（已踩过）。
    URL 必须来自上传响应或缓存。
    """
    return {
        ref.asset_key: asset_urls[ref.asset_key]
        for ref in post.images
        if ref.exists and ref.asset_key in asset_urls
    }


def prepare_post(post: ParsedPost, asset_urls: dict[str, str]) -> tuple[ParsedPost, str]:
    """完整变换一篇：标题归一化 → 图注归并 → 图片替换 → 附加尾注。

    返回 ``(原 ParsedPost, 最终正文)``。返回原对象是为了让调用方还能取到
    frontmatter 里的标题/日期/分类（正文变换不碰这些元数据）。

    ``asset_urls``：``asset_key -> 最终 URL``，见 :func:`build_replacements`。

    **顺序不能换**：图注归并必须在图片替换**之前**做，因为它要先看上一行的
    ``alt``（HTML 形态）来判断图注是否重复；等图片被换成 Markdown 之后就只剩
    ``![alt](url)`` 形态了。两种形态虽然都认，但先归并能少一次转换。
    """
    body, _notes = normalize_headings(post.body, post.title)
    body, _captions, _dropped = merge_captions_into_alt(body)

    # 前面两步都可能**删除**行，行号因此可能改变，所以必须基于新正文重新提取
    # 图片引用，不能复用旧行号——否则替换会定位到错行，把图片语法写进别处。
    shifted = ParsedPost(
        source=post.source,
        frontmatter=post.frontmatter,
        body=body,
        title=post.title,
        date_raw=post.date_raw,
        tags=post.tags,
        categories=post.categories,
        cover_raw=post.cover_raw,
        abbrlink=post.abbrlink,
        hidden=post.hidden,
        sticky=post.sticky,
    )
    shifted.images = extract_images(shifted, post.source.parent)

    rewritten = render_body(shifted, build_replacements(shifted, asset_urls))
    rewritten += build_disclaimer(
        abbrlink=post.abbrlink,
        missing=collect_missing(post),
        status="draft" if post.hidden else "published",
        sticky=post.sticky,
    )
    return post, rewritten


def expected_asset_name(asset_key: str, digest: str) -> str:
    """服务端会落盘的文件名：``{hash 前 16 位}{扩展名}``。

    导入器把它作为上传时的文件名提交，服务端原样保存，于是「重跑时同一个
    内容算出同一个 URL」成立——幂等性的基础。
    """
    return f"{digest[:16]}{image_extension(asset_key)}"


def upload_subdir(now: datetime | None = None) -> str:
    """服务端当前使用的月份子目录，例如 ``202609``。

    与 ``AttachmentService.upload`` 的 ``now.strftime('%Y%m')`` 必须一致；
    预检用它在**不联网**时构造出与真实导入完全相同的 URL。
    """
    moment = now or datetime.now(UTC)
    return moment.strftime("%Y%m")


def image_extension(asset_key: str) -> str:
    return Path(asset_key).suffix.lower()


def hash_file(path: Path) -> str:
    return content_hash(path.read_bytes())


__all__ = [
    "MIGRATED_ON",
    "ORIGIN",
    "build_disclaimer",
    "build_replacements",
    "collect_missing",
    "expected_asset_name",
    "hash_file",
    "image_extension",
    "merge_captions_into_alt",
    "normalize_headings",
    "prepare_post",
    "same_heading_as_title",
    "upload_subdir",
]
