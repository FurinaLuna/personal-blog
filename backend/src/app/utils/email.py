"""邮件发送（utils 层叶子：只依赖 config，不知道任何业务概念）。

设计取舍：
- 任何失败（网络 / 认证 / 超时）都吞掉只记日志返回 False——通知是旁路功能，
  绝不能拖垮评论主流程；
- ``smtp_enabled=False`` 直接短路，让「没配 SMTP 的个人博客」零成本运行。
"""

from __future__ import annotations

import logging
from email.header import Header
from email.mime.text import MIMEText

import aiosmtplib

from app.config import settings

logger = logging.getLogger(__name__)


class EmailSender:
    """极简 SMTP 发信器。一封信一次连接——个人博客量级不值得做连接复用。"""

    async def send(self, *, to: str, subject: str, text: str) -> bool:
        """发一封纯文本邮件。

        Args:
            to: 收件人地址。
            subject: 主题（可含中文，自动按 UTF-8 编码头字段）。
            text: 纯文本正文。

        Returns:
            是否发送成功。失败细节只在服务端日志里，不外抛。
        """
        if not settings.smtp_enabled:
            return False
        try:
            message = MIMEText(text, "plain", "utf-8")
            # 主题里的中文必须显式编码，否则部分客户端显示成乱码
            message["Subject"] = Header(subject, "utf-8")
            message["From"] = settings.smtp_from or settings.smtp_username
            message["To"] = to
            async with aiosmtplib.SMTP(
                hostname=settings.smtp_host,
                port=settings.smtp_port,
                use_tls=settings.smtp_use_tls,
            ) as smtp:
                await smtp.login(settings.smtp_username, settings.smtp_password)
                await smtp.send_message(message)
            return True
        except Exception:
            logger.exception("邮件发送失败 to=%s subject=%s", to, subject)
            return False
