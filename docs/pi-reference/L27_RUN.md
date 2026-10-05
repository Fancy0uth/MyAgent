# L27 Pi v0.85.1 运行记录

## 参考环境

- 来源：`https://github.com/earendil-works/pi.git`
- Tag：`v0.85.1`
- Checkout：`../pi-reference`（相对 MyAgent 项目目录）
- `git describe --tags --always --dirty`：`v0.85.1`
- Node：`v24.14.0`
- npm：`11.9.0`
- Checkout 初始状态：干净

## 已执行

```text
npm ci --ignore-scripts                 PASS
```

安装输出报告了 npm 配置警告、弃用依赖提示和 12 个审计漏洞（3 moderate、9 high）；本课没有运行 `npm audit fix`，避免改变固定依赖。

```text
<PI_CHECKOUT>/node_modules/.bin/tsc \
  --noEmit --strict --target ES2022 --module NodeNext \
  --moduleResolution NodeNext native_practice/typescript/agent_protocol.ts
```

结果：PASS（无输出、退出码 0）。

## 已验证或阻塞

```text
npm run build:offline                 BLOCKED
```

首次错误是 `packages/ai/src/providers/data/amazon-bedrock.json` 不存在。随后按 Pi 自己的提示执行了官方补救命令 `npm run hydrate:model-data`，但它又因缺少 `kimi-coding` provider 数据失败：`Cannot hydrate missing providers: kimi-coding`。

没有手工伪造模型数据，也没有把这个结果记录成构建通过。

```text
npm test --workspace=@earendil-works/pi-agent-core -- test/agent-loop.test.ts
```

结果：BLOCKED。测试尚未进入任何用例，导入阶段报错：`Cannot find package '@earendil-works/pi-ai/utils/uuid'`。这说明当前 checkout 尚未生成可供 workspace 互相导入的内部构建产物。

因此，本文件中的 PASS 只代表固定 checkout、依赖安装和独立 TypeScript 类型练习已验证；Pi 的完整离线构建和原生 agent-loop 测试仍需补齐官方模型数据与 workspace 构建链后复测。
