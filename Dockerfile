# L15 的项目专属执行镜像。
#
# 这个镜像只提供稳定的 Python + bash 环境，不把 MyAgent 源码复制进去。
# 任务开始时，再由 SandboxExecutor 只挂载指定的 /workspace。
FROM python:3.13-slim-bookworm

LABEL org.opencontainers.image.title="myagent-sandbox"
LABEL org.opencontainers.image.description="受控运行 MyAgent 测试和命令的基础环境"

# 明确安装 bash，保证容器内的命令解释器存在。
RUN apt-get update \
    && apt-get install --yes --no-install-recommends bash \
    && rm -rf /var/lib/apt/lists/*

# 使用普通用户运行命令，避免把容器内的 root 权限当成默认能力。
RUN groupadd --system agent \
    && useradd --system --gid agent --create-home --shell /bin/bash agent \
    && mkdir --parents /workspace \
    && chown --recursive agent:agent /workspace

WORKDIR /workspace
USER agent

# 让 Python 输出立即出现在 Agent 的 stdout/stderr 中，并减少临时字节码文件。
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

CMD ["bash"]
