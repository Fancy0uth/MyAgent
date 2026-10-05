# L18 代码导读：会话树与分支

## 这一课解决什么

L16 把会话保存成追加式 JSONL，但每条记录只能接在文件上一行，历史因此是一条线。L18 仍保留原来的 JSONL 字段和 `schema_version=1`，让 `parent_id` 指向任意一条更早的记录，于是同一条聊天记录可以有多个后续分支。

分支只决定 Agent 使用哪段聊天上下文。它不会把工作区文件还原到历史时刻，也不会撤销以前已经发生的写入。

## 先看一份会话树

下面简化展示四条记录。B 分支先追加，随后用户把活动位置选回 A：

```text
u1: user_message       parent=null  “修复这个问题”
a1: assistant_message  parent=u1    “方案 A”
b1: assistant_message  parent=u1    “方案 B”
s1: branch_selection   parent=a1    selected_node_id=a1
```

文件仍按追加顺序保存：`u1 → a1 → b1 → s1`。但活动路径沿 `s1` 的父指针回溯后是 `u1 → a1 → s1`。`get_model_history()` 会筛出其中的聊天消息，所以模型看到 `u1` 和 `a1`，看不到兄弟分支 `b1`，也看不到导航记录 `s1`。

`branch_selection` 自己是一个新节点，父节点是被选中的聊天节点。之后 `run_agent()` 追加的新运行记录就接在它后面。这样重新打开 `SessionStore` 时，最后一条记录仍能确定活动位置，不需要重写之前的 JSONL 内容。

## 从存储层开始读：`sessions.py`

### 加载和校验父图

`SessionStore._read_and_validate()` 先检查每一行的 JSON、字段、版本、记录类型和 ID，再调用 `_validate_parent_graph()` 检查父关系：

1. 每条非根记录的父 ID 必须存在；否则报 `ORPHAN_PARENT`。
2. 沿父指针检查循环；否则报 `CYCLE_DETECTED`。循环检查发生在父记录顺序检查之前，损坏的循环不会被误报成普通顺序错误。
3. 父节点必须在子节点之前写入；违反时拒绝加载。

`_refresh_state()` 从完整文件重建 ID 索引和当前活动叶子。旧的线性会话满足这套规则，可以直接加载，不需要迁移文件或升级 schema。

### 追加和创建分支

`SessionStore.append()` 不指定 `parent_id` 时，默认接在当前活动叶子上。指定一个已存在的历史 ID，则从该节点建立新分支。两条记录可以有相同父 ID；文件顺序仍只向后追加。

新记录只有在写入并 `fsync` 成功后才更新内存索引和活动叶子，延续 L16 的“写失败不伪造历史”约束。

### 取活动路径和模型历史

- `get_active_path()` 从活动叶子沿父指针回溯，再反转成根到叶子的路径。返回值是深拷贝，调用方改消息不会改写存储索引。
- `get_model_history()` 只留下 `user_message`、`assistant_message`、`tool_result` 的 payload。助手工具调用和 `tool_call_id` 原样保留，工具协议才能成对往返。
- `select_active_node(node_id)` 只接受存在的用户消息、助手消息或工具结果。不存在的 ID 和运行元数据节点都会以 `INVALID_BRANCH_TARGET` 失败；不会悄悄跳到最新记录。
- `render_tree()` 把聊天节点按父子关系缩进显示，并略过运行状态、工具调用日志和操作账本等纯元数据。

## 再看 Agent 如何使用活动分支：`agent.py`

`run_agent()` 收到 `session_store` 后，先从 `get_active_path()` 取得活动路径上的聊天消息，并检查其中是否有尚未匹配结果的工具调用。若有未完成调用，返回 `recovery_required` 和调用 ID；不会请求模型、记录新运行或追加本轮问题。调用者应先用 `recover_agent()` 核对恢复状态。

没有未完成调用时，`run_agent()` 才把本轮问题接到历史末尾。`run_context` 同时保存本次请求使用的聊天记录 ID；若运行中断，`recover_agent()` 可以还原完整的模型上下文。当前运行的最终回答、工具调用和状态判断仍只看该次运行的记录。结果是：

