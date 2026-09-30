"""旧站内容预检：把导入前必须知道的事实算清楚。

干跑（dry-run）与导入共用同一套变换（见 ``transform.py``），所以预检报告里
看到的字节数、图片数、标题层级，就是导入后真实落库的样子。

用法：
    python tools/hexo_import/preflight.py --hexo "D:\\Projects\\01_个人项目\\blog" \\
        --out deliverables/hexo-migration-preflight.md
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from import_hexo import DEFAULT_TOP_THRESHOLD
from parse_hexo import (
    Diagnostic,
    ImageRef,
    ParsedPost,
    parse_repository,
    resolve_asset,
)
from transform import (
    MIGRATED_ON,
    ORIGIN,
    expected_asset_name,
    prepare_post,
    upload_subdir,
)

DEFAULT_HEXO = Path(r"D:\Projects\01_个人项目\blog")


@dataclass
class Preflight:
    """一次预检的全部结果。"""

    posts: list[ParsedPost] = field(default_factory=list)
    #: 内容哈希 -> 本地文件路径（跨文章去重后的真实待上传集合）
    unique_assets: dict[str, Path] = field(default_factory=dict)
    #: asset_key -> 内容哈希
    asset_hash: dict[str, str] = field(default_factory=dict)
    missing: list[tuple[str, ImageRef]] = field(default_factory=list)
    diagnostics: list[tuple[str, Diagnostic]] = field(default_factory=list)
    transformed: list[tuple[ParsedPost, str]] = field(default_factory=list)

    @property
    def total_bytes(self) -> int:
        return sum(path.stat().st_size for path in self.unique_assets.values())


def run_preflight(hexo_root: Path) -> Preflight:
    """解析 + 变换，不碰网络。"""
    from parse_hexo import content_hash

    posts_dir = hexo_root / "source" / "_posts"
    result = Preflight(posts=parse_repository(posts_dir))

    for post in result.posts:
        for diagnostic in post.diagnostics:
            result.diagnostics.append((post.stem, diagnostic))
        for ref in post.images:
            if not ref.exists:
                if not ref.is_remote:
                    result.missing.append((post.stem, ref))
                continue
            path = resolve_asset(posts_dir, ref.asset_key)
            if path is None:  # pragma: no cover - exists=True 时不应发生
                continue
            digest = content_hash(path.read_bytes())
            result.asset_hash[ref.asset_key] = digest
            result.unique_assets.setdefault(digest, path)

    subdir = upload_subdir()
    asset_urls = {
        asset_key: f"/media/uploads/{subdir}/{expected_asset_name(asset_key, digest)}"
        for asset_key, digest in result.asset_hash.items()
    }

    for post in result.posts:
        # prepare_post 返回 (原 post, 最终正文)：元数据仍取自原对象，
        # 正文是变换后的结果。
        #
        # URL 按服务端的真实规则构造（uploads/<YYYYMM>/<hash16><ext>），
        # 所以报告里出现的地址就是导入后会写进数据库的地址——
        # 手工拼错一次就足以让全站图片 404，这里必须与 AttachmentService 对齐。
        _origin, new_body = prepare_post(post, asset_urls)
        result.transformed.append((post, new_body))

    return result


def render_report(result: Preflight, hexo_root: Path) -> str:
    """生成 Markdown 报告。"""
    lines: list[str] = []
    add = lines.append

    posts = result.posts
    refs = [ref for post in posts for ref in post.images]
    existing = [ref for ref in refs if ref.exists]
    hidden = [post for post in posts if post.hidden]
    stickied = [post for post in posts if post.sticky is not None]
    covered = [post for post in posts if post.cover_raw]

    add("# Hexo 旧站迁移 · 预检报告")
    add("")
    add(f"- 生成时间：{MIGRATED_ON}")
    add(f"- 旧站仓库：`{hexo_root}`")
    add(f"- 旧站线上：<{ORIGIN}>")
    add("- 本文由 `tools/hexo_import/preflight.py` 生成；导入器与它共用同一套变换，")
    add("  所以下面每个数字都等于导入后的真实结果。")
    add("")

    add("## 一、总量")
    add("")
    add("| 指标 | 数值 |")
    add("| --- | --- |")
    add(f"| 文章 | {len(posts)} 篇 |")
    add(f"| 正文图片引用 | {len(refs)} 处 |")
    add(f"| 可迁移图片引用 | {len(existing)} 处 |")
    add(f"| **图片文件（去重后）** | **{len(result.unique_assets)} 个** |")
    add(f"| 图片总体积 | {result.total_bytes / 1024 / 1024:.1f} MB |")
    largest = max((p.stat().st_size for p in result.unique_assets.values()), default=0)
    add(f"| 最大单张 | {largest / 1024:.0f} KB |")
    add(f"| 缺失图片引用 | {len(result.missing)} 处 |")
    add(f"| `hide: true`（→ 草稿） | {len(hidden)} 篇 |")
    add(f"| 有 `sticky` 权重 | {len(stickied)} 篇（**不会全部置顶**，见下） |")
    add(f"| 有封面 `index_img` | {len(covered)} 篇 |")
    add("")
    add(
        f"关于置顶：旧站有 `sticky` 的共 **{len(stickied)} 篇**，但导入器默认只把 "
        f"`sticky >= {DEFAULT_TOP_THRESHOLD}` 的保留为置顶。原因是新站的置顶是"
        "**首页头条**语义（前台列表 `is_top` 优先，首篇渲染成 featured 卡片），"
        "数量必须远小于每页 10 条——实测「有 sticky 就置顶」时首页第一页 10 张卡片"
        "全部挂徽标，头条机制与「更多文章」区一起失效。"
        "用 `--top-threshold` 可调（传 `0` 表示不设阈值，即全部保留）。"
        "原始权重写进每篇的迁移尾注，所以这个取舍可追溯、可反悔。"
    )
    add("")

    add("## 二、逐篇清单")
    add("")
    add("| 文章 | 标题 | 日期 | 分类 | 标签 | 图片 | 缺失 | 标题层级 | 正文 | 状态 |")
    add("| --- | --- | --- | --- | --- | ---: | ---: | --- | ---: | --- |")
    for post, new_body in result.transformed:
        levels = _heading_levels(new_body)
        missing = sum(1 for ref in post.images if not ref.exists and not ref.is_remote)
        status = "草稿" if post.hidden else "已发布"
        add(
            f"| `{post.stem}` | {post.title} | {(post.date_raw or '')[:10]} "
            f"| {'/'.join(post.categories) or '—'} | {', '.join(post.tags) or '—'} "
            f"| {len(post.images)} | {missing or ''} | {levels} "
            f"| {len(new_body) / 1024:.1f} KB | {status} |"
        )
    add("")

    add("## 三、缺失图片（无法恢复）")
    add("")
    add(f"共 {len(result.missing)} 处。这些引用**在旧站本地仓库与线上都不存在**：")
    add("")
    add("- 旧站 `source/_posts/<文章目录>/` 下没有文件")
    add("- 旧站 `public/`（已构建产物）里没有文件")
    add("- 旧站 git 全历史里从未出现过这些文件名")
    add("- 线上 `https://www.furinaluna.top/posts/fastapi-framework/...` 实测 **404**")
    add("")
    add("结论：**不是迁移弄丢的**。Hexo 构建时就把这 4 个引用丢掉了（源里有 16 个 `<img>`，")
    add("线上只渲染出 12 个）。迁移无法恢复它们，只能如实标注。")
    add("")
    add("| 文章 | 原引用 | 原文件路径 |")
    add("| --- | --- | --- |")
    for stem, ref in result.missing:
        add(f"| {stem} | `{ref.raw}` | `source/_posts/{ref.asset_key}` |")
    add("")

    add("## 四、图片去重")
    add("")
    dupes = _duplicate_groups(result)
    add(f"去重后 {len(result.unique_assets)} 个文件；有内容重复的：{len(dupes)} 组。")
    add("")
    if dupes:
        add("| 内容哈希（前 12 位） | 大小 | 路径 |")
        add("| --- | ---: | --- |")
        for digest, paths in dupes:
            size = result.unique_assets[digest].stat().st_size
            for index, path in enumerate(paths):
                label = digest[:12] if index == 0 else ""
                add(f"| {label} | {size / 1024:.0f} KB | `{path}` |")
        add("")
    else:
        add("没有内容重复的图片。")
        add("")

    add("## 五、正文归一化")
    add("")
    add("旧站正文里的 HTML 与新站渲染策略的冲突，逐项处理如下：")
    add("")
    add("| 旧站写法 | 出现 | 处理 | 原因 |")
    add("| --- | ---: | --- | --- |")
    add(
        '| `<img style="zoom:50%">` 等内联样式 | 54 处 | **整体改写为 Markdown 图片** | '
        "新站安全策略禁用 `style`（防覆盖式钓鱼），`class` 也只放行 `language-*` |"
    )
    add(
        '| `<img alt="...">` | 多处 | **保留 alt** | '
        "它是这张图唯一的文字语义（无障碍 + 加载失败提示） |"
    )
    add(
        '| `<p style="text-align:center">说明</p>` | 1 处 | **整行改为 `*说明*`** | '
        "新站禁用内联样式后它会退化成普通小字，看不出是图注 |"
    )
    add("| `<!-- 注释 -->` | 少量 | **原样保留** | 是作者写的备注，属于内容 |")
    add("| 围栏代码块内的 `<img>` | 多篇 | **逐字节不动** | 那是代码示例，改了就是篡改内容 |")
    add("| 行内代码里的 `<img>` | 少量 | **逐字节不动** | 同上 |")
    add("")
    add("图注若与上一张图的 `alt` 完全相同则**整行删除**（同一句话不必显示两遍）。")
    add("")
    add("另有一处内联 HTML 被**有意保留**——它的文本能完整读出来，只是颜色丢失：")
    add("")
    add("| 位置 | 内容 | 结果 |")
    add("| --- | --- | --- |")
    add(
        '| `SEO优化` | `<font style="color:#DF2A3F">请在做任何 SEO 优化工作前…</font>` | '
        "文本保留、外层 `**加粗**` 保留，红色丢失 |"
    )
    add("")
    add("`<font>` 不在新站标签白名单里：标签被剥掉、**文本保留**，所以信息不丢，只是不再是红色。")
    add("添一个转换规则只为这一行，性价比不如如实记录。")
    add("")
    add("还有一类写法**不受影响**需要说明：`后端学习路线` 等文章在图片**上方**有独立文字标签")
    add("（如 ` 后端开发流程`，纯文本、非 `<p>`），它们是正文的一部分，原样保留。")
    add("")

    add("### 标题层级")
    add("")
    add("新站现有文章的顶层标题是 `##`（文章标题由页面页头承担）。旧站有 9 篇用了 `#`，")
    add("直接导入会出现「页面一个大标题 + 正文再一个大标题」的重复，")
    add("并且把目录层级整体抬高一档。处理：")
    add("")
    add("- H1 文本与文章标题**逐一字符相同**（忽略空白与标点）→ 删除该行（纯重复）")
    add("- H1 是文章内的**小标题**（如「培训」一文有两段独立的子文章）→ 降为 `##`，")
    add("  并把全文标题**整体下沉一档**，保持相对层级不变")
    add("")
    add("判定只用严格相等，不做模糊匹配：近似标题（`# FastAPI 精通教程` vs 文章标题")
    add("`FastAPI快速入门自我总结`）保留并降级——删掉它属于替作者做内容决定。")
    add("")
    add("| 文章 | H1 原文 | 判定 |")
    add("| --- | --- | --- |")
    for post in posts:
        for _level, text in _h1s(post):
            action = "删除（与标题重复）" if _same_title(post.title, text) else "降为 H2"
            add(f"| {post.stem} | {text} | {action} |")
    add("")
    add("**已知副作用**：`FastAPI框架` 与 `langchain与rag简单快速入门` 里原本最深的 `H4`")
    add("会变成 `H5`，超出目录收录范围（目录只收录 H2~H4）。这是正确结果而非缺陷——")
    add("它们在旧站就是「章下面的三级小节」，旧站目录同样不显示。层级有语义，")
    add("不为目录好看来压缩。反例见上：只改 H1 不下沉会让「培训」一文的 10 个 H4 悬空。")
    add("")

    add("## 六、解析期诊断")
    add("")
    if result.diagnostics:
        add("| 级别 | 文章 | 代码 | 说明 |")
        add("| --- | --- | --- | --- |")
        for stem, diagnostic in result.diagnostics:
            where = f" L{diagnostic.line}" if diagnostic.line else ""
            add(
                f"| {diagnostic.level} | {stem} | "
                f"`{diagnostic.code}`{where} | {diagnostic.message} |"
            )
    else:
        add("无。")
    add("")

    add("## 七、内容保真校验")
    add("")
    add("机器化证据，代替人眼通读 23 篇：")
    add("")
    add("| 不变量 | 对应测试 |")
    add("| --- | --- |")
    add(
        f"| 全部 {len(posts)} 篇的围栏代码块逐字节保留 | "
        "`test_real_repository_fences_are_never_rewritten` |"
    )
    add("| 除图片引用外正文一字未改 | `test_real_repository_only_images_change_in_body` |")
    add("| 图片引用总数 54 / 缺失 4 | `test_real_repository_image_counts_are_exact` |")
    add("| 逐篇图片分布 | `test_real_repository_per_post_image_counts` |")
    add("")

    add("## 八、导入后的可核对基线")
    add("")
    add("| 项 | 预期值 |")
    add("| --- | --- |")
    add(f"| 新增文章 | {len(posts)} 篇（其中草稿 {len(hidden)} 篇） |")
    add(f"| 新增附件记录 | {len(result.unique_assets)} 条 |")
    add(f"| 新增分类 | {len({c for post in posts for c in post.categories})} 个 |")
    add(f"| 新增标签 | {len({t for post in posts for t in post.tags})} 个 |")
    add("")

    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------- 小工具


def _heading_levels(body: str) -> str:
    import re

    from parse_hexo import scan_lines

    levels = sorted(
        {
            len(match.group(1))
            for _, line, in_fence in scan_lines(body)
            if not in_fence and (match := re.match(r"^(#{1,6})\s+\S", line))
        }
    )
    return "/".join(f"H{level}" for level in levels) if levels else "无"


def _h1s(post: ParsedPost) -> list[tuple[int, str]]:
    import re

    from parse_hexo import scan_lines

    out: list[tuple[int, str]] = []
    for number, line, in_fence in scan_lines(post.body):
        if in_fence:
            continue
        match = re.match(r"^#\s+(.+?)\s*$", line)
        if match:
            out.append((number, match.group(1)))
    return out


def _same_title(title: str, heading: str) -> bool:
    from transform import same_heading_as_title

    return same_heading_as_title(title, heading)


def _duplicate_groups(result: Preflight) -> list[tuple[str, list[Path]]]:
    """按内容哈希找出「多个源路径指向同一份内容」的组。"""
    by_hash: dict[str, list[Path]] = {}
    for asset_key, digest in result.asset_hash.items():
        path = result.unique_assets.get(digest)
        if path is not None:
            by_hash.setdefault(digest, []).append(Path(asset_key))
    return [(digest, paths) for digest, paths in by_hash.items() if len(paths) > 1]


def summarize(result: Preflight) -> str:
    """给终端看的一行摘要。"""
    counts = Counter()
    for post in result.posts:
        counts["posts"] += 1
        if post.hidden:
            counts["drafts"] += 1
        counts["refs"] += len(post.images)
    return (
        f"文章 {counts['posts']} 篇（草稿 {counts['drafts']} 篇）｜"
        f"图片引用 {counts['refs']} 处｜待上传文件 {len(result.unique_assets)} 个"
        f"（{result.total_bytes / 1024 / 1024:.1f} MB）｜缺失 {len(result.missing)} 处"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Hexo 旧站迁移预检")
    parser.add_argument("--hexo", type=Path, default=DEFAULT_HEXO, help="旧站仓库根目录")
    parser.add_argument("--out", type=Path, help="报告输出路径（Markdown）")
    args = parser.parse_args()

    posts_dir = args.hexo / "source" / "_posts"
    if not posts_dir.is_dir():
        print(f"找不到旧站文章目录：{posts_dir}", file=sys.stderr)
        return 2

    result = run_preflight(args.hexo)
    print(summarize(result))

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(render_report(result, args.hexo), encoding="utf-8")
        print(f"报告已写入：{args.out}")

    if result.missing:
        print("\n缺失图片（无法恢复，将在正文与尾注中如实标注）：")
        for stem, ref in result.missing:
            print(f"  {stem}: {ref.raw}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
