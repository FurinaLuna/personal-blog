"""Hexo 旧站内容解析器。

把 ``<hexo 仓库>/source/_posts/*.md`` 解析成与站点无关的中间结构
（:class:`ParsedPost`），供 ``import_hexo.py`` 上传图片、建文章。

## 为什么解析器要独立成模块

迁移的质量全部取决于这一步是否忠实：一个「差不多」的正则会把代码块里的
HTML 示例也当成真图片替换掉，而**这种损坏在数据库里看不出来**——文章照样
入库、页面照样渲染，只是内容被悄悄改过了。所以：

1. 解析逻辑与网络 IO 彻底分开，前者可被单元测试逐条钉死，不需要服务器；
2. 核心不变量是「**围栏代码块与行内代码的内容字节不变**」，由测试直接断言。

## 扫描为什么不能只用正则全文替换

旧站正文里混着大量**真实 HTML**（``<img>`` 带内联样式）与**代码示例**
（围栏里写着另一个 ``<img>``）。两者在纯正则眼里完全一样，
只有做「围栏感知」的逐行状态机才能区分：

- 在围栏内：原样透传，一个字符都不动；
- 在围栏外：识别 HTML ``<img>`` 与 Markdown ``![alt](src)`` 两种引用。

行内代码（反引号包起来的部分）同样要跳过：``这是一张 `<img src="x.png">` 的写法``
里那个引用是**被讲解的对象**，不是要被替换的图片。

围栏规则按 CommonMark：开围栏的字符（反引号或波浪号）与长度决定闭合条件，
所以「四反引号里包着三反引号」这种嵌套写法不会把状态机带偏。
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Literal

# ---------------------------------------------------------------- 数据结构

FenceKind = Literal["backtick", "tilde"]
ImageOrigin = Literal["html", "markdown"]


@dataclass(frozen=True)
class Diagnostic:
    """一条解析期发现的问题，供预检报告与导入日志使用。"""

    level: Literal["error", "warn", "info"]
    code: str
    message: str
    line: int | None = None

    def __str__(self) -> str:
        where = f" L{self.line}" if self.line is not None else ""
        return f"[{self.level.upper()}] {self.code}{where}: {self.message}"


@dataclass(frozen=True)
class ImageRef:
    """正文里的一处图片引用。

    ``asset_key`` 是「相对 ``_posts`` 目录的 POSIX 路径」，例如
    ``农大迷你马拉松/9.jpg``；它是磁盘查找与去重的唯一键。
    ``origin`` 记录原始写法（HTML 标签还是 Markdown 图片），
    转换时据此决定 alt 文本从哪个属性取。
    """

    raw: str
    asset_key: str
    origin: ImageOrigin
    alt: str
    line: int
    exists: bool
    #: 该引用在正文里的精确原文，替换时按它定位（同一路径可能带不同样式）
    matched_text: str

    @property
    def is_remote(self) -> bool:
        return self.raw.startswith(("http://", "https://", "//", "data:"))


@dataclass
class ParsedPost:
    """一篇解析完成的旧站文章。"""

    source: Path
    frontmatter: dict[str, object]
    #: 去掉 frontmatter 之后的正文（尚未做图片替换）
    body: str
    title: str
    date_raw: str | None
    tags: list[str]
    categories: list[str]
    cover_raw: str | None
    abbrlink: str | None
    hidden: bool
    sticky: int | None
    images: list[ImageRef] = field(default_factory=list)
    diagnostics: list[Diagnostic] = field(default_factory=list)
    #: 正文里围栏代码块的内容（含围栏行），用于「内容未被篡改」的校验
    fences: list[str] = field(default_factory=list)
    #: 被跳过未解析的区域类型计数，纯粹为了报告可读
    skipped_inline_code: int = 0

    @property
    def slug_hint(self) -> str:
        """按标题生成 slug 的原始输入（真正的唯一化由服务端做）。"""
        return self.title

    @property
    def stem(self) -> str:
        return self.source.stem


# ---------------------------------------------------------------- frontmatter

_FM_RE = re.compile(r"^---[ \t]*\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n|$)", re.DOTALL)
_SEQ_ITEM_RE = re.compile(r"^\s*-\s*(.+?)\s*$")


def split_frontmatter(text: str) -> tuple[str, str]:
    """切出 frontmatter 原文与正文。

    返回 ``(frontmatter 原文, 正文)``；没有 frontmatter 时前者为空串。

    只认**文件开头**的 ``---``：旧站正文里也会出现 ``---`` 分隔线，
    用 ``search`` 就会把正文腰斩。
    """
    match = _FM_RE.match(text)
    if not match:
        return "", text
    return match.group(1), text[match.end() :]


def parse_frontmatter(raw: str) -> dict[str, object]:
    """解析 Hexo frontmatter 的**子集**。

    刻意不引入 PyYAML：整个迁移只用到「标量」与「字符串列表」两种形状，
    而 YAML 的完整语义（锚点、多行块、类型标签）在这里既用不上，
    又会把「``tags: C++ 竞赛模板 算法`` 该不该拆成三个标签」这类
    **业务问题**伪装成解析细节。这里显式处理四种形状，行为可被测试钉死：

    1. ``key: value``
    2. ``key: 'value'`` / ``key: "value"``（去引号）
    3. 缩进的 ``- item`` 列表
    4. ``#`` 开头的整行注释

    行内 ``#`` **不**当注释：``title: 前端学习路线(从入门到入土)`` 这类值里
    合法地含有 ``#`` 的概率虽低，但 ``abbrlink: 297d2532 # 注释`` 这种写法
    反而更少见——宁可把 ``#`` 留在值里，也不要静默截断标题。
    """
    result: dict[str, object] = {}
    lines = raw.splitlines()
    index = 0
    while index < len(lines):
        line = lines[index]
        index += 1
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip()
        value = value.strip()

        # 列表形状：本行的值部分为空，后续若干行是 `- item`
        if not value:
            items: list[str] = []
            while index < len(lines):
                item_match = _SEQ_ITEM_RE.match(lines[index])
                if not item_match:
                    break
                items.append(_unquote(item_match.group(1)))
                index += 1
            if items:
                result[key] = items
            else:
                result[key] = ""
            continue

        result[key] = _unquote(value)
    return result


def _unquote(value: str) -> str:
    """去掉成对的引号。

    只处理**首尾都是同一种引号**的情况；``C++竞赛代码模板`` 里的 ``+``
    不该被任何引号规则影响。
    """
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    return value


def split_taxonomy(value: object) -> list[str]:
    """把 Hexo 的 tags/categories 归一成字符串列表。

    旧站同时存在三种写法，且都合法：

    - ``tags: C++ 竞赛模板 算法``        —— 空格分隔
    - ``tags: 'C++, STL, 语法'``        —— 逗号分隔
    - ``tags:\\n  - langchain``          —— YAML 列表

    还有 ``tags: 'Python, 人工智能'`` 这种**引号包着逗号列表**的混合形状。

    分隔规则：**含逗号就按逗号切，否则按空白切**。
    反过来（先按空白切）会把 ``C++, STL, 语法`` 切成 ``C++,`` 这种带标点的碎片，
    标签库里就会多出几个永远对不上的脏标签。
    """
    if isinstance(value, list):
        raw_items = [str(item) for item in value]
    elif isinstance(value, str):
        raw_items = [value]
    else:
        return []

    out: list[str] = []
    seen: set[str] = set()
    for chunk in raw_items:
        for item in re.split(r"[,，]", chunk) if re.search(r"[,，]", chunk) else chunk.split():
            name = item.strip().strip("'\"")
            if name and name not in seen:
                seen.add(name)
                out.append(name)
    return out


def to_int(value: object) -> int | None:
    """宽松取整：``sticky: 12`` 与 ``sticky: '12'`` 都要能读出来。"""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError:
            return None
    return None


def to_bool(value: object) -> bool:
    """``hide: true`` / ``hide: 'true'`` / ``hide: 1`` 都算真。"""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"true", "yes", "1", "on"}
    if isinstance(value, int):
        return value != 0
    return False


# ---------------------------------------------------------------- 路径解析

_HEX_ESCAPE_RE = re.compile(r"%[0-9A-Fa-f]{2}")


def normalize_asset_key(raw: str) -> str:
    """把图片引用归一成「相对 ``_posts`` 的 POSIX 路径」。

    旧站的引用形状并不统一，全都要能落到同一个键上：

    - ``农大迷你马拉松/9.jpg``   —— 页面包相对路径（``post_asset_folder: true``）
    - ``/img/p15.webp``          —— 站点级 ``source/img`` 下的绝对路径
    - ``img/p5.webp``            —— 少了前导斜杠的站点级路径（旧站笔误，线上 200）
    - ``%E5%86%9C%E5%A4%A7/1.jpg`` —— 百分号编码
    - ``./后端/1.jpg``            —— ``./`` 前缀

    统一规则：去百分号编码 → 反斜杠转正斜杠 → 去掉 ``./`` 与前导 ``/``
    → Unicode NFC 归一（中文文件名在 macOS 上是 NFD，Windows 上是 NFC，
    不归一会让「同一张图」在两台机器上算出不同的键，去重与查找全部失效）。
    """
    text = raw.strip().split("#", 1)[0].split("?", 1)[0]
    if _HEX_ESCAPE_RE.search(text):
        try:
            from urllib.parse import unquote

            text = unquote(text)
        except (ValueError, UnicodeDecodeError):  # pragma: no cover - 极端畸形输入
            pass
    text = text.replace("\\", "/")
    while text.startswith("./"):
        text = text[2:]
    text = text.lstrip("/")
    # PurePosixPath 负责折叠 `a/./b` 与 `a//b`，但不解析 `..`（我们也不需要它）
    normalized = str(PurePosixPath(text))
    return unicodedata.normalize("NFC", normalized)


def resolve_asset(posts_dir: Path, asset_key: str) -> Path | None:
    """把 asset_key 落到磁盘路径。

    旧站的解析顺序（必须与 Hexo 的 ``marked.prependRoot + postAsset`` 一致）：

    1. ``_posts/<key>``           —— 页面包（图片跟文章同名目录放）
    2. ``<hexo 仓库>/source/<key>`` —— 站点级资源（``source/img/...``）

    返回 ``None`` 表示两个位置都没有。放在 ``_posts`` 里**按字面**查找，
    不做大小写或 NFC/NFD 的二次猜测：磁盘上找不到就是找不到，
    静默猜一个相近名字只会让「图丢了」变成「图变成了另一张」。

    安全：拒绝任何逃出根目录的键（``../``），即使上游已经归一化过。
    """
    if not asset_key or asset_key.startswith(".."):
        return None
    for root in (posts_dir, posts_dir.parent):
        candidate = (root / asset_key).resolve()
        try:
            candidate.relative_to(root.resolve())
        except ValueError:
            continue
        if candidate.is_file():
            return candidate
    return None


# ---------------------------------------------------------------- 围栏扫描

_FENCE_OPEN_RE = re.compile(r"^(\s{0,3})(`{3,}|~{3,})\s*([^\s`]*)")


def scan_lines(body: str) -> list[tuple[int, str, bool]]:
    """逐行标注 ``(行号, 行内容, 是否在围栏代码块内)``。

    ``是否在围栏内`` 对**开围栏行与闭围栏行本身**都为 True：它们也属于代码块，
    不能被改写。

    闭合条件按 CommonMark：同种字符、长度不短于开围栏、且后面只有空白。
    这样 `` ```` `` 里包着的 ``` 不会提前闭合，`` ~~~ `` 与 ``` 互不干扰。
    """
    out: list[tuple[int, str, bool]] = []
    fence_char = ""
    fence_len = 0
    for number, line in enumerate(body.splitlines(), start=1):
        if fence_char:
            out.append((number, line, True))
            closing = re.match(
                r"^\s{0,3}(" + re.escape(fence_char) + r"{" + str(fence_len) + r",})\s*$",
                line,
            )
            if closing:
                fence_char = ""
                fence_len = 0
            continue

        opening = _FENCE_OPEN_RE.match(line)
        if opening:
            fence_char = opening.group(2)[0]
            fence_len = len(opening.group(2))
            out.append((number, line, True))
            continue

        out.append((number, line, False))
    return out


_INLINE_CODE_RE = re.compile(r"(?<!`)(`+)(?!`)(.+?)(?<!`)\1(?!`)")


def mask_inline_code(line: str) -> str:
    """把行内代码替换成等长占位，使后续正则不会匹配到其中的内容。

    **等长**是刻意的：行号、列偏移、以及「同一行里 HTML 标签跨行的匹配」
    都不受影响，替换回原文时按位置切片即可。若用变长占位，
    后续所有偏移计算都要做映射表，得不偿失。

    用 U+0000 填充：它不可能出现在 Markdown 正文里，
    也不会被任何引用的正则匹配到。
    """
    return _INLINE_CODE_RE.sub(lambda m: "\x00" * len(m.group(0)), line)


# ---------------------------------------------------------------- 图片提取

# HTML <img ...>：**不跨行**匹配（旧站所有 <img> 都在单行内闭合；
# 允许跨行会把「<img 开标签 ... 正文 ... 另一个标签」误并成一处引用）。
#
# 边界必须用**两个断言**夹住，不能只写 ``<img\b``：
#
# - ``(?<![-\w])`` 拦 ``<image>``（SVG 的 image 元素）；
# - ``(?=[\s/>])`` 拦 ``<img-x>``。
#
# 为什么 ``\b`` 不行：单词边界只在「单词字符与非单词字符之间」成立，而 ``-``
# 是非单词字符，``<img-x>`` 里 ``img`` 之后的 ``\b`` **照样成立**——实测就是
# 靠这条测试抓出来的。<img> 标签名后合法的续接只有空白、``/``、``>`` 三种。
_HTML_IMG_RE = re.compile(r"(?<![-\w])<img(?=[\s/>])[^>]*>", re.IGNORECASE)
_ATTR_RE = re.compile(r"""([a-zA-Z_:][-a-zA-Z0-9_:.]*)\s*=\s*("[^"]*"|'[^']*'|[^\s"'>]+)""")