```text
活动分支聊天历史 + 本轮问题
          ↓
model_client.respond(history)
          ↓
助手消息 / 工具调用 / 工具结果继续落入当前活动路径
```

没有传 `session_store` 时，历史仍从本轮问题开始，不会创建会话文件。当前分支选择是 `SessionStore` 的 Python API；本课没有增加命令行分支选择界面。

## 恢复为什么同时看两种范围

`recover_agent()` 把记录分成两种用途：

- **聊天恢复**只看 `get_active_path()`。兄弟分支的助手回答、工具调用、工具结果和运行状态不会混进本次恢复上下文。
- **写入安全审计**仍将完整 `SessionStore.load()` 交给 `_inspect_pending_operations()`。切换聊天分支不能隐藏其他分支里结果未知、尚未批准或需要按哈希核对的文件操作。

如果最近的活动记录是分支选择，而且它之后还没有新的 `run_context`，恢复会返回 `branch_selected`（若活动路径有未完成工具调用，则返回 `branch_selected_with_pending_tool_calls` 并列出调用 ID；若有待核对写入，则返回写入核对原因）。这表示用户选定了历史位置，不表示原运行需要自动重放。调用者必须先查看未完成调用；即使给出只读重试预算，这个分支选择路径也不会自动重试。写入工具不会因为切换分支而自动执行。

## 如何手动试读

```python
from sessions import SessionStore

store = SessionStore("session.jsonl")
for entry in store.load():
    print(entry["id"], entry["parent_id"], entry["type"])

print(store.render_tree())
print(store.get_model_history())

# 将 active-node-id 替换成树上某个聊天节点的真实 ID。
store.select_active_node("active-node-id")
```

分支内容必须来自实际会话记录；不要照抄示例 ID。测试使用临时目录和假模型，不访问真实 Provider。

## 文件与测试索引

- [sessions.py](../sessions.py)：父图验证、活动路径、历史筛选、分支选择和文本树。
- [agent.py](../agent.py)：普通请求的分支上下文，以及活动路径恢复和全局写入核对。
- [tests/test_branches.py](../tests/test_branches.py)：父图错误、兄弟分支隔离、树输出和 Agent 请求上下文。
- [tests/test_recovery.py](../tests/test_recovery.py)：导航后的恢复语义，以及跨分支写入操作审计。
- [tests/test_sessions.py](../tests/test_sessions.py)：旧 JSONL 兼容和原有持久化行为。

运行 L18 相关回归：

```bash
python -m unittest -v tests.test_recovery tests.test_branches tests.test_coding_flow tests.test_sessions
```

运行完整离线套件：

```bash
python -m unittest discover -s tests -v
```

## 本课边界

- 选聊天分支不会回滚磁盘文件。需要比较不同修复结果时，要另行使用独立工作区或受控快照。
- 本课不做“复制为新会话”、Git 分支切换或文件快照。
- Mock/fake 测试证明本地历史选择和协议行为，不证明真实模型服务表现。

---

## 逐步追踪一次分支请求

前面的内容是概览。本节按代码实际执行顺序，从日志记录一路追到模型入参，再追到中断恢复。读代码时建议打开 [sessions.py](../sessions.py) 和 [agent.py](../agent.py) 对照。

### 一、先区分文件顺序和父子路径

一行 JSONL 仍然是一个独立对象：

~~~json
{
  "schema_version": 1,
  "id": "u1",
  "parent_id": null,
  "timestamp": "2026-09-24T00:00:00+00:00",
  "type": "user_message",
  "payload": {"role": "user", "content": "共同问题"}
}
~~~

字段可以分两组读：

- id 和 parent_id 定义图上的身份与父子关系。
- type 和 payload 定义记录是聊天消息、运行元数据还是操作审计。
- schema_version 继续为 1，L18 没有改 JSONL 格式。
- timestamp 只是记录时间；代码用文件行位置检查追加顺序，用 parent_id 选择祖先路径，不用时间戳排序。

比如：

