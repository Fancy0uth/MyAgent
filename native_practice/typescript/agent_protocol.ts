/**
 * L27：用 TypeScript 描述已经理解过的 Agent/RPC 数据结构。
 * 这里只练类型、Promise、回调和 AbortSignal，不重写 Python Agent。
 */

export type RpcCommand = "prompt" | "get_state" | "abort";

export interface AgentRequest {
  id: string;
  type: RpcCommand;
  payload: {
    question?: string;
    targetId?: string;
  };
}

export interface ToolCall {
  id: string;
  name: string;
  arguments: Record<string, unknown>;
}

export type AgentEvent =
  | { event: "tool_started"; toolCall: ToolCall }
  | { event: "tool_finished"; toolCallId: string; ok: boolean }
  | { event: "text_delta"; text: string }
  | { event: "finished"; status: "completed" | "failed" | "cancelled" };

export interface AbortableRunner {
  run(
    request: AgentRequest,
    signal: AbortSignal,
    onEvent?: (event: AgentEvent) => void,
  ): Promise<AgentEvent[]>;
}

export async function runWithAbort(
  runner: AbortableRunner,
  request: AgentRequest,
  signal: AbortSignal,
  onEvent?: (event: AgentEvent) => void,
): Promise<AgentEvent[]> {
  return runner.run(request, signal, onEvent);
}

export async function* streamFinishedEvents(
  events: AsyncIterable<AgentEvent>,
): AsyncIterable<AgentEvent> {
  for await (const event of events) {
    yield event;
    if (event.event === "finished") {
      return;
    }
  }
}
