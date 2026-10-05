# Pi 源码导读与 MyAgent 并行学习路线

这份文档用于帮助你一边继续完成当前的 MyAgent，一边阅读 Pi 的源码。目标不是复制 Pi 的全部代码，而是理解一个成熟 Agent 的关键模块如何协作，再把其中适合学习的设计逐步映射到当前项目。

## 1. 先明确阅读对象

Pi 当前官方仓库是 `earendil-works/pi`。旧链接 `badlogic/pi-mono` 会跳转到这个仓库。本导读使用官方仓库 `main` 分支的路径；如果以后要做严格复现，建议先固定一个具体的 commit 或 tag，避免源码更新导致路径和行为变化。

本导读只安排源码阅读，不要求你现在克隆第二个项目，也不要求马上重写现有 MyAgent。

## 2. 阅读源码时只问三个问题

每读一个文件，先不要试图理解全部细节，只回答：

1. 这个模块接收什么输入？
2. 它改变了什么状态，尤其是消息历史、会话状态或文件状态？
3. 它把什么结果交给下一个模块或模型？

如果这三个问题还回答不出来，就先继续追踪调用关系，不要急着记语法。

## 3. Pi 的整体执行链

可以先把 Pi 想成下面这条链：

```text
用户 / CLI
    ↓
AgentSession / Agent
    ↓
模型提供者与模型客户端
    ↓
模型返回普通文本或 tool call
    ↓
工具执行：read / write / edit / bash
    ↓
tool result 回到消息历史
    ↓
下一轮模型调用，或结束本轮任务
```

Pi 的默认工具是 `read`、`write`、`edit` 和 `bash`。它的 `bash` 工具默认是在本机启动 shell，而不是默认使用 Docker；当前 MyAgent 可以把 Docker 作为额外的隔离层来学习和实现。这个区别很重要：Pi 的重点是 Agent 编排和工具抽象，MyAgent 的 L15 重点是先把执行边界和安全隔离设计清楚。

## 4. Pi 源码与 MyAgent 的对照表

| Pi 源码 | 主要职责 | 当前 MyAgent 对应位置 | 学习重点 |
|---|---|---|---|
| `packages/agent/src/agent.ts` | Agent 核心循环、模型调用、工具调用与结果回传 | `agent.py` 的 `run_agent` 及相关循环 | 一轮模型调用如何变成多轮执行 |
| `packages/coding-agent/src/core/agent-session.ts` | 面向编码任务的会话编排和生命周期 | `agent.py`、`main.py`，以及后续会话模块 | 谁负责启动、推进和结束一次任务 |
| `packages/coding-agent/src/core/tools/read.ts` | 读取文件、处理读取参数、返回工具结果 | `tools.py`、`agent_tools/coding_flow.py` | 工具输入约束与结果格式 |
| `packages/coding-agent/src/core/tools/write.ts` | 创建或覆盖文件 | `agent_tools/write.py` | 提案、确认、原子写入和写回校验 |
| `packages/coding-agent/src/core/tools/edit.ts` | 对文件进行精确编辑 | `agent_tools/edit.py` | 唯一匹配、歧义拒绝和冲突处理 |
| `packages/coding-agent/src/core/tools/bash.ts` | 执行 shell 命令并管理超时、输出和终止 | `agent_tools/execution.py`；下一步 L15 | 为什么执行工具必须有边界 |
| `packages/coding-agent/src/core/session-manager.ts` | 创建、读取、切换和管理会话 | 当前的事件/评测记录；后续 L16 `sessions.py` | 运行记录和可恢复会话的区别 |
| `packages/coding-agent/docs/session-format.md` | 说明会话 JSONL 的数据格式 | 当前 `evals/` 的 JSONL 记录；后续 L16 | 一次运行如何持久化为可追踪数据 |
| `packages/coding-agent/src/core/sdk.ts` | 为程序提供可编程的 Agent 入口 | `main.py`、`model_client.py` | CLI 入口和程序化入口如何分层 |

这个表只表示“能力映射”，不表示两个项目的文件结构完全相同。阅读时应先理解职责，再比较实现方式。

## 5. 推荐的阅读顺序

### 第一阶段：先看 Agent 主循环

先读：