~~~text
行 1：u1 user_message       parent=null
行 2：a1 assistant_message  parent=u1   内容为“方案 A”
行 3：b1 assistant_message  parent=u1   内容为“方案 B”
行 4：s1 branch_selection   parent=a1   选中 a1
~~~

文件顺序是 u1、a1、b1、s1；从最后一条 s1 沿 parent_id 回溯，反转后得到活动路径 u1 → a1 → s1。get_model_history() 再过滤掉 s1 这个导航元数据，传给模型的是 u1 和 a1。B 仍在完整日志中，也仍能从 render_tree() 看到。

如果下一轮 Agent 接着 A 运行，run_context 和新 user_message 会追加在 s1 之后。它们的父链经过 s1、a1、u1，因此新的活动路径继续继承 A 的聊天，但不会继承 B。

| 要回答的问题 | 看哪个结构 |
| --- | --- |
| 所有分支、工具记录和操作账本分别发生过什么？ | 全部 JSONL 行，按文件顺序 |
| 现在这轮模型应该看到哪些祖先消息？ | 从末端沿 parent_id 得到活动路径，再按聊天 type 过滤 |

### 二、从 sessions.py 的校验函数往下读

建议阅读顺序：

1. **SessionStore._read_and_validate()**：逐行解析 JSON，检查必需字段、版本、ID、记录类型和 payload。文件末尾没有换行会被视为可能未写完，不会静默忽略。
2. **_validate_parent_graph()**：校验整份文件的 parent_id 关系。先确认父 ID 存在，再检测环，最后检查父节点必须早于子节点。
3. **_refresh_state()**：从通过验证的全量记录重建 ID 索引、文件顺序和 _last_id。
4. **append()**：决定默认父节点或采用显式父节点，序列化并追加 JSONL。
5. **get_active_path() / get_model_history()**：分别构造活动路径与模型聊天协议。
6. **get_unresolved_tool_calls()**：检查助手调用是否都有匹配工具结果。
7. **select_active_node() / render_tree()**：持久化导航位置、面向人展示分支树。

父图验证的错误分类不是多余的防御代码：

| 检查 | 错误码 | 例子 |
| --- | --- | --- |
| 只有首条记录可以没有父节点 | BROKEN_PARENT | 文件后面的行 parent_id 又是 null |
| 父 ID 必须存在 | ORPHAN_PARENT | 子记录指向不存在的 ID |
| 沿 parent_id 不能回到已在当前路径的节点 | CYCLE_DETECTED | A 指向 B，B 又指向 A |
| 父记录必须先写入 | PARENT_ORDER | 子行出现在父行之前 |

循环检查用 0/1/2 三态，而不是递归：0 是未访问，1 是当前回溯路径，2 是已完成验证。沿父指针再次遇到状态 1 就是环；走到根或已验证节点后，把本次路径都标成 2。这个写法也不会因为会话特别长而触发递归深度限制。

_refresh_state() 每次追加前重新读文件并验证，避免依据旧缓存使用不存在的父 ID 或重复 ID。该 SessionStore 设计按单写入者使用；刷新和 fsync 不构成多进程并发写锁。

append() 是创建分支的实际接口：

- parent_id 不传时，接在文件当前末条记录后，保持通常的连续运行。
- parent_id 指向已有历史记录时，在图上从该节点创建一个新孩子。
- 写入成功、flush 并 fsync 之后，才推进 _entries_by_id 和 _last_id。若写盘失败，内存不会先行假装记录存在。

select_active_node() 不修改旧行或内存指针。它只接受 user_message、assistant_message、tool_result，然后调用 append() 新增 branch_selection，payload 记 selected_node_id，parent_id 指向被选择的聊天节点。因此导航动作本身可审计，重新打开文件也不会丢掉选择。

get_active_path() 从 _last_id 沿父指针向上收集节点，再反转成根到叶。它返回深拷贝，所以调用者改返回的消息不影响 SessionStore 内部索引。get_model_history() 只保留三类 payload：user_message、assistant_message、tool_result。工具调用 ID 和工具结果仍保留，Provider 才能把结果关联到对应请求。

