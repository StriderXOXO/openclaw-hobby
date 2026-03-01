"""日志工具模块。

提供统一的日志格式和结构化活动日志工具。
"""

import logging


def setup_logging(name: str, level: int = logging.INFO):
    """配置标准日志格式。"""
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[logging.StreamHandler()],
    )
    return logging.getLogger(name)
