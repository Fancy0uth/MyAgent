# L18 会话树与分支实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** 在保留 L16/L17 JSONL 历史和写入安全语义的前提下，支持同一会话多分支、活动路径模型上下文和分支安全恢复。

**Architecture:** SessionStore 将所有 JSONL 记录建成带父指针的追加式有向树，活动叶子和导航记录持久化在同一个日志中。Agent 只从活动祖先路径构建聊天历史；恢复的消息分析走活动路径，副作用操作账本仍扫描完整日志。

**Tech Stack:** Python 标准库、现有 `unittest`、现有 Mock/fake ModelClient；不新增依赖。

**Spec:** `docs/superpowers/specs/2026-09-24-session-branches-design.md`

## Global Constraints

- 保留 JSONL 字段和 schema_version=1；旧线性父子链必须仍可加载。
- 新记录的父节点必须是已存在记录；父图必须无环；不得静默回退到最新历史。
- 模型聊天上下文只由活动路径的用户、助手和工具结果消息组成。
- 操作意图、批准和结果在恢复安全检查中仍从完整日志读取。
- 分支切换不会回滚工作区文件；禁止自动 `git reset --hard` 或清理用户未提交文件。
- 不新增依赖、不调用真实模型、不访问 `ev-ai-assistant`。
- 不提交 Git；项目规则要求提交另行获得用户授权。
- 所有实现行为使用测试先行：看见目标测试因预期的功能缺失而失败后再写生产代码。

## Review Focus

1. **兄弟分支内容泄漏：** `run_agent` 发给 Provider 的消息是否只含活动祖先路径；Task 3 集成测试固定这一行为。
2. **损坏父图被错误接受：** 孤儿、循环及晚于子节点的父记录是否被拒绝；Task 1 固定错误分类和旧线性兼容。
3. **重新打开后活动分支丢失：** 分支选择是否真正落盘；Task 2 关闭并新建 SessionStore 后检查活动路径。
4. **工具协议历史被筛坏：** 活动历史是否保留助手工具调用、调用 ID 和对应工具结果；Task 2/3 检查完整协议往返。
5. **切换分支隐藏文件副作用：** 恢复是否仍扫描非活动分支的操作账本；Task 4 用 A 分支待核对写入和 B 分支消息验证。

---

### Task 1: 把线性父链校验扩展成可校验的父图

**Files:**
- Modify: `sessions.py`
- Create: `tests/test_branches.py`
- Modify: `tests/test_sessions.py`

**Interfaces:**
- Consumes: 现有 `SessionStore.append(entry_type, payload, entry_id=None, timestamp=None)` 与 `SessionLoadError`。
- Produces: `append(entry_type, payload, parent_id=None, entry_id=None, timestamp=None)` 可从显式的既有记录分叉；省略或传入 `None` 时接当前活动叶子（空文件除外）；加载时接受合法分支并拒绝坏父图。

- [x] **Step 1: 写显式兄弟分支测试**

创建 `tests/test_branches.py`，先放入可复用的临时会话夹具和所需导入，再写显式兄弟分支测试：

```python
import json
import tempfile
import unittest
from pathlib import Path

from sessions import SessionLoadError, SessionStore


def make_entry(entry_id, parent_id, entry_type="user_message"):
    return {
        "schema_version": 1,
        "id": entry_id,
        "parent_id": parent_id,
        "timestamp": "2026-09-24T00:00:00+00:00",
        "type": entry_type,
        "payload": {"role": "user", "content": entry_id},
    }


def write_entries(path, entries):
    path.write_text(
        "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in entries),
        encoding="utf-8",
    )


class TemporarySessionTestCase(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.session_path = Path(self.temp_dir.name) / "session.jsonl"

    def tearDown(self):
        self.temp_dir.cleanup()


class SessionGraphTests(TemporarySessionTestCase):
    def test_append_can_make_two_children_of_one_node(self):
        store = SessionStore(self.session_path)
        root = store.append("user_message", {"role": "user", "content": "共同问题"})

        branch_a = store.append(
            "assistant_message",
            {"role": "assistant", "content": "A"},
            parent_id=root["id"],
        )
        branch_b = store.append(
            "assistant_message",
            {"role": "assistant", "content": "B"},
            parent_id=root["id"],
        )

        loaded = SessionStore(self.session_path).load()
        self.assertEqual(branch_a["parent_id"], root["id"])
        self.assertEqual(branch_b["parent_id"], root["id"])
        self.assertEqual(
            [entry["id"] for entry in loaded],
            [root["id"], branch_a["id"], branch_b["id"]],
        )
```

