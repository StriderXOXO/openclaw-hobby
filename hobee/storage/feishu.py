"""飞书多维表格 (Feishu Bitable) 存储后端实现。

使用飞书开放平台 API：
- 认证：企业自建应用 → tenant_access_token
- 记录 CRUD：Bitable Records API
- 文件上传：Drive Media Upload API

需要的环境变量 / 配置：
- FEISHU_APP_ID: 飞书应用 ID
- FEISHU_APP_SECRET: 飞书应用 Secret
- FEISHU_APP_TOKEN: 多维表格 App Token
- 各兴趣源的 TABLE_ID

参考文档：https://open.feishu.cn/document/server-docs/docs/bitable-v1/bitable-overview
"""

import json
import logging
import os
import time
from typing import Optional

import requests

from .base import StorageBackend

log = logging.getLogger(__name__)

FEISHU_BASE = "https://open.feishu.cn/open-apis"


class FeishuBitableBackend(StorageBackend):
    """飞书多维表格存储后端。

    Usage:
        backend = FeishuBitableBackend(
            app_id="cli_xxx",
            app_secret="xxx",
            app_token="xxx",
            table_id="tblXxx",
        )
        record_id = backend.create_record({"名称": "示例", "编号": "guid-123"})
    """

    def __init__(self, app_id: str, app_secret: str, app_token: str, table_id: str):
        self.app_id = app_id
        self.app_secret = app_secret
        self.app_token = app_token
        self.table_id = table_id
        self._token: Optional[str] = None
        self._token_expires: float = 0

    @classmethod
    def from_env(cls, table_id_env: str = "TABLE_ID") -> "FeishuBitableBackend":
        """从环境变量创建实例。

        Args:
            table_id_env: 表格 ID 的环境变量名（如 PODCAST_TABLE_ID）。
        """
        return cls(
            app_id=os.environ["FEISHU_APP_ID"],
            app_secret=os.environ["FEISHU_APP_SECRET"],
            app_token=os.environ["FEISHU_APP_TOKEN"],
            table_id=os.environ[table_id_env],
        )

    # ------------------------------------------------------------------
    # Auth
    # ------------------------------------------------------------------

    def _ensure_token(self):
        """获取或刷新 tenant_access_token。"""
        if self._token and time.time() < self._token_expires - 300:
            return

        log.info("Refreshing Feishu tenant_access_token")
        r = requests.post(
            f"{FEISHU_BASE}/auth/v3/tenant_access_token/internal",
            json={"app_id": self.app_id, "app_secret": self.app_secret},
            timeout=10,
        )
        r.raise_for_status()
        data = r.json()
        if data.get("code") != 0:
            raise RuntimeError(f"Feishu auth failed (code={data.get('code')}, msg={data.get('msg', 'unknown')})")
        self._token = data["tenant_access_token"]
        self._token_expires = time.time() + data.get("expire", 7200)
        log.info("Feishu token refreshed, expires in %ds", data.get("expire", 7200))

    def _headers(self) -> dict:
        self._ensure_token()
        return {"Authorization": f"Bearer {self._token}"}

    # ------------------------------------------------------------------
    # StorageBackend interface
    # ------------------------------------------------------------------

    def create_record(self, fields: dict) -> str:
        r = requests.post(
            f"{FEISHU_BASE}/bitable/v1/apps/{self.app_token}/tables/{self.table_id}/records",
            headers=self._headers(),
            json={"fields": fields},
            timeout=15,
        )
        r.raise_for_status()
        data = r.json()
        if data.get("code") != 0:
            raise RuntimeError(f"Create record failed: {data}")
        record_id = data["data"]["record"]["record_id"]
        log.info("Created bitable record: %s", record_id)
        return record_id

    def update_record(self, record_id: str, fields: dict) -> None:
        r = requests.put(
            f"{FEISHU_BASE}/bitable/v1/apps/{self.app_token}/tables/{self.table_id}/records/{record_id}",
            headers=self._headers(),
            json={"fields": fields},
            timeout=15,
        )
        r.raise_for_status()
        data = r.json()
        if data.get("code") != 0:
            raise RuntimeError(f"Update record failed: {data}")
        log.info("Updated record %s", record_id)

    def find_record(self, field_name: str, field_value: str) -> Optional[dict]:
        filter_expr = f'CurrentValue.[{field_name}]="{field_value[:200]}"'
        try:
            r = requests.get(
                f"{FEISHU_BASE}/bitable/v1/apps/{self.app_token}/tables/{self.table_id}/records",
                headers=self._headers(),
                params={"filter": filter_expr, "page_size": 1},
                timeout=15,
            )
            r.raise_for_status()
            data = r.json()
            if data.get("code") != 0:
                log.warning("Record lookup failed (code %s), skipping", data.get("code"))
                return None
            items = data.get("data", {}).get("items", [])
            if items:
                record = items[0]
                return {
                    "record_id": record["record_id"],
                    "fields": record.get("fields", {}),
                }
        except Exception as e:
            log.warning("Record lookup error: %s", e)
        return None

    def find_record_by_guid(self, guid: str) -> Optional[str]:
        record = self.find_record("编号", guid)
        if record:
            record_id = record["record_id"]
            log.info("Found existing record for GUID %s: %s", guid[:30], record_id)
            return record_id
        return None

    def list_records(
        self,
        filter_expr: Optional[str] = None,
        page_size: int = 100,
    ) -> list[dict]:
        params: dict = {"page_size": page_size}
        if filter_expr:
            params["filter"] = filter_expr

        r = requests.get(
            f"{FEISHU_BASE}/bitable/v1/apps/{self.app_token}/tables/{self.table_id}/records",
            headers=self._headers(),
            params=params,
            timeout=15,
        )
        r.raise_for_status()
        data = r.json()
        if data.get("code") != 0:
            raise RuntimeError(f"List records failed: {data}")
        items = data.get("data", {}).get("items", [])
        return [
            {"record_id": item["record_id"], "fields": item.get("fields", {})}
            for item in items
        ]

    def upload_media(self, file_path: str, file_name: str) -> str:
        size = os.path.getsize(file_path)
        with open(file_path, "rb") as f:
            r = requests.post(
                f"{FEISHU_BASE}/drive/v1/medias/upload_all",
                headers=self._headers(),
                data={
                    "file_name": file_name,
                    "parent_type": "bitable_file",
                    "parent_node": self.app_token,
                    "size": str(size),
                },
                files={"file": (file_name, f)},
                timeout=60,
            )
        r.raise_for_status()
        data = r.json()
        if data.get("code") != 0:
            raise RuntimeError(f"Upload failed: {data}")
        file_token = data["data"]["file_token"]
        log.info("Uploaded %s -> file_token=%s", file_name, file_token)
        return file_token

    # ------------------------------------------------------------------
    # Feishu-specific helpers
    # ------------------------------------------------------------------

    def list_fields(self) -> list[dict]:
        """列出表格中所有字段。"""
        r = requests.get(
            f"{FEISHU_BASE}/bitable/v1/apps/{self.app_token}/tables/{self.table_id}/fields",
            headers=self._headers(),
            timeout=15,
        )
        r.raise_for_status()
        data = r.json()
        if data.get("code") != 0:
            raise RuntimeError(f"List fields failed: {data}")
        return data.get("data", {}).get("items", [])

    def create_field(self, field_name: str, field_type: int = 1) -> str:
        """创建表格字段。field_type: 1=text, 2=number, 5=datetime, 15=url, 17=attachment。"""
        r = requests.post(
            f"{FEISHU_BASE}/bitable/v1/apps/{self.app_token}/tables/{self.table_id}/fields",
            headers=self._headers(),
            json={"field_name": field_name, "type": field_type},
            timeout=15,
        )
        r.raise_for_status()
        data = r.json()
        if data.get("code") != 0:
            raise RuntimeError(f"Create field failed: {data}")
        field_id = data["data"]["field"]["field_id"]
        log.info("Created field '%s' -> %s", field_name, field_id)
        return field_id

    def send_chat_message(self, chat_id: str, content: str) -> None:
        """向飞书群聊发送消息（用于 Watchdog 告警和心跳分享）。"""
        r = requests.post(
            f"{FEISHU_BASE}/im/v1/messages",
            headers=self._headers(),
            params={"receive_id_type": "chat_id"},
            json={
                "receive_id": chat_id,
                "msg_type": "text",
                "content": json.dumps({"text": content}),
            },
            timeout=15,
        )
        r.raise_for_status()
        data = r.json()
        if data.get("code") != 0:
            log.warning("Send message failed: %s", data)
        else:
            log.info("Sent message to chat %s", chat_id)
