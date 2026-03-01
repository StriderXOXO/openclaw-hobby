# 踩坑记录

这些是在开发和运营 openclaw-hobby 系统过程中积累的经验教训。每一条都来自真实的线上问题。

---

## 1. 心跳 Session 隔离（最重要的一课）

**问题**：心跳（Heartbeat）在持久 session 中运行时，LLM 会使用之前对话中缓存的时间戳，导致分享决策基于错误的时间。例如，所有决策日志显示相同的 "12:08 CST"，即使实际已过去数小时。

**根因**：LLM 在长 session 中的时间推理能力会退化。当对话超过 36+ 轮且包含大量时间数据时，LLM 会优化效率、重用之前工具调用返回的时间戳。即使指令中明确写了 "Step 0: 先运行 date 命令"，最终也会被忽略。

**修复**：

```json
// moltbot.json heartbeat config
{
    "session": "isolated"   // ← 关键！每次心跳独立 session
}
```

**教训**：**Session 上下文污染是一个 bug 类别**。历史中的演示模式（demonstrated patterns）比文本指令更强。唯一可靠的修复方案是隔离 session，防止历史积累。跨心跳的状态通过 `mind-state.json` 的 `recent_observations` 字段持久化。

---

## 2. SSH 引号嵌套问题

**问题**：通过 SSH 执行包含 heredoc 的远程命令时，`read -r -d '' VAR` 会导致命令挂起。

**原因**：heredoc 的定界符在 SSH 传输过程中被错误解析。

**修复**：用单引号变量赋值替代 heredoc：

```bash
# 错误 — 会挂起
ssh root@host 'read -r -d "" MSG << "EOF"
some content
EOF'

# 正确
ssh root@host 'MSG="some content"; echo "$MSG"'
```

---

## 3. 飞书 Bitable 字段类型陷阱

### 日期时间字段

```python
# 错误 — ISO 字符串会被忽略
{"转录时间": "2025-01-15T08:30:00Z"}

# 正确 — 必须是毫秒时间戳
{"转录时间": int(time.time()) * 1000}
```

### URL 字段

```python
# 错误 — 纯字符串
{"链接": "https://example.com"}

# 正确 — 必须是 dict
{"链接": {"link": "https://example.com", "text": "显示文本"}}
```

### 附件字段

```python
# 错误 — 纯 token
{"转录文件": "file_token_abc"}

# 正确 — 必须是列表套 dict
{"转录文件": [{"file_token": "file_token_abc"}]}

# 上传：先通过 /drive/v1/medias/upload_all 获得 file_token
```

---

## 4. YouTube 云服务器 IP 封锁

**问题**：在云服务器（如腾讯云、AWS EC2）上用 `yt-dlp` 或 `youtube-transcript-api` 获取 YouTube 字幕会失败：
- "Sign in to confirm you're not a bot"
- "IP belonging to a cloud provider"

**根因**：YouTube 识别并封锁了主流云服务商的 IP 段。

**修复**：通过家用宽带 IP 的机器代理字幕请求。

```python
# youtube daemon 中的字幕获取逻辑
def fetch_subtitles(video_id, config):
    proxy_endpoint = config.get("subtitle_proxy_endpoint")
    if proxy_endpoint:
        # 通过家用 IP 机器代理
        resp = requests.post(f"{proxy_endpoint}/youtube/transcript", ...)
    else:
        # 直接获取（仅限非云服务器环境）
        from youtube_transcript_api import YouTubeTranscriptApi
        ...
```

**教训**：部署在云服务器上时，任何涉及用户身份验证/人机验证的 API 都可能失败。需要准备代理方案。

---

## 5. CUDA 版本不匹配

**问题**：GPU 机器上 `faster-whisper` 启动失败，报 CUDA 相关错误。

**根因**：系统 CUDA 11 但 faster-whisper 需要 CUDA 12。Python venv 中安装了 nvidia-cudnn-cu12 等包，但 systemd 服务不会读取 venv 的库路径。

**修复**：在 systemd service 中显式设置 `LD_LIBRARY_PATH`：

```ini
[Service]
Environment="LD_LIBRARY_PATH=/path/to/venv/lib/python3.10/site-packages/nvidia/cudnn/lib:/path/to/venv/lib/python3.10/site-packages/nvidia/cublas/lib"
```

---

## 6. Moltbot Cron 的异步陷阱

**问题**：用 `moltbot cron run <uuid> --force` 测试 cron，以为执行完毕，实际上只是触发了异步任务。