- [x] **Step 2: 跑测试确认红灯**

运行：`python -m unittest -v tests.test_branches.SessionGraphTests.test_append_can_make_two_children_of_one_node`

预期：失败在 `append()` 尚不接受 `parent_id`，而不是测试导入或 fixture 错误。

- [x] **Step 3: 写孤儿和循环父图测试**

上面的文件已包含模块级 JSONL 记录构造器。把以下两个行为测试方法加入已存在的 `SessionGraphTests` 类：

```python
    def test_load_rejects_orphan_parent(self):
        write_entries(self.session_path, [make_entry("child", "missing")])
        with self.assertRaises(SessionLoadError) as caught:
            SessionStore(self.session_path).load()
        self.assertEqual(caught.exception.code, "ORPHAN_PARENT")

    def test_load_reports_cycle_before_parent_order_error(self):
        write_entries(self.session_path, [
            make_entry("first", "second"),
            make_entry("second", "first"),
        ])
        with self.assertRaises(SessionLoadError) as caught:
            SessionStore(self.session_path).load()
        self.assertEqual(caught.exception.code, "CYCLE_DETECTED")
```

- [x] **Step 4: 跑父图测试确认红灯**

运行：`python -m unittest -v tests.test_branches.SessionGraphTests`

预期：父图测试因旧实现的线性限制或错误码不同而失败；如果是语法/导入错误，先修测试再继续。

同时把 `tests/test_sessions.py::test_wrong_schema_version_and_broken_parent_are_rejected` 对缺失父节点的预期从 `BROKEN_PARENT` 改为规格中的 `ORPHAN_PARENT`；版本号断言保持不变。

- [x] **Step 5: 实现父图索引和校验**

在 `SessionStore._read_and_validate()` 中先解析并验证现有字段、ID 和类型，建立 `id -> entry` / 文件顺序索引；随后检查未知父 ID、用 DFS 检查循环、最后检查父记录必须在子记录之前。合法旧线性文件保持原样可加载。为 `append()` 增加 `parent_id` 参数：默认使用当前活动记录 ID；显式值必须已存在；新 ID 成功落盘后才更新索引和活动叶子。把 `branch_selection` 纳入默认记录类型。

- [x] **Step 6: 跑存储与旧兼容测试**

运行：`python -m unittest -v tests.test_branches.SessionGraphTests tests.test_sessions`

预期：显式分叉、父图错误以及原有 JSONL、编码、重复 ID、部分末行和 Agent 持久化测试全部通过；旧“上一条记录”损坏用例现在明确验证孤儿父节点错误码。

---

### Task 2: 活动节点选择、路径历史和文本树

**Files:**
- Modify: `sessions.py`
- Modify: `tests/test_branches.py`

**Interfaces:**
- Consumes: Task 1 的图索引和 `append(entry_type, payload, parent_id=parent_id)`。
- Produces: `get_active_path() -> list[dict]`、`get_model_history() -> list[dict]`、`select_active_node(node_id) -> dict`、`render_tree() -> str`。

- [x] **Step 1: 写活动路径隔离和重开测试**

在 `tests/test_branches.py` 添加 `class SessionBranchTests(TemporarySessionTestCase)`，并在类中写：

```python
    def test_selected_branch_survives_reopen_and_excludes_sibling(self):
        store = SessionStore(self.session_path)
        root = store.append("user_message", {"role": "user", "content": "共同问题"})
        answer_a = store.append(
            "assistant_message", {"role": "assistant", "content": "A 私有内容"},
            parent_id=root["id"],
        )
        store.append(
            "assistant_message", {"role": "assistant", "content": "B 私有内容"},
            parent_id=root["id"],
        )
        store.select_active_node(answer_a["id"])

        reopened = SessionStore(self.session_path)
        path_ids = [entry["id"] for entry in reopened.get_active_path()]
        contents = [message.get("content") for message in reopened.get_model_history()]

        self.assertIn(answer_a["id"], path_ids)
        self.assertNotIn("B 私有内容", contents)
        self.assertIn("A 私有内容", contents)
```

- [x] **Step 2: 跑测试确认红灯**

运行：`python -m unittest -v tests.test_branches.SessionBranchTests.test_selected_branch_survives_reopen_and_excludes_sibling`

