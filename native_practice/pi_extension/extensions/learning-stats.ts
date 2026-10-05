/**
 * L28：一个显式加载的 Pi 原生扩展示例。
 *
 * 这个扩展只做三件事：
 * 1. 监听 agent 和 tool 的生命周期，显示最小运行统计；
 * 2. 注册一个只读的 learning_read 工具；
 * 3. 在 write/edit 工具执行前拦截 tests/、.env 和 .git/。
 *
 * 它没有 shell、网络、任意代码执行能力，也不会自动加入 Pi 的扩展目录。
 * 需要通过 `pi -e <这个文件>` 显式加载。
 */

import { readFile } from "node:fs/promises";
import { isAbsolute, relative, resolve, sep } from "node:path";

import type { ExtensionAPI, ExtensionContext } from "@earendil-works/pi-coding-agent";

const MAX_LINES = 80;

export interface ReadArguments {
	path: string;
	start_line?: number;
	end_line?: number;
}

export interface ReadDetails {
	ok: boolean;
	path?: string;
	start_line?: number;
	end_line?: number;
	content?: string;
	truncated?: boolean;
	error?: string;
	message?: string;
}

interface ResolvedPath {
	ok: true;
	absolutePath: string;
	relativePath: string;
}

interface PathError {
	ok: false;
	error: "INVALID_PATH" | "PATH_NOT_ALLOWED";
	message: string;
}

/**
 * 把用户给的相对路径限制在当前工作目录内。
 * 这里使用 path.relative 判断，而不是简单地查找字符串 ".."，
 * 因为绝对路径和不同平台的路径分隔符都需要统一处理。
 */
export function resolveWorkspacePath(cwd: string, rawPath: unknown): ResolvedPath | PathError {
	if (typeof rawPath !== "string" || rawPath.trim() === "") {
		return { ok: false, error: "INVALID_PATH", message: "path 必须是非空字符串" };
	}

	const root = resolve(cwd);
	const absolutePath = resolve(root, rawPath);
	const relativePath = relative(root, absolutePath);
	const isOutside = relativePath === ".." || relativePath.startsWith(`..${sep}`) || isAbsolute(relativePath);

	if (isOutside) {
		return { ok: false, error: "PATH_NOT_ALLOWED", message: "只能读取当前工作目录内的文件" };
	}

	return { ok: true, absolutePath, relativePath: relativePath || "." };
}

function errorResult(error: string, message: string): { content: [{ type: "text"; text: string }]; details: ReadDetails } {
	const details: ReadDetails = { ok: false, error, message };
	return {
		content: [{ type: "text", text: JSON.stringify(details) }],
		details,
	};
}

/**
 * 执行只读工具的核心逻辑，单独导出便于离线测试，不依赖真实 Pi 进程。
 */
export async function readWorkspaceText(
	cwd: string,
	args: ReadArguments,
): Promise<{ content: [{ type: "text"; text: string }]; details: ReadDetails }> {
	const resolved = resolveWorkspacePath(cwd, args.path);
	if (!resolved.ok) return errorResult(resolved.error, resolved.message);

	const startLine = args.start_line ?? 1;
	const endLine = args.end_line ?? startLine + MAX_LINES - 1;
	if (!Number.isInteger(startLine) || !Number.isInteger(endLine) || startLine < 1 || endLine < startLine) {
		return errorResult("INVALID_RANGE", "行号必须是正整数，并且 end_line 不能小于 start_line");
	}
	if (endLine - startLine + 1 > MAX_LINES) {
		return errorResult("INVALID_RANGE", `一次最多读取 ${MAX_LINES} 行`);
	}

	let text: string;
	try {
		text = await readFile(resolved.absolutePath, "utf8");
	} catch {
		return errorResult("READ_FAILED", `无法读取文件：${resolved.relativePath}`);
	}

	const lines = text.split(/\r?\n/);
	if (startLine > lines.length) {
		return errorResult("LINE_OUT_OF_RANGE", `起始行超出文件范围：${startLine}`);
	}

	const actualEndLine = Math.min(endLine, lines.length);
	const content = lines
		.slice(startLine - 1, actualEndLine)
		.map((line, index) => `${startLine + index}: ${line}`)
		.join("\n");
	const details: ReadDetails = {
		ok: true,
		path: resolved.relativePath,
		start_line: startLine,
		end_line: actualEndLine,
		content,
		truncated: actualEndLine < endLine,
	};

	return {
		content: [{ type: "text", text: content }],
		details,
	};
}

function protectedWriteReason(cwd: string, rawPath: unknown): string | undefined {
	const resolved = resolveWorkspacePath(cwd, rawPath);
	if (!resolved.ok) return "只能写入当前工作目录内的文件";

	const path = resolved.relativePath.replaceAll(sep, "/");
	if (path === ".env" || path.startsWith(".git/") || path === ".git" || path === "tests" || path.startsWith("tests/")) {
		if (path === ".env") return "Writes to .env are blocked by learning-stats";
		if (path === ".git" || path.startsWith(".git/")) return "Writes under .git/ are blocked by learning-stats";
		return "Writes under tests/ are blocked by learning-stats";
	}
	return undefined;
}

/**
 * Pi 的工具参数最终需要一个 JSON Schema。
 * 这里直接写最小 schema，避免扩展本身额外引入运行时依赖；
 * Pi v0.85.1 的 registerTool 会按这个 schema 校验参数。
 */
const readParameters = {
	type: "object",
	properties: {
		path: { type: "string", description: "当前工作目录内的相对文件路径" },
		start_line: { type: "integer", minimum: 1 },
		end_line: { type: "integer", minimum: 1 },
	},
	required: ["path"],
	additionalProperties: false,
} as any;

export default function learningStatsExtension(pi: ExtensionAPI): void {
	let agentRuns = 0;
	let toolRuns = 0;

	// 生命周期事件只更新内存统计，不写文件、不调用模型。
	pi.on("agent_start", async (_event, ctx) => {
		agentRuns += 1;
		ctx.ui.setStatus("learning-stats", `agent runs: ${agentRuns}, tools: ${toolRuns}`);
	});

	pi.on("tool_execution_end", async (_event, ctx) => {
		toolRuns += 1;
		ctx.ui.setStatus("learning-stats", `agent runs: ${agentRuns}, tools: ${toolRuns}`);
	});

	// 只读工具：它只读当前 cwd 下的文本，不提供 write、bash 或网络能力。
	pi.registerTool({
		name: "learning_read",
		label: "Learning Read",
		description: "Read a small line range from a text file inside the current working directory.",
		parameters: readParameters,
		async execute(_toolCallId, params, _signal, _onUpdate, ctx) {
			return readWorkspaceText(ctx.cwd, params as ReadArguments);
		},
	});

	// tool_call 在真正执行 write/edit 之前触发，因此这里可以安全地阻断受保护路径。
	pi.on("tool_call", async (event, ctx: ExtensionContext) => {
		if (event.toolName !== "write" && event.toolName !== "edit") return undefined;
		const reason = protectedWriteReason(ctx.cwd, event.input.path);
		return reason ? { block: true, reason } : undefined;
	});
}
