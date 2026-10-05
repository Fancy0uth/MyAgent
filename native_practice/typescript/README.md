# L27 TypeScript 类型练习

`agent_protocol.ts` 只把已经在 Python 中理解过的 Agent 请求、工具调用、事件和取消信号写成 TypeScript 类型。

它不是第二个 Agent，也不连接模型。验证方式：

```bash
cd /path/to/pi-reference
./node_modules/.bin/tsc \
  --noEmit --strict --target ES2022 --module NodeNext \
  --moduleResolution NodeNext /path/to/MyAgent/native_practice/typescript/agent_protocol.ts
```

重点观察：联合类型限制 `event` 字段，`Promise` 表示异步结果，回调传递事件，`AbortSignal` 表示取消信号，`AsyncIterable` 配合 `for await...of` 逐个消费事件。
