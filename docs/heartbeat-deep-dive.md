# 心跳机制详解

心跳 (Heartbeat) 是 openclaw-hobby 的核心创新。它不是简单的定时推送，而是一个持续感知、综合判断、主动决策的智能循环。

---

## 什么是心跳驱动

OpenClaw 框架原生支持心跳机制: 你可以配置一个时间间隔 (比如 10 分钟)，框架会定期唤醒 Agent，Agent 读取预设的指令文件 (HEARTBEAT.md)，执行里面定义的流程，然后挂起等待下一次唤醒。

```json
// OpenClaw gateway 配置 (moltbot.json)
{
  "heartbeat": {
    "every": "10m",
    "target": "feishu",
    "session": "isolated",
    "activeHours": { "start": "07:00", "end": "23:00" }
  }
}
```

关键配置:
- `every: "10m"`: 每 10 分钟唤醒一次
- `session: "isolated"`: 每次心跳使用独立会话 (极其重要，详见后文)
- `activeHours`: 只在活跃时段运行 (CST 07:00-23:00)

每次唤醒，Agent 就像「醒来看一眼」-- 有值得分享的就说，没有就继续睡。

---

## 7 步决策流程详解

每次心跳触发，Agent 按照 HEARTBEAT.md 定义的 7 个步骤执行:

### Step 0: 获取当前时间

```bash
date "+%Y-%m-%d %H:%M CST"
```

**输入**: 无
**输出**: 当前 CST 时间字符串
**决策逻辑**: 无 (纯数据获取)

这一步看起来简单，却至关重要。Agent 必须通过实际执行 `date` 命令获取真实时间，而不是使用记忆中的时间。后面所有的时间判断 (时间窗口、冷却、计数器重置) 都依赖这一步。

