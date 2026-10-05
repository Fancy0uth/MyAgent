# MyAgent：可验证的学习版 Agent

这是一个从“模型调用工具”逐步扩展到事件记录、会话恢复、流式输出、取消、RPC 和确定性评测的学习项目。默认运行不访问网络；只有明确加上 `--real` 才会创建 DeepSeek 客户端。

## 环境

```bash
conda activate project
python -m pip install openai python-dotenv
```

真实模式从项目根目录的 `.env` 读取 `DEEPSEEK_API_KEY`。密钥只放在本地环境，不要提交到 Git。

## 运行入口

默认 Mock 模式：

```bash
python main.py
```

真实 DeepSeek 模式（会产生 API 消耗）：

```bash
python main.py --real
```

交互模式和持久化会话：

```bash
python main.py --interactive --session runs/learning-session.jsonl
```

可以通过 `--max-model-requests` 和 `--max-tool-executions` 限制预算。`main.py` 只是入口，实际循环在 `agent.py` 的 `run_agent()` 中。

## 自定义 JSONL RPC

这是本项目为学习协议设计的 RPC，不宣称兼容 Pi 原生 RPC。它要求一行一个 JSON 对象，支持 `prompt`、`get_state` 和 `abort`：

```bash
python -c 'import sys; from rpc import RpcServer; RpcServer().serve(sys.stdin.buffer, sys.stdout.buffer)' <<'EOF'
{"id":"state-1","type":"get_state","payload":{}}
EOF
```

RPC 标准输出只放协议消息；调试信息不能混入 stdout。`rpc_client.py` 是注入式 Python client，测试时不会直接绕过协议调用 Agent。

## 评测与兼容性

答案评测：

```bash
python evals/run_evals.py
python evals/run_evals.py --real --limit 1
```

确定性能力矩阵：

```bash
python evals/run_compatibility.py --mode myagent
python evals/run_compatibility.py --mode pi
```

矩阵结果追加保存到 `docs/compatibility_results/`，不会覆盖以前的记录。`not_run` 不是通过，Pi 原生结果还受到固定 checkout 构建阻塞的影响，详见 `docs/pi-reference/L27_RUN.md`。

## 测试

```bash
python -m unittest discover -s tests -p 'test_*.py' -v
python -m py_compile rpc.py rpc_client.py ui.py main.py evals/run_compatibility.py
git diff --check
```

测试默认只使用 Mock、临时目录和只读练习资料，不调用真实模型。

## 目录职责

- `main.py`：命令行入口、模式选择和交互入口。
- `agent.py`：模型—工具多轮循环、历史、预算、取消、压缩、会话和扩展管理。
- `model_client.py`：Mock 与 DeepSeek 客户端适配。
- `tools.py`：练习资料的只读工具和 JSON 分发。
- `events.py`：统一事件记录器。
- `rpc.py`、`rpc_client.py`：本项目自定义 JSONL RPC。
- `evals/`：答案评测和确定性兼容性矩阵。
- `native_practice/`：Pi v0.85.1 对照练习，不会自动加载到本项目。
- `docs/`：课程设计、运行证据、能力映射和失败复盘。
