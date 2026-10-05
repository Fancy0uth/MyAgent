# repo-reader-agent 学习进度

> 接续检查记录：2026-09-17。这里区分用户自述、代码事实和本轮验证，不把初始化模板当作当前进度。当前工作树的教程是 `mds/LEARNING_GUIDE.md`，本文件是唯一动态记录。

## 2026-10-04：L26—L30 收尾实现与最终验收记录

### 学习起点与本轮范围

- 用户明确要求：保留 L25 以前的成果，直接完成剩余课程；本轮按已批准的 L26—L30 设计和计划执行，不重建项目、不重做入门课。
- 用户此前已自述旧路线推进到第七关；这属于学习经历记录。下面的 PASS/BLOCKED 只表示本轮代码或命令证据，不反推用户是否学过。
- 本轮没有调用真实 DeepSeek API，没有访问 `ev-ai-assistant`，没有修改外部 Pi checkout 内核，也没有提交或推送 Git。

### L26：自定义 JSONL/RPC

- 新增 `rpc.py`：严格 UTF-8、必须一个终止 LF、禁止多行原始输入、JSON 顶层必须是对象；只接受 `prompt`、`get_state`、`abort`，校验请求 ID、忙碌状态和参数。
- 新增 `rpc_client.py`：只负责编码请求、调用注入的 server、解码响应，不直接调用 `run_agent()` 或工具。
- `RpcServer` 使用一个活动 worker、一个取消 `Event` 和一条受锁保护的输出队列；`accepted`、事件、最终 `result` 都保留原 `request_id`。
- TDD 证据：先得到 `ModuleNotFoundError: No module named 'rpc'`；实现后 `python -m unittest tests.test_rpc -v` 实际运行 10 个测试并全部通过。
- RPC pipe 冒烟实际输出 `accepted → event* → result`；`get_state` 也实际返回 `idle` JSON。

### L27：固定 Pi v0.85.1 对照环境与 TypeScript 契约

- 在 MyAgent 项目目录旁创建固定 checkout：`../pi-reference`，来源为 `https://github.com/earendil-works/pi.git`，tag `v0.85.1`，commit `d981de1229ef899957bbe968bc8dcda02a21f477`。
- `npm ci --ignore-scripts`：PASS；Node `v24.14.0`、npm `11.9.0`。安装报告的审计漏洞未通过 `npm audit fix` 改写固定依赖。
- 新增 `native_practice/typescript/agent_protocol.ts` 和 README；固定 AbortSignal、请求、工具调用、事件类型。严格 TypeScript 类型检查：PASS。
- `npm run build:offline`：BLOCKED。缺少 `amazon-bedrock.json`；随后按官方提示执行 `npm run hydrate:model-data`，又因 `kimi-coding` provider 数据缺失失败。
- `npm test --workspace=@earendil-works/pi-agent-core -- test/agent-loop.test.ts`：BLOCKED，导入阶段缺少 `@earendil-works/pi-ai/utils/uuid`，没有进入测试用例。
- 以上阻塞详见 `docs/pi-reference/L27_RUN.md`；没有手工伪造模型数据，也没有将 Pi 原生测试写成通过。

### L28：显式加载的原生扩展练习

- 新增 `native_practice/pi_extension/extensions/learning-stats.ts`：统计 `agent_start`/`tool_execution_end`，注册限定当前工作目录、最多 80 行的只读 `learning_read`，并在 `tool_call` 阶段阻止 `tests/`、`.env`、`.git/` 写入。
- 没有复制到 `~/.pi/agent/extensions/`，必须用 `pi -e` 显式加载；扩展不提供 shell、网络或任意写文件能力。
- 新增独立行为探针 `native_practice/pi_extension/extension_test.ts`。探针实际 PASS：路径越界返回 `PATH_NOT_ALLOWED`，测试目录写入返回固定 block 结果。
- Pi CLI 原生显式加载：BLOCKED，因为 L27 构建未生成 `node_modules/.bin/pi`；这不影响扩展独立探针的 PASS。

### L29：确定性能力兼容性矩阵

- 新增 `evals/compatibility_tasks.jsonl`、`evals/run_compatibility.py`、`tests/test_compatibility.py` 和 `docs/COMPATIBILITY.md`。
- 结果只比较 JSONL、事件顺序、取消和错误边界，不比较模型自由文本；状态固定为 `not_run`、`subset_verified`、`fixed_contract_compatible`、`not_applicable`。
- 结果文件采用追加写入，不覆盖历史：`docs/compatibility_results/myagent.jsonl`、`docs/compatibility_results/pi.jsonl`。
- 首轮 MyAgent 结果：1 项 `fixed_contract_compatible`、3 项 `subset_verified`、2 项 `not_run`、2 项 `not_applicable`。
- 首轮 Pi 结果：1 项 `not_applicable`、7 项 `not_run`；这是环境证据，不是 Pi 通过。
- `python -m unittest tests.test_compatibility -v`：4 个测试 PASS；两个 runner 命令均实际运行。

### L30：交付、失败复盘和学习地图

- 新增当前交付版根目录 `README.md`（原工作树中的旧 README 已在本轮前处于删除状态；此次按 L30 计划补回并更新），涵盖 Mock、`--real`、`--interactive`、`--session`、RPC、答案评测和兼容性评测。
- 新增 `docs/SOURCE_MAP.md`：标注 MyAgent、固定 Pi checkout、能力状态和阻塞边界。
- 新增 `docs/FAILURE_REVIEW.md`：记录 Pi 构建阻塞和 L26 测试先红后绿的输入、轨迹、诊断、修改、回归命令和结果。
- 更新 `evals/README.md`，区分答案评测与确定性矩阵。
- `python -m unittest tests.test_delivery_docs -v`：3 个测试 PASS；文档没有密钥值，也没有把 Pi 阻塞写成通过。

### 本轮最终验证

- `python -m unittest discover -s tests -p 'test_*.py' -v`：实际运行 182 个测试，全部 PASS，退出码 0。
- `python -m py_compile rpc.py rpc_client.py ui.py main.py evals/run_compatibility.py`：PASS。
- `git diff --check`：PASS。
- `printf '这个项目叫什么名字？\\n' | python main.py`：Mock 模式完成，3 次模型请求、2 次工具执行，事件摘要和工具调用 ID 正常显示。
- `printf '/exit\\n' | python main.py --interactive`：交互入口正常退出。
- RPC prompt pipe：实际输出 `accepted`、多条带原 `request_id` 的事件和 `completed` result。
- 本轮没有真实模型验证；用户此前提供的真实调用证据继续保留为历史证据，不与本轮 Mock/离线 PASS 混写。

### 当前结论与下一步

- L26、L28（独立扩展探针）、L29、L30 的 MyAgent 侧代码和离线证据已完成；L27 的固定 Pi 环境记录、TypeScript 练习已完成，但 Pi 原生构建、原生 agent-loop 和 CLI 扩展加载仍为 BLOCKED。
- L29 中 `session_branch`、`compaction_boundary` 在本次最小矩阵中是 `not_run`，但它们已有既存的 MyAgent 独立单元测试；不能把 runner 的 `not_run` 改写成兼容性通过。
- 当前项目已经形成闭环：入口 → Agent loop → 工具/策略 → 事件/取消/会话 → RPC/UI → 评测/兼容性 → 文档证据。
- 下一步不再批量写代码；先进行用户验收和逐文件导读。若继续 Pi 复现，则只需处理 `pi-reference` 的官方模型数据与 workspace 构建链，并重新运行 L27/L28 原生验证。

## 2026-10-04：L25 终端交互与 TUI 最小边界