# Markdown 图片 ![...](...) ；链接 [..](..) 不算图片
_MD_IMG_RE = re.compile(r"!\[([^\]]*)\]\(\s*(<[^>]*>|[^)\s]+)(?:\s+[\"'(]([^)\"']*)[\"')])?\s*\)")

# 引用式图片 ![alt][id] —— 旧站未使用；识别到就报告而不是静默漏掉，
# 因为「没解析到」和「本来就没有」在报告里必须能区分开。
_MD_IMG_REF_RE = re.compile(r"!\[([^\]]*)\]\[([^\]]*)\]")


def _attr_value(tag: str, name: str) -> str:
    for match in _ATTR_RE.finditer(tag):
        if match.group(1).lower() == name:
            value = match.group(2)
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            return value
    return ""


def _decode_entities(text: str) -> str:
    """还原最基本的 HTML 实体。

    alt 里的 ``&amp;`` ``&quot;`` 若不还原，会原样写进新站的 Markdown 图片标题，
    再被 marked 二次转义成 ``&amp;amp;``。
    """
    for entity, char in (
        ("&amp;", "&"),
        ("&quot;", '"'),
        ("&#39;", "'"),
        ("&lt;", "<"),
        ("&gt;", ">"),
    ):
        text = text.replace(entity, char)
    return text


