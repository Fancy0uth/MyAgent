# L26—L30 学习路线完成设计

**日期：** 2026-10-04
**范围：** 继续使用现有 MyAgent；不重建项目，不修改 `ev-ai-assistant`，不把 Python 学习版误称为 Pi 原生实现。

## 目标

在现有 L25 文本 REPL、Agent 循环、事件、会话、扩展和评测基础上，完成：

1. L26：一个可被其他程序调用的、stdout 仅输出 JSONL 的本地 RPC 接口；
2. L27：必要的 TypeScript 类型练习，以及固定 Pi 参考版本的一个 Mock 局部测试；
3. L28：一个由固定版本 Pi 显式加载的原生 TypeScript 扩展；
4. L29：能力差分矩阵和确定性对照任务；
5. L30：可重复安装、测试、评测、失败复盘和限制声明。

## 非目标与边界

- L26 的协议是学习版自定义协议，不声称兼容 Pi 原生 RPC。
- L27/L28 使用独立的固定版本 Pi checkout；不修改 Pi 内核，不把它自动导入 MyAgent 的 Python 扩展目录。
- L28 的扩展只统计工具事件、提供一个纯函数只读工具，并阻止一个明确的危险写入；它不提供 Shell、任意代码执行或 OS 沙箱。
- L29 不比较模型自然语言逐字一致，只比较固定输入下的结构、状态、错误、事件和文件变化。
- L30 的真实 DeepSeek 调用只有在固定任务确实需要时才做最小次数；普通回归继续使用 Mock。
- 不自动提交、推送、部署或接入 `ev-ai-assistant`。

## 总体结构

```text
CLI / TUI / RPC
       │
       ▼
  run_agent()  ── events ──► UI / RPC event messages
       │
       ├── tools.py / agent_tools/
       ├── sessions.py / context.py
       └── model_client.py

MyAgent deterministic contracts ◄──► compatibility runner
                                      │
                                      └── fixed Pi reference / native extension
```

RPC、CLI 和 UI 都只做输入输出适配，不复制 Agent 循环。Pi 原生扩展练习与 Python 扩展保持物理目录和加载入口分离。

## L26：JSONL / RPC

### 请求协议

每条请求必须是 UTF-8、以 LF 结尾的一个 JSON 对象：

```json
{"id":"req-1","type":"prompt","payload":{"question":"读取 README"}}
```

初始命令只有：

- `prompt`：`payload.question` 是非空字符串；
- `get_state`：`payload` 可为空对象；
- `abort`：`payload.target_id` 可选，默认取消当前运行。

协议层拒绝缺少 `id`、重复 ID、未知命令、损坏 JSON、错误 UTF-8、空问题和忙碌时的第二个 `prompt`。

### 响应协议

每条响应也是一行 JSON，并带 `request_id`：

```json
{"request_id":"req-1","type":"accepted","payload":{"run_id":"run-1"}}
{"request_id":"req-1","type":"event","payload":{"event":"tool_started", "tool_name":"read_file"}}
{"request_id":"req-1","type":"result","payload":{"status":"completed", "answer":"..."}}
```

协议错误使用 `type=error`，`payload` 至少包含稳定的 `error` 和可读的 `message`。stdout 不输出调试文本；测试/调试日志只能进入 stderr 或注入的 logger。

### 并发与取消

`prompt` 在独立工作线程中运行，先返回 `accepted`；主输入循环继续读取后续 JSON，因此可以接收 `abort`。所有输出通过单一锁保护的写入器串行化，禁止多线程交错 JSON。`abort` 设置现有 `cancel_event`，沿用 Agent 的协作式取消语义，不声称能中断已经发出的远程 HTTP 请求。

### L26 接口

- `decode_request(line: bytes) -> dict`
- `encode_message(message: dict) -> bytes`
- `RpcServer.handle_request(request: dict) -> list[dict]`
- `RpcServer.serve(input_stream, output_stream) -> None`
- `RpcClient.request(message: dict) -> list[dict]`（用于测试和示例，不复制 Agent 循环）

