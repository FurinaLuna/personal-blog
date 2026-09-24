"""文章域模块级纯函数（``ArticleService`` 已按读写拆走）。

本模块只留 ``_is_year_month``：它是 ``list_by_month`` 的参数校验纯函数，
而 ``tests/test_articles.py::TestArchive::test_year_month_validator`` **直接**
``from app.services.article_service import _is_year_month``。本次重构的约束是
「不改测试一个字」，所以它的导入路径必须留在原地——跟着方法一起搬走会改坏测试，
加一层转发模块又只是把路径绕一下。宁可留一个 13 行的模块，也不动测试。

读路径：``app.services.article_query_service``（``ArticleQueryService``）。
写路径：``app.services.article_command_service``（``ArticleCommandService``）。
"""

from __future__ import annotations


def _is_year_month(value: str) -> bool:
    """校验 ``YYYY-MM``，顺便确认月份在 1-12 之间（``2026-13`` 要拦住）。"""
    if len(value) != 7 or value[4] != "-":
        return False
    year, month = value[:4], value[5:]
    return year.isdigit() and month.isdigit() and 1 <= int(month) <= 12