> 踩坑故事: 如果不使用 isolated session，Agent 会在长对话中复用之前 `date` 命令的输出，导致所有心跳都认为是同一个时间。详见 [session isolation 章节](#session-isolation-的重要性)。

### Step 1: 检查消息类型

**输入**: 本次唤醒附带的消息 (可能是空的心跳触发，也可能是用户消息)
**输出**: 分支判断 -- 用户消息走对话处理，纯心跳继续后续步骤
**决策逻辑**:

- 用户发来微信文章链接 → 提取话题，更新 user-signals.json，回复用户
- 用户发来其他消息 → 正常对话，不执行心跳流程
- 纯心跳触发 → 继续 Step 2

这个设计允许心跳和用户对话共享同一个 Agent 实例，用户发消息时 Agent 不会突然开始推送内容。

### Step 2: 读取状态文件

**输入**: 文件系统上的状态文件
**输出**: Agent 内存中的完整上下文

Agent 依次读取:
1. `mind-state.json` — 认知状态 (上次分享时间、今日分享数、偏好权重)
2. `user-signals.json` — 用户信号 (近期分享的文章话题)
3. `watchdog-alerts.json` — 系统健康状态
4. 各兴趣源的 `pending-shares.json` — 待分享内容列表

**Watchdog 告警处理**: 如果系统健康状态为 `critical`，Agent 会用自然语气告诉用户 (「诶我发现系统有点问题...」)。`degraded` 状态静默记录，`healthy` 直接忽略。

**每日计数器重置**: 比较 `last_share` 的日期和今天日期，如果是新的一天，重置 `items_shared_today` 为 0。

### Step 3: 检查分享时机

**输入**: Step 0 的当前时间 + Step 2 的状态
**输出**: 「继续评分」或「跳到 Step 6 (无事可做)」

必须同时满足以下条件:

| 条件 | 说明 |
|------|------|
| 当前小时在 `preferred_windows_cst` 中 | 偏好窗口: 7, 12, 16, 19 点 |
| 当前小时不在 `busy_hours_cst` 中 | 忙碌时段: 9, 10, 11, 14, 15 点 |
| 距上次分享超过 2 小时 | 冷却机制，避免频繁打扰 |
| 今日分享数 < `max_daily` (15) | 每日上限 |

任何一个条件不满足，直接跳到 Step 6。大部分心跳都在这一步结束 -- 一天 96 次心跳 (16 小时 x 每小时 6 次)，通常只有 3-5 次会进入评分流程。

### Step 4: 跨兴趣源全局评分 (核心)

**输入**: 所有兴趣源的待分享内容 + 用户信号 + 长期偏好
**输出**: 2-4 个高分内容

这是整个系统最核心的步骤。详见下一节 [跨兴趣源全局评分](#跨兴趣源全局评分)。

### Step 5: 执行分享

**输入**: Step 4 选出的高分内容
**输出**: 发送给用户的消息

**语气要求**: 像微信聊天一样自然。

好的例子:
- 「诶我刚看了个视频，讲 xxx 的，挺有意思」
- 「这个播客聊到一个观点我觉得挺对的...」

不好的例子:
- 「今日推荐: 1. xxx 2. xxx」
- 「以下是我为您筛选的内容:」

如果分享多条内容，分开发几条消息，每条都像朋友随口一说。不要一次性列清单。

分享后更新状态:
- `pending-shares.json` 中标记 `shared: true`
- `mind-state.json` 中更新 `last_share` 和 `items_shared_today`

### Step 6: 无事可做

**输入**: 时间窗口/冷却/评分结果
**输出**: `HEARTBEAT_OK`

如果不满足分享条件或没有高分内容，Agent 回复 `HEARTBEAT_OK`，更新 `last_tick`，然后挂起等待下一次唤醒。用户看不到这个回复。

### Step 7: 记录观察

**输入**: 本次心跳中的任何值得记录的模式
**输出**: 更新 `mind-state.json` 中的 `recent_observations` 和 `content_preferences`

这一步是可选的。Agent 如果注意到某种模式 (比如用户连续分享 Agent 相关文章)，会记录到状态文件中，供后续心跳参考。

由于使用 isolated session，Agent 没有跨心跳的记忆。`recent_observations` (最多 5 条) 和 `content_preferences` 就是跨心跳传递学习成果的机制。

---

## 跨兴趣源全局评分

### 为什么不分别评分

传统做法: 每个兴趣源独立评分，各自推送 top-N。问题:

- 如果 Twitter 今天产出了 3 条 9 分内容，而 YouTube 只有 5 分内容，用户还是会收到低质量的 YouTube 推荐
- 无法做到「今天 Twitter 质量高就多推 Twitter，YouTube 质量低就少推」
- 各源推送时间不协调，用户可能在 5 分钟内收到 3 个源的推送

openclaw-hobby 的做法: 把所有源的所有待分享内容放在一个池子里，统一评分，全局排序，只推最好的 2-4 条。

### 评分维度

| 维度 | 权重 | 说明 |
|------|------|------|
| 长期兴趣匹配 | 40% | 内容话题与 `content_preferences.topics` 的匹配度 |
| 近期信号相关性 | 30% | 是否与用户近期分享的文章话题相关 |
| 内容质量 | 20% | 作者信誉、内容深度、原创性 |
| 时效性 | 10% | 新鲜度，越新越好 |

### 加分与减分

| 因素 | 分值调整 | 说明 |
|------|---------|------|
| 与近期 user-signals 话题直接相关 | +0.15 | 用户近期关注的话题优先 |
| 来自信誉良好的作者/频道 | +0.10 | 质量信号 |
| 有深度分析 (非泛泛而谈) | +0.10 | 内容深度 |
| 今天已分享过同一来源 | -0.20 | 来源多样性 |
| 与昨天分享的内容高度重复 | -0.30 | 去重 |
| 纯粹娱乐/八卦 | -0.50 | 内容质量底线 |

### 评分过程

Agent 不只是做关键词匹配。它真正阅读 Triage 生成的摘要和亮点 (或原始字幕/转录)，形成自己的理解，然后综合所有维度打分。

**重要原则**: 如果 Agent 没有读过内容的完整分析或原文，就不会推荐。「推荐时说'我听了这期'意味着你真的读过转录」。

---

## 用户信号如何影响评分

### 信号采集

当用户在聊天中发送一个微信文章链接 (`mp.weixin.qq.com`)，Agent 会:

1. 识别出这是一篇文章分享
2. 抓取文章内容
3. 提取 3-5 个话题关键词
4. 更新 `user-signals.json`

```
用户发送: mp.weixin.qq.com/s/xxx
         │
         ▼
Agent 提取: { "topics": ["AI Agent", "自主系统", "LLM"] }
         │
         ▼
user-signals.json: topic_weights["AI Agent"] += 1
         │
         ▼
下次心跳评分时, "AI Agent" 相关内容获得 +0.15 加分
```

### 信号衰减

超过 7 天的信号权重减半。这确保系统对用户兴趣的变化有响应性 -- 用户上周关注 AI Agent，这周关注机器人，系统会跟着调整。

### 无需手动配置

用户不需要在任何地方手动设置「我对 AI 感兴趣」。系统从用户的日常行为中持续学习。你只需要像平时一样在聊天中分享文章，系统就会自动调整。

---

## 时间窗口策略

### 设计考量

用户不是任何时候都适合接收内容推荐。openclaw-hobby 定义了两类时间:

**偏好窗口 (preferred_windows_cst: 7, 12, 16, 19)**
- 7:00 — 早晨起床，适合快速浏览
- 12:00 — 午休，有闲暇看内容
- 16:00 — 下午茶时间，工作间隙
- 19:00 — 下班后，放松浏览

**忙碌时段 (busy_hours_cst: 9, 10, 11, 14, 15)**
- 9-11 — 上午工作高峰期
- 14-15 — 下午工作高峰期

### 实际行为

一天中心跳的行为分布:

```
07:00-07:59  ████ 可能分享 (preferred window)
08:00-08:59  ──── 只检查，不分享 (非 preferred)
09:00-11:59  ╳╳╳╳ 跳过 (busy hours)
12:00-12:59  ████ 可能分享 (preferred window)
13:00-13:59  ──── 只检查，不分享
14:00-15:59  ╳╳╳╳ 跳过 (busy hours)
16:00-16:59  ████ 可能分享 (preferred window)
17:00-18:59  ──── 只检查，不分享
19:00-19:59  ████ 可能分享 (preferred window)
20:00-22:59  ──── 只检查，不分享
23:00-06:59  ╳╳╳╳ 不运行 (activeHours 外)
```

即使在 preferred window 中，还要满足冷却时间 (距上次分享 > 2 小时) 和每日上限 (< 15 条)。实际一天通常分享 3-6 条。

---

## Session Isolation 的重要性

这是我们踩过的最大的坑，值得单独详细讲。

### 问题: 时间缓存 Bug

**场景**: 心跳配置为 persistent session (所有心跳共享同一个对话历史)。

**现象**: 查看一天的决策日志，发现所有条目的时间戳都一样:

```jsonl
{"time": "12:08 CST", "action": "idle", ...}
{"time": "12:08 CST", "action": "idle", ...}
{"time": "12:08 CST", "action": "idle", ...}
{"time": "12:08 CST", "action": "idle", ...}
```

即使几个小时过去了，Agent 依然认为现在是 12:08。

**根本原因**: LLM 在长对话中会对效率做优化。当它看到历史中已经有一个 `date` 命令的输出是 "12:08 CST"，它会跳过再次执行 `date` 命令，直接复用之前的结果。这不是 bug，而是 LLM 的行为模式 -- 在 36+ 轮对话后，LLM 倾向于复用已有信息而非执行新命令。

**尝试过的解决方案 (都失败了)**:
- 在 HEARTBEAT.md 中加粗强调「必须执行 date 命令」 -- 前几次有效，之后失效
- 在 Step 0 开头写「绝对不要使用记忆中的时间」 -- 历史中的 demonstrated patterns 比文字指令更强
- 添加「如果你跳过了 date 命令，整个流程无效」 -- 依然被忽略

**唯一可靠的解决方案**: `"session": "isolated"`

```json
{
  "heartbeat": {
    "session": "isolated"
  }
}
```

每次心跳使用全新的对话上下文。没有历史记录，就没有可复用的旧时间。Agent 每次都必须真正执行 `date` 命令。

### 代价与补偿

isolated session 的代价是 Agent 没有跨心跳的记忆。补偿机制:

- `mind-state.json` 中的 `recent_observations` 字段 (最多 5 条) 承担了跨心跳的学习传递
- `content_preferences` 持久化到文件，不依赖对话记忆
- `user-signals.json` 独立于 session 存在

这个设计遵循一个原则: **持久化状态属于文件系统，不属于对话历史。**

---

## 示例决策日志

### 一次 share 决策

```json
{
  "time": "2026-02-28 12:08 CST",
  "action": "share",
  "reason": "午间窗口，选择了 2 个高分内容",
  "items": [
    {
      "source": "youtube",
      "id": "vid-abc123",
      "score": 0.92,
      "title": "DeepSeek R1 训练细节首次公开"
    },
    {
      "source": "podcast",
      "id": "ep-def456",
      "score": 0.88,
      "title": "与 OpenAI 前研究员聊 Scaling Law"
    }
  ],
  "scoring_factors": {
    "user_signal_match": ["AI", "LLM", "DeepSeek"],
    "top_interest_match": "AI/LLM/Agent",
    "diversity_check": "2 sources (youtube + podcast)"
  }
}
```

Agent 在 12:08 被唤醒，检查发现在 preferred window (12 点)，冷却已过，有 2 条高分内容。YouTube 视频因为与用户近期分享的 DeepSeek 文章高度相关获得加分 (+0.15)。Podcast 因为深度分析获得加分 (+0.10)。Agent 分两条消息自然地推荐给用户。

### 一次 idle 决策

```json
{
  "time": "2026-02-28 10:08 CST",
  "action": "idle",
  "reason": "busy_hours: 10 点在忙碌时段"
}
```

Agent 在 10:08 被唤醒，发现 10 点在 busy_hours 中，直接回复 `HEARTBEAT_OK`，不做任何评分。

### 另一次 idle 决策

```json
{
  "time": "2026-02-28 16:18 CST",
  "action": "idle",
  "reason": "冷却中: 距上次分享 (16:05) 仅 13 分钟，需等待 2 小时"
}
```

Agent 在 16:18 被唤醒，虽然在 preferred window (16 点) 中，但 13 分钟前刚分享过，冷却时间 (2 小时) 未到，跳过。

---

## mind-state.json 的跨心跳学习机制

由于使用 isolated session，Agent 没有跨心跳的对话记忆。但它需要从过去的心跳中学习。解决方案是通过 `mind-state.json` 中的两个字段:

### recent_observations

最多保留 5 条观察记录。Agent 在 Step 7 中写入:

```json
{
  "recent_observations": [
    "用户连续两天在 19 点分享后互动，19 点窗口效果最好",
    "本周 3 篇 Agent 相关内容被分享，用户反馈积极",
    "Podcast 内容比 Twitter 更容易获得高分",
    "用户周末活跃时间比工作日晚 1 小时",
    "YouTube 字幕质量高的视频评分更准确"
  ]
}
```

每次新心跳在 Step 2 读到这些观察后，会据此调整行为。新的观察替换最旧的，保持队列长度不超过 5。

### content_preferences

持久化的话题和格式权重:

```json
{
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

这些权重会被 Step 7 中的 Agent 根据观察逐步调整。比如如果 Agent 注意到用户对 robotics 内容反应冷淡，它可能会把 robotics 的权重从 0.8 降到 0.65。

**核心理念**: 每次心跳是无状态的 (isolated session)，但通过文件系统实现了有状态的学习。这避免了长对话的各种退化问题，同时保留了学习能力。