预期：因活动路径/选择接口尚不存在而失败。

- [x] **Step 3: 写非法活动节点测试**

```python
    def test_selection_rejects_unknown_id_and_non_chat_type(self):
        store = SessionStore(self.session_path)
        user = store.append("user_message", {"role": "user", "content": "问题"})
        metadata = store.append("run_context", {"run_id": "run-1"})

        for invalid_id in ("missing", metadata["id"]):
            with self.subTest(invalid_id=invalid_id):
                with self.assertRaises(SessionLoadError) as caught:
                    store.select_active_node(invalid_id)
                self.assertEqual(caught.exception.code, "INVALID_BRANCH_TARGET")

        self.assertEqual(store.get_model_history()[0]["content"], "问题")
        self.assertEqual(store.get_active_path()[-1]["id"], metadata["id"])
        self.assertEqual(user["type"], "user_message")
```

- [x] **Step 4: 跑非法选择测试确认红灯**

运行：`python -m unittest -v tests.test_branches.SessionBranchTests.test_selection_rejects_unknown_id_and_non_chat_type`

预期：因分支选择和聊天记录筛选未实现而失败。

- [x] **Step 5: 实现活动路径接口**

实现父指针回溯并返回根到叶子的副本；只将 `user_message`、`assistant_message`、`tool_result` 放进模型历史；选择节点时验证类型并追加父节点为目标 ID 的 `branch_selection` 记录。文本树按聊天消息显示，跳过运行/操作元数据但保留最近的可见聊天祖先关系。每条返回的消息使用独立字典，调用方修改历史不能改写 SessionStore 内部索引。

- [x] **Step 6: 验证路径、树和工具协议**

增加测试：

```python
    def test_model_history_preserves_tool_call_and_matching_result(self):
        store = SessionStore(self.session_path)
        user = store.append("user_message", {"role": "user", "content": "读文件"})
        call = {"id": "call-1", "name": "read_file", "arguments": {"path": "README.md"}}
        assistant = store.append(
            "assistant_message",
            {"role": "assistant", "content": None, "tool_call": call},
            parent_id=user["id"],
        )
        result = {"role": "tool", "tool_call_id": "call-1", "content": "文件内容"}
        store.append("tool_result", result, parent_id=assistant["id"])

        history = store.get_model_history()
        self.assertEqual(history[1]["tool_call"]["id"], "call-1")
        self.assertEqual(history[2]["tool_call_id"], "call-1")

    def test_render_tree_shows_both_chat_branches_without_run_metadata(self):
        store = SessionStore(self.session_path)
        root = store.append("user_message", {"role": "user", "content": "共同问题"})
        store.append("run_context", {"run_id": "hidden-run"}, parent_id=root["id"])
        store.append(
            "assistant_message", {"role": "assistant", "content": "A 答案"},
            parent_id=root["id"],
        )
        store.append(
            "assistant_message", {"role": "assistant", "content": "B 答案"},
            parent_id=root["id"],
        )

        rendered = store.render_tree()
        self.assertIn("共同问题", rendered)
        self.assertIn("A 答案", rendered)
        self.assertIn("B 答案", rendered)
        self.assertNotIn("hidden-run", rendered)
        self.assertLess(rendered.index("共同问题"), rendered.index("A 答案"))
        self.assertLess(rendered.index("共同问题"), rendered.index("B 答案"))
        a_line = next(line for line in rendered.splitlines() if "A 答案" in line)
        b_line = next(line for line in rendered.splitlines() if "B 答案" in line)
        self.assertTrue(a_line.startswith("  "))
        self.assertTrue(b_line.startswith("  "))
```

树输出每深入一层缩进两个空格，便于断言共同用户节点是两条回答的父级。

把路径、非法选择、工具历史和渲染测试都放在该类。随后运行 `python -m unittest -v tests.test_branches.SessionBranchTests`，预期通过。

---

### Task 3: 让 Agent 请求只携带活动分支

**Files:**
- Modify: `agent.py`
- Modify: `tests/test_branches.py`
- Regression: `tests/test_agent.py`, `tests/test_sessions.py`

**Interfaces:**
- Consumes: Task 2 的 `SessionStore.get_model_history()`。
- Produces: `run_agent(question, model_client=client, session_store=store)` 把活动分支消息和本轮新问题传给 `model_client.respond()`；未传 SessionStore 的行为不变。

