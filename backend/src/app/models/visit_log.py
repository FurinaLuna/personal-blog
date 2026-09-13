"""文章访问日志模型（统计趋势数据源）。

只记三样东西：哪篇文章、哪一天（UTC）、谁（IP 的 HMAC 摘要）。
逐次访问全记（PV 语义），UV 靠查询时 ``count(distinct ip_hash)`` 聚合；
**不存明文 IP**——统计只需要「区分不同访客」，隐私最小化，
换密钥即全量失效也无所谓（趋势曲线不差这几个人）。
"""

from __future__ import annotations

from datetime import date

from sqlalchemy import Date, ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class VisitLog(Base, TimestampMixin):
    """一条 = 一次已发布文章的详情访问。"""

    __tablename__ = "visit_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    # 文章删除时日志跟着删：统计趋势面向「现存内容」，不保留孤儿数据
    article_id: Mapped[int] = mapped_column(
        ForeignKey("articles.id", ondelete="CASCADE"), nullable=False
    )
    # UTC 日期：按天聚合的分组键。存 date 而非 datetime——细粒度时间对
    # 「按天看曲线」毫无价值，还让索引体积翻倍
    date: Mapped[date] = mapped_column(Date, nullable=False)
    # HMAC-SHA256 输出的十六进制（64 字符）。None 位置（测试 ASGITransport
    # 取不到 client）由服务层归一成常量摘要，列上保持非空
    ip_hash: Mapped[str] = mapped_column(String(64), nullable=False)

    # (article_id, date)：单篇文章某天的聚合查询；date：全局按天聚合（仪表盘）
    __table_args__ = (
        Index("ix_visit_logs_article_id_date", "article_id", "date"),
        Index("ix_visit_logs_date", "date"),
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<VisitLog id={self.id} article={self.article_id} date={self.date}>"
