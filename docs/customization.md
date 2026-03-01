# 自定义指南

本文档介绍如何根据自己的需求定制 openclaw-hobby 系统。

---

## 1. 添加新兴趣源

所有兴趣源守护进程都继承 `BaseDaemon`，只需实现 `collect_once()` 方法。

### 步骤

#### 1.1 创建 daemon 目录

```
daemons/
└── your-hobby/
    ├── daemon.py          # 守护进程主逻辑
    └── your_api.py        # API 封装（可选）
```

#### 1.2 实现 daemon

```python
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from hobee.config import HobbyConfig
from hobee.daemon import BaseDaemon
from hobee.logging_utils import setup_logging

log = setup_logging("your-hobby")


class YourHobbyDaemon(BaseDaemon):
    """你的兴趣源采集守护进程。"""

    CYCLE_MIN = 120 * 60   # 最小间隔 2 小时
    CYCLE_MAX = 240 * 60   # 最大间隔 4 小时

    def __init__(self, config, storage):
        super().__init__("your-hobby", config, storage)
        # 初始化你需要的 API client、状态文件等

    def collect_once(self):
        """执行一次采集。"""
        # 1. 从数据源获取新内容
        items = self._fetch_new_items()

        for item in items:
            guid = item["id"]

            # 2. 去重 + 存入后端
            record_id = self.dedup_and_store(guid, {
                "编号": guid,
                "名称": item["title"],
                # ... 其他字段
            })

            # 3. 加入待分享队列
            self.add_pending_item({
                "id": f"your-hobby-{guid}",
                "source": "your-hobby",
                "title": item["title"],
                "summary": item["summary"][:1500],
                "record_id": record_id,
                "shared": False,
                "triaged": False,
            })

    def _fetch_new_items(self):
        # 你的采集逻辑
        return []
```

#### 1.3 添加配置

创建 `config/your-hobby.example.json`:

```json
{
    "feishu_app_id": "YOUR_FEISHU_APP_ID",
    "feishu_app_secret": "YOUR_FEISHU_APP_SECRET",
    "feishu_app_token": "YOUR_APP_TOKEN",
    "feishu_table_id": "YOUR_TABLE_ID",
    "cycle_min_minutes": 120,
    "cycle_max_minutes": 240
}
```

#### 1.4 添加 systemd 服务

复制 `systemd/hobby-podcast.service` 并修改路径。

#### 1.5 添加 triage 支持

在 `triage/triage_helper.py` 的 `HOBBY_CONFIG` 字典中添加你的兴趣源配置：

```python
HOBBY_CONFIG = {
    # ... existing hobbies ...
    "your-hobby": {
        "content_field": "内容",      # bitable 中内容所在的字段名
        "analysis_fields": ["摘要", "亮点", "主题标签"],
        "system_prompt": "你是一位...",  # LLM 分析提示词
    },
}
```

---

## 2. 替换存储后端

默认使用飞书多维表格 (Feishu Bitable)。你可以实现自己的存储后端。

### 实现 StorageBackend 接口

```python
# hobee/storage/notion.py（示例）
from hobee.storage.base import StorageBackend


class NotionBackend(StorageBackend):
    """Notion 数据库存储后端。"""

    def __init__(self, api_key, database_id):
        self.api_key = api_key
        self.database_id = database_id

    def create_record(self, fields: dict) -> str:
        # 调用 Notion API 创建页面
        # 返回 page_id
        ...

    def update_record(self, record_id: str, fields: dict) -> None:
        # 调用 Notion API 更新页面属性
        ...

    def find_record(self, field_name: str, field_value: str):
        # 调用 Notion API 查询数据库
        # 返回匹配的记录或 None
        ...

    def list_records(self, filter_expr=None, page_size=100):
        # 调用 Notion API 列出页面
        ...

    def upload_media(self, file_path: str, file_name: str) -> str:
        # 上传文件到 Notion / S3 等
        # 返回文件标识符
        ...
```

### 注册到 HobbyConfig

修改 `hobee/config.py` 的 `create_storage()` 方法：

```python
def create_storage(self):
    backend_type = self.get("storage_backend", "feishu")

    if backend_type == "feishu":
        from .storage.feishu import FeishuBitableBackend
        return FeishuBitableBackend(...)

    elif backend_type == "notion":
        from .storage.notion import NotionBackend
        return NotionBackend(
            api_key=self.require("notion_api_key"),
            database_id=self.require("notion_database_id"),
        )

    # ... 其他后端
```

### 存储后端对比