- [x] **Step 1: 写 Agent 上下文隔离测试**

```python
class CaptureFinalClient:
    def __init__(self):
        self.messages = None

    def respond(self, messages):
        self.messages = [dict(message) for message in messages]
        return {"type": "final", "content": "分支 A 已继续。"}

class AgentBranchTests(TemporarySessionTestCase):
    def test_run_agent_sends_only_selected_branch_to_model(self):
        store = SessionStore(self.session_path)
        root = store.append("user_message", {"role": "user", "content": "共同问题"})
        answer_a = store.append(
            "assistant_message", {"role": "assistant", "content": "A 私有内容"},
            parent_id=root["id"],
        )
        store.append(
            "assistant_message", {"role": "assistant", "content": "B 私有内容"},
            parent_id=root["id"],
        )
        store.select_active_node(answer_a["id"])
        client = CaptureFinalClient()

        result = run_agent("继续 A", model_client=client, session_store=store)

        contents = [message.get("content") for message in client.messages]
        self.assertEqual(result["status"], "completed")
        self.assertIn("A 私有内容", contents)
        self.assertNotIn("B 私有内容", contents)
        self.assertEqual(contents[-1], "继续 A")
```

把 `CaptureFinalClient` 定义在 `tests/test_branches.py`，并添加 `from agent import run_agent`。把上面的测试放入继承 `TemporarySessionTestCase` 的 `AgentBranchTests`。

- [x] **Step 2: 跑测试确认红灯**

运行：`python -m unittest -v tests.test_branches.AgentBranchTests.test_run_agent_sends_only_selected_branch_to_model`

预期：当前 `run_agent` 只把本轮用户问题发给 Provider，故缺少 A 上下文并按断言失败。

- [x] **Step 3: 接入活动历史**

在 `run_agent` 追加本轮 `run_context` 之前，复制 `session_store.get_model_history()`；初始化 `history` 时把该副本接上本轮用户消息。后续现有 assistant/tool 消息继续经原 `persist_entry` 追加。无 SessionStore 时使用空的历史前缀，预算、事件、取消和工具调用逻辑保持原状。

- [x] **Step 4: 跑 Agent 与会话回归**

运行：`python -m unittest -v tests.test_branches.AgentBranchTests tests.test_agent tests.test_sessions`

预期：分支隔离、工具 ID/结果往返、Mock 完成路径和原 SessionStore 行为全部通过；不调用真实模型。

---

### Task 4: 让恢复遵守活动路径但保留全局副作用审计

**Files:**
- Modify: `agent.py`
- Modify: `tests/test_recovery.py`
- Modify: `tests/test_branches.py`

**Interfaces:**
- Consumes: `SessionStore.load()` 的完整日志、`get_active_path()` 的当前分支和 L17 `_inspect_pending_operations()`。
- Produces: 活动路径限定对话重建与当前运行状态；完整日志继续决定待核对写入操作。

- [x] **Step 1: 写跨分支恢复测试**

在 `tests/test_recovery.py` 的 `RecoveryTests` 中添加下面两个行为测试。第一个确认导航到旧消息后不会把已经完成的祖先运行误报为待恢复运行：

```python
    def test_branch_selection_does_not_resume_completed_ancestor_run(self):
        self.store.append(
            "run_context",
            {
                "run_id": "completed-before-branch",
                "workspace": str(READ_ONLY_ROOT.resolve()),
                "permission_profile": "read_only",
                "provider": "MockModelClient",
            },
        )
        user = self.store.append(
            "user_message", {"role": "user", "content": "旧用户请求"}
        )
        self.store.append(
            "assistant_message", {"role": "assistant", "content": "旧最终回答"}
        )
        self.store.append("run_status", {"status": "completed"})
        self.store.select_active_node(user["id"])

        recovered = recover_agent(
            self.store,
            model_client=MockModelClient(),
            workspace_root=READ_ONLY_ROOT,
            permission_profile="read_only",
        )

        self.assertEqual(recovered["status"], "recovery_required")
        self.assertEqual(recovered["reason"], "branch_selected")
        self.assertEqual(
            [message["content"] for message in recovered["history"]],
            ["旧用户请求"],
        )

    def test_recovery_uses_active_messages_but_checks_all_branch_operations(self):
        shared = self.store.append(
            "user_message", {"role": "user", "content": "共同任务"}
        )
        call = {"id": "edit-on-a", "name": "propose_edit", "arguments": {}}
        answer_a = self.store.append(
            "assistant_message",
            {"role": "assistant", "content": None, "tool_call": call},
            parent_id=shared["id"],
        )
        self.store.append("tool_call", call, parent_id=answer_a["id"])
        self.store.record_operation_intent(
            "operation-on-a",
            {
                "ok": True,
                "proposal_id": "proposal-on-a",
                "path": "sample.py",
                "old_sha256": None,
                "new_sha256": "a" * 64,
            },
            tool_call_id=call["id"],
            workspace_root=self.root,
        )
        answer_b = self.store.append(
            "assistant_message",
            {"role": "assistant", "content": "B 私有回答"},
            parent_id=shared["id"],
        )
        self.store.select_active_node(answer_b["id"])

        recovered = recover_agent(
            self.store,
            model_client=MockModelClient(),
            workspace_root=self.root,
            permission_profile="read_only",
        )

        contents = [message.get("content") for message in recovered["history"]]
        self.assertIn("B 私有回答", contents)
        self.assertNotIn(None, contents[1:])
        self.assertEqual(
            recovered["pending_operations"][0]["operation_id"], "operation-on-a"
        )
```

