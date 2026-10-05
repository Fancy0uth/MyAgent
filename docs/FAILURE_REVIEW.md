# 失败复盘

## 复盘一：L27 Pi 离线构建阻塞

### 输入

在 MyAgent 项目目录旁的 `pi-reference`、tag `v0.85.1` checkout 中执行官方离线构建。

### 可观察轨迹

```text
npm run build:offline
→ packages/ai/src/providers/data/amazon-bedrock.json 不存在

npm run hydrate:model-data
→ Cannot hydrate missing providers: kimi-coding
```

随后运行 `npm test --workspace=@earendil-works/pi-agent-core -- test/agent-loop.test.ts`，在导入阶段得到：

```text
Cannot find package '@earendil-works/pi-ai/utils/uuid'
```

### 诊断

固定 checkout 的模型数据和内部 workspace 构建产物没有准备完整。这个问题发生在 Pi 测试用例执行之前，不是测试断言失败。

### 修改

没有手工伪造模型数据，也没有修改 Pi checkout。只在 `docs/pi-reference/L27_RUN.md` 和兼容性结果中记录为 `blocked` / `not_run`。

### 回归命令

```bash
npm run build:offline
npm test --workspace=@earendil-works/pi-agent-core -- test/agent-loop.test.ts
```

### 结果

本轮仍为 blocked；不能写成 Pi 原生构建或 agent-loop 通过。独立 TypeScript 类型练习和 MyAgent 侧测试不依赖这个结果。

## 复盘二：L26 协议测试的预期失败

### 输入

先运行新建的 `tests/test_rpc.py`，但 `rpc.py` 尚不存在。

### 可观察轨迹

```text
ModuleNotFoundError: No module named 'rpc'
```

### 诊断

测试先于实现运行，说明红灯确实锁定了缺失的协议模块，而不是测试误报。

### 修改

实现严格 UTF-8、单行 LF、JSON 对象和请求状态校验，并增加异步 `RpcServer` 与注入式 `RpcClient`。

### 回归命令

```bash
python -m unittest tests.test_rpc -v
```

### 结果

L26 RPC 测试通过；取消、忙碌请求、事件关联 ID 和 EOF 排空行为均由测试覆盖。