get_unresolved_tool_calls() 遍历上述消息：助手消息中的 tool_call 或 tool_calls 把 ID 放入待处理表；role=tool 的消息用 tool_call_id 移除对应项。最后剩下的 ID 就是日志里没有完成结果配对的调用。

### 三、追踪 run_agent() 的消息数据流

当传入 session_store 时，run_agent() 会先构造以下数据：

| 局部数据 | 内容 | 用途 |
| --- | --- | --- |
| active_entries | 当前分支上的所有记录类型 | 计算聊天上下文 |
| stored_history | 活动路径的聊天 payload | 作为本轮模型历史前缀 |
| history_entry_ids | 前缀对应的 JSONL ID | 崩溃后还原请求原来的上下文 |
| history | stored_history 加上本轮新消息和运行中结果 | 传给 Provider，并作为运行结果返回 |

函数顺序刻意安排成这样：

1. 参数有效性检查。
2. 读取活动路径，取出聊天 payload 和对应 ID。
3. 检查悬空工具调用。若有，直接返回 recovery_required 与调用 ID；不会请求模型、记录新的 run_context 或追加这次用户问题。
4. 创建客户端和事件记录器，追加 run_context，记下 Provider 类型、工作区、权限 profile 以及 history_entry_ids。
5. 追加本轮 user_message。
6. 调用 client.respond(history)。
7. 若响应是工具调用，先保存 assistant_message，再逐个执行工具并保存 tool_result；工具结果随后作为下一次模型请求的历史。
8. 若响应是 final，保存最终 assistant_message，由 finish() 追加 run_status。

一次工具往返在模型历史里保持成对协议：

~~~text
user
assistant(tool_call id=call-1)
tool(tool_call_id=call-1)
assistant(final)
~~~

多个工具调用会放在同一条 assistant_message 的 tool_calls 列表中。如果其中一个结果已经写入、另一个还没有，整体批次仍是不完整的。继续塞入新用户消息会让模型收到半截工具协议，因此 L18 先拦住新请求，要求调用者走恢复检查。

没有传 session_store 时，history 仍从本轮问题开始，不创建 JSONL 文件。这保证会话持久化是显式启用的。

### 四、recover_agent() 为什么要重建两份 history

recover_agent() 不调用 client.respond()，但可以在明确预算和上下文匹配时重试白名单只读工具。它开头同时取得：

- 全部 entries：交给 _inspect_pending_operations() 检查所有分支留下的写入账本。
- active_path：用于重建聊天消息、工具调用和当前运行状态。

两者范围不同是安全要求。假设 A 分支产生了一个结果未知的编辑操作，用户后来选到 B：恢复聊天只应该包含 B；但检查磁盘操作仍必须看见 A 的写入账本，否则切换分支就会把已经发生的副作用藏起来。

进入普通恢复流程前，函数还会检查“最近一次 branch_selection 是否晚于最近 run_context”：

- 是：这只是一次导航，没有新运行等待恢复。返回 branch_selected。
- 如果所选路径上有未完成调用，返回 branch_selected_with_pending_tool_calls 和 ID。
- 这个分支选择早退不会执行只读重试，即使调用者传了 retry budget。
- 否：说明导航后已有新 run_context，继续分析该运行。

确定当前运行起点后，函数构造两份列表：

~~~text
history = 按 run_context.history_entry_ids 找回的祖先聊天 + 当前运行消息
current_history = 当前运行消息和工具结果
~~~

完整 history 用来表示模型当时实际收到的上下文；current_history 用于判断当前运行有没有最终回答、哪些工具调用仍然悬空、run_status 是否缺失。这样 A 分支旧回答不会被误认成 B 分支新运行的答案。

旧 L16 的 run_context 没有 history_entry_ids。对于这类日志，代码兼容地退回到最近 run_status 和 user_message 所确定的旧式运行边界；新的 L18 日志则能精确恢复模型上下文前缀。

### 五、读恢复结果时要同时看 reason 与两个待处理列表