- [x] **Step 2: 跑测试确认红灯**

运行：`python -m unittest -v tests.test_recovery.RecoveryTests.test_branch_selection_does_not_resume_completed_ancestor_run tests.test_recovery.RecoveryTests.test_recovery_uses_active_messages_but_checks_all_branch_operations`

预期：旧恢复逻辑按物理顺序纳入被切换掉的祖先运行/兄弟分支，至少一个行为断言按预期失败；不能以导入或语法错误作为红灯。

- [x] **Step 3: 改用活动路径恢复聊天**

在 `recover_agent` 中用 `get_active_path()` 构造路径内的 `run_context`、消息、调用、结果和状态；分支导航标记之后若没有新运行上下文，不得把导航前的运行误判为当前可恢复运行。工具结果重建仍绑定原 `tool_call_id`，只读重试预算及 Provider/工作区/权限比较保持原规则。

- [x] **Step 4: 保持写入账本全局扫描**

将完整 `entries = session_store.load()` 继续传给 `_inspect_pending_operations()`；不要改成活动路径。新增回归断言证明未知结果/待确认操作不会因切换聊天分支而消失，也不触发写入执行。

- [x] **Step 5: 跑恢复、分支与编码流程回归**

运行：`python -m unittest -v tests.test_recovery tests.test_branches tests.test_coding_flow tests.test_sessions`

预期：旧 L17 恢复、操作账本冲突保护、新 L18 活动分支恢复都通过；不发起真实模型请求或执行写入工具。

---

### Task 5: 更新学习进度并完成全量验证

**Files:**
- Modify: `mds/PROGRESS.md`
- Verify: `sessions.py`, `agent.py`, `tests/test_branches.py`, related tests

- [x] **Step 1: 运行整个标准库测试套件**

运行：`python -m unittest discover -s tests -v`

记录实际测试总数、通过/失败/跳过数量及退出码；出现的失败要按名称报告，不得只报告定向测试。

- [x] **Step 2: 运行静态完整性检查**

运行：`python -m py_compile sessions.py agent.py tests/test_branches.py tests/test_recovery.py tests/test_sessions.py`，再运行 `git diff --check`。将两条命令的实际结果记录下来。

- [x] **Step 3: 更新 PROGRESS.md 的 L18 记录**

明确记录：用户自述/教学确认、已查看的实现、代码变更位置、实际验证命令及结果、真实模型未调用、文件回退未实现、理解复述待核对，以及下一小步。只标记本轮确实实现并验证的内容，不预先标记后续课程完成。

- [x] **Step 4: 检查最终差异并交付导读**

检查 `git status --short` 和 `git diff --check`，确认未改动无关用户文件且没有提交。向学习者按“旧线性链 → 父图 → 活动路径 → 模型历史 → 分支恢复 → 工作区不会回滚”的顺序逐段讲解，给出关键函数、调用顺序、测试覆盖和实际验证边界。

---

## 执行顺序和建议

Task 1 → Task 2 → Task 3 → Task 4 → Task 5。建议选择 **Native（由当前助教逐任务实现）**：存储图、Agent 历史和 L17 恢复的接口高度耦合，连续实现更利于保持安全语义并在最终导读中串起完整调用链。开始实现前，用户需审阅本计划并确认执行方式。
