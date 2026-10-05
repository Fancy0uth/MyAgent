# L28 Pi 原生扩展运行记录

## 固定 API 依据

- Pi checkout：MyAgent 项目目录旁的 `pi-reference`
- 固定版本：`v0.85.1`
- 文档：`packages/coding-agent/docs/extensions.md`
- 类型：`packages/coding-agent/src/core/extensions/types.ts`
- 使用的 API：`pi.on("agent_start")`、`pi.on("tool_execution_end")`、`pi.on("tool_call")`、`pi.registerTool()`

## 已验证

```text
<PI_CHECKOUT>/node_modules/.bin/tsx \
  native_practice/pi_extension/extension_test.ts
```

结果：PASS，输出 `native extension probe passed`。

探针确认：

1. 扩展注册了两个统计事件和一个写入保护钩子；
2. `learning_read` 可以读取当前目录内的指定行；
3. `../outside.txt` 返回 `PATH_NOT_ALLOWED`；
4. `tests/result.txt` 被 `tool_call` 返回 `{ block: true, reason: ... }`。

这是一项不调用模型的本地行为验证，不等同于 Pi CLI 已经加载扩展。

## 原生加载状态

```text
pi -e /absolute/path/to/learning-stats.ts
```

结果：BLOCKED。固定 checkout 的 `npm run build:offline` 因缺少 `kimi-coding` 模型数据阻塞，`node_modules/.bin/pi` 尚未生成，因此当前环境无法运行 Pi CLI 的显式加载命令。

边界：L28 的扩展源码和安全行为已通过独立探针；Pi 原生加载仍需先修复 L27 记录的官方 workspace 构建链，再复测。
