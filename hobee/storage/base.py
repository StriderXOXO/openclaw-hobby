"""存储后端抽象接口。

所有兴趣源守护进程通过此接口存储和查询内容记录。
默认提供飞书多维表格 (Feishu Bitable) 实现。
你可以实现自己的后端（Notion、Airtable、SQLite 等），只需继承 StorageBackend。
"""

from abc import ABC, abstractmethod
from typing import Optional


class StorageBackend(ABC):
    """存储后端抽象基类。

    每个兴趣源（podcast / youtube / twitter）都有一个独立的 StorageBackend 实例，
    对应一张表/集合。
    """

    @abstractmethod
    def create_record(self, fields: dict) -> str:
        """创建一条记录。

        Args:
            fields: 字段名 → 值的字典。

        Returns:
            记录 ID（字符串）。
        """

    @abstractmethod
    def update_record(self, record_id: str, fields: dict) -> None:
        """更新已有记录的字段。

        Args:
            record_id: 记录 ID。
            fields: 要更新的字段字典。
        """

    @abstractmethod
    def find_record(self, field_name: str, field_value: str) -> Optional[dict]:
        """按字段值查找记录（用于去重）。

        Args:
            field_name: 查询的字段名。
            field_value: 查询的字段值。

        Returns:
            匹配的记录字典（含 record_id），未找到返回 None。
        """

    @abstractmethod
    def list_records(
        self,
        filter_expr: Optional[str] = None,
        page_size: int = 100,
    ) -> list[dict]:
        """列出记录（可选过滤）。

        Args:
            filter_expr: 后端特定的过滤表达式（飞书/Notion 各有语法）。
            page_size: 每页记录数。

        Returns:
            记录字典列表。
        """

    @abstractmethod
    def upload_media(self, file_path: str, file_name: str) -> str:
        """上传媒体文件（转录文件、截图等）。

        Args:
            file_path: 本地文件路径。
            file_name: 上传后的文件名。

        Returns:
            文件标识符（file_token 等），用于关联到记录的附件字段。
        """

    def find_record_by_guid(self, guid: str) -> Optional[str]:
        """按 GUID（编号）字段查找记录 ID，用于去重。

        默认实现调用 find_record。子类可覆盖以优化。

        Returns:
            记录 ID 字符串，未找到返回 None。
        """
        record = self.find_record("编号", guid)
        if record:
            return record.get("record_id")
        return None