- 用户批准 bounded 设计；本轮沿用现有 Agent，不引入第三方终端库，不调用真实模型，不访问 `ev-ai-assistant`。
- 新增 `ui.py` 的 `TerminalUIState`：只消费 Agent 事件并保存界面状态，不直接调用工具、文件或模型。它能显示运行状态、最近工具、完成/拦截数量、流式文本、最终答案和错误列表。
- 新增 `parse_input_line()` 和 `run_repl()`：支持普通问题以及 `/new`、`/compact`、`/resume`、`/exit`。REPL 通过依赖注入调用 `run_agent()`，没有复制第二套 Agent 循环。
- `/compact` 只为下一次问题开启已有的 `use_compaction=True`；`/resume` 只有传入 `SessionStore` 才执行已有的 `recover_agent()` 检查，不会伪装成自动恢复模型运行；`/new` 只重置屏幕状态，不删除磁盘会话。
- `main.py` 增加 `--interactive` 和可选 `--session`；默认单问题入口保持不变。交互模式仍按 `--real` 才会创建真实 DeepSeek 客户端。
- 新增 `tests/test_ui_state.py`，覆盖状态事件、工具拦截/错误保留、中文多行文本、命令解析、压缩开关、退出和无会话恢复边界。
- TDD 证据：先运行 `python -m unittest tests.test_ui_state -v`，在 `ui.py` 不存在时按预期导入失败；实现后首次运行暴露“最终错误覆盖工具拦截错误”，补充错误列表后定向 5 个测试全部通过。
- 本轮集成验证：`tests.test_ui_state`、`tests.test_main`、`tests.test_agent_events`、`tests.test_queue` 实际运行 20 个测试，全部通过；Mock 交互冒烟也完成。
- 本轮全量验证：`python -m unittest discover -s tests -p 'test_*.py'` 实际运行 165 个测试，全部通过，退出码 0；未调用真实模型。Docker 镜像测试本轮也通过。
- 静态检查：`python -m py_compile ui.py main.py agent.py events.py tests/test_ui_state.py` 和 `git diff --check` 均通过。
- L25 边界：当前是简单、可测试的文本 REPL，不是 `pi-tui` 的差分渲染引擎；没有窗口尺寸布局、光标编辑、实时异步取消或第三方 UI 依赖。原有核心去掉 UI 后仍可独立测试。
- 授权变化：用户授权在本项目确实需要验证真实 Provider 行为时调用 `.env` 中的 DeepSeek API；调用前仍采用最小请求范围，不在普通离线回归中自动使用，并单独记录真实调用证据与 Mock 证据。
- 下一小步：进入 L26，定义只输出 JSONL 的 RPC 接口，让其他程序通过协议调用同一个 Agent 核心。

## 2026-10-04：L24 事件 Hooks 与可插拔扩展

- 用户批准本课的 bounded 设计；本轮沿用现有 Agent，不新建项目，不安装依赖，不调用真实模型，不访问 `ev-ai-assistant`。
- 新增 `extensions.py` 的 `ExtensionManager`：只接受调用方显式注册的事件监听器、`before_tool` 检查器和纯只读工具，不根据文件存在自动导入或执行扩展。
- `events.py` 增加同步 `subscribe()`；事件先写入记录，再调用监听器。监听器异常不被事件层吞掉，Agent 会在真正分发工具前将其转为 `EXTENSION_ERROR`。
- `agent.py` 的 `run_agent()` 增加可选 `extension_manager`。工具调用先经过 `before_tool`：允许时才发出 `tool_started` 并分发；拒绝时回传带 `tool_call_id` 的结构化失败、记录 `tool_blocked`，不调用底层 dispatcher，也不增加工具执行次数。
- `tools.py` 的 `dispatch_tool()` 增加可选 `extra_tools`，因此扩展工具复用现有 JSON 校验和结果格式，不复制另一套分发器。
- `ExtensionManager.register_read_only_tool()` 要求显式提供 OpenAI/DeepSeek 风格的 function definition，拒绝与基础工具重名；`close()` 会撤销本管理器追加到模型工具定义中的条目。
- `policy.py` 增加测试文件保护判断：`tests/` 路径和 `test_*.py` / `*_test.py` 文件不能作为可编辑目标；`protect_validation_files()` 只是前置提醒，底层 policy 仍是最终边界。
- 新增 `tests/test_extensions.py`，覆盖事件顺序、回调异常、工具拦截、检查器异常、显式只读工具、重名和测试文件保护；扩展 `tests/test_policy.py` 覆盖测试文件不可编辑。
- TDD 证据：先运行定向测试确认 `extensions.py` 缺失；实现后发现并修复 `dict_values` 深拷贝问题和测试断言问题；随后定向测试实际运行 14 个，全部通过。
- 本轮非 Docker 全量回归：实际运行 158 个测试，全部通过，退出码 0；使用 Mock、假 Provider、临时目录，未调用真实模型。
- 本轮完整发现：实际运行 159 个测试，Docker 镜像类因 Docker Desktop 构建基础镜像时返回 `unable to lease content: lease does not exist` 产生 1 个环境错误；这不是 L24 测试失败，Docker 环境问题待单独重试。
- 静态检查：`python -m py_compile agent.py events.py extensions.py policy.py tools.py tests/test_extensions.py tests/test_policy.py` 和 `git diff --check` 均通过。
- L24 边界：扩展是进程内、显式、受信任 Python 回调，不是 OS 沙箱；只读工具注册约束工具契约，但不自动赋予文件或 Shell 权限；全局模型工具定义在管理器生命周期内临时追加，调用方应在结束时 `close()`。
- 下一小步：进入 L25，先做终端交互层与 TUI 的最小可测试边界；复用 L22 的输入队列和 L09 的事件，不把 UI 逻辑塞回工具函数。

## 2026-10-03：L23 Skills 与 Prompt Templates

- 用户确认本课 bounded 设计；本轮沿用现有 Agent，不新建项目，不调用真实模型，不访问 `ev-ai-assistant`，不安装依赖。
- 新增 `resources.py` 的 `ResourceCatalog`：从固定的 `resources/skills/` 和 `prompts/` 目录读取文本资源，不 import Python 文件、不执行 Shell。
- Skill 使用固定三项元数据：`name`、`purpose`、`allowed_paths`。`list_skills()` 只返回名称、用途、允许路径和来源；`load_skill(name)` 必须显式调用后才返回全文和 `context_instruction`。
- Skill 加载会检查安全资源路径、重复名称、UTF-8 编码、内容长度和明显越权指令；返回的上下文指令固定为 `trusted=False`，不会增加工具或权限。
- 新增 `resources/skills/code-review/SKILL.md`，只包含代码审查步骤和关注点；新增 `prompts/code_review.txt`，只使用 `{{file_path}}`、`{{focus}}` 固定占位符。
- Prompt Template 只做声明变量替换，缺少变量、多余变量、非字符串变量、未知模板和路径穿越都会返回结构化错误；模板内容不会被当作 Python 或 Shell 执行。
- 新增 `tests/test_resources.py`，覆盖元数据/全文分离、显式加载、上下文边界、重复名称、未知/穿越路径、超长内容、越权指令、模板变量和未知 Python 文件。
- TDD 证据：先运行 `python -m unittest tests.test_resources -v`，在 `resources.py` 不存在时按预期得到 `ModuleNotFoundError`；实现后定向运行 10 个测试全部通过。
- 本轮全量验证：`python -m unittest discover -s tests -p 'test_*.py'` 实际运行 152 个测试，全部通过，退出码 0；使用 Mock、假 Provider、临时目录和 Docker 测试，未调用真实模型。
- L23 边界：当前是固定资源目录和自定义文本格式，不是 Pi 的全部 Skill 发现优先级、第三方包加载或完整提示注入防御；`main.py` 默认入口仍不自动加载 Skill，调用方必须显式使用 `ResourceCatalog` 并把结果放入 `context_instructions`。
- 下一小步：进入 L24，给工具执行和事件增加可插拔扩展点；扩展不能绕过现有 policy，也不能通过文件存在而自动执行不可信代码。

## 2026-09-29：L22 Steering 与 Follow-up 队列

