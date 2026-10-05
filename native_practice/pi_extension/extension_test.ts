import assert from "node:assert/strict";
import { mkdtemp, mkdir, readFile, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";

import extension from "./extensions/learning-stats.ts";

type Handler = (event: any, context: any) => unknown;

const handlers = new Map<string, Handler>();
const tools: any[] = [];

const fakePi = {
	on(name: string, handler: Handler) {
		handlers.set(name, handler);
	},
	registerTool(tool: any) {
		tools.push(tool);
	},
};

const context = (cwd: string) => ({
	cwd,
	ui: {
		setStatus() {},
		notify() {},
	},
});

async function main(): Promise<void> {
	extension(fakePi as any);

	assert.equal(handlers.has("agent_start"), true);
	assert.equal(handlers.has("tool_execution_end"), true);
	assert.equal(handlers.has("tool_call"), true);
	assert.equal(tools.length, 1);
	assert.equal(tools[0].name, "learning_read");

	const cwd = await mkdtemp(join(tmpdir(), "myagent-extension-"));
	await mkdir(join(cwd, "tests"));
	await writeFile(join(cwd, "notes.txt"), "第一行\n第二行\n第三行\n", "utf8");

	const toolResult = await tools[0].execute(
		"tool-1",
		{ path: "notes.txt", start_line: 2, end_line: 2 },
		undefined,
		undefined,
		context(cwd),
	);
	assert.match(toolResult.content[0].text, /2: 第二行/);
	assert.equal(toolResult.details.ok, true);

	const outsideResult = await tools[0].execute(
		"tool-2",
		{ path: "../outside.txt", start_line: 1, end_line: 1 },
		undefined,
		undefined,
		context(cwd),
	);
	assert.equal(outsideResult.details.ok, false);
	assert.equal(outsideResult.details.error, "PATH_NOT_ALLOWED");

	const blockResult = await handlers.get("tool_call")?.(
		{ toolName: "write", input: { path: "tests/result.txt" } },
		context(cwd),
	);
	assert.deepEqual(blockResult, {
		block: true,
		reason: "Writes under tests/ are blocked by learning-stats",
	});

	await handlers.get("agent_start")?.({ type: "agent_start" }, context(cwd));
	await handlers.get("tool_execution_end")?.({ type: "tool_execution_end" }, context(cwd));
	assert.equal((await readFile(join(cwd, "notes.txt"), "utf8")).includes("第一行"), true);

	console.log("native extension probe passed");
}

void main().catch((error: unknown) => {
	console.error(error);
	process.exitCode = 1;
});