| 现场 | reason 示例 | 恢复行为 |
| --- | --- | --- |
| 空文件 | empty_session | 没有可重建的历史 |
| 刚选中旧节点，尚未开新运行 | branch_selected | 这是导航，不请求模型 |
| 选中的路径含悬空调用 | branch_selected_with_pending_tool_calls | 列出调用 ID，不自动重试 |
| 普通中断留下只读调用且预算为零 | read_only_retry_budget_required | 默认不替调用者执行读取 |
| 调用可能有副作用 | unknown_side_effect_result | 不盲目重放工具 |
| 工作区、权限或 Provider 与记录不符 | recovery_context_mismatch | 不在不同现场继续 |
| 最终回答已写入 | final_answer_was_persisted | 返回已有答案，不重新请求 |
| 上下文有效且没有待处理项 | context_and_history_validated | 状态为 ready_to_resume |

白名单只读工具是 list_files、read_file、search_text。自动重试需要调用者明确提供预算，并且 workspace 与 permission_profile 匹配；结果写回时继续使用原 tool_call_id。若工具出错或结果写盘失败，不伪造 tool_result 成功记录。

对写入账本，_inspect_pending_operations() 只观察，不重放。它核对 intent、approval、result、运行上下文以及文件的旧/新 SHA-256；结果未知时可能要求人工核对。聊天分支可以切换，磁盘文件不会随之回滚。

### 六、测试按风险阅读

先看 [tests/test_branches.py](../tests/test_branches.py)，再看 [tests/test_recovery.py](../tests/test_recovery.py)：

| 测试 | 它防止什么回归 |
| --- | --- |
| test_append_can_make_two_children_of_one_node | append() 退回只会接文件上一行的线性模型 |
| test_load_rejects_orphan_parent / test_load_reports_cycle_before_parent_order_error | 接受损坏的父图或把环误分类 |
| test_selected_branch_survives_reopen_and_excludes_sibling | 只在内存里选分支，重开文件后选择丢失 |
| test_model_history_preserves_tool_call_and_matching_result | 分支过滤时破坏模型工具协议 |
| test_run_agent_sends_only_selected_branch_to_model | 兄弟分支泄漏到 Provider 入参 |
| test_run_agent_requires_recovery_for_selected_unfinished_tool_call | 悬空的单个调用被新问题带进模型请求 |
| test_run_agent_requires_recovery_for_partial_multi_tool_results | 多调用只回来部分结果时仍继续运行 |
| test_recovery_restores_branch_context_used_by_interrupted_run | 崩溃恢复丢失模型实际看到的祖先上下文 |
| test_branch_selection_does_not_resume_completed_ancestor_run | 导航被误认为未完成运行 |
| test_recovery_uses_active_messages_but_checks_all_branch_operations | 活动聊天过滤错误地连跨分支写入审计也过滤掉 |
| test_selected_branch_reports_unresolved_calls_without_retrying | 选择含悬空调用时，恢复入口意外重试只读工具 |

Agent 隔离测试使用抓取型假客户端断言 respond() 的真实入参；恢复上下文测试模拟客户端调用期间进程崩溃，再比较客户端收到的消息和恢复结果。这两种测试分别验证边界两端：发出去的是什么，恢复出来的是否一致。

L18 定向回归命令：

~~~bash
python -m unittest -v tests.test_recovery tests.test_branches tests.test_coding_flow tests.test_sessions
~~~

全量离线套件命令：

~~~bash
python -m unittest discover -s tests -v
~~~

### 七、读完后用四个问题自查

1. B 分支还在 JSONL 中，为什么当前模型请求看不到它？
2. 为什么选择节点要追加 branch_selection，而不是改写旧节点或只修改内存变量？
3. 一个助手消息要求两个工具、日志只完成一个结果时，为什么 run_agent() 必须先停？
4. 为什么聊天恢复只看活动路径，而写入核对必须扫描整个会话文件？

能回答这四题，就抓住了 L18 的四个核心：父图决定路径、追加日志保留历史、工具调用需要完整配对、聊天上下文与文件副作用采用不同审计范围。
