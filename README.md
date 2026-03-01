# openclaw-hobby

**治愈你的信息焦虑。** 让 AI 替你听完 2 小时的播客，只把最精华的 5 分钟推给你。

> Your AI reads the internet so you don't have to.
> Monitors Podcasts, YouTube, and Twitter 24/7 — analyzes with AI — shares what matters, when it matters.

[![Python 3.9+](https://img.shields.io/badge/python-3.9+-blue.svg)](https://python.org)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Tests: 91 passed](https://img.shields.io/badge/tests-91%20passed-brightgreen.svg)]()

---

## 这是给谁用的？

- **忙碌的开发者/创业者** — 想跟踪技术趋势但没时间每天刷 feed
- **播客重度用户** — 订阅了 20 个播客但每周只听 2 个
- **信息焦虑患者** — 怕错过重要内容，又讨厌通知轰炸
- **自建狂魔** — 不信任算法推荐，想要自己掌控信息源

---

## 为什么选 openclaw-hobby？

- **不再错过重要内容** — 7x24 替你监控 Podcast / YouTube / Twitter，你睡觉它不睡
- **只看精华，不看噪音** — AI 读完全文/听完全集，提炼摘要和亮点
- **在对的时间收到推送** — 不是机械定时，而是 AI 判断「现在合适吗？这条值得吗？」
- **越用越懂你** — 转发一篇文章，系统自动调整你的兴趣偏好

### vs 其他方案

| | FreshRSS | n8n | TrendRadar | **openclaw-hobby** |
|---|---------|-----|------------|-----|
| 数据源 | RSS | 自定义工作流 | 多平台关键词 | Podcast + YouTube + Twitter |
| AI 分析 | 无 | 需自建 | 关键词过滤 | **LLM 深度摘要 + 评分** |
| 推送逻辑 | 手动查看 | 自定义触发 | 定时推送 | **AI 判断最佳时机** |
| 用户反馈 | 无 | 无 | 无 | **转发即学习** |
| 部署难度 | Docker | Docker | GitHub Actions | `pip install` + `hobee demo` |

---

## 快速体验

### 30 秒试试看（零配置）

```bash
git clone https://github.com/StriderXOXO/openclaw-hobby.git
cd openclaw-hobby
pip install -e .
hobee demo
# → 立即看到采集到的中文科技播客摘要
```

### 5 分钟部署（开始持续采集）

```bash
hobee setup                          # 交互式配置向导
hobee podcast daemon                 # 启动持续采集（或用 systemd 管理）
```

### 按需进阶

| 想要什么 | 怎么做 | 时间 |
|---------|--------|------|
| 先看看效果 | `pip install -e . && hobee demo` | 30 秒 |
| 持续采集 Podcast | `hobee podcast daemon` | 1 分钟 |
| AI 智能分析 | 设 1 个 `LLM_API_KEY` → `hobee triage podcast` | 2 分钟 |
| 飞书知识库 + 推送 | `hobee setup` 向导配置 | 10 分钟 |
| 全套：3 源 + 心跳 + 飞书 | `hobee setup` + systemd 部署 | 30 分钟 |

<details>
<summary>添加更多内容源</summary>

```bash
# Podcast: 免费，零 API Key
hobee podcast subscribe <rss_url>
hobee podcast search "科技"

# Twitter: 需 twitterapi.io key（在 .env 中设置 TWITTER_API_KEY）
# YouTube: 需 Google OAuth（运行 scripts/setup.sh 按提示授权）
```

</details>

<details>
<summary>LLM 智能分析</summary>

```bash
# 在 .env 中设置 LLM_API_KEY，支持 Anthropic / DeepSeek / OpenAI 兼容
hobee triage podcast               # 分析播客内容
hobee triage youtube               # 分析 YouTube 内容
```

</details>

<details>
<summary>飞书知识库</summary>

```bash
hobee setup                        # 选择飞书存储，按提示配置
# 或手动编辑 .env，设置 STORAGE_BACKEND=feishu + 飞书凭证
```

</details>

---

## 系统架构

```
              采集层 (Daemons)              存储层 (Storage)
          ┌────────────────────┐         ┌──────────────────────┐
          │  Podcast Daemon    │────────>│  SQLite (默认，零配置) │
          │  YouTube Daemon    │────────>│  或                   │
          │  Twitter Daemon    │────────>│  飞书多维表格 (可选)   │
          │  (无 LLM, 纯采集) │         │  或自定义后端          │
          └────────────────────┘         └──────────┬───────────┘
                                                    │
              分析层 (Triage)                        │
          ┌────────────────────┐                    │
          │  LLM 深度分析      │<───────────────────┘
          │  摘要 / 亮点       │  查询未分析记录
          │  精选原文 / 标签   │  写回分析结果
          └────────────────────┘
                    │
                    ▼
              决策层 (Heartbeat)
          ┌────────────────────┐
          │  AI Agent          │  每 10 分钟唤醒
          │  跨源全局评分       │  ──────> 飞书 / Telegram / Discord
          │  用户信号加权       │          推送给用户
          │  时间窗口判断       │
          └────────────────────┘

              监控层 (Watchdog)
          ┌────────────────────┐
          │  3 级健康检测       │  2min / 10min / 30min
          │  自动故障恢复       │  崩溃重启、JSON 修复
          │  每日运营报告       │  CST 8:00 推送
          └────────────────────┘
```

**数据流**: 守护进程采集 → 存储持久化 → LLM 深度分析 → AI 全局评分 → 主动推送给用户

---

## CLI 命令速查

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

## 常见问题

**不装 OpenClaw 能用吗？**
> 能。采集 + LLM 分析完全独立运行。心跳智能推送需要 [OpenClaw](https://github.com/openclaw/openclaw) Agent 运行时。

**费用多少？**
> 内容采集完全免费。LLM 分析按量付费：DeepSeek 约 ¥0.1/条，Claude Haiku 约 $0.01/条。不开 LLM 也能用，只是没有智能摘要。

**能加自己的内容源吗？**
> 能。继承 `BaseDaemon`，实现 `collect_once()` 方法即可。详见 [定制指南](docs/customization.md)。

**支持 Windows / Docker 吗？**
> 目前需要 Linux（systemd 管理服务）。Docker 支持 coming soon。macOS 可用于开发和 demo。

**和 RSS 阅读器有什么区别？**
> RSS 阅读器是被动的——你去刷它。openclaw-hobby 是主动的——它来找你，而且只在合适的时间推送最值得看的内容。

---

## 文档

| 文档 | 说明 |
|------|------|
| [系统架构](docs/architecture.md) | 四层架构设计、数据流、状态文件 |
| [心跳详解](docs/heartbeat-deep-dive.md) | 7 步决策流程、全局评分、用户信号 |
| [部署指南](docs/deployment.md) | 单机/多机部署、systemd 配置 |
| [定制指南](docs/customization.md) | 添加新兴趣源、替换存储后端 |
| [踩坑记录](docs/lessons-learned.md) | 实战经验教训 |

---

## 目录结构

<details>
<summary>点击展开</summary>

```
openclaw-hobby/
├── hobee/                     # 核心库
│   ├── cli.py                 # hobee CLI 入口
│   ├── config.py              # 统一配置 (env > config.json > 默认值)
│   ├── daemon.py              # BaseDaemon 守护进程基类
│   └── storage/               # 可插拔存储后端
│       ├── base.py            # StorageBackend ABC
│       ├── sqlite.py          # SQLite 实现 (默认)
│       └── feishu.py          # 飞书多维表格实现
├── daemons/                   # 各兴趣源守护进程
│   ├── podcast/               # Podcast RSS 监控 + 转录
│   ├── youtube/               # YouTube 频道监控 + 字幕
│   └── twitter/               # Twitter 账号监控
├── triage/                    # LLM 内容分析
├── agent/                     # AI Agent 指令模板 (含 {{占位符}})
├── watchdog/                  # 健康监控 + 自动恢复
├── config/                    # 配置模板
├── systemd/                   # systemd 服务单元
├── scripts/                   # 安装 / 部署脚本
├── tests/                     # 单元测试 (91 个)
├── docs/                      # 详细文档
└── .env.example               # 环境变量模板
```

</details>

---

## 前置依赖

| 依赖 | 必需？ | 说明 |
|------|--------|------|
| Python 3.9+ | 是 | 核心运行环境 |
| requests + feedparser | 是 | `pip install -e .` 自动安装 |
| [OpenClaw](https://github.com/openclaw/openclaw) | 心跳推送需要 | AI Agent 运行时 |
| LLM API | 可选 | 任何 Anthropic Messages API 兼容服务 |
| 飞书企业应用 | 可选 | 知识库存储 + 消息推送 |

---

## Contributing

欢迎贡献！请查看 [CONTRIBUTING.md](CONTRIBUTING.md) 了解如何参与。

## License

[MIT](LICENSE)

---

如果这个项目帮你从信息焦虑中解脱了一点点，请点个 Star 支持一下！

有问题或建议？欢迎 [提 Issue](https://github.com/StriderXOXO/openclaw-hobby/issues)。