- 用户批准本课的 bounded 设计；本轮沿用现有 Agent，不新建项目，不调用真实 DeepSeek，不访问 `ev-ai-assistant`，不开放新的写入工具。
- 新增 `input_queue.py` 的 `InstructionQueue`：只接受 `steering` 和 `follow_up`，记录唯一 ID，按 FIFO 取出，并保留未消费消息；队列使用锁保护，允许测试模拟工具运行期间的输入。
- `agent.py` 的 `run_agent()` 新增可选 `instruction_queue`。Steering 只在完整工具批次结束后进入下一次模型上下文；Follow-up 只在当前模型给出最终回答后进入下一轮。每次最多消费一条，Steering 优先于 Follow-up。
- 已消费指令会转换为普通 `user` 消息并进入模型历史与可选 SessionStore；取消或预算耗尽时，剩余消息通过结果中的 `pending_instructions` 返回，不自动重放已经执行的工具。
- 本课暂未做复杂键盘 UI；队列是独立适配器，调用方可以在工具执行期间从另一个线程提交消息。`main.py` 默认入口仍保持原来的单次问题交互。
- TDD 证据：先运行 `python -m unittest tests.test_queue -v`，在 `input_queue.py` 不存在时按预期得到 `ModuleNotFoundError`；补入队列后，测试又因 `run_agent()` 尚未接受 `instruction_queue` 而失败；接入循环后转绿。
- 本轮定向验证：`python -m unittest tests.test_queue -v` 实际运行 6 个测试，全部通过。
- 本轮全量验证：`python -m unittest discover -s tests -p 'test_*.py'` 实际运行 142 个测试，全部通过，退出码 0；包含 Mock、假 Provider、Docker 和临时目录测试，未调用真实模型。
- 静态检查：本轮随后执行 `python -m py_compile agent.py input_queue.py tests/test_queue.py` 和 `git diff --check`；若均无输出则视为通过。
- L22 边界：队列目前是进程内内存状态，不会跨进程/重启持久化；没有实时 TUI、并行工具执行和远程请求强制中断。持久会话只保存已经消费的指令消息，未消费消息由本次结果明确交回调用方。
- 下一小步：进入 L23，先做 Skills/Prompt Templates 的最小资源加载器；重点区分“模型提示资料”“可执行工具”和“模型客户端”。

## 2026-09-28：L21 流式输出与安全工具参数组装

- 用户批准 L21 的 bounded 设计；本轮继续使用现有 Agent，不调用真实 DeepSeek 网络，不修改真实文件，不访问 `ev-ai-assistant`。
- 新增 `streaming.py` 的 `StreamAssembler` 和 `consume_stream()`：文本片段可以逐步回调；工具参数按 `call_id` 分别累积；只有收到正常 `finish` 且 JSON 参数完整后，才返回 Agent 可执行的工具调用。
- 流协议边界：缺少 `start`、半截 JSON、工具调用名称变化、重复结束、未知结束原因和 `finish_reason=length` 都返回结构化 `StreamProtocolError`；即使截断后的 JSON 恰好能解析，也不会执行。
- `model_client.py`：`MockModelClient.stream()` 将既有 Mock 响应拆成流事件；`DeepSeekModelClient.stream()` 将 OpenAI 兼容 SDK 的假/真实 chunk 归一化为内部事件，并保留 `call_id`、工具参数片段和结束原因。真实网络本轮没有开启。
- `agent.py` 的 `run_agent()` 增加 `use_streaming=False` 和 `on_text_delta`。默认仍走原来的完整响应接口；显式开启后，文本通过回调和事件逐片输出，工具仍沿用原有校验与分发流程，且流式取消发生在 finish 前时不会执行工具。
- `events.py` 增加 `model_stream_started`、`model_text_delta`、`model_stream_finished` 三类事件；原有非流式事件序列保持兼容。
- 新增 `tests/test_streaming.py`，覆盖正常文本流、两个工具调用交错、半截 JSON、长度截断、重复结束、消费取消、Agent 取消防止工具执行、Mock 多轮流和 DeepSeek 假 SDK 流。
- TDD 证据：测试先因 `streaming.py` 不存在而导入失败；补入组装器后流式 Agent 集成测试再验证 `run_agent()` 的新参数和事件路径；最后全量回归转绿。
- 本轮定向验证：`python -m unittest -v tests.test_streaming` 实际运行 9 个测试，全部通过。
- 本轮全量验证：`python -m unittest discover -s tests -v` 实际运行 136 个测试，全部通过，退出码 0；包含 Docker、L19/L20、Mock 和假 Provider 测试。
- 静态检查：`python -m py_compile streaming.py model_client.py agent.py events.py tests/test_streaming.py` 与 `git diff --check` 均通过。
- L21 边界：当前是同步生成器式流处理，不是完整异步 UI；`main.py` 默认仍使用非流式入口；真实 DeepSeek 流式网络行为尚未验证。
- 学习者理解状态：代码和离线证据已完成；仍待复述“为什么看到完整 finish 之前不能执行工具”。
- 下一小步：进入 L22，学习 Steering 与 Follow-up 队列，区分运行中的新指令、自然结束后的新任务和取消。

## 2026-09-28：L20 固定摘要与手动上下文压缩

- 用户批准 L20 的 bounded 设计；本轮继续沿用现有 Agent，不删除原始会话，不调用真实模型，不访问 `ev-ai-assistant`。
- `context.py` 新增 `build_compacted_context()`：将较早的完整消息组交给固定摘要器处理，只为模型请求生成“摘要 + 最近完整消息组”。返回值增加 `compaction` 元数据，记录摘要覆盖的 entry ID、最近保留的 entry ID 和是否实际压缩。
- 固定摘要字段为 `goal`、`constraints`、`completed_facts`、`unfinished_items`、`failures`、`key_files`、`next_step`。摘要只从用户消息和可解析的工具结果提取，成功事实保留工具调用 ID，失败保留错误码和消息，不让摘要器把未验证内容写成已完成事实。
- 压缩前会检查所有消息组：未完成的工具调用组返回 `COMPACTION_UNSAFE`；工具结果超过限制返回 `COMPACTION_GROUP_TOO_LARGE`；最近消息组不能放入预算时返回明确错误，不拆分半条协议。
- `agent.py` 的 `run_agent()` 新增 `use_compaction=False` 和 `compaction_keep_recent_groups=2`。默认仍使用 L19 的 `build_context()`；只有显式开启压缩时才使用固定摘要器。完整 `history` 和 SessionStore 原始记录不变。
- 新增 `tests/test_compaction.py`，覆盖固定字段、当前任务保留、工具调用/结果成组、未完成组、超大结果、重复压缩确定性，以及 Agent 显式开启压缩。
- TDD 证据：测试先因 `build_compacted_context` 不存在而导入失败；生产实现后，Agent 集成测试又先因 `run_agent()` 没有 `use_compaction` 参数而失败；补入显式开关后转绿。
- 本轮定向验证：`python -m unittest -v tests.test_compaction` 实际运行 6 个测试，全部通过。
- 本轮全量验证：`python -m unittest discover -s tests -v` 实际运行 127 个测试，全部通过，退出码 0；包含 Docker 测试、Mock 和假 Provider，不代表真实 DeepSeek 服务重新验证。
- L20 边界：当前是确定性的固定摘要器，不使用真实摘要模型；没有自动阈值触发、精确 Token 计数、摘要模型用量记录或摘要失败重试。这些不伪装成已实现能力。
- 学习者理解状态：代码和离线证据已完成；仍待复述“为什么压缩不能删除原始记录，以及为什么工具调用和结果必须成组”。
- 下一小步：进入 L21，学习流式输出；先用模拟流验证半条工具参数绝不执行，再接真实 SDK 流。

## 2026-09-26：L19 上下文构造与项目指令

