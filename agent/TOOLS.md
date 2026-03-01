# TOOLS.md — 可用工具清单

本文件列出你可以使用的所有工具及其正确用法。**必须使用这里描述的方式调用工具，不要自己猜测或安装新工具。**

---

## 禁止事项

1. **禁止在 VPS 本地运行 Whisper**：VPS 无 GPU，本地转录会超时失败。必须使用下方的远程 Whisper API。
2. **禁止 `pip install` 新包**：用现有工具完成任务。如果确实缺少某个工具，告知用户而不是自行安装。

---

## 1. 音频转录（远程 Whisper API）

GPU 机器上运行着 Whisper API 服务，具备 GPU 加速。

### 上传文件转录

```bash
curl -X POST {{WHISPER_ENDPOINT}}/transcribe \
  -H "Authorization: Bearer {{WHISPER_TOKEN}}" \
  -F "file=@/path/to/audio.mp3"
```

### URL 转录（直接传音频 URL）

```bash
curl -X POST {{WHISPER_ENDPOINT}}/transcribe_url \
  -H "Authorization: Bearer {{WHISPER_TOKEN}}" \
  -H "Content-Type: application/json" \
  -d '{"url": "https://example.com/audio.mp3"}'
```

### 健康检查

```bash
curl {{WHISPER_ENDPOINT}}/health
```

### 注意事项
- 支持格式：mp3, m4a, wav, ogg, mp4, aac, flac
- 返回 JSON，包含 `text` 字段（转录文本）和 `segments` 字段（带时间戳的分段）
- 长音频（>30 分钟）可能需要较长处理时间，耐心等待

---

## 2. YouTube 字幕获取（代理）

如果你的 VPS 使用云服务器 IP，YouTube 可能会封锁直接字幕获取请求。建议通过 GPU 机器或有住宅 IP 的机器代理获取。

```bash
curl -X POST {{WHISPER_ENDPOINT}}/youtube/transcript \
  -H "Authorization: Bearer {{WHISPER_TOKEN}}" \
  -H "Content-Type: application/json" \
  -d '{"video_id": "dQw4w9WgXcQ", "languages": ["zh-Hans", "zh", "en"]}'
```

返回 JSON，包含 `transcript` 字段（字幕文本列表，每项有 `text`、`start`、`duration`）。

> 如果你的 VPS IP 未被封锁，也可以在 VPS 上直接使用 `yt-dlp --write-subs` 获取字幕。

---

## 3. 飞书文档（feishu-markdown MCP）

通过 `mcporter` 调用飞书文档 MCP 服务。

### 创建飞书文档

```bash
mcporter call feishu-markdown.upload_markdown_text text="# 标题\n\n正文内容" title="文档标题"
```

### 更新已有飞书文档

```bash
mcporter call feishu-markdown.update_feishu_document url="https://xxx.feishu.cn/docx/xxx" markdown="# 新内容" mode="replace"
```

### 注意事项
- 当用户说"创建飞书文档"、"写飞书文档"时使用此工具
- 创建完成后返回文档 URL 给用户

---

## 4. 飞书多维表格（feishu-base MCP）— 知识库

通过 `mcporter` 调用飞书多维表格 MCP 服务，用于读写 Bitable 数据。

**多维表格是你的知识库**——你收集的所有内容（播客、YouTube 视频、推文等）都存储在这里，包括原始数据和 LLM 分析结果。

### 知识表

| 内容类型 | table_id | 关键字段 |
|---------|----------|---------|
| 播客 | `{{PODCAST_TABLE_ID}}` | 编号, 名称, 播客名称, 摘要, 亮点, 精选原文, 主题标签 |
| YouTube | `{{YOUTUBE_TABLE_ID}}` | 编号, 名称, 频道, 摘要, 亮点, 精选原文, 主题标签 |
| Twitter | `{{TWITTER_TABLE_ID}}` | 编号, 用户, 内容, 摘要, 主题标签 |

**app_token（所有表共用）**: `{{FEISHU_APP_TOKEN}}`

### 基础操作

```bash
# 列出记录
mcporter call feishu-base.list_records app_token="{{FEISHU_APP_TOKEN}}" table_id="{{PODCAST_TABLE_ID}}"

# 创建记录
mcporter call feishu-base.create_record app_token="{{FEISHU_APP_TOKEN}}" table_id="{{PODCAST_TABLE_ID}}" fields='{"字段名": "值"}'

# 更新记录
mcporter call feishu-base.update_record app_token="{{FEISHU_APP_TOKEN}}" table_id="{{PODCAST_TABLE_ID}}" record_id="recXXX" fields='{"摘要": "新摘要"}'
```

### 按话题搜索（用于对话和心跳评分）

