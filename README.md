# Repo Reader Agent

这是一个只读的代码仓库问答 Agent 练习项目。Agent 可以列出文件、搜索文本、读取文件片段，并在回答中引用实际的文件路径和行号。

## 环境准备

请在项目根目录执行：

```bash
conda activate project
python -m pip install openai python-dotenv
```

在项目根目录创建 `.env`：

```text
DEEPSEEK_API_KEY=你的 DeepSeek API Key
```

`.env` 已加入 `.gitignore`，不要把密钥提交到 Git 或写进代码。

## 运行方式

默认使用第 4 关的离线模拟模型，不会访问网络：

```bash
python main.py
```

明确加上 `--real` 才会创建 DeepSeek 客户端并调用真实模型：

```bash
python main.py --real
```

也可以调整运行预算：

```bash
python main.py --real --max-model-requests 5 --max-tool-executions 10
```

程序启动时会显示当前模式、模型名称和预算。真实模式需要 `.env` 中存在 `DEEPSEEK_API_KEY`。

## 文件职责

- `main.py`：命令行入口和运行模式选择。
- `agent.py`：模型请求、工具执行、历史记录和预算控制。
- `model_client.py`：模拟模型与 DeepSeek 客户端的协议适配。
- `tools.py`：只读工具和 JSON 调用分发器。
- `sample_repo/`：被分析的练习资料，不是 Agent 自己的源码。
- `tests/`：离线自动测试，不会发起真实模型请求。

## 测试

```bash
python -m unittest discover -s tests -v
```

测试使用假模型响应验证真实客户端的消息转换，因此不会消耗 DeepSeek 额度。
