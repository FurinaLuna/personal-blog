"""工具模块（横切基础层，被所有业务层引用，自身不依赖任何业务层）。

各子模块按职责独立，使用时**直连子模块导入**：

- ``app.utils.exceptions`` —— 领域异常族（service 抛、main 统一翻译成 HTTP）
- ``app.utils.security``  —— 密码哈希与 JWT 签发/校验
- ``app.utils.text``      —— 纯字符串工具（slugify / 摘要提取 / 阅读时长估算）
- ``app.utils.slug``      —— slug 唯一化（重名自动加后缀）
- ``app.utils.storage``   —— 存储后端抽象（``StorageBackend`` 协议 + 本地实现）
- ``app.utils.ratelimit`` —— 进程内滑动窗口限流器
- ``app.utils.logging``   —— 请求 ID 与 JSON 日志

刻意**不提供聚合门面**：此前门面导出与实际使用完全脱节（全项目 0 处
``from app.utils import ...``，调用方全部直连子模块），保留只会制造
「在门面里加一行就全局生效」的错觉。新增工具请建独立子模块并在此登记。
"""