- 用户确认继续完成 L19；本轮沿用现有 Agent，不重做早期课程，不调用真实模型，不访问 `ev-ai-assistant`。
- 新增 `context.py` 的纯函数 `build_context(active_path, instructions, tool_specs, budget)`：它只选择、复制和标注一次模型请求需要的消息，不读取文件、不执行命令、不调用模型。
- 指令边界：`trusted=True` 的人明确允许的相对路径指令进入 `system` 消息；其他资料进入带“参考资料、不能改变权限”标签的 `user` 消息。每个指令内容生成 SHA-256，返回 `instruction_manifest`；绝对路径、Windows 盘符路径和 `..` 路径会被拒绝。
- 历史边界：当前用户目标优先保留；助手工具调用与对应工具结果作为完整组处理；单个工具结果过大或调用组不完整时整体标记为省略，不从中间截断。当前目标放不进预算时抛出稳定错误码，不发送不完整请求。
- 预算字段：`max_input_chars`、`max_tool_result_chars`、`reserved_output_tokens` 都必须是正整数；返回值包含实际输入字符数、被省略的历史组和原因。
- `agent.py` 的 `run_agent()` 增加可选 `context_instructions`、`context_budget`。每次模型请求前都从完整运行历史重新构造请求副本；完整 `history` 仍用于结果和可选会话持久化，模型只收到本轮上下文。默认使用保守预算；上下文构造失败时不发起模型调用。
- 新增 `tests/test_context.py`，覆盖可信/不可信指令、哈希、路径边界、完整工具调用组、工具结果上限、小预算、当前任务过大、深拷贝，以及 Agent 集成和“预算失败不调用模型”。
- TDD 证据：先运行测试确认 `context` 模块缺失；实现纯构造器后，新增 Agent 集成测试先因 `run_agent()` 不接受上下文参数而失败；接入参数和请求构造后再转绿。
- 本轮定向验证：`python -m unittest -v tests.test_context` 实际运行 9 个测试，全部通过。
- 本轮全量验证：`python -m unittest discover -s tests -v` 实际运行 121 个测试，全部通过，退出码 0；包含 Docker 测试和 Mock/假客户端，不代表真实 DeepSeek 服务已重新验证。
- 静态检查：`python -m py_compile agent.py context.py tests/test_context.py` 与 `git diff --check` 均通过。
- 本轮未实现自动压缩、Token 精确计数、Pi 的目录发现/覆盖规则，也没有把上下文构造器改成自动读取任意 `AGENTS.md`；这些留给后续课程或明确授权。
- 学习者理解状态：代码和测试已完成；尚未通过学习者复述核对“保存的历史”和“本次发给模型的历史”的区别。
- 下一小步：进入 L20，先用固定摘要器做“手动压缩”的可追溯版本；保留原始会话，不让摘要冒充无损记忆。

## 2026-09-24：L18 会话树、分支与活动路径

- 用户请求：完成 L18 并写代码导读；本轮按仓库现有设计与计划实现，不调用真实模型、不改工作区文件、不提交。
- `sessions.py` 把 JSONL `parent_id` 从线性相邻关系扩展为可验证的父图；保留 `schema_version=1` 和原记录格式。加载会拒绝孤儿父节点、循环、晚于子节点的父记录和多根记录。旧线性会话仍兼容。
- `SessionStore.append(parent_id=...)` 可以从已有历史节点建立兄弟分支；新增 `branch_selection` 用追加记录保存导航位置。`get_active_path()`、`get_model_history()`、`select_active_node()` 和 `render_tree()` 分别提供活动路径、聊天历史、节点选择和文本树。返回的路径/模型消息是副本。
- `run_agent(session_store=...)` 在记录新运行前取活动分支聊天历史，再附上本轮问题；Provider 看不到兄弟分支或导航/运行元数据。不传 SessionStore 时仍使用原单轮起始历史。
- `run_agent()` 会先检查活动路径是否有未匹配结果的工具调用；有则返回 `recovery_required` 和调用 ID，不发模型请求或追加新记录。新 `run_context` 记录本次请求实际使用的聊天消息 ID。
- `recover_agent()` 的运行和消息分析只看活动路径，并用 `history_entry_ids` 还原中断运行请求实际使用的祖先上下文；当前运行状态、最终回答和待执行调用仍按当前运行切分。若历史分支选择之后尚无新 `run_context`，不会误把已完成的祖先运行作为待恢复运行。写入操作核对仍读取完整 JSONL，切换聊天分支不会隐藏其他分支的待核对副作用。
- 最终代码审查补出的恢复边界：选中含未完成工具调用的节点时，恢复结果列出调用 ID 且不自动重试；`run_agent()` 对单个悬空调用和多调用批次的部分结果都要求先恢复。审查发现的两项重要问题均以测试先红后绿修复。
- 新增 `tests/test_branches.py`，覆盖兄弟分支、孤儿与循环父图、活动节点重开、非法节点、工具协议字段、文本树、Agent 上下文隔离、未完成工具调用拦截和中断后上下文恢复；扩展 `tests/test_recovery.py` 覆盖导航恢复和跨分支写入审计；`tests/test_sessions.py` 将缺失父节点错误码更新为 `ORPHAN_PARENT`。
- 新增 [L18_CODE_GUIDE.md](L18_CODE_GUIDE.md)，解释记录树、活动路径、Agent 上下文、恢复边界、测试入口和本课未实现的文件回滚。
- TDD 证据：分支图测试先因 `append()` 不接受 `parent_id`、错误码不符而失败；活动路径测试先因 API 不存在而失败；Agent 隔离测试缺少 A 分支消息而失败；恢复测试分别把已完成历史误判为 `completed`、把 A 分支空工具调用混入 B 分支。最终审查补充的悬空调用拦截、部分多调用结果、恢复历史前缀和选中分支悬空调用也都先失败、再通过。
- 定向验证：`python -m unittest -v tests.test_recovery tests.test_branches tests.test_coding_flow tests.test_sessions` 实际运行 34 个测试，全部通过。
- 全量离线回归：`python -m unittest discover -s tests -v` 实际运行 112 个测试，全部通过，退出码 0；Docker 镜像/沙箱集成测试也在本轮运行并通过。未调用真实模型。
- 静态检查：`python -m py_compile sessions.py agent.py tests/test_branches.py tests/test_recovery.py tests/test_sessions.py` 与 `git diff --check` 均通过。
- 边界与理解状态：分支选择不会还原磁盘文件；本轮没有新增 CLI 分支菜单、复制会话、Git 切换或快照。学习者对父图、活动历史及恢复账本的复述尚待核对。
- 下一小步：L19 上下文构造；L20 之前先确认如何从保存的活动历史生成单次模型请求副本。

## 2026-09-24：L17 受控恢复，不自动重放副作用

- `agent.py` 新增 `recover_agent()`：从最新运行的 JSONL 记录重建模型消息历史，识别缺少 tool result 的调用；本函数本身不发起模型请求。
- `run_agent(session_store=...)` 现在会先记录 `run_context`，包含工作区、权限 profile 和 Provider 客户端类型，供恢复前比对。恢复入口只在显式只读重试预算大于 0、调用属于 `list_files` / `read_file` / `search_text`，且工作区和只读 profile 匹配时重试；结果继续使用原 `tool_call_id` 落盘。
- `run_coding_flow(..., session_store=...)` 会保存完整消息/工具结果，并为写入增加独立 `operation_id`、提议路径、新旧 SHA-256、绑定提议的批准记录和操作结果；日志不保存提议的文件正文。
- 结果未知的写入不会被再次执行。恢复会比较目标文件当前哈希与提议旧/新哈希：新哈希表示预期效果已观察到但操作结果缺失；旧哈希或其他哈希都只报告需要人工判断/存在冲突。已记录成功的写入若后来文件版本变化，也会阻止继续。
- 已记录最终回答但缺少最终状态时，恢复会返回已有回答，不再请求模型。悬空工具结果只有在读取调用实际重试成功，或操作账本已有确定成功/无副作用结果时才补入；`unknown` 状态绝不伪造成功。
- 验证：先运行 `python -m unittest -v tests.test_recovery`，因 `recover_agent` 尚不存在而按预期失败；实现后 `python -m unittest -v tests.test_recovery tests.test_sessions tests.test_coding_flow` 实际运行 20 个测试全部通过。
- 全量回归：`python -m unittest discover -s tests -v` 实际运行 98 个测试全部通过，退出码 0；包括 Docker 镜像和沙箱集成测试。未调用真实模型。
- 额外静态检查：`python -m py_compile agent.py sessions.py agent_tools/coding_flow.py tests/test_recovery.py tests/test_coding_flow.py tests/test_sessions.py` 和 `git diff --check` 均通过。
- 验证边界：Provider 检查核对传入客户端的类型和 `respond()` 接口，不代表 DeepSeek 当前网络/API 一定可用；没有新增 CLI `--resume`，当前入口返回经过核验的历史和下一步状态，不自动继续模型循环。
- 用户理解情况：本轮尚未收到对恢复状态和操作账本的复述，理解掌握度待核对。
- 下一小步：进入 L18，学习如何在 `parent_id` 线性链上引入会话分支；L17 无需重新运行真实模型。

## 2026-09-21：L16 会话持久化

