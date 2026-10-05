# L29：能力兼容性矩阵

这张矩阵比较的是“可观察的工程行为”，不是模型回答的逐字相等。每次运行会把一条结果追加到 JSONL，不覆盖历史记录。

## 状态含义

| 状态 | 含义 |
| --- | --- |
| `fixed_contract_compatible` | 满足本项目固定的协议契约，适合做确定性比较 |
| `subset_verified` | 只验证了该能力的一个安全子集 |
| `not_run` | 当前没有运行该能力，不能当作通过 |
| `not_applicable` | 该能力不属于当前实现，或两边协议目标不同 |

## 运行

```bash
python evals/run_compatibility.py \
  --mode myagent \
  --task-file evals/compatibility_tasks.jsonl \
  --output docs/compatibility_results/myagent.jsonl

python evals/run_compatibility.py \
  --mode pi \
  --task-file evals/compatibility_tasks.jsonl \
  --output docs/compatibility_results/pi.jsonl
```

`myagent` 模式只使用 Mock、临时对象和只读资料；`pi` 模式当前会如实记录固定 Pi checkout 尚未完成构建的情况。

## 当前首轮结果

- MyAgent：1 项 `fixed_contract_compatible`、3 项 `subset_verified`、2 项 `not_run`、2 项 `not_applicable`。
- Pi v0.85.1：1 项 `not_applicable`、7 项 `not_run`；不是 Pi 通过。
- 详细 JSONL：`docs/compatibility_results/myagent.jsonl` 和 `docs/compatibility_results/pi.jsonl`。

## 设计边界

- 任务不发送真实 DeepSeek 请求；
- 不比较自由文本答案；
- 不把 MyAgent 的自定义 JSONL 协议宣称为 Pi 原生 RPC 兼容；
- Pi 原生结果只有在固定版本 fixture 实际执行后，才可以提升为 `subset_verified` 或 `fixed_contract_compatible`。
