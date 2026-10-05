"""L26：供测试和示例使用的最小 RPC 客户端适配器。"""

from __future__ import annotations

from typing import Any

from rpc import RpcServer, decode_request, encode_message


class RpcClient:
    """只负责协议编解码和传递请求，不复制 Agent 执行循环。"""

    def __init__(self, server: RpcServer):
        if not isinstance(server, RpcServer):
            raise TypeError("server 必须是 RpcServer")
        self.server = server

    def request(self, message: dict[str, Any]) -> list[dict]:
        """把请求经过 JSONL 编解码后交给注入的服务端。"""
        wire_message = encode_message(message)
        request = decode_request(wire_message)
        responses = self.server.handle_request(request)
        responses.extend(self.server.poll())
        return responses

    def poll(self) -> list[dict]:
        """读取服务端已经产生的事件或结果。"""
        return self.server.poll()