- 已新增 `sessions.py` 的 `SessionStore`：一行一条 UTF-8 JSONL，字段为 `schema_version`、`id`、`parent_id`、`timestamp`、`type`、`payload`。
- `append()` 会先重新校验已有文件；只有整行成功写入并 `fsync` 后才更新内存中的最后一条 ID。每条新记录的 `parent_id` 指向上一条成功记录，形成当前学习版的线性链。
- `load()` 会拒绝坏 JSON、错误版本、重复 ID、断裂父子关系、非法记录类型和不完整的最后一行；不会静默丢弃历史。
- `run_agent()` 增加可选的 `session_store` 参数。传入后会记录用户消息、完整助手消息、工具调用、工具结果和最终 `run_status`；不传时默认不创建会话文件，原有入口行为不变。
- 本轮没有接入恢复、分支、重放、自动压缩，也没有把会话记录和事件记录、评测记录混为一体。
- 实际验证：先运行 `python -m unittest -v tests.test_sessions`，在实现前因缺少 `sessions` 模块按预期失败；补入实现后定向运行 7 个会话测试全部通过。
- 实际验证：`python -m unittest discover -s tests -v` 运行 88 个测试全部通过；没有调用真实模型。`git diff --check` 和 `python -m py_compile` 在本轮最后检查。
- 当时下一小步：进入 L17，学习如何从追加式记录中恢复任务，并明确哪些工具副作用不能自动重复；该课程已在 2026-09-24 按上方记录完成代码和离线验证。

## 2026-09-21：新增 Pi 源码导读

- 已创建 `mds/PI_SOURCE_GUIDE.md`，用于把 Pi 的 Agent 核心、基础工具、bash 执行器、会话管理和 SDK 入口映射到当前 MyAgent。
- 官方源码链接已核对；导读中的 Pi 路径基于当前 `earendil-works/pi` 的 `main` 分支。以后做严格复现时仍应固定 commit 或 tag。
- 本轮只新增学习文档，没有修改 Python 源码，没有安装依赖，也没有调用真实模型。
- 下一小步：阅读 Pi 的 `bash.ts`，写出 MyAgent L15 执行隔离的三个安全条件，然后再补第一个 L15 测试。

## 2026-09-21：L15 Docker 环境准备

- 已安装并启动 Docker Desktop 4.91.0；当前机器为 Apple Silicon，Docker Engine 返回 `linux/arm64`。
- 用户级 Docker CLI 已加入 shell profile，新终端中可直接使用 `docker`。
- 实际验证：`zsh -lic 'docker version --format "Client={{.Client.Version}} Server={{.Server.Version}}"'` 返回客户端和服务端均为 29.8.0。
- 实际验证：`zsh -lic 'docker run --rm hello-world'` 成功运行官方测试容器；容器使用 `--rm`，测试结束后容器已自动删除，镜像仍保留供后续学习使用。
- 安装过程中 Homebrew 的系统级命令链接因需要 macOS 管理员密码而回滚，随后改用 Docker.app 的用户级 CLI 配置；没有读取或保存密码。
- L15 尚未完成：还没有创建项目的 `Dockerfile` 或 `SandboxExecutor`，也没有开放模型执行任意 bash。下一小步是先定义隔离执行器契约并写失败测试。

## 当前定位

| 项目 | 记录 |
| --- | --- |
| 用户学习进度 | 用户自述：旧路线已推进到第七关；旧关卡不机械等同于新教程 L07。 |
| 当前实现范围 | 在既有 L25 基础上，已增加 L26 自定义 JSONL/RPC、L27 固定 Pi 对照和 TypeScript 契约、L28 显式原生扩展练习、L29 确定性兼容性矩阵、L30 交付文档与失败复盘。 |
| 当前阶段 | L26—L30 的 MyAgent 侧代码、文档和离线测试已完成；固定 Pi 的依赖安装和 TypeScript 练习通过，但 Pi 原生构建、agent-loop 测试和 CLI 扩展加载仍有明确环境阻塞。真实 Provider 证据继续与 Mock 证据分开。 |
| 本轮下一小步 | L26—L30 收尾完成；下一步是用户验收和逐文件导读，或在用户明确继续 Pi 复现时修复固定 checkout 的官方构建链。 |
| 不应执行 | 不把 `not_run`/`blocked` 写成通过，不把自定义 RPC 写成 Pi 原生兼容，不在离线回归中自动调用真实模型，不访问 `ev-ai-assistant`。 |

## 接续检查结果

| 能力 | 代码位置 | 证据状态 | 新教程对应 | 是否需要补充 |
| --- | --- | --- | --- | --- |
| 读取、列表、搜索 | `tools.py:136`、`:182`、`:195` | 代码已查看；本轮测试覆盖并通过 | L02—L03 | 不重做；可在后续按需补边界测试 |
| 工具分发 | `tools.py:228`—`:293` | 代码已查看；本轮测试覆盖未知工具、非法 JSON 和工具错误并通过 | L04 | 不重做 |
| Mock Provider 与历史检查 | `model_client.py:73`—`:142` | 代码已查看；本轮 Agent 测试通过 | L05 | 不重做 |
| 多轮 Agent loop | `agent.py:98`—`:283` | 本轮离线测试通过；Mock 入口实际完成 3 次模型请求、2 次工具执行 | L06 | 不重做 |
| 调用 ID 与工具结果回传 | `agent.py:212`—`:264`、`model_client.py:202`—`:265` | 本轮测试通过并在 Mock 输出中看到对应 ID | L05—L06 | 不重做 |
| DeepSeek 适配 | `model_client.py:145`—`:382` | 代码已查看；假客户端协议测试通过；本轮未调用真实 API | L08 | 真实证据保留为历史用户自述，不本轮重跑 |
| 真实评测 | `evals/run_evals.py`、`runs/eval_runs.jsonl` | 用户此前自述曾运行 `--real --limit 1` 并通过；本轮又收到用户贴出的 `python main.py --real` 完整输出，显示真实模式完成 4 次模型请求和 4 次工具执行；Codex 本轮未自行重跑 | L08／L30 部分 | 保留为真实调用证据；后续可再补正式评测记录 |
| 运行上限与错误处理 | `agent.py:117`—`:146`、`:236`—`:281` | 预算、错误和未执行调用有代码与测试证据；状态已统一为 `stopped` 加 `reason` | L06 | 不再是当前阻塞 |
| 事件、取消与耗时 | `events.py`、`agent.py`、`main.py`；`run_agent` 接收 `cancel_event`，记录模型/工具/运行耗时，入口显示事件摘要 | L09.1/L09.2/L09.3 代码已查看；事件集成与入口测试通过；Mock 入口实际看到摘要 | L09 | 最小实现已补齐；仍需学习者复述设计，真实 API 取消未验证 |

## 课程映射

