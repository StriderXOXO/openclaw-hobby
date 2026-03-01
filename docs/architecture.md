# 系统架构

## 设计哲学: 哑采集 + 智能分析

openclaw-hobby 的核心设计思想可以用一句话概括:

> **守护进程是「哑」的，智能集中在 Triage 和 Heartbeat。**

为什么这样设计？

| 层 | 是否使用 LLM | 运行频率 | 成本 |
|---|---|---|---|
| 采集层 (Daemons) | 否 | 持续运行 | 几乎为零 (纯 API 调用) |
| 存储层 (Storage) | 否 | 按需写入 | 取决于存储方案 |
| 分析层 (Triage) | 是 | 每 2-4 小时 | 可控 (batch 处理) |
| 决策层 (Heartbeat) | 是 | 每 10 分钟 | 极低 (读取摘要即可) |

如果每条内容在采集时就调用 LLM 分析，一天几十条内容的 API 成本会很高。把 LLM 集中到 Triage 层做 batch 处理，成本可预测、可控制。更重要的是，Heartbeat 不需要重新分析原始内容 -- 它读取的是 Triage 已经生成的摘要和标签，决策速度快，token 消耗低。

---

## 四层架构详解

```
┌─────────────────────────────────────────────────────────────────┐
│                     用户 (飞书/Telegram/Discord)                 │
└────────────────────────────┬────────────────────────────────────┘
                             │ 推送分享 / 接收用户信号
                             │
┌────────────────────────────▼────────────────────────────────────┐
│                    决策层: Heartbeat Engine                       │
│                                                                  │
│  OpenClaw Agent 每 10 分钟唤醒                                    │
│  ┌──────────────────────────────────────────────────────────┐    │
│  │ Step 0: 获取时间                                         │    │
│  │ Step 1: 检查消息类型 (用户消息 vs 心跳触发)               │    │
│  │ Step 2: 读取状态文件 (mind-state, user-signals, alerts)  │    │
│  │ Step 3: 检查分享时机 (时间窗口 + 冷却)                    │    │
│  │ Step 4: 跨兴趣源全局评分                                  │    │
│  │ Step 5: 执行分享 (自然语气)                               │    │
│  │ Step 6: 无事可做 → HEARTBEAT_OK                          │    │
│  │ Step 7: 记录观察 (跨心跳学习)                             │    │
│  └──────────────────────────────────────────────────────────┘    │
└────────────────────────────┬────────────────────────────────────┘
                             │ 读取分析结果 + 待分享内容
                             │
┌────────────────────────────▼────────────────────────────────────┐
│                    分析层: Triage Crons                           │
│                                                                  │
│  triage_helper.py — 统一分析脚本                                  │
│  ┌──────────────────────────────────────────────────────────┐    │
│  │ podcast-triage  (每 3 小时)  → 摘要/亮点/精选原文/标签    │    │
│  │ youtube-triage  (每 3 小时)  → 摘要/亮点/精选原文/标签    │    │
│  │ twitter-triage  (每 2 小时)  → 摘要/标签                  │    │
│  └──────────────────────────────────────────────────────────┘    │
│  读取全文(字幕/转录/推文) → LLM 分析 → 写回存储 + 标记 triaged   │
└────────────────────────────┬────────────────────────────────────┘
                             │ 写入 / 查询记录
                             │
┌────────────────────────────▼────────────────────────────────────┐
│                    存储层: StorageBackend                         │
│                                                                  │
│  ┌─────────────────┐  ┌─────────────────┐  ┌────────────────┐  │
│  │ 飞书多维表格     │  │ Notion (TODO)   │  │ SQLite (TODO)  │  │
│  │ (默认实现)       │  │                 │  │                │  │
│  └─────────────────┘  └─────────────────┘  └────────────────┘  │
│                                                                  │
│  StorageBackend ABC:                                             │
│    create_record()   — 创建记录                                   │
│    update_record()   — 更新字段                                   │
│    find_record()     — 按字段查找 (去重)                          │
│    list_records()    — 列表查询 (可选过滤)                        │
│    upload_media()    — 上传媒体文件                                │
└────────────────────────────┬────────────────────────────────────┘
                             │ 写入原始记录
                             │
┌────────────────────────────▼────────────────────────────────────┐
│                    采集层: Daemons                                │
│                                                                  │
│  BaseDaemon 基类提供:                                            │
│    主循环 (采集→休眠→重复)、去重、pending-shares 管理、活动日志   │
│                                                                  │
│  ┌─────────────────┐  ┌─────────────────┐  ┌────────────────┐  │
│  │ PodcastDaemon   │  │ YouTubeDaemon   │  │ TwitterDaemon  │  │
│  │ RSS + 转录      │  │ API + 字幕提取  │  │ API 监控       │  │
│  │ 2-4h 周期       │  │ 4-6h 周期       │  │ 1.5-3h 周期    │  │
│  └─────────────────┘  └─────────────────┘  └────────────────┘  │
│                                                                  │
│  不调用 LLM — 纯 API 抓取，成本几乎为零                          │
└─────────────────────────────────────────────────────────────────┘
```