| 特性 | 飞书 Bitable | Notion | SQLite |
|------|------------|--------|--------|
| 免费额度 | 充足 | 有限 | 无限 |
| 中文支持 | 原生 | 好 | 好 |
| API 速率限制 | 中等 | 严格 | 无 |
| 可视化管理 | 飞书 App | Notion App | 需额外工具 |
| 适合场景 | 国内团队 | 国际用户 | 本地/自部署 |

---

## 3. 调整心跳评分策略

心跳（Heartbeat）是系统的核心决策机制。通过修改 `agent/HEARTBEAT.md` 来调整。

### 3.1 时间窗口

```markdown
# 在 HEARTBEAT.md 中修改
- 偏好时段: 7, 12, 16, 19 CST    ← 改为你的偏好时段
- 忙碌时段: 9, 10, 11, 14, 15 CST ← 改为你的忙碌时段
```

### 3.2 每日分享上限

修改 `config/mind-state.example.json`:

```json
{
    "max_daily": 15,  // ← 调整每日最大分享数
    "user_schedule": {
        "timezone": "Asia/Shanghai",
        "preferred_windows": [7, 12, 16, 19],
        "busy_hours": [9, 10, 11, 14, 15]
    }
}
```

### 3.3 内容偏好权重

修改 `config/mind-state.example.json` 中的 `content_preferences`:

```json
{
    "content_preferences": {
        "topics": {
            "AI": 0.9,
            "programming": 0.85,
            "science": 0.8,
            "philosophy": 0.7
        },
        "formats": {
            "podcast": 0.8,
            "youtube": 0.8,
            "twitter": 0.7
        }
    }
}
```

### 3.4 评分规则

在 `HEARTBEAT.md` 的 Step 4（评分）中，可以调整：

- **基础分**：内容质量、相关性
- **时间衰减**：内容新鲜度如何影响分数
- **用户信号加成**：用户分享的文章如何影响权重
- **冷却机制**：同一来源连续分享的间隔

---

## 4. 调整采集策略

### Twitter 策略权重

在 `daemons/twitter/daemon.py` 中修改 `DEFAULT_STRATEGIES`:

```python
DEFAULT_STRATEGIES = {
    "check_timeline": 40,    # 检查关注用户（40%概率）
    "explore_people": 25,    # 发现新人（25%概率）
    "topic_search": 20,      # 话题搜索（20%概率）
    "thread_reading": 15,    # 深入线程（15%概率）
}
```

### 采集频率

每个 daemon 都有 `CYCLE_MIN` 和 `CYCLE_MAX`（秒）:

| Daemon | 默认间隔 | 推荐范围 |
|--------|---------|---------|
| Podcast | 2-4 小时 | 1-6 小时 |
| YouTube | 4-6 小时 | 2-8 小时 |
| Twitter | 1.5-3 小时 | 1-4 小时 |

频率越高，API 消耗越大。Twitter 和 YouTube 有速率限制，请注意控制。

### 内容过滤阈值

各 daemon 中的 engagement score 阈值控制最低收录标准：

```python
# twitter daemon.py
if item["score"] > 5:     # check_timeline: 较低阈值
if item["score"] > 10:    # explore_people: 中等阈值
if item["score"] > 15:    # topic_search: 较高阈值
```

---

## 5. Triage 提示词优化

`triage/triage_helper.py` 中的 LLM 提示词决定了内容分析质量。

### 关键经验

1. **使用中文提示词**：对中文内容，中文提示词效果远好于英文
2. **提供具体示例**：好的/差的输出示例能显著提升质量
3. **结构化输出**：要求 JSON 格式输出便于后续处理
4. **角色设定**：明确分析师角色，比通用提示好得多

### 示例改进

```python
# 差：
system_prompt = "分析这个播客"

# 好：
system_prompt = """你是一位资深的科技播客分析师。

任务：分析以下播客转录文本，提取核心价值。

输出格式（JSON）：
{
    "摘要": "100-200字概括核心论点",
    "亮点": ["亮点1", "亮点2", "亮点3"],
    "精选原文": "最有价值的原始片段（50-100字）",
    "主题标签": ["标签1", "标签2"]
}

注意：
- 摘要要有信息量，不要泛泛而谈
- 亮点要具体，包含数据或独特观点
- 精选原文直接引用，不要改写"""
```

---

## 6. 多语言适配

系统默认面向中文用户（飞书、中文提示词）。适配其他语言：

1. **存储字段名**：修改 bitable 字段名（`编号` → `ID`，`摘要` → `Summary`）
2. **Triage 提示词**：将中文提示词改为目标语言
3. **HEARTBEAT.md**：翻译决策指令
4. **SOUL.md**：调整人格描述语言
5. **聊天通道**：将飞书替换为 Slack / Discord / Telegram