实现可以把同步单步处理和线程式 `serve()` 分开，但两者必须共享协议校验和响应格式。

## L27：固定 Pi 参考与 TypeScript 局部测试

- 固定参考版本：Pi `v0.85.1`，目录放在 MyAgent 外部的独立 checkout，例如项目目录旁的 `pi-reference`；该目录不成为 MyAgent 的嵌套 Git 仓库。
- 在 MyAgent 的 `native_practice/typescript/agent_protocol.ts` 写一页类型练习，覆盖 `type/interface`、联合类型、函数回调、`Promise`、`AbortSignal` 和异步迭代，不重写 Python Agent。
- 参考 checkout 只按其锁文件安装依赖；先检查 Node/npm 版本、HEAD、工作区状态，再执行 `npm ci --ignore-scripts`、版本要求允许时的离线构建和一个明确使用测试替身的 Agent 局部测试。
- 真实模型测试不属于 L27 必需验收；失败时记录具体环境阻塞，不把安装成功等同于原版测试通过。

## L28：Pi 原生扩展

- 在 MyAgent 的 `native_practice/pi_extension/extensions/learning-stats.ts` 保存扩展源码和说明，不修改参考 checkout 内核。
- 扩展 API、导出工厂函数和事件名称必须以固定版本 `docs/extensions.md` 与类型定义为准；不照搬旧博客 API。
- 第一项能力统计工具调用次数；第二项是输入校验后的纯函数只读工具；第三项是通过原生 Hook 阻止明确的测试文件写入。
- 运行时必须使用显式 `-e` 加载该文件，关闭未知扩展自动发现；保留加载命令、Node/Pi 版本和验证结果。
- 若当前环境缺少 Pi checkout、Node 依赖或可运行脚本，保留源码和失败诊断，但不能写“原生扩展已加载”。

## L29：兼容性矩阵

新增 `docs/COMPATIBILITY.md` 和 `evals/compatibility_tasks.jsonl`。每项能力记录：

- `not_run`：没有执行；
- `subset_verified`：只验证了明确子集；
- `fixed_contract_compatible`：固定合同下结构一致；
- `not_applicable`：该能力不在学习版范围。

首批对照合同覆盖：工具参数/错误、读取范围、事件时序、取消状态、会话恢复、分支上下文、压缩边界、资源加载、扩展拦截和 RPC JSONL。每条结果记录输入夹具版本、实现版本、输出摘要、差异和验证命令。原版与学习版使用独立临时工作区和会话目录。

## L30：交付与复盘

补齐或更新：

- `README.md`：安装、Mock 默认模式、真实 API 启用、交互/RPC 入口、能力和限制；
- `docs/SOURCE_MAP.md`：Pi 固定版本源码位置与 MyAgent 对应模块；
- `docs/COMPATIBILITY.md`：矩阵与证据；
- `docs/FAILURE_REVIEW.md`：一个真实或确定性失败案例的输入、轨迹、定位、修复和回归；
- `evals/`：Mock 与真实结果分开保存，真实结果注明模型、代码版本、任务夹具和调用次数；
- `PROGRESS.md`：分别记录 L26—L30 的代码事实、验证证据、外部环境阻塞和未支持能力。

交付验收必须包含：干净环境下的 Mock 启动说明、快速测试命令、RPC 协议样例、Pi 原生测试/扩展的实际或阻塞证据、兼容性矩阵和限制声明。

## 测试策略

每个课程先写失败测试，再实现最小代码，再运行受影响测试和全量回归。Python 侧使用现有 `unittest`；TypeScript 侧使用固定 Pi checkout 自带脚本和明确的测试替身。真实 DeepSeek 只作为独立、最小范围的补充证据，不能替代协议、状态和安全边界测试。