**真相**：`moltbot cron run` 是 fire-and-forget。实际执行发生在 agent session 中。

**同步测试方法**：

```bash
# 异步（火后不管）
moltbot cron run <uuid> --force

# 同步（等待完成，可看输出）
moltbot agent --session-id main --message "Run triage for podcast" --channel last
```

**教训**：对于 `--system-event` 类型的 cron，消息只是排队进入 session，需要等下一次用户交互才会触发 agent。需要主动执行的 cron 应使用 `--session isolated --message "..."` 模式。

---

## 7. Tailscale 与 VPN 路由冲突

**问题**：SSH 到 Tailscale IP（100.64.x.x）突然连不上，但 Tailscale 状态显示 online。

**根因**：某些 VPN 或代理工具的 TUN 模式会劫持 Tailscale 的 CGNAT 路由范围（100.64.0.0/10），导致 SSH 流量走代理而非 Tailscale 隧道。

**诊断**：

```bash
# macOS
route -n get 100.64.x.x    # 替换为你的 Tailscale IP
# 如果显示 utun1024（VPN） → 问题确认
# 应该显示 utun8（Tailscale）

# Linux
ip route get 100.64.x.x
```

**修复**：在 VPN/代理工具的 TUN 设置中添加路由排除：

```
route-exclude-address: 100.64.0.0/10
```

---

## 8. JSON 状态文件损坏

**问题**：Agent 尝试编辑 `mind-state.json` 时失败，导致心跳停止运行。

**根因**：手动编辑 JSON 时漏了逗号，导致文件不合法。Agent 的编辑操作（增量修改）依赖文件是合法 JSON。

**预防**：

```bash
# 手动编辑后务必验证
cat mind-state.json | jq .

# Watchdog 自动检测并修复
# watchdog.py 的 liveness check 会检查关键 JSON 文件
```

**教训**：Watchdog 应该包含 JSON 完整性检查，并能自动修复（从备份恢复或重置为默认值）。

---

## 9. LLM 提示词质量差距巨大

**问题**：一句英文提示 "analyze this podcast" 产出的摘要几乎无用。

**修复**：结构化中文提示词 + 角色设定 + 正反示例 + 反模式列举，产出质量天壤之别。

**关键要素**：
1. **角色设定**：明确 LLM 是什么角色（"资深科技播客分析师"）
2. **输出格式**：严格的 JSON schema + 字段说明
3. **正面示例**：展示高质量输出长什么样
4. **负面示例**：明确列出常见的低质量模式（"不要说泛泛而谈的话"）
5. **长度限制**：明确每个字段的字数范围

**教训**：Prompt engineering 的投入产出比极高。花 2 小时优化提示词，比花 20 小时优化代码逻辑更有效。

---

## 10. 并发 SSH 触发封锁

**问题**：对 VPS 同时发起多个 SSH/SCP 连接时，后续连接被拒绝。

**根因**：VPS 的安全策略（类 fail2ban）将短时间内的多次连接视为暴力破解尝试。

**规则**：**永远不要并行 SSH/SCP 到 VPS**。部署多个文件时，用一次 SSH session 内的 tar 传输：

```bash
# 错误 — 并行 SCP
scp file1 root@vps:/path/ &
scp file2 root@vps:/path/ &

# 正确 — 打包后单次传输
tar czf - file1 file2 | ssh root@vps 'cd /path && tar xzf -'
```

---

## 11. loginctl enable-linger 不可或缺

**问题**：SSH 登出后，user-level systemd 服务（如 OpenClaw gateway）自动停止。

**根因**：默认情况下，Linux 会在用户 session 结束时杀死该用户的所有进程。

**修复**：

```bash
loginctl enable-linger root   # 或你的用户名
```

**教训**：任何 `--user` 级别的 systemd 服务都需要 linger。这是部署 OpenClaw gateway 的前置条件。

---

## 总结：系统运营心法

1. **隔离是最好的调试工具**：Session 隔离、进程隔离、网络隔离——出问题时先想隔离
2. **状态文件是脆弱的**：JSON 文件会损坏、会过时、会冲突。Watchdog 必须监控它们
3. **LLM 不是确定性系统**：同样的输入可能产出不同的结果。用结构化约束来缩小输出空间
4. **基础设施比代码更容易出问题**：网络路由、CUDA 版本、IP 封锁、SSH 引号——这些非代码问题消耗了 60% 的调试时间
5. **可观测性 > 预防**：你无法预防所有问题，但 good logging + watchdog + structured decision logs 能让你快速定位和修复