- [Pi Agent 核心](https://github.com/earendil-works/pi/blob/main/packages/agent/src/agent.ts)
- [MyAgent 的 `agent.py`](../agent.py)

重点追踪一次最小流程：

```text
用户请求
  → messages
  → 模型响应
  → tool call
  → 工具结果
  → 结果追加回 messages
  → 下一轮模型调用
```

读完后，你应该能回答：

- 为什么工具结果必须带回同一个 `tool_call_id`？
- 为什么一次工具调用完成后，通常还要再次调用模型？
- 什么条件下 Agent 循环可以结束？

这部分对应你已经完成的多轮 Mock 执行、工具调用 ID、工具结果回传和真实模型适配。

### 第二阶段：读四个基础工具

按这个顺序读：

1. [read 工具](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/src/core/tools/read.ts)
2. [write 工具](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/src/core/tools/write.ts)
3. [edit 工具](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/src/core/tools/edit.ts)
4. [bash 工具](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/src/core/tools/bash.ts)

然后分别回看：

- `tools.py`
- `agent_tools/write.py`
- `agent_tools/edit.py`
- `agent_tools/execution.py`

阅读时重点比较四件事：

1. 工具的输入参数如何定义和校验？
2. 工具失败时返回什么结构？
3. 工具是否会直接修改真实文件或执行真实命令？
4. 调用过程中如何处理超时、取消、输出过长和权限边界？

你会看到，当前 MyAgent 的 L11-L13 已经把“提案—确认—执行—验证”拆得更保守。这不是多余步骤，而是为了让学习项目先建立安全的执行边界。

### 第三阶段：专门读 bash 与 L15

先读：

- [Pi bash 工具](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/src/core/tools/bash.ts)
- `agent_tools/execution.py`
- `agent_tools/coding_flow.py`

当前 L13 已经验证了：固定测试用例、固定参数、输出限制、超时、取消和进程组清理。下一步 L15 要学习的是“如何给可执行工具增加隔离配置”，而不是立刻让模型执行任意 shell 命令。

先写下这三个设计问题，再开始改代码：

- 命令允许访问哪些目录？
- 哪些环境变量绝不能传给子进程？
- 超时或取消时，如何保证子进程和子进程的子进程一起结束？

### 第四阶段：读会话和持久化

先读：

- [Agent Session](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/src/core/agent-session.ts)
- [Session Manager](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/src/core/session-manager.ts)
- [Session Format](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/session-format.md)

然后再对照当前的事件记录、评测 JSONL 和未来的 L16。

需要区分：

- 事件记录：回答“这次运行发生了什么”；
- 评测记录：回答“这次任务是否通过”；
- 会话持久化：回答“之后能否恢复同一段对话和上下文”。

它们都可以是 JSONL，但用途不同，不能只因为格式相同就混成一个文件。

### 第五阶段：读 SDK 入口

最后读：

- [Pi SDK](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/src/core/sdk.ts)
- `main.py`
- `model_client.py`

重点理解分层：

- `main.py` 负责接收参数、启动任务和展示结果；
- `agent.py` 负责 Agent 循环；
- `model_client.py` 负责把统一的模型请求转换为具体供应商 API；
- `tools.py` 或 `agent_tools/` 负责工具能力和安全边界。

因此，`main.py` 只调用 `run_agent()` 并不是缺点，而是入口层和业务层分开的表现。

## 6. 每次阅读的具体方法

每次只读一个函数或一个小模块，按下面顺序做：

1. 先看函数签名，圈出输入和返回值。
2. 找出调用它的上层函数。
3. 找出它调用的下层函数。
4. 记录它读写了哪些状态。
5. 找到失败、超时、取消和权限检查的位置。
6. 用三句话写出自己的总结：输入是什么、做了什么、输出给谁。

建议每次控制在 30 到 45 分钟。不要一次读完整个 Pi 仓库，也不要一开始就追踪 UI、扩展系统和所有 Provider 细节。

## 7. 和当前 MyAgent 的并行学习计划

| 顺序 | 当前目标 | 你要做的事情 | 验收方式 |
|---|---|---|---|
| 现在 | 理解 Agent 主循环 | 追踪 `agent.py` 中一轮模型—工具—结果回传 | 能画出一次调用链 |
| L15 | 安全执行隔离 | 先为隔离配置写测试，再设计执行适配器 | 不调用任意主机 shell，测试能证明边界 |
| L16 | 会话持久化 | 对照 Pi 的 session format 设计 append-only JSONL | 退出后能读取并恢复最小会话 |
| 后续 | 扩展和更复杂工具 | 先理解 Pi 的扩展入口，再决定是否加入 MyAgent | 新能力不破坏原有工具协议 |

L15 的学习入口是：先理解 Pi 的 `bash.ts` 如何描述执行边界，再回到当前 `agent_tools/execution.py`，为自己的隔离策略补测试。不要先把 Docker、任意 shell 和真实模型一次性接进主流程。

## 8. 当前项目的下一个小步骤

本轮源码导读完成后，下一小步是：

> 只阅读 Pi 的 `bash.ts`，然后用三句话写出 MyAgent 的 L15 执行器必须满足的三个安全条件。

建议使用下面这个模板记录：

```text
1. 命令或测试只能访问：
2. 传给子进程的环境变量必须：
3. 超时或取消时必须：
```

写完后，再开始 L15 的第一个测试。这样可以确保实现是从约束出发，而不是先写一个能运行但边界不清楚的 shell 工具。

## 9. 官方源码入口

- [Pi Coding Agent README](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/README.md)
- [Agent core](https://github.com/earendil-works/pi/blob/main/packages/agent/src/agent.ts)
- [Agent session](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/src/core/agent-session.ts)
- [Session manager](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/src/core/session-manager.ts)
- [Session format](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/session-format.md)
- [SDK](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/src/core/sdk.ts)
- [Read tool](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/src/core/tools/read.ts)
- [Write tool](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/src/core/tools/write.ts)
- [Edit tool](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/src/core/tools/edit.ts)
- [Bash tool](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/src/core/tools/bash.ts)

## 10. 阅读进度

- [ ] 读完 `agent.ts`，画出一轮 Agent 循环
- [ ] 读完 `read.ts`，说明工具参数和错误结果
- [ ] 读完 `write.ts` 与 `edit.ts`，说明安全确认和唯一匹配
- [ ] 读完 `bash.ts`，写出 L15 的三个安全条件
- [ ] 读完 session manager 和 session format，区分事件记录、评测记录和会话恢复
- [ ] 读完 SDK，说明入口层、Agent 层、模型层和工具层的职责
