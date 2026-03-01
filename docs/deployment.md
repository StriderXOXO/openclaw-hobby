# 部署指南

## 前置条件

| 依赖 | 版本 | 说明 |
|------|------|------|
| Python | 3.10+ | 守护进程、Triage 脚本、Watchdog |
| pip 包 | `requests` | HTTP 客户端 (存储后端 API 调用) |
| OpenClaw | 最新版 | Agent 运行时、心跳、Cron、消息通道 |
| LLM API | - | 兼容 Anthropic Messages API 的提供商 |
| systemd | - | 守护进程管理 (Linux) |

可选依赖:

| 依赖 | 用途 |
|------|------|
| 飞书企业自建应用 | 默认存储后端 + 消息推送 |
| Whisper API | Podcast 音频转录 |
| Twitter API | Twitter 内容采集 |
| YouTube Data API | YouTube 内容采集 |

---

## 单机部署 (VPS 上全部运行)

适合入门和个人使用。所有组件运行在同一台 VPS 上。

### Step 1: 安装 OpenClaw

```bash
# 安装 OpenClaw (参考 OpenClaw 官方文档)
npm install -g openclaw

# 验证安装
openclaw --version
```

### Step 2: 克隆仓库

```bash
cd /root
git clone https://github.com/your-org/openclaw-hobby.git
cd openclaw-hobby
```

### Step 3: 配置环境变量

```bash
cp .env.example .env
vim .env
```

必须填写的环境变量:

```bash
# LLM (Triage 分析必需)
LLM_ENDPOINT=https://api.anthropic.com/v1/messages
LLM_API_KEY=sk-your-key-here
LLM_MODEL=claude-sonnet-4-20250514

# 飞书 (默认存储后端)
FEISHU_APP_ID=cli_xxx
FEISHU_APP_SECRET=your-secret
FEISHU_APP_TOKEN=your-app-token
PODCAST_TABLE_ID=tblXxx
YOUTUBE_TABLE_ID=tblXxx
TWITTER_TABLE_ID=tblXxx
```

### Step 4: 安装 Python 依赖

```bash
pip3 install requests
```

### Step 5: 配置 OpenClaw Agent

准备 Agent 指令文件:

```bash
# 创建 Agent 指令目录
mkdir -p ~/clawd

# 复制 Agent 指令文件
cp agent/HEARTBEAT.md ~/clawd/
cp agent/SOUL.md ~/clawd/
```

配置 OpenClaw gateway:

```bash
# 初始化 OpenClaw 配置
openclaw config set gateway.mode local
openclaw config set gateway.auth.token $(openssl rand -hex 32)

# 配置心跳
# 编辑 ~/.moltbot/moltbot.json，添加:
```

```json
{
  "agents": {
    "defaults": {
      "heartbeat": {
        "every": "10m",
        "target": "feishu",
        "session": "isolated",
        "activeHours": { "start": "07:00", "end": "23:00" }
      }
    }
  }
}
```

