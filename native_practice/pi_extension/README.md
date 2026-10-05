# L28：Pi 原生扩展练习

`extensions/learning-stats.ts` 是一个只在显式指定时加载的 Pi 扩展。它刻意保持很小：

- 监听 `agent_start` 和 `tool_execution_end`，在状态栏显示运行计数；
- 注册只读工具 `learning_read`，只允许读取当前工作目录内最多 80 行文本；
- 监听 `tool_call`，阻止对 `tests/`、`.env` 和 `.git/` 的 `write`/`edit`。

扩展没有 shell、网络请求或任意写文件能力。

## 独立行为探针

在 MyAgent 根目录执行：

```bash
cd /path/to/MyAgent
/path/to/pi-reference/node_modules/.bin/tsx \
  native_practice/pi_extension/extension_test.ts
```

这个探针使用假的 `ExtensionAPI`，不调用模型，验证扩展注册的事件、工具和保护钩子。

## Pi 显式加载形式

Pi v0.85.1 的原生形式是：

```bash
pi -e /absolute/path/to/learning-stats.ts
```

本项目没有把扩展复制到 `~/.pi/agent/extensions/`，所以不会自动加载未知代码。