def extract_images(post: ParsedPost, posts_dir: Path) -> list[ImageRef]:
    """按行提取正文里的图片引用（围栏与行内代码内的一律跳过）。

    返回顺序与正文出现顺序一致——测试依赖这一点做逐处比对。
    """
    images: list[ImageRef] = []
    for number, line, in_fence in scan_lines(post.body):
        if in_fence:
            continue
        masked = mask_inline_code(line)

        for match in _HTML_IMG_RE.finditer(masked):
            tag = match.group(0)
            src = _attr_value(tag, "src")
            if not src:
                post.diagnostics.append(
                    Diagnostic("warn", "html-img-no-src", f"<img> 没有 src：{tag[:60]}", number)
                )
                continue
            images.append(
                _make_ref(post, posts_dir, src, _attr_value(tag, "alt"), "html", number, tag)
            )

        for match in _MD_IMG_RE.finditer(masked):
            raw_src = match.group(2)
            if raw_src.startswith("<") and raw_src.endswith(">"):
                raw_src = raw_src[1:-1]
            images.append(
                _make_ref(
                    post,
                    posts_dir,
                    raw_src,
                    match.group(1),
                    "markdown",
                    number,
                    match.group(0),
                )
            )

        for match in _MD_IMG_REF_RE.finditer(masked):
            post.diagnostics.append(
                Diagnostic(
                    "warn",
                    "markdown-ref-image",
                    f"引用式图片暂不处理：![{match.group(1)}][{match.group(2)}]",
                    number,
                )
            )
    return images


