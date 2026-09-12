"""请求上下文：请求 ID 透传 + 结构化日志。

为什么这两件事要放一起：日志的价值取决于能不能把一次请求产生的多行日志串起来。
请求 ID 就是这个「串」，所以生成、注入、写入日志三者必须同一处管理，
散在中间件和业务代码里迟早会漏。
"""

from __future__ import annotations

import json
import logging
import re
import sys
import uuid
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any

# 请求 ID 的合法形态：客户端可以自带（便于跨服务串联），但必须限制字符集与长度。
# 不校验就直接回显到响应头和日志里，等于把日志注入（伪造换行/JSON 结构）的口子留给外部。
_REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,64}$")

REQUEST_ID_HEADER = "X-Request-ID"

_current_request_id: ContextVar[str] = ContextVar("request_id", default="-")


def new_request_id() -> str:
    return uuid.uuid4().hex[:16]


def sanitize_request_id(raw: str | None) -> str:
    """校验客户端传入的请求 ID，不合法就换一个。

    Args:
        raw: 请求头里的原始值，可能是 None 或任意内容。

    Returns:
        合法且可直接写入日志/响应头的请求 ID。
    """
    if raw and _REQUEST_ID_PATTERN.match(raw):
        return raw
    return new_request_id()


def set_request_id(request_id: str) -> None:
    _current_request_id.set(request_id)


def get_request_id() -> str:
    """取当前请求 ID；不在请求上下文里时返回 ``-``（例如脚本、定时任务）。"""
    return _current_request_id.get()


class JsonFormatter(logging.Formatter):
    """把日志记录序列化成一行 JSON。

    相比人读的文本格式，它的价值在于：日志收集器可以直接按字段过滤/聚合，
    不需要为「提取状态码」写正则。
    """

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname.lower(),
            "logger": record.name,
            "msg": record.getMessage(),
            "request_id": get_request_id(),
        }

        # 业务侧通过 logger.info("...", extra={"extra_fields": {...}}) 附加结构化字段
        extra_fields = getattr(record, "extra_fields", None)
        if isinstance(extra_fields, dict):
            payload.update(extra_fields)

        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)

        return json.dumps(payload, ensure_ascii=False)


def setup_logging(*, json_output: bool = True, level: int = logging.INFO) -> None:
    """配置应用日志。

    只接管 ``blog`` 这个 logger，不动 root——uvicorn 自己的访问日志格式
    由它自己管，强行改写会把启动信息也变成 JSON，反而更难读。

    幂等：重复调用不会叠加 handler（测试会反复 create_app）。
    """
    logger = logging.getLogger("blog")
    logger.setLevel(level)
    logger.propagate = False

    for handler in list(logger.handlers):
        logger.removeHandler(handler)

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        JsonFormatter()
        if json_output
        else logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s")
    )
    logger.addHandler(handler)
