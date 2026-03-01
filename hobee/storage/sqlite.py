"""SQLite 存储后端实现。

零配置本地存储，适合快速上手和单机部署。
数据库使用 WAL 模式，记录字段以 JSON 格式存储。

Usage:
    backend = SQLiteBackend("/path/to/data.db")
    record_id = backend.create_record({"名称": "示例", "编号": "guid-123"})
"""

import json
import logging
import os
import shutil
import sqlite3
import uuid
from typing import Optional

from .base import StorageBackend

log = logging.getLogger(__name__)


class SQLiteBackend(StorageBackend):
    """SQLite 本地存储后端。

    每条记录存储为一行，字段以 JSON 格式保存在 fields 列中。
    支持通过 json_extract() 进行字段级查询。
    """

    def __init__(self, db_path: str):
        self.db_path = db_path
        self.media_dir = os.path.join(os.path.dirname(db_path), "media")
        os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
        os.makedirs(self.media_dir, exist_ok=True)
        self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_schema(self):
        with self._connect() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS records (
                    record_id TEXT PRIMARY KEY,
                    fields TEXT NOT NULL DEFAULT '{}',
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_records_guid
                ON records (json_extract(fields, '$.编号'))
            """)

    # ------------------------------------------------------------------
    # StorageBackend interface
    # ------------------------------------------------------------------

    def create_record(self, fields: dict) -> str:
        record_id = f"rec_{uuid.uuid4().hex[:16]}"
        import time
        now = time.time()
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO records (record_id, fields, created_at, updated_at) VALUES (?, ?, ?, ?)",
                (record_id, json.dumps(fields, ensure_ascii=False), now, now),
            )
        log.info("Created SQLite record: %s", record_id)
        return record_id

    def update_record(self, record_id: str, fields: dict) -> None:
        import time
        with self._connect() as conn:
            row = conn.execute(
                "SELECT fields FROM records WHERE record_id = ?", (record_id,)
            ).fetchone()
            if not row:
                raise ValueError(f"Record not found: {record_id}")
            existing = json.loads(row["fields"])
            existing.update(fields)
            conn.execute(
                "UPDATE records SET fields = ?, updated_at = ? WHERE record_id = ?",
                (json.dumps(existing, ensure_ascii=False), time.time(), record_id),
            )
        log.info("Updated SQLite record %s", record_id)

    def find_record(self, field_name: str, field_value: str) -> Optional[dict]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT record_id, fields FROM records WHERE json_extract(fields, ?) = ? LIMIT 1",
                (f"$.{field_name}", field_value[:200]),
            ).fetchone()
            if row:
                return {
                    "record_id": row["record_id"],
                    "fields": json.loads(row["fields"]),
                }
        return None

    def find_record_by_guid(self, guid: str) -> Optional[str]:
        record = self.find_record("编号", guid)
        if record:
            log.info("Found existing record for GUID %s: %s", guid[:30], record["record_id"])
            return record["record_id"]
        return None

    def list_records(
        self,
        filter_expr: Optional[str] = None,
        page_size: int = 100,
    ) -> list[dict]:
        with self._connect() as conn:
            if filter_expr:
                # Simple filter support: "field_name=value"
                if "=" in filter_expr:
                    field, _, value = filter_expr.partition("=")
                    field = field.strip()
                    value = value.strip().strip('"')
                    rows = conn.execute(
                        "SELECT record_id, fields FROM records WHERE json_extract(fields, ?) = ? ORDER BY created_at DESC LIMIT ?",
                        (f"$.{field}", value, page_size),
                    ).fetchall()
                else:
                    rows = conn.execute(
                        "SELECT record_id, fields FROM records ORDER BY created_at DESC LIMIT ?",
                        (page_size,),
                    ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT record_id, fields FROM records ORDER BY created_at DESC LIMIT ?",
                    (page_size,),
                ).fetchall()
        return [
            {"record_id": row["record_id"], "fields": json.loads(row["fields"])}
            for row in rows
        ]

    def upload_media(self, file_path: str, file_name: str) -> str:
        dest = os.path.join(self.media_dir, file_name)
        # Avoid overwriting: append uuid suffix if exists
        if os.path.exists(dest):
            base, ext = os.path.splitext(file_name)
            dest = os.path.join(self.media_dir, f"{base}_{uuid.uuid4().hex[:8]}{ext}")
        shutil.copy2(file_path, dest)
        log.info("Copied media %s -> %s", file_name, dest)
        return dest