def _make_ref(
    post: ParsedPost,
    posts_dir: Path,
    raw: str,
    alt: str,
    origin: ImageOrigin,
    line: int,
    matched_text: str,
) -> ImageRef:
    key = normalize_asset_key(raw)
    remote = raw.startswith(("http://", "https://", "//", "data:"))
    resolved = None if remote else resolve_asset(posts_dir, key)
    if resolved is None and not remote:
        post.diagnostics.append(
            Diagnostic("error", "missing-asset", f"图片在磁盘上不存在：{raw}", line)
        )
    return ImageRef(
        raw=raw,
        asset_key=key,
        origin=origin,
        alt=_decode_entities(alt).strip(),
        line=line,
        exists=resolved is not None,
        matched_text=matched_text,
    )


# ---------------------------------------------------------------- 单篇解析


def parse_post(path: Path) -> ParsedPost:
    """解析单个 ``.md`` 文件（不做磁盘图片查找，纯文本层）。"""
    text = path.read_text(encoding="utf-8")
    raw_fm, body = split_frontmatter(text)
    fm = parse_frontmatter(raw_fm)

    post = ParsedPost(
        source=path,
        frontmatter=fm,
        body=body,
        title=str(fm.get("title") or path.stem).strip(),
        date_raw=_optional_str(fm.get("date")),
        tags=split_taxonomy(fm.get("tags")),
        categories=split_taxonomy(fm.get("categories")),
        cover_raw=_optional_str(fm.get("index_img")),
        abbrlink=_optional_str(fm.get("abbrlink")),
        hidden=to_bool(fm.get("hide")),
        sticky=to_int(fm.get("sticky")),
    )

    if not raw_fm:
        post.diagnostics.append(Diagnostic("warn", "no-frontmatter", "文件没有 frontmatter"))
    if not post.title:
        post.diagnostics.append(Diagnostic("error", "no-title", "标题为空"))
    if not post.date_raw:
        post.diagnostics.append(Diagnostic("warn", "no-date", "没有 date，将回退到文件修改时间"))

    post.fences = [line for _, line, in_fence in scan_lines(body) if in_fence]
    return post