| 课程 | 当前判断 | 说明 |
| --- | --- | --- |
| L01—L06 | 已有代码和离线运行证据；理解证据仍按学习者复述单独记录 | 不重新上入门课 |
| L07 | 本轮未读取或运行原版 Pi 参考；它是对照课，不阻塞继续修本项目核心 | 可延后按需学习 |
| L08 | 适配代码和假客户端测试已有；真实调用只有用户历史自述，本轮未验证 | 不把 Mock 当真实能力 |
| L09 | 最小范围已实现：事件格式、记录器、Agent 接入、协作式取消、耗时和终端观察摘要均有代码与离线证据 | 当前课程的代码步骤已完成；理解与真实服务行为仍单独验证 |
| L10 | L10.1 缺陷样例与独立验收、L10.2 工作副本、L10.3 工作区内可编辑路径检查均已完成 | 代码、离线测试和手动边界检查有证据；尚未给模型开放写入，也没有声称具备 OS 级沙箱 |
| L11 | 已增加 `agent_tools/write.py` 的提议、确认、冲突检查和写后核验，并增加 `main.py` 的提议展示格式化 | 代码和离线测试有证据；未接入模型工具，仍需学习者理解确认绑定 |
| L12 | 已增加 `agent_tools/edit.py` 的唯一匹配精确编辑；编辑提议复用 L11 的哈希、差异、确认、冲突和原子写入流程 | 代码和离线测试有证据；未接入模型工具，仍需学习者理解“为什么多匹配必须拒绝” |
| L13 | 已增加 `agent_tools/execution.py` 的固定案例注册表和本地受控子进程执行器；只接受 `case_id`，返回退出码、stdout、stderr、超时、取消、截断和耗时 | 代码和离线测试有证据；尚未接入 Agent，当前不是 Docker/OS 级沙箱 |
| L14 | 已增加 `agent_tools/coding_flow.py`，并从 `agent.py` 暴露 `run_coding_flow`；Mock 流程会读取工作区、运行测试、提出精确编辑、等待确认、应用编辑并再次测试 | 代码和离线测试有证据；未调用真实模型，未把新闭环接入 `main.py` 默认入口 |
| L15 | 已创建项目专属 `Dockerfile`、`.dockerignore` 和 `agent_tools/sandbox.py`；`SandboxExecutor` 使用临时容器、工作区挂载、网络关闭、只读根、非 root、输出限制、超时和取消清理 | 11 个 Docker 集成测试、完整 81 个 Python 测试和无残留容器检查有本轮证据；仍需显式实例化才会使用，未接入默认 Agent 或真实模型 |
| L16 | 已实现追加式 JSONL 会话记录，并接入 `run_agent(session_store=...)` | 代码已查看；7 个定向测试和 88 个全量测试通过；未实现恢复、分支和重放 |
| L17 | 受控恢复入口、只读调用显式预算、写入操作账本、哈希核对、最终结果恢复已有代码和离线测试证据 | Provider 仅检查客户端接口与类型；未新增 CLI `--resume`，未调用真实模型；用户理解待复述 |
| L18 | 父图校验、活动路径/历史、分支导航、Agent 分支上下文和活动路径恢复已有代码与离线测试证据 | 112 项全量测试通过；操作账本继续全局审计；文件不回滚；学习者理解待复述 |
| L19 | 单次请求上下文构造、可信指令边界、哈希清单、预算和完整工具调用组；Agent 每轮请求前使用上下文副本 | 9 个定向测试、121 个全量测试和静态检查通过；未调用真实模型；Token 精确计数和自动压缩尚未实现 |
| L20 | 固定摘要字段、保留原始历史、摘要加最近完整消息组、超大/不完整工具组拒绝压缩、Agent 显式压缩开关 | 6 个定向测试、127 个全量测试通过；固定摘要器未调用模型；自动压缩和精确 Token 计数未实现 |
| L21 | 流式文本和工具参数组装、按 call ID 隔离交错调用、正常结束后才执行工具、长度截断/重复结束/取消边界、Mock 与假 SDK 适配 | 9 个定向测试、136 个全量测试、静态检查通过；未调用真实网络；main.py 默认入口仍是非流式 |
| L22 | 内存队列、Steering/Follow-up 边界、FIFO、一条一条消费、预算停止和取消时返回未消费消息 | 6 个定向测试、142 个全量测试通过；未做复杂 UI、跨进程持久化或真实网络取消 |
| L23 | 固定目录 Skill 元数据、显式全文加载、不可执行的参考上下文、固定 Prompt Template 替换和资源安全边界 | 10 个定向测试、152 个全量测试通过；未实现 Pi 全部发现优先级、第三方包加载或自动入口加载 |
| L24 | 事件订阅、`before_tool`、显式纯只读工具注册、扩展工具分发和测试文件保护 | 14 个定向测试、158 个非 Docker 全量测试和静态检查通过；未实现 Pi 原生扩展加载和完整 TUI |
| L25 | 事件驱动的终端状态、简单 REPL、`/new`、`/compact`、`/resume`、`/exit` 和 `--interactive` 入口 | 5 个定向、20 个集成、165 个全量测试通过；Mock 交互冒烟通过；不是 Pi 原生 TUI 差分渲染 |
| L26 | 已实现并验证 | `rpc.py`、`rpc_client.py`、10 个定向测试、RPC pipe 冒烟通过 |
| L27 | 部分完成，原生构建阻塞 | 固定 v0.85.1 checkout、npm 安装、TypeScript 类型练习通过；Pi offline build 和 agent-loop 测试 blocked |
| L28 | 子集完成，原生加载阻塞 | 原生扩展源码与独立行为探针通过；Pi CLI 显式 `-e` 因未生成 CLI blocked |
| L29 | 已实现并验证子集 | 4 个 runner 测试、MyAgent/Pi 首轮矩阵已生成；Pi 对照未运行不等于通过 |
| L30 | 已实现并验证 | README、源码地图、失败复盘、评测说明和 3 个文档契约测试完成 |

## 本轮实际验证