---

## 每层职责与关键设计决策

### 采集层 (Daemons)

**职责**: 7x24 不间断地从各数据源拉取新内容，存入存储后端，同时写入本地 `pending-shares.json` 供心跳评分使用。

**关键设计决策**:

- **不使用 LLM**: 守护进程只做数据搬运，不做任何智能分析。这保证了运行成本极低，即使 LLM API 挂了，采集不受影响。
- **随机化休眠间隔**: 每次采集后随机休眠 `CYCLE_MIN` ~ `CYCLE_MAX` 秒，避免被数据源识别为爬虫。
- **BaseDaemon 基类**: 提供主循环、信号处理、JSON 读写、去重、活动日志等通用逻辑。新增兴趣源只需继承并实现 `collect_once()` 方法。
- **去重**: 通过 `StorageBackend.find_record_by_guid()` 在存储层做去重，避免重复存储。

### 存储层 (StorageBackend)

**职责**: 提供统一的记录 CRUD 接口，将具体存储实现与业务逻辑解耦。

**关键设计决策**:

- **抽象接口 (ABC)**: `StorageBackend` 定义了 5 个抽象方法: `create_record`、`update_record`、`find_record`、`list_records`、`upload_media`。任何存储系统只要实现这 5 个方法就能接入。
- **默认飞书实现**: `FeishuBitableBackend` 封装了飞书开放平台 API，包括 token 管理、记录 CRUD、文件上传、消息发送。
- **从 HobbyConfig 创建**: `config.create_storage()` 根据配置自动创建对应的存储后端实例。

### 分析层 (Triage)

**职责**: 定期扫描存储后端中未分析的内容，调用 LLM 生成结构化分析 (摘要、亮点、精选原文、主题标签)，写回存储。

**关键设计决策**:

- **统一脚本**: `triage_helper.py` 一个脚本处理所有兴趣源，通过 `HOBBY_CONFIG` 字典区分各源的内容类型和分析深度。
- **结构化中文 Prompt**: 使用 `===` 分隔符的结构化 Prompt 模板，确保 LLM 输出可靠解析。这是经过大量调试后的最佳实践 -- 简单英文 prompt 的输出质量远不如结构化中文 prompt。
- **batch 处理**: 每次 Triage 处理固定数量的条目 (默认 5-10)，配合 rate limiting，避免 LLM API 过载。
- **triaged 标记**: 已分析的条目标记 `triaged: true`，避免重复分析。

### 决策层 (Heartbeat)

**职责**: 每 10 分钟被 OpenClaw 唤醒，读取所有待分享内容，综合评分，在合适的时间像朋友一样把最值得看的内容推送给用户。

**关键设计决策**:

- **心跳驱动而非 Cron**: 不是「每天 8 点推送」，而是「持续感知，时机合适就分享」。详见 [心跳机制详解](heartbeat-deep-dive.md)。
- **跨兴趣源全局评分**: 所有内容放在一起评分，确保用户收到的是全局最优。
- **用户信号反馈**: 用户分享的微信文章自动提取话题，调整评分权重。
- **session isolation**: 每次心跳使用独立会话，避免长对话导致的时间缓存 bug。

---

## 状态文件说明

系统运行时维护以下关键状态文件:

### mind-state.json

心跳引擎的认知状态，跨心跳持久化。

```json
{
  "last_tick": "2026-02-28 19:08 CST",
  "sharing_cooldown": {
    "last_share": "2026-02-28 16:12 CST",
    "items_shared_today": 4,
    "max_daily": 15
  },
  "preferred_windows_cst": [7, 12, 16, 19],
  "busy_hours_cst": [9, 10, 11, 14, 15],
  "recent_observations": [
    "用户近期分享了 2 篇 Agent 相关文章，提高 Agent 话题权重",
    "Podcast 内容分享后反馈较好，适当增加 Podcast 比例"
  ],
  "content_preferences": {
    "topics": {
      "AI/LLM/Agent": 0.9,
      "robotics": 0.8,
      "deep-tech": 0.75,
      "startup/product": 0.7
    },
    "formats": {
      "podcast": 0.8,
      "youtube": 0.7,
      "twitter": 0.6
    }
  }
}
```