def _optional_str(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def parse_repository(posts_dir: Path) -> list[ParsedPost]:
    """解析目录下全部 ``.md``，按发布时间升序返回（导入顺序即此序）。"""
    posts: list[ParsedPost] = []
    for path in sorted(posts_dir.glob("*.md")):
        post = parse_post(path)
        post.images = extract_images(post, posts_dir)
        posts.append(post)
    posts.sort(key=lambda item: (item.date_raw or "", item.stem))
    return posts


# ---------------------------------------------------------------- 正文重写


def render_body(post: ParsedPost, replacements: dict[str, str]) -> str:
    """把正文里的图片引用替换成新站的 Markdown 图片语法。

    ``replacements`` 是 ``asset_key -> 新 URL``。

    设计取舍（逐条都有原因）：

    - **统一改写成 Markdown 图片**，不再保留 ``<img>`` 标签。新站的
      DOMPurify 配置禁掉 ``style``、并把 ``class`` 裁到只剩 ``language-*``，
      旧站那些 ``zoom:50%`` / 卡片阴影 / 居中标题全部会被剥掉——留着标签
      只是留一堆死属性。Markdown 图片则完全走渲染器的正常路径。
    - **原样保留 alt**：它是这张图唯一的文字语义（无障碍 + 图片加载失败时的提示）。
    - **保留 HTML 注释**：旧站正文里有 ``<!-- 注意：... -->`` 这类作者备注，
      属于内容的一部分，不擅自删除。
    - **找不到替换目标时保持原样**（例如图片缺失）：宁可留一个坏引用让
      预检报告把它列出来，也不要替换成空字符串把线索抹掉。
    """
    occurrences: dict[str, int] = {}
    lines = post.body.splitlines(keepends=True)

    # 先把「行号 -> 该行的引用」分好组，再逐行重写：
    # 同一行里出现多个 <img> 时（旧站确实有），由 _rewrite_line 按行内位置处理。
    by_line: dict[int, list[ImageRef]] = {}
    for ref in post.images:
        by_line.setdefault(ref.line, []).append(ref)

    out: list[str] = []
    for number, line in enumerate(lines, start=1):
        refs = by_line.get(number)
        if refs:
            line = _rewrite_line(line, refs, replacements, occurrences)
        out.append(line)
    return "".join(out)


def _rewrite_line(
    line: str,
    refs: list[ImageRef],
    replacements: dict[str, str],
    occurrences: dict[str, int],
) -> str:
    """重写一行里的一处或多处图片引用。

    两处关键设计：

    1. **用掩码串定位，用原文切片写回**。行内代码里可能有 ``<img src="x">``
       这样的示例，掩码（等长，见 :func:`mask_inline_code`）让它不会被找到；
       而掩码与原串逐字符对齐，所以掩码里找到的偏移对原文同样成立。
    2. **从右往左替换**。先算出本行所有引用的 ``(起点, 终点)`` 并按起点排序，
       再从最右边一处开始替换——右侧的偏移永远不会被左侧的长度变化影响，
       于是不需要维护任何偏移平移表。
    """
    masked = mask_inline_code(line)
    spans: list[tuple[int, int, ImageRef]] = []
    # 先算出每处引用的位置再排序：同一行里出现多个引用时（旧站确实有），
    # 必须按**行内从左到右**的顺序替换，不能依赖调用方给的顺序。
    # 掩码后发现不了的（落在行内代码里）直接排除，不参与定位。
    located = [(masked.find(ref.matched_text), ref) for ref in refs]
    located = [(pos, ref) for pos, ref in located if pos >= 0]
    for position, ref in sorted(located, key=lambda item: item[0]):
        found = masked.find(ref.matched_text, position)
        if found < 0:  # pragma: no cover - 位置已在上面算过，这里只是防御
            continue
        spans.append((found, found + len(ref.matched_text), ref))

    result = line
    # 逆序：右侧先替换，左侧偏移不受影响
    for start, end, ref in reversed(spans):
        url = replacements.get(ref.asset_key)
        if not url:
            continue
        occurrences[ref.asset_key] = occurrences.get(ref.asset_key, 0) + 1
        alt = _escape_markdown_text(ref.alt) if ref.alt else f"图{occurrences[ref.asset_key]}"
        result = result[:start] + f"![{alt}]({url})" + result[end:]
    return result


_MD_SPECIAL_RE = re.compile(r"([\\\[\]()|])")


def _escape_markdown_text(text: str) -> str:
    """转义会破坏 Markdown 图片语法的字符。

    只处理这六种：``\\`` ``[`` ``]`` ``(`` ``)`` ``|``。
    前五个会让 marked 提前截断 alt，``|`` 会破坏表格单元格。
    刻意不做全量转义——把中文标点也转成 ``\\，`` 只会让 alt 变得难读，
    而 alt 是这张图唯一的文字语义。
    """
    return _MD_SPECIAL_RE.sub(r"\\\1", text)


# ---------------------------------------------------------------- 迁移尾注

DISCLAIMER_TEMPLATE = (
    "> **关于本文**：本文于 {date} 从旧站 [furinaluna.top]({origin}) 迁移而来，"
    "原始地址：<{origin}{link}>。"
)


def build_disclaimer(origin: str, abbrlink: str | None, migrated_on: str) -> str:
    """生成迁移尾注。

    为什么要写进正文而不是只存数据库：这段文字是**给读者与未来的自己**看的，
    要能随文章一起被复制、导出、订阅。旧站的 ``abbrlink`` 是它唯一的稳定
    ID，不记下来就再也回不到原文了。
    """
    link = f"/posts/{abbrlink}.html" if abbrlink else "/"
    return DISCLAIMER_TEMPLATE.format(date=migrated_on, origin=origin.rstrip("/"), link=link)


def content_hash(data: bytes) -> str:
    """图片去重用的内容指纹。"""
    return hashlib.sha256(data).hexdigest()