**注意**: `"session": "isolated"` 是必须的。不设置会导致时间缓存 bug，详见 [踩坑记录](lessons-learned.md#1-session-isolation-是必须的)。

### Step 6: 创建 workspace 目录

```bash
mkdir -p ~/.openclaw/workspace/hobby/logs
mkdir -p ~/.openclaw/workspace/podcast-hobby/transcripts
mkdir -p ~/.openclaw/workspace/youtube-hobby/subtitles
mkdir -p ~/.openclaw/workspace/twitter-hobby
```

### Step 7: 确保 gateway 持久运行

```bash
# 关键: 启用 linger，否则 SSH 断开后 gateway 会停止
loginctl enable-linger $(whoami)

# 启动 gateway
openclaw gateway run &
```

---

## 多机部署 (VPS + GPU 机器)

适合需要 Podcast 音频转录的场景。VPS 运行所有服务，GPU 机器运行 Whisper API 做音频转录。

```
┌── VPS ──────────────────┐     ┌── GPU 机器 ─────────────┐
│  3 Daemons              │     │  Whisper API (FastAPI)   │
│  Triage Crons           │────>│  faster-whisper + CUDA   │
│  Heartbeat Engine       │     │  端口: 9876              │
│  Watchdog               │     └──────────────────────────┘
│  OpenClaw Gateway       │
└─────────────────────────┘
```

### GPU 机器上安装 Whisper API

```bash
# 创建 Python 虚拟环境
python3 -m venv whisper-venv
source whisper-venv/bin/activate

# 安装依赖
pip install fastapi uvicorn faster-whisper

# 启动 Whisper API
python3 whisper_api.py --host 0.0.0.0 --port 9876
```

### VPS 上配置转录代理

在 `.env` 中设置:

```bash
WHISPER_ENDPOINT=http://<gpu-machine-ip>:9876
WHISPER_TOKEN=your-whisper-api-token
```

### 网络要求

两台机器需要网络互通。推荐使用 Tailscale 组建虚拟局域网:

```bash
# 两台机器分别安装 Tailscale
curl -fsSL https://tailscale.com/install.sh | sh
tailscale up

# 使用 Tailscale IP 通信
# VPS → GPU: http://100.x.x.x:9876
```

**注意**: YouTube 字幕获取可能在云服务商 IP 上被封锁。如果遇到「Sign in to confirm you're not a bot」错误，需要通过住宅 IP 的机器代理字幕请求。详见 [踩坑记录](lessons-learned.md#3-youtube-封锁云服务商-ip)。

---

## systemd 服务安装

### 守护进程服务

为每个兴趣源创建 systemd 服务:

```ini
# /etc/systemd/system/podcast-hobby.service
[Unit]
Description=OpenClaw Podcast Hobby Daemon
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart=/usr/bin/python3 /path/to/openclaw-hobby/daemons/podcast/podcast_daemon.py daemon
Restart=always
RestartSec=60
Environment=TZ=Asia/Shanghai
Environment=PYTHONUNBUFFERED=1
EnvironmentFile=/path/to/openclaw-hobby/.env
WorkingDirectory=/path/to/openclaw-hobby

[Install]
WantedBy=multi-user.target
```

YouTube 和 Twitter 类似，修改 `ExecStart` 路径即可。

```bash
# 安装服务
sudo cp systemd/*.service /etc/systemd/system/
sudo systemctl daemon-reload

# 启用并启动
sudo systemctl enable podcast-hobby youtube-hobby twitter-hobby
sudo systemctl start podcast-hobby youtube-hobby twitter-hobby

# 查看状态
sudo systemctl status podcast-hobby youtube-hobby twitter-hobby
```

**关键**: `PYTHONUNBUFFERED=1` 环境变量确保 Python 输出立即写入 journalctl，否则日志会延迟很久才出现。

### Watchdog 服务

```ini
# /etc/systemd/system/watchdog.service
[Unit]
Description=OpenClaw Hobby Watchdog
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart=/usr/bin/python3 /path/to/openclaw-hobby/watchdog/watchdog.py daemon
Restart=always
RestartSec=30
Environment=TZ=Asia/Shanghai
Environment=PYTHONUNBUFFERED=1
EnvironmentFile=/path/to/openclaw-hobby/.env
WorkingDirectory=/path/to/openclaw-hobby

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable watchdog
sudo systemctl start watchdog
```

---

## OpenClaw Gateway 配置

### moltbot.json 完整示例

```json
{
  "agents": {
    "defaults": {
      "model": {
        "provider": "your-provider",
        "model": "claude-sonnet-4-20250514"
      },
      "heartbeat": {
        "every": "10m",
        "target": "feishu",
        "session": "isolated",
        "activeHours": { "start": "07:00", "end": "23:00" }
      }
    }
  },
  "models": {
    "providers": {
      "your-provider": {
        "endpoint": "https://api.anthropic.com",
        "apiKey": "sk-your-key",
        "api": "anthropic-messages",
        "authHeader": true
      }
    }
  },
  "gateway": {
    "mode": "local",
    "auth": {
      "token": "your-gateway-token"
    }
  }
}
```

**注意**: `"api": "anthropic-messages"` 必须显式设置，否则 Agent 启动时会报 "Unhandled API" 错误。

---

## Triage Cron 注册

Triage 使用 OpenClaw Cron 定期触发:

```bash
# 注册 Podcast Triage (每 3 小时)
openclaw cron add \
  --name "podcast-triage" \
  --schedule "0 */3 * * *" \
  --session isolated \
  --message "请运行 podcast triage: python3 /path/to/triage/triage_helper.py podcast --batch-size 5"

# 注册 YouTube Triage (每 3 小时)
openclaw cron add \
  --name "youtube-triage" \
  --schedule "0 1,4,7,10,13,16,19,22 * * *" \
  --session isolated \
  --message "请运行 youtube triage: python3 /path/to/triage/triage_helper.py youtube --batch-size 5"

# 注册 Twitter Triage (每 2 小时)
openclaw cron add \
  --name "twitter-triage" \
  --schedule "0 */2 * * *" \
  --session isolated \
  --message "请运行 twitter triage: python3 /path/to/triage/triage_helper.py twitter --batch-size 10"

# 查看已注册的 cron
openclaw cron list
```

**注意**: Triage cron 使用 `--session isolated`，不需要 `--deliver` 参数 (静默执行，不推送结果给用户)。

---

## 存储后端配置: 飞书多维表格

### Step 1: 创建飞书企业自建应用

1. 访问 [飞书开放平台](https://open.feishu.cn)
2. 创建企业自建应用
3. 获取 App ID 和 App Secret
4. 开通以下权限:
   - `bitable:app` — 多维表格读写
   - `im:message` — 消息发送 (用于心跳推送)
   - `drive:file` — 文件上传 (用于附件)

### Step 2: 创建多维表格

1. 在飞书中创建一个多维表格
2. 从 URL 中获取 App Token (形如 `ABCDeFGHiJKlMnOpQrStUvWxYz`)
3. 为每个兴趣源创建一张表:

**Podcast 表字段**:

| 字段名 | 类型 | 说明 |
|--------|------|------|
| 名称 | 文本 | 节目标题 |
| 编号 | 文本 | GUID (去重用) |
| 链接 | URL | 节目链接 |
| 播客名称 | 文本 | 所属播客 |
| 发布时间 | 日期 | 发布日期 (毫秒时间戳) |
| 摘要 | 文本 | Triage 生成的摘要 |
| 亮点 | 文本 | Triage 生成的亮点 |
| 精选原文 | 文本 | Triage 精选的原话 |
| 主题标签 | 文本 | Triage 生成的标签 |

**YouTube 表** 和 **Twitter 表** 类似，字段名可能略有不同。

4. 获取每张表的 Table ID (从 URL 或 API 获取)

### Step 3: 填写环境变量

```bash
FEISHU_APP_ID=cli_xxx
FEISHU_APP_SECRET=your-secret
FEISHU_APP_TOKEN=your-app-token
PODCAST_TABLE_ID=tblXxx
YOUTUBE_TABLE_ID=tblXxx
TWITTER_TABLE_ID=tblXxx
FEISHU_CHAT_ID=oc_xxx    # 可选: 消息推送的群聊 ID
```

---

## 日常运维命令

### 服务管理

```bash
# 查看服务状态
systemctl status podcast-hobby youtube-hobby twitter-hobby watchdog

# 查看日志 (最近 1 小时)
journalctl -u podcast-hobby --since "1 hour ago"

# 实时查看日志
journalctl -u podcast-hobby -f

# 重启服务
systemctl restart podcast-hobby

# 停止服务
systemctl stop podcast-hobby
```

### Watchdog 命令

```bash
# 查看健康报告
python3 watchdog/watchdog.py report

# 查看健康报告 (JSON 格式)
python3 watchdog/watchdog.py report --json

# 预览每日报告 (不发送)
python3 watchdog/watchdog.py daily-report --dry-run

# 发送每日报告
python3 watchdog/watchdog.py daily-report

# 查看指定日期的报告
python3 watchdog/watchdog.py daily-report --date 2026-02-27
```

### Triage 管理

```bash
# 查看各兴趣源的未分析数量
python3 triage/triage_helper.py status

# 手动触发 Triage
python3 triage/triage_helper.py podcast --batch-size 5

# 干跑 (查看哪些条目会被处理)
python3 triage/triage_helper.py podcast --dry-run
```

### 心跳调试

```bash
# 查看心跳是否正常触发
journalctl --user -u moltbot-gateway | grep heartbeat

# 查看今天的决策日志
cat ~/.openclaw/workspace/hobby/logs/decisions-$(date +%Y-%m-%d).jsonl | tail -5

# 查看 mind-state
cat ~/.openclaw/workspace/hobby/mind-state.json | python3 -m json.tool

# 检查时间戳是否正常 (应该都不同)
cat ~/.openclaw/workspace/hobby/logs/decisions-$(date +%Y-%m-%d).jsonl | python3 -c "
import sys, json
for line in sys.stdin:
    d = json.loads(line)
    print(d.get('time', 'N/A'), d.get('action', 'N/A'))
"

# 重启 gateway (如果心跳卡住)
systemctl --user restart moltbot-gateway
```

---

## 常见问题排查

### 1. 服务启动失败

**现象**: `systemctl status xxx` 显示 `failed`

**排查**:
```bash
journalctl -u xxx --since "5 minutes ago" --no-pager
```

常见原因:
- Python 依赖缺失 → `pip3 install requests`
- 环境变量未加载 → 检查 `EnvironmentFile` 路径
- 路径错误 → 检查 `ExecStart` 和 `WorkingDirectory`

### 2. Triage 没有分析结果

**现象**: `triage_helper.py status` 显示大量 untriaged

**排查**:
- 检查 LLM API 是否正常: `curl -s $LLM_ENDPOINT` 是否返回
- 检查 pending-shares.json 中的条目是否有 `record_id`
- 手动运行: `python3 triage/triage_helper.py podcast --batch-size 1`

### 3. 心跳不触发

**现象**: 决策日志长时间没有新条目

**排查**:
```bash
# 检查 gateway 是否运行
systemctl --user status moltbot-gateway

# 检查 linger 是否启用
loginctl show-user $(whoami) | grep Linger

# 重启 gateway
systemctl --user restart moltbot-gateway
```

### 4. 飞书 API 报错

**现象**: `Create record failed: {"code": 1254043, ...}`

常见错误码:
- `1254043`: 权限不足 → 检查应用权限配置
- `1254045`: 字段不存在 → 检查表格字段名是否匹配
- `1254300`: token 过期 → 存储后端会自动刷新，如持续出现检查 app_id/app_secret

### 5. 心跳时间戳不变

**现象**: 决策日志中所有条目时间戳相同

**原因**: 未配置 session isolation

**修复**:
```bash
# 确认 heartbeat 配置中有 "session": "isolated"
cat ~/.moltbot/moltbot.json | python3 -c "
import sys, json
d = json.load(sys.stdin)
hb = d.get('agents', {}).get('defaults', {}).get('heartbeat', {})
print('session:', hb.get('session', 'NOT SET'))
"
# 如果不是 "isolated"，修改配置后重启 gateway
systemctl --user restart moltbot-gateway
```