| 字段 | 说明 |
|------|------|
| `last_tick` | 上次心跳执行时间 |
| `sharing_cooldown` | 分享冷却状态: 上次分享时间、今日已分享数、每日上限 |
| `preferred_windows_cst` | 偏好分享时间窗口 (CST 小时) |
| `busy_hours_cst` | 忙碌时段 (不分享) |
| `recent_observations` | 最近 5 条跨心跳观察 (用于 isolated session 间的学习传递) |
| `content_preferences` | 话题权重和格式偏好 (从用户信号中持续学习) |

### pending-shares.json (每个兴趣源各一份)

守护进程写入的待分享内容队列，心跳引擎消费。

```json
[
  {
    "id": "ep-abc123",
    "title": "ChatGPT 背后的技术演进",
    "summary": "深入分析 GPT 系列模型从 1 到 4 的架构变化...",
    "record_id": "recXXX",
    "transcript_path": "/path/to/transcript.txt",
    "shared": false,
    "triaged": true,
    "topic_tags": "AI, GPT, transformer, 大模型"
  }
]
```

| 字段 | 说明 |
|------|------|
| `id` | 唯一标识 (GUID) |
| `summary` | Triage 生成的摘要 (或守护进程的原始预览) |
| `record_id` | 存储后端中的记录 ID |
| `transcript_path` / `subtitles_path` | 全文内容文件路径 |
| `shared` | 是否已被心跳分享 |
| `triaged` | 是否已被 Triage 分析 |
| `topic_tags` | 主题标签 (Triage 生成) |

### user-signals.json

用户行为信号，影响心跳评分权重。

```json
{
  "articles": [
    {
      "url": "https://mp.weixin.qq.com/s/xxx",
      "title": "AI Agent 的未来",
      "topics": ["AI Agent", "自主系统", "LLM"],
      "added_at": "2026-02-27T10:30:00Z"
    }
  ],
  "topic_weights": {
    "AI Agent": 3,
    "自主系统": 2,
    "LLM": 5
  }
}
```

### watchdog-alerts.json

Watchdog 写入的健康状态摘要，心跳引擎读取并决定是否告知用户。

```json
{
  "overall_status": "healthy",
  "last_check": "2026-02-28T19:05:00Z",
  "services": {
    "podcast-hobby": { "status": "active", "uptime": "3d 2h" },
    "youtube-hobby": { "status": "active", "uptime": "3d 2h" },
    "twitter-hobby": { "status": "active", "uptime": "3d 2h" }
  },
  "alerts": []
}
```

---

## 数据流全景

```
原始数据源          采集层            存储层           分析层          决策层          用户
──────────       ──────────       ──────────       ──────────     ──────────     ──────

Podcast RSS  ──> PodcastDaemon ──> create_record ──────────────────────────────────────
                      │                 │
                      │          pending-shares.json
                      │                 │
                      │                 ▼
                      │          triage_helper.py ──> LLM 分析 ──> update_record
                      │                                    │
                      │                                    ▼
                      │                              摘要/亮点/标签
                      │                                    │
                      │                                    ▼
                      └──────────────────────────> Heartbeat 读取  ──> 全局评分
                                                         │
YouTube API  ──> YouTubeDaemon ──> (同上流程)  ──────────>│
                                                         │
Twitter API  ──> TwitterDaemon ──> (同上流程)  ──────────>│
                                                         │
                                                    ┌────▼─────┐
                                                    │ 评分 > 阈值 │── 是 ──> 分享给用户
                                                    │ + 时间窗口  │
                                                    └────┬─────┘
                                                         │── 否 ──> HEARTBEAT_OK
```

---

## 为什么不用简单 Cron 而是 Heartbeat?

传统做法是设置一个 cron 定时任务，比如「每天 8 点和 18 点推送内容」。openclaw-hobby 选择心跳机制，原因如下:

| 维度 | Cron 定时推送 | 心跳驱动分享 |
|------|-------------|------------|
| **全局视野** | 每个 cron 只看自己那个源 | 心跳看到所有源的所有待分享内容 |
| **时间感知** | 固定时间推送，不管用户是否忙碌 | 感知用户时间窗口 (preferred/busy hours) |
| **用户上下文** | 无法感知用户近期兴趣变化 | 读取 user-signals，动态调整评分 |
| **自然交互** | 机械的「今日推荐清单」 | 像朋友随口分享，语气自然 |
| **跨源竞争** | 各源独立推送，可能同时发一堆 | 全局排序，只推最好的 2-4 条 |
| **冷却机制** | 无 (或粗粒度) | 精确到小时的冷却，避免打扰用户 |
| **学习能力** | 无 | 跨心跳积累观察，调整偏好权重 |

心跳机制的代价是每 10 分钟消耗少量 token (读状态文件 + 判断)。但由于大部分心跳只是读取状态后回复 `HEARTBEAT_OK`，实际 token 消耗很低。
