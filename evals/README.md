# 评测目录

## 答案评测

`run_evals.py` 读取 `tasks.jsonl`，检查 Agent 回答是否包含任务要求的证据。默认使用 Mock；只有明确加 `--real` 才会调用 DeepSeek。

```bash
python evals/run_evals.py
python evals/run_evals.py --real --limit 1
```

每个任务的摘要追加到 `runs/eval_runs.jsonl`。记录只保存状态、计数和耗时，不保存 API 密钥，也不默认保存完整对话内容。

## 确定性兼容性矩阵

`compatibility_tasks.jsonl` 和 `run_compatibility.py` 比较协议、事件、取消和错误边界，不比较自由文本答案：

```bash
python evals/run_compatibility.py --mode myagent
python evals/run_compatibility.py --mode pi
```

状态 `not_run` 表示没有证据，不能当作通过。当前 Pi 原生构建阻塞记录见 `docs/pi-reference/L27_RUN.md`。
