# 源码与课程能力地图

状态只描述当前仓库或固定外部 checkout 的证据，不把“文件存在”当成“功能已经测试通过”。

| 能力 | MyAgent 位置 | Pi 对照位置 | 状态 | 说明 |
| --- | --- | --- | --- | --- |
| 模型—工具循环 | `agent.py` | `pi-reference/packages/agent/src/` | implemented / subset | MyAgent 有 Mock 和真实客户端；Pi 原生测试受构建阻塞 |
| 文件读取、列表、搜索 | `tools.py` | `pi-reference/packages/coding-agent/src/core/tools/` | implemented | 只读范围和错误码由各自实现定义 |
| 事件时间线 | `events.py`、`agent.py` | `pi-reference/packages/coding-agent/docs/extensions.md` | implemented / subset | Python 事件全链路已有单测；原生扩展有本地探针 |
| 取消与预算 | `agent.py`、`rpc.py` | `pi-reference/packages/agent/src/` | implemented / subset | Python Mock/RPC 已验证；Pi 原生未运行 |
| 会话、分支、压缩 | `sessions.py`、`context.py`、`agent.py` | `pi-reference/packages/coding-agent/src/core/session-manager.ts` | implemented / not_run | MyAgent 有独立单测，L29 runner 未重复创建持久化夹具 |
| 自定义 JSONL RPC | `rpc.py`、`rpc_client.py` | 不直接对应 | implemented | 明确是 MyAgent 自定义协议，不宣称 Pi 兼容 |
| Pi 原生 TypeScript 类型练习 | `native_practice/typescript/agent_protocol.ts` | 固定 tag `v0.85.1` | subset | 独立 `tsc --strict` 通过 |
| Pi 原生扩展 | `native_practice/pi_extension/extensions/learning-stats.ts` | `packages/coding-agent/docs/extensions.md` | subset / blocked | 本地行为探针通过；Pi CLI 显式加载因 checkout 构建阻塞 |
| 能力兼容性矩阵 | `evals/run_compatibility.py` | `docs/compatibility_results/pi.jsonl` | implemented / not_run | 只记录确定性观察，不比较自由文本 |
| 交付与失败复盘 | `README.md`、`docs/FAILURE_REVIEW.md` | `docs/pi-reference/` | implemented | 文档测试覆盖命令、证据状态和秘密值保护 |
