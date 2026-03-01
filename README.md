# openclaw-hobby

**基于 [OpenClaw](https://github.com/openclaw/openclaw) 的自主兴趣内容策展系统 -- 7x24 无人值守采集、智能分析、心跳驱动主动分享。**

---

## 快速体验

### 30 秒试试看（零配置）

```bash
git clone https://github.com/your-org/openclaw-hobby.git
cd openclaw-hobby
pip install -e .
hobee demo
# → 立即看到采集到的中文科技播客
```

### 5 分钟部署（开始持续采集）

```bash
hobee setup                          # 交互式配置
hobee podcast daemon                 # 启动持续采集（或用 systemd 管理）
```

### 进阶：添加更多内容源

每个源独立可选，按需添加：

```bash
# Podcast: 免费，零 API Key
hobee podcast subscribe <rss_url>
hobee podcast search "科技"

# Twitter: 需 twitterapi.io key（在 .env 中设置 TWITTER_API_KEY）
# YouTube: 需 Google OAuth（运行 scripts/setup.sh 按提示授权）
```

### 进阶：LLM 智能分析

```bash
# 在 .env 中设置 LLM_API_KEY，支持 Anthropic / DeepSeek / OpenAI 兼容
hobee triage podcast               # 分析播客内容
hobee triage youtube               # 分析 YouTube 内容
```

### 进阶：飞书知识库

```bash
hobee setup                        # 选择飞书存储，按提示配置
# 或手动编辑 .env，设置 STORAGE_BACKEND=feishu + 飞书凭证
```

---

## 系统架构总览

```
              采集层 (Daemons)              存储层 (StorageBackend)
          ┌────────────────────┐         ┌──────────────────────┐
          │  Podcast Daemon    │────────>│  SQLite (默认，零配置) │
          │  YouTube Daemon    │────────>│  或                   │
          │  Twitter Daemon    │────────>│  飞书多维表格 (可选)   │
          │  (无 LLM, 纯采集) │         │  或自定义后端          │
          └────────────────────┘         └──────────┬───────────┘
                                                    │
              分析层 (Triage)                        │
          ┌────────────────────┐                    │
          │  triage_helper.py  │<───────────────────┘
          │  LLM 深度分析      │  查询未分析记录
          │  摘要/亮点/精选原文 │  写回分析结果
          │  主题标签           │
          └────────────────────┘
                    │
                    ▼
              决策层 (Heartbeat)
          ┌────────────────────┐
          │  OpenClaw Agent    │  每 10 分钟唤醒
          │  读取所有待分享内容 │
          │  跨兴趣源全局评分   │  ──────> 飞书/Telegram/Discord
          │  用户信号加权       │          推送给用户
          │  时间窗口判断       │
          └────────────────────┘

              监控层 (Watchdog)
          ┌────────────────────┐
          │  服务健康检查       │  3 级检测 (2min/10min/30min)
          │  自动故障恢复       │  崩溃重启、JSON 修复
          │  每日运营报告       │  CST 8:00 推送飞书
          └────────────────────┘
```

**数据流**: 守护进程采集原始内容 → 存储后端持久化 → Triage 用 LLM 深度分析 → Heartbeat 全局评分并主动分享给用户。

---

## 核心创新

1. **心跳驱动主动分享** — Agent 每 10 分钟判断「有没有值得分享的 + 现在合不合适」，不是机械定时推送
2. **跨兴趣源全局评分** — Podcast/YouTube/Twitter 放在一起排序，确保用户收到最值得看的内容
3. **用户信号反馈循环** — 用户转发一篇文章，系统自动调整兴趣权重
4. **哑采集 + 智能分析** — Daemon 只做 API 抓取（零成本），LLM 集中在 Triage 和 Heartbeat 层
5. **可插拔存储** — 默认 SQLite 零配置，可切换飞书/Notion/自定义

---

## CLI 命令一览

```bash
hobee demo                     # 零配置体验
hobee status                   # 系统状态
hobee setup                    # 交互式配置

hobee podcast daemon           # 播客采集守护进程
hobee podcast subscribe <url>  # 订阅播客
hobee podcast search <query>   # 搜索播客 (iTunes)
hobee podcast list             # 列出订阅

hobee triage <hobby>           # LLM 分析 (podcast/youtube/twitter)
hobee triage <hobby> --dry-run # 预览待分析内容
```

---

## 配置层级

| 场景 | 需要什么 | 时间 |
|------|---------|------|
| 先看看是啥 | `pip install -e . && hobee demo` | 30 秒 |
| 只跑 Podcast | 零配置 | 1 分钟 |
| 加上 LLM 分析 | 设 1 个 `LLM_API_KEY` | 2 分钟 |
| 完整飞书体验 | `hobee setup` 向导 | 10 分钟 |
| 3 源 + 心跳 + 飞书 | `hobee setup` + systemd | 30 分钟 |

---

## 目录结构

```
openclaw-hobby/
├── hobee/                     # 核心库
│   ├── __init__.py
│   ├── cli.py                 # hobee CLI 入口
│   ├── config.py              # 统一配置 (env > config.json > 默认值)
│   ├── daemon.py              # BaseDaemon 守护进程基类
│   ├── logging_utils.py       # 日志工具
│   └── storage/               # 存储后端
│       ├── base.py            # StorageBackend ABC
│       ├── sqlite.py          # SQLite 实现 (默认)
│       └── feishu.py          # 飞书多维表格实现
├── daemons/                   # 各兴趣源守护进程
│   ├── podcast/               # Podcast RSS 监控 + 转录
│   ├── youtube/               # YouTube 频道监控 + 字幕
│   └── twitter/               # Twitter 账号监控
├── triage/                    # LLM 内容分析
│   └── triage_helper.py
├── agent/                     # OpenClaw Agent 指令 (含 {{占位符}})
│   ├── HEARTBEAT.md           # 心跳决策 7 步流程
│   ├── TOOLS.md               # 可用工具清单
│   └── SOUL.md                # Agent 人格设定
├── watchdog/                  # 健康监控
├── config/                    # 配置模板
├── systemd/                   # systemd 服务单元
├── scripts/                   # 安装 / 部署 / 工具脚本
│   ├── setup.sh               # 一键安装
│   └── generate-agent-files.sh # 自动填充 agent 占位符
├── docs/                      # 详细文档
├── pyproject.toml             # Python 包定义
├── .env.example               # 环境变量模板 (分层标注)
└── README.md
```

---

## 前置依赖

| 依赖 | 版本 | 必需？ | 用途 |
|------|------|--------|------|
| Python | 3.10+ | 是 | 核心运行 |
| requests + feedparser | * | 是 | 基础采集 (pip install -e .) |
| [OpenClaw](https://github.com/openclaw/openclaw) | 最新版 | 心跳/Cron 需要 | Agent 运行时 |
| LLM API | Anthropic 兼容 | 可选 | Triage 分析 |
| 飞书应用 | - | 可选 | 存储 + 推送 |

---

## 文档

| 文档 | 说明 |
|------|------|
| [系统架构](docs/architecture.md) | 四层架构设计、数据流、状态文件 |
| [心跳详解](docs/heartbeat-deep-dive.md) | 7 步决策流程、全局评分、用户信号 |
| [部署指南](docs/deployment.md) | 单机/多机部署、systemd 配置 |
| [定制指南](docs/customization.md) | 添加新兴趣源、替换存储后端 |
| [踩坑记录](docs/lessons-learned.md) | 经验教训 |

---

## License

[MIT](LICENSE)
