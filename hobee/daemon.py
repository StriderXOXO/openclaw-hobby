"""守护进程基类 — 所有兴趣源采集器的通用模式。

每个兴趣源守护进程（Podcast / YouTube / Twitter）继承 BaseDaemon，
只需实现 collect_once() 方法。基类处理：
- 主循环（采集 → 随机间隔休眠 → 重复）
- pending-shares.json 读写
- 去重（通过 StorageBackend.find_record_by_guid）
- 结构化活动日志（daemon-{name}-YYYY-MM-DD.jsonl）
- 信号处理（SIGTERM 优雅退出）

Usage:
    class PodcastDaemon(BaseDaemon):
        def collect_once(self):
            # ... 采集逻辑 ...
            return [{"id": "...", "title": "...", ...}]

    daemon = PodcastDaemon("podcast", config, storage)
    daemon.run_forever()
"""

import json
import logging
import random
import signal
import sys
import time
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .config import HobbyConfig
from .storage.base import StorageBackend

log = logging.getLogger(__name__)


class BaseDaemon(ABC):
    """兴趣源采集守护进程基类。"""

    # 子类可覆盖这些默认值
    CYCLE_MIN: int = 120 * 60   # 最小循环间隔（秒）
    CYCLE_MAX: int = 240 * 60   # 最大循环间隔（秒）

    def __init__(
        self,
        hobby_name: str,
        config: HobbyConfig,
        storage: StorageBackend,
    ):
        self.hobby_name = hobby_name
        self.config = config
        self.storage = storage
        self._running = True

        # Workspace paths
        self.workspace = config.workspace
        self.workspace.mkdir(parents=True, exist_ok=True)

        self.pending_file = config.pending_shares_file
        self.log_dir = config.log_dir
        self.log_dir.mkdir(parents=True, exist_ok=True)

        # Register signal handlers for graceful shutdown
        signal.signal(signal.SIGTERM, self._handle_signal)
        signal.signal(signal.SIGINT, self._handle_signal)

    def _handle_signal(self, signum, frame):
        log.info("Received signal %s, shutting down gracefully...", signum)
        self._running = False

    # ------------------------------------------------------------------
    # JSON helpers
    # ------------------------------------------------------------------

    @staticmethod
    def load_json(path: Path, default=None):
        """加载 JSON 文件，不存在则返回 default。"""
        if path.exists():
            with open(path) as f:
                return json.load(f)
        return default if default is not None else {}

    @staticmethod
    def save_json(path: Path, data):
        """保存 JSON 文件。"""
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    # ------------------------------------------------------------------
    # Activity logging (for observability / watchdog / daily report)
    # ------------------------------------------------------------------

    def log_event(self, event: str, **kwargs):
        """写入结构化活动日志。"""
        log_file = self.log_dir / f"daemon-{self.hobby_name}-{datetime.now().strftime('%Y-%m-%d')}.jsonl"
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "source": self.hobby_name,
            "event": event,
            **kwargs,
        }
        with open(log_file, "a") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    # ------------------------------------------------------------------
    # Pending shares management
    # ------------------------------------------------------------------

    def load_pending(self) -> list[dict]:
        """加载 pending-shares.json。"""
        return self.load_json(self.pending_file, [])

    def save_pending(self, items: list[dict]):
        """保存 pending-shares.json。"""
        self.save_json(self.pending_file, items)

    def add_pending_item(self, item: dict):
        """添加一个待分享条目（线程安全写入）。"""
        pending = self.load_pending()
        pending.append(item)
        self.save_pending(pending)
        self.log_event(
            "item_queued",
            item_id=item.get("id", "unknown"),
            title=item.get("title", "")[:50],
            queue_size_after=len(pending),
        )

    def dedup_and_store(self, guid: str, fields: dict) -> Optional[str]:
        """去重后存入存储后端。返回 record_id（已存在则返回已有 ID）。"""
        existing_id = self.storage.find_record_by_guid(guid)
        if existing_id:
            log.info("Record already exists for GUID %s: %s", guid[:30], existing_id)
            return existing_id

        record_id = self.storage.create_record(fields)
        time.sleep(1)  # rate limit
        return record_id

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    @abstractmethod
    def collect_once(self):
        """执行一次采集。子类实现具体的采集逻辑。

        此方法应该：
        1. 从数据源获取新内容
        2. 调用 self.dedup_and_store() 存入后端
        3. 调用 self.add_pending_item() 加入待分享队列
        """

    def run_forever(self):
        """主循环：采集 → 随机间隔休眠 → 重复。"""
        log.info(
            "%s daemon starting (cycle: %d-%d min)",
            self.hobby_name,
            self.CYCLE_MIN // 60,
            self.CYCLE_MAX // 60,
        )
        log.info("Workspace: %s", self.workspace)

        while self._running:
            try:
                self.log_event("cycle_start")
                self.collect_once()
                self.log_event("cycle_end")
            except Exception as e:
                log.error("Collect cycle failed: %s", e, exc_info=True)
                self.log_event("cycle_error", error=str(e))

            if not self._running:
                break

            sleep_time = random.randint(self.CYCLE_MIN, self.CYCLE_MAX)
            log.info("Sleeping %d minutes until next cycle", sleep_time // 60)

            # Sleep in small increments to allow graceful shutdown
            end_time = time.time() + sleep_time
            while time.time() < end_time and self._running:
                time.sleep(min(30, end_time - time.time()))

        log.info("%s daemon stopped", self.hobby_name)