- 工作目录：MyAgent 项目根目录
- 命令：`python -m unittest discover -s tests -v`
- 前一步基线：实际发现并运行 25 个测试，25 个通过，退出码 0；测试使用 Mock、假客户端和临时目录，未调用真实模型。
- 命令：`printf '%s\n' '任务数据保存在哪里？' | python main.py`
- 结果：默认 `mock` 模式完成，`status` 为 `completed`，`model_requests` 为 3，`tool_executions` 为 2；历史中实际看到 `list_files` 和 `read_file` 的调用 ID 及对应 tool 消息。
- 本轮小步修改：先更新两个预算测试并确认它们因旧 `stopped_limit` 失败；再在 `agent.py` 增加 `reason` 字段，并将模型/工具预算停止分别改为 `model_call_limit` / `tool_call_limit`。
- 修改后验证：`python -m unittest discover -s tests -p 'test_agent.py' -v` 实际运行 6 个测试并全部通过；随后全套测试实际运行 25 个并全部通过，退出码均为 0。
- L09.1 修改：新增 `events.py` 的 `EventRecorder` 和 `tests/test_events.py`，覆盖事件顺序、工具身份、未知事件和缺少字段。
- L09.1 验证：`python -m unittest discover -s tests -p 'test_events.py' -v` 实际运行 5 个测试并全部通过；随后全套测试实际运行 30 个并全部通过，退出码均为 0。
- L09.2 修改：`run_agent` 接收可选 `event_recorder`，为有效运行生成 `run_id`，记录 `run_started`、模型前后、工具前后和 `run_finished`，并把事件快照放进结果。
- L09.2 验证：`python -m unittest discover -s tests -p 'test_agent_events.py' -v` 实际运行 3 个测试并全部通过；随后全套测试实际运行 33 个并全部通过，退出码均为 0。
- 入口验证：默认 Mock 模式运行 `main.py` 后，实际输出了同一个 `run_id` 下的 `run_started`、模型前后、工具前后和 `run_finished` 事件。
- L09.3 修改：`run_agent` 增加可选 `cancel_event`，在模型请求前和工具执行前检查取消；模型、工具和整次运行的完成事件增加 `duration_ms`；`main.py` 增加 `format_event_summary`，先打印可读事件时间线，再打印完整 JSON。
- L09.3 定向验证：`python -m unittest discover -s tests -p 'test_agent_events.py' -v` 实际运行 5 个测试并全部通过；`python -m unittest discover -s tests -p 'test_main.py' -v` 实际运行 3 个测试并全部通过。
- L09.3 全量验证：`python -m unittest discover -s tests -v` 实际运行 36 个测试并全部通过，退出码为 0。
- L09.3 入口验证：通过管道输入问题运行 `python main.py`，Mock 模式实际输出 `run_started`、模型/工具开始与结束、`run_finished` 的摘要，并显示毫秒耗时。
- 用户提供真实模式证据：运行 `python main.py --real` 后，真实模式完成 4 次模型请求、4 次工具执行；其中第一次 `read_file` 因请求 200 行超过 80 行上限而返回 `INVALID_RANGE`，后续模型根据工具错误改为读取 1—80 行并成功完成。最终 `status` 为 `completed`，总耗时约 6252.86ms。
- L19 本轮验证：`python -m unittest -v tests.test_context` 实际运行 9 个测试并全部通过；`python -m unittest discover -s tests -v` 实际运行 121 个测试并全部通过；`python -m py_compile agent.py context.py tests/test_context.py` 和 `git diff --check` 通过。测试未联网、未调用真实模型。
- L20 本轮验证：`python -m unittest -v tests.test_compaction` 实际运行 6 个测试并全部通过；`python -m unittest discover -s tests -v` 实际运行 127 个测试并全部通过；固定摘要器为本地确定性代码，未调用真实模型。
- L21 本轮验证：`python -m unittest -v tests.test_streaming` 实际运行 9 个测试并全部通过；`python -m unittest discover -s tests -v` 实际运行 136 个测试并全部通过；`python -m py_compile streaming.py model_client.py agent.py events.py tests/test_streaming.py` 和 `git diff --check` 通过。未调用真实 DeepSeek 网络。
- L22 本轮验证：`python -m unittest tests.test_queue -v` 实际运行 6 个测试并全部通过；`python -m unittest discover -s tests -p 'test_*.py'` 实际运行 142 个测试并全部通过；`python -m py_compile agent.py input_queue.py tests/test_queue.py` 与 `git diff --check` 通过。未调用真实模型。
- L23 本轮验证：`python -m unittest tests.test_resources -v` 实际运行 10 个测试并全部通过；`python -m unittest discover -s tests -p 'test_*.py'` 实际运行 152 个测试并全部通过；未调用真实模型。资源模块的编译检查和 `git diff --check` 在本轮最终检查中补做。
- L24 本轮验证：`python -m unittest tests.test_extensions tests.test_policy -v` 实际运行 14 个测试并全部通过；排除 Docker 镜像构建类后，其他测试实际运行 158 个并全部通过；`python -m py_compile agent.py events.py extensions.py policy.py tools.py tests/test_extensions.py tests/test_policy.py` 与 `git diff --check` 通过。完整测试发现 159 个测试时，Docker 基础镜像构建因 `unable to lease content: lease does not exist` 产生 1 个环境错误；未调用真实模型。
- L25 本轮验证：`python -m unittest tests.test_ui_state -v` 实际运行 5 个测试并全部通过；与入口、事件、队列测试合计 20 个通过；`printf '/compact\n查看项目文件\n/exit\n' | python main.py --interactive` 使用 Mock 完成交互冒烟；`python -m unittest discover -s tests -p 'test_*.py'` 实际运行 165 个测试并全部通过；`python -m py_compile ui.py main.py agent.py events.py tests/test_ui_state.py` 与 `git diff --check` 通过。未调用真实模型。
- 真实调用证据边界：以上是用户提供的完整终端输出，证明该次运行表现符合真实模式和错误恢复流程；本轮没有由 Codex 自行发起 DeepSeek 请求，也没有把这次运行自动写入新的 JSONL 评测记录。
- 尚未验证：Codex 本轮没有自行调用 DeepSeek；取消仍是协作式取消，只保证下一次模型/工具调用前停止，不代表能中断已经发出的远程请求；尚未做独立的实时观察面板或真实服务用量记录。
- 用户理解检查：用户正确说明了工具错误会被程序回传给模型，模型再根据错误调整参数；已补充说明程序不会自行修改模型参数。
- L10.1 修改：在项目根目录新增 `buggy.py` 和 `test_mean_test.py`。前者提供一个故意把分母写成 `len(values) + 1` 的平均数函数；后者使用标准库 `unittest` 写出四条独立验收规则。
- L10.1 验证：先运行 `python -m unittest -v test_mean_test.py`，确认空的被测模块不能提供 `mean`；补入故意缺陷后再次运行，实际发现 4 个测试，其中 2 个通过、2 个失败。失败分别是 `[5]` 得到 `2.5` 而非 `5.0`，`[2, 4, 6]` 得到 `3.0` 而非 `4.0`；空列表异常和正负抵消用例通过。此时没有修改主 Agent，也没有调用模型。
- L10.2 修改：新增 `fixtures/mean_case/buggy_original.py` 作为未修改的原始夹具，新增 `policy.py`、`workspaces/.gitkeep` 和 `tests/test_policy.py`；`policy.py` 负责校验安全的 `run_id`、创建独立运行目录、复制原始文件和计算 SHA-256。`.gitignore` 忽略生成的工作区内容但保留目录占位文件。
- L10.2 红灯验证：先运行 `python -m unittest -v tests.test_policy`，在 `policy.py` 尚未存在时得到预期的 `ModuleNotFoundError`；补入最小实现后重新运行，实际 3 个测试全部通过。
- L10.2 绿色验证：`python -m py_compile policy.py buggy.py test_mean_test.py` 通过；`buggy.py` 与 `fixtures/mean_case/buggy_original.py` 的 SHA-256 实际一致；`git diff --check` 通过。故意缺陷测试仍保持 2 个失败，这是本课预期，不是修复完成。
- L10.2 手动验证：在临时目录调用 `prepare_workspace("run-001")`，修改生成的副本后，实际确认原始夹具哈希仍未变化；实验文件未写入项目工作区，也未调用模型。
- L10.3 修改：`policy.py` 增加 `resolve_editable_path`，同时检查 POSIX/Windows 风格绝对路径、`..` 路径、工作区边界和符号链接；新增允许工作区文件、拒绝原始/上级路径和拒绝符号链接逃逸的测试。
- L10.3 红灯验证：先运行 `python -m unittest -v tests.test_policy`，在策略函数尚未存在时得到预期的导入错误；补入实现后重新运行，实际 6 个策略测试全部通过。
- L10.3 全量验证：`python -m py_compile policy.py tests/test_policy.py` 通过；`python -m unittest discover -s tests -v` 实际运行 42 个测试并全部通过，未调用真实模型；`git diff --check` 通过。
- L10 隔离边界手动验证：在临时工作区中允许 `buggy_original.py`，拒绝 `../agent.py`、`../.env` 和原始夹具的绝对路径；命令没有读取或修改 `.env`，也没有调用模型。
- L10 边界说明：当前是应用层路径策略，不是操作系统沙箱；没有独立隔离执行环境时，不能运行模型生成的任意代码或命令。
- L11 修改：新增 `agent_tools/write.py` 和 `tests/test_write.py`；`WriteProposalStore` 支持生成待确认提议、保存旧/新 SHA-256、生成 unified diff、要求路径/内容/旧哈希完全匹配的确认、检测外部冲突、同目录临时文件替换，以及写后读取核验。
- L11 修改：`main.py` 增加 `format_write_proposal`，只负责展示提议，不让默认 Agent 循环直接获得写工具。
- L11 红灯验证：先运行 `python -m unittest -v tests.test_write tests.test_main`，在 `agent_tools` 和展示函数尚未存在时得到预期导入/属性错误；补入实现后定向测试实际运行 10 个并全部通过。
- L11 全量验证：`python -m unittest discover -s tests -v` 实际运行 49 个测试并全部通过，未调用真实模型；提议测试全部使用临时目录，没有修改真实样例、`.env` 或主项目文件。
- L11 边界说明：写入接口目前只能由 Python 调用方显式传入确认信息；尚未注册到模型工具清单，因此真实模型不能自行写文件。
- L12 红灯验证：先运行 `python -m unittest -v tests.test_edit`，在编辑模块尚未存在时得到预期的 `ModuleNotFoundError`；这是先写测试、再写实现的红灯阶段。
- L12 修改：新增 `agent_tools/edit.py` 和 `tests/test_edit.py`。编辑函数读取工作区文件，要求 `old_text` 恰好出现一次；0 次返回 `NO_MATCH`，多次返回 `AMBIGUOUS_MATCH`，唯一匹配才生成待确认提议。
- L12 复用边界：`EditProposalStore` 不重复实现写入逻辑，而是调用 `WriteProposalStore`；因此编辑提议仍然包含差异和新旧哈希，执行时仍要求精确确认、检查外部冲突，并在写后重新读取校验。
- L12 定向验证：`python -m unittest -v tests.test_edit tests.test_write tests.test_main` 实际运行 17 个测试并全部通过；覆盖唯一替换、无匹配、多匹配、空旧文本、确认写入、冲突、中文和无末尾换行。
- L12 全量验证：`python -m unittest discover -s tests -v` 实际运行 56 个测试并全部通过，退出码为 0；没有调用真实模型。`git diff --check` 通过。
- L12 边界说明：精确编辑模块目前只能由 Python 调用方显式调用和确认，尚未注册到模型工具清单；因此它不会让真实模型自动修改文件。
- L13 红灯验证：先运行 `python -m unittest -v tests.test_execution`，在执行模块尚未存在时得到预期的 `ModuleNotFoundError`；这是先写测试、再写实现的红灯阶段。
- L13 修改：新增 `agent_tools/execution.py` 和 `tests/test_execution.py`。`TestCaseRegistry` 保存可信代码预先登记的固定命令，`TestExecutor.run_tests(case_id)` 不接受模型传入的命令、路径或 Shell 字符串。
- L13 执行边界：使用 `subprocess.Popen(..., shell=False)` 和参数数组启动测试；stdin 关闭；只传递少量运行环境变量，不继承 `DEEPSEEK_API_KEY`；超时会结束独立进程组。
- L13 结果合同：执行器返回 `exit_code`、`stdout`、`stderr`、`timed_out`、`truncated` 和 `duration_ms`。测试失败由非零 `exit_code` 表示，不会被误判为执行器自身崩溃。
- L13 定向验证：`python -m unittest -v tests.test_execution` 实际运行 7 个测试并全部通过；覆盖非法案例、通过、失败、超时、输出截断、Key 隔离和固定命令。
- L13 全量验证：`python -m unittest discover -s tests -v` 实际运行 63 个测试并全部通过，退出码为 0；没有调用真实模型。`git diff --check` 通过。
- L13 取消补充：`TestExecutor.run_tests` 增加关键字参数 `cancel_event`；启动前取消不会创建子进程，运行中取消会终止独立进程组，并返回 `cancelled=True`。
- L13 取消验证：新增启动前取消和运行中取消测试；运行中测试会启动一个子进程，取消后确认线程结束且子进程没有继续写入完成标记。定向测试实际运行 9 个并全部通过；全量测试实际运行 68 个并全部通过。
- L13 边界说明：当前是固定案例的本地子进程执行器，不是 Docker 或操作系统级沙箱；没有开放给模型，也没有声称可以安全执行任意代码。
- L14 红灯验证：先运行 `python -m unittest -v tests.test_coding_flow`，在闭环模块尚未存在时得到预期的 `ModuleNotFoundError`；这是先写测试、再写实现的红灯阶段。
- L14 修改：新增 `agent_tools/coding_flow.py`，实现 `read_workspace_file` 和 `run_coding_flow`；并在 `agent.py` 暴露该入口，原来的只读 `run_agent` 保持不变。
- L14 流程边界：模型只能请求 `read_workspace_file`、`run_tests`、`propose_edit` 三种动作；编辑必须由可信的 `confirm_edit` 回调返回绑定提议 ID、路径和哈希的确认对象，没有确认就返回 `waiting_confirmation`。
- L14 验收规则：即使模型直接返回“已修复”，只要没有 `exit_code == 0` 的测试通过证据，就返回 `UNVERIFIED_REPAIR`；测试失败由真实退出码回传给模型，允许模型继续提出修改。
- L14 定向验证：`python -m unittest -v tests.test_coding_flow tests.test_agent tests.test_edit tests.test_execution` 实际运行 23 个测试并全部通过；覆盖完整修复、缺少确认和无验证声明三种路径。
- L14 全量验证：`python -m unittest discover -s tests -v` 实际运行 66 个测试并全部通过，退出码为 0；没有调用真实模型。`git diff --check` 待本轮最后检查。
- L14 边界说明：当前只验证 Mock 修复流程；`run_coding_flow` 尚未接入 DeepSeek 的新工具清单，也没有开放到 `main.py` 默认入口。真实模型接入前仍需单独设计消息格式、确认界面和隔离执行。
- L15 修改：新增项目根目录 `Dockerfile` 和 `.dockerignore`。镜像基于 `python:3.13-slim-bookworm`，显式安装 bash，创建普通用户 `agent`，默认工作目录为 `/workspace`，不复制项目源码或安装模型客户端。
- L15 红灯验证：先运行 `python -m unittest -v tests.test_docker_image`，在 `Dockerfile` 和 `.dockerignore` 不存在时，实际因缺少构建文件和忽略文件失败；这是预期的测试先行阶段。
- L15 镜像验证：`python -m unittest -v tests.test_docker_image` 实际运行 2 个测试并全部通过；构建了 `myagent-sandbox:py313`，容器在 `--network none`、`--read-only`、`--tmpfs /tmp`、`/workspace` 工作目录下成功运行 Python 和 bash，并确认不是 root 用户。
- L15 修改：新增 `agent_tools/sandbox.py` 的 `SandboxExecutor`。它只允许挂载配置的 `workspace_root` 内的普通目录，使用参数数组和 `shell=False` 启动 Docker；容器使用 `--rm`、`--network none`、`--read-only`、`--cap-drop ALL`、`no-new-privileges`、`--tmpfs /tmp` 和项目镜像。
- L15 执行结果：返回 `exit_code`、`stdout`、`stderr`、`timed_out`、`cancelled`、`truncated` 和 `duration_ms`；宿主侧只保留 Docker 客户端需要的变量，不把模型 API Key 传给容器。
- L15 取消/清理：超时或取消时先执行 `docker kill`，再终止 Docker 客户端进程组，最后执行 `docker rm --force`；正常运行依赖 `--rm` 自动删除容器。
- L15 定向验证：`python -m unittest -v tests.test_sandbox` 实际运行 11 个测试并全部通过，覆盖正常命令、非零退出、工作区持久化、越界工作区、空命令、网络关闭、只读根、输出截断、API Key 隔离、超时和取消。
- L15 完整回归：`python -m unittest discover -s tests -v` 实际运行 81 个测试并全部通过，退出码为 0；没有调用真实模型。
- L15 残留检查：`docker ps -a --filter ancestor=myagent-sandbox:py313` 没有返回容器；`git diff --check` 和 `python -m py_compile agent_tools/sandbox.py agent_tools/execution.py tests/test_sandbox.py` 均通过。
- L15 镜像信息：`docker image inspect` 显示镜像大小约 207 MB，系统为 Linux、架构为 arm64、默认用户为 `agent`、工作目录为 `/workspace`。
- L15 边界说明：`SandboxExecutor` 目前是显式 Python API，没有注册到模型工具清单，也没有接入 `main.py` 默认入口；镜像隔离是学习版边界，不声称等同于完整生产级沙箱。
- L16 红灯验证：新增 `tests/test_sessions.py` 后，首次运行因 `sessions.py` 尚不存在得到预期的 `ModuleNotFoundError`。
- L16 修改：新增 `sessions.py`。`SessionStore` 负责追加和完整校验 JSONL；`SessionLoadError.code` 用于区分 `INVALID_JSON`、`PARTIAL_TAIL`、`UNSUPPORTED_SCHEMA`、`DUPLICATE_ID` 和 `BROKEN_PARENT` 等错误。
- L16 Agent 接入：`run_agent()` 只在收到可选 `session_store` 时持久化历史；助手工具调用和工具结果分别保存，工具预算耗尽时产生的 `NOT_EXECUTED` 结果也会保存，最后追加运行状态。
- L16 定向验证：`python -m unittest -v tests.test_sessions tests.test_agent tests.test_agent_events` 实际运行 18 个测试并全部通过。
- L16 全量验证：`python -m unittest discover -s tests -v` 实际运行 88 个测试并全部通过，退出码为 0；没有调用真实模型。
- L16 边界验证：不传 `session_store` 时，Agent 仍能完成 Mock 流程且不会凭空创建会话文件；坏 JSON、错误版本、重复 ID、断裂父链、Unicode 和不完整尾行均有测试。
- L16 边界说明：当前记录是单进程单写入者的线性会话格式；没有自动从会话恢复下一次模型上下文，没有会话树、分支、重放、压缩或流式增量记录。
- 下一课定位：进入 L17，先实现“读取最后有效记录并恢复到安全阶段”的最小能力，同时把可能重复工具副作用的问题单独列为测试。

## 理解与独立修改

用户自述已推进旧路线第七关；学习者已正确解释：`status=stopped` 表示 Agent 运行终止，`reason=tool_call_limit` 表示因为工具执行预算耗尽而停止。L09 事件已接入 Agent，并已补充取消与耗时；事件如何跟随每一轮模型/工具执行产生、取消为何不能等同于强制中断，仍待学习者复述或独立修改验证，不能仅凭测试通过填写“已完全掌握”。

## 权限记录

| 能力 | 当前状态 |
| --- | --- |
| 读取和修改本学习项目文档 | 本轮已按用户授权更新 |
| 修改 Python 源码 | 本轮新增 `sessions.py`、`tests/test_sessions.py`，并给 `agent.py` 的 `run_agent()` 增加可选会话记录接入；未接入真实模型 |
| 真实模型请求 | 本轮未启用；需用户明确授权 |
| 模型写文件/执行命令 | 未启用 |
| 访问/修改 `ev-ai-assistant` | 未授权 |

## 后续增量记录格式

```text
日期 / Lxx / 小步：
实际查看或修改：
实际命令与结果：
学习者解释或独立改动：
仍未验证/当前阻塞：
下一小步：
授权变化（有明确依据才写）：
```