```bash
# 按主题标签搜索
mcporter call feishu-base.list_records \
  app_token="{{FEISHU_APP_TOKEN}}" \
  table_id="{{PODCAST_TABLE_ID}}" \
  filter='CurrentValue.[主题标签].contains("AI")'

# 按频道/用户搜索
mcporter call feishu-base.list_records \
  app_token="{{FEISHU_APP_TOKEN}}" \
  table_id="{{YOUTUBE_TABLE_ID}}" \
  filter='CurrentValue.[频道]="3Blue1Brown"'

# 获取最近记录（按时间排序）
mcporter call feishu-base.list_records \
  app_token="{{FEISHU_APP_TOKEN}}" \
  table_id="{{PODCAST_TABLE_ID}}" \
  sort='[{"field_name":"转录时间","desc":true}]' \
  page_size=10
```

### 字段类型注意
- **日期字段**：使用毫秒时间戳（`int(time.time()) * 1000`），不是 ISO 字符串
- **URL 字段**：使用 `{"link": "url", "text": "显示文本"}`，不是纯字符串
- **附件字段**：使用 `[{"file_token": "token"}]`，需先通过飞书 API 上传文件获取 token
- **主题标签字段**：纯文本，逗号分隔，例如 `"AI, 开源模型, Agent"`

---

## 5. 播客搜索（iTunes API）

免费，无需 API key。

```bash
curl -s "https://itunes.apple.com/search?term=关键词&media=podcast&limit=10" | jq '.results[] | {name: .trackName, author: .artistName, rss: .feedUrl}'
```

返回播客标题、作者、RSS URL、分类、集数等信息。

---

## 6. 网页抓取

```bash
echo '{"urls": ["https://example.com"]}' | python3 ~/clawd/skills/web-scraping/scripts/scraper.py
```

本地运行，无需远程调用。

---

## 7. yt-dlp（音频/视频下载）

```bash
# 下载音频
yt-dlp -x --audio-format mp3 "https://youtube.com/watch?v=xxx"

# 获取视频信息（不下载）
yt-dlp --dump-json "https://youtube.com/watch?v=xxx"
```

---

## 8. Watchdog 健康监控

VPS 上运行着 watchdog 服务，持续监控所有 hobby 系统服务。

### 查看健康报告（人类可读）

```bash
python3 ~/.openclaw/skills/watchdog/scripts/watchdog.py report
```

### 查看健康报告（JSON，供你读取分析）

```bash
python3 ~/.openclaw/skills/watchdog/scripts/watchdog.py report --json
```

### 告警文件

Watchdog 会自动写入 `~/.openclaw/workspace/hobby/watchdog-alerts.json`。你在心跳 Step 2 中已经会读取此文件。

### 状态含义

| overall_status | 含义 | 你的反应 |
|---------------|------|---------|
| `healthy` | 一切正常 | 忽略 |
| `degraded` | 有小问题（某个 daemon 延迟等）| 静默记录 |
| `critical` | 严重问题（网关宕机、心跳停止）| 告知用户 |

---

## 9. 内容分析（Triage Helper）

自动分析未处理的 hobby 内容，生成摘要/亮点/精选原文/主题标签，并更新飞书多维表格。

```bash
# 分析播客（默认每批5条）
python3 ~/.openclaw/skills/triage/scripts/triage-helper.py podcast --batch-size 5

# 分析 YouTube
python3 ~/.openclaw/skills/triage/scripts/triage-helper.py youtube --batch-size 5

# 分析推文
python3 ~/.openclaw/skills/triage/scripts/triage-helper.py twitter --batch-size 10

# 查看待分析状态
python3 ~/.openclaw/skills/triage/scripts/triage-helper.py status
```

---

## 10. 通用 CLI 工具

以下工具均已安装，可直接使用：

| 工具 | 用途 |
|------|------|
| `curl` | HTTP 请求 |
| `wget` | 文件下载 |
| `jq` | JSON 处理 |
| `ffmpeg` | 音视频转换/处理 |
| `python3` | Python 脚本执行 |

---

## 常见任务速查

| 任务 | 正确方式 | 错误方式 |
|------|----------|----------|
| 转录音频 | `curl POST {{WHISPER_ENDPOINT}}/transcribe` | `whisper audio.mp3` |
| 转录音频 URL | `curl POST {{WHISPER_ENDPOINT}}/transcribe_url` | `pip install whisper` |
| 获取 YouTube 字幕 | `curl POST {{WHISPER_ENDPOINT}}/youtube/transcript` | `youtube-transcript-api` |
| 创建飞书文档 | `mcporter call feishu-markdown.*` | 写本地 .md 文件 |
| 搜索播客 | iTunes API | `pip install podcastindex` |
| 下载 YouTube 音频 | `yt-dlp -x` | 可以用 |
| 抓取网页 | `scraper.py` | 可以用 |

---

## 模板变量

使用前需替换以下占位符：

| 占位符 | 说明 |
|--------|------|
| `{{WHISPER_ENDPOINT}}` | Whisper API 地址（如 `http://your-gpu-server:9876`） |
| `{{WHISPER_TOKEN}}` | Whisper API Bearer token |
| `{{FEISHU_APP_TOKEN}}` | 飞书多维表格 app_token |
| `{{PODCAST_TABLE_ID}}` | 播客多维表格 table_id |
| `{{YOUTUBE_TABLE_ID}}` | YouTube 多维表格 table_id |
| `{{TWITTER_TABLE_ID}}` | Twitter 多维表格 table_id |
