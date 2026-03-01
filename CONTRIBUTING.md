# Contributing

感谢你对 openclaw-hobby 的关注！欢迎各种形式的贡献。

## 如何参与

### 报告 Bug / 提建议

直接 [创建 Issue](https://github.com/StriderXOXO/openclaw-hobby/issues)，描述你遇到的问题或想法。

### 添加新内容源

最有价值的贡献之一是添加新的内容源。只需要：

1. 在 `daemons/` 下创建新目录
2. 继承 `hobee.daemon.BaseDaemon`，实现 `collect_once()` 方法
3. 参考 `daemons/podcast/daemon.py` 的模式

详见 [定制指南](docs/customization.md)。

### 提交代码

```bash
# 1. Fork & clone
git clone https://github.com/YOUR_USERNAME/openclaw-hobby.git
cd openclaw-hobby

# 2. 安装开发依赖
pip install -e ".[dev]"

# 3. 运行测试
pytest tests/ -v

# 4. 创建分支、提交、发 PR
git checkout -b feature/my-change
# ... 改代码 ...
pytest tests/ -v  # 确保测试通过
git commit -m "Add: ..."
git push origin feature/my-change
```

## 代码规范

- 保持现有代码风格
- 新功能请附带测试
- Commit message 用英文，简洁明了
