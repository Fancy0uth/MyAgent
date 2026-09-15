"""只读练习资料工具，以及统一的工具调用分发器。"""

from pathlib import Path, PureWindowsPath
from typing import Any, Callable, Dict, List, Optional, Tuple
import json


ROOT = Path(__file__).resolve().parent / "sample_repo"
ALLOWED_FILES = {"README.md"}
MAX_FILE_BYTES = 256 * 1024
MAX_READ_LINES = 80
MAX_SEARCH_MATCHES = 50
MAX_RESULT_CHARS = 12_000


def _failure(error: str, message: str) -> dict:
    return {"ok": False, "error": error, "message": message}


def _resolve_allowed_file(path: Any) -> Tuple[Optional[Path], Optional[dict]]:
    """校验路径，并返回允许访问的文件路径。"""
    if not isinstance(path, str) or not path.strip():
        return None, _failure("INVALID_ARGUMENT", "path 必须是非空字符串")

    raw_path = path.strip()#返回一个去除首尾空白字符的字符串副本。
    posix_path = Path(raw_path)
    windows_path = PureWindowsPath(raw_path)

    if (
        posix_path.is_absolute()
        or windows_path.is_absolute()
        or bool(windows_path.drive)
    ):
        return None, _failure("ACCESS_DENIED", "不允许使用绝对路径")

    if ".." in posix_path.parts or ".." in windows_path.parts:
        return None, _failure("ACCESS_DENIED", "不允许使用上级目录路径")

    try:
        candidate = ROOT / posix_path
        relative_path = candidate.relative_to(ROOT).as_posix()
        resolved_root = ROOT.resolve()
        resolved_candidate = candidate.resolve()
    except (OSError, RuntimeError, ValueError):
        return None, _failure("ACCESS_DENIED", "路径无法安全解析")

    if (
        resolved_candidate != resolved_root
        and resolved_root not in resolved_candidate.parents
    ):
        return None, _failure("ACCESS_DENIED", "目标路径超出允许目录")

    current = ROOT
    for part in posix_path.parts:
        if part in ("", "."):
            continue
        current = current / part
        if current.is_symlink():
            return None, _failure("ACCESS_DENIED", "不允许读取符号链接")

    if relative_path not in ALLOWED_FILES:
        return None, _failure("ACCESS_DENIED", "目标文件不在允许清单中")

    if not candidate.exists() or not candidate.is_file():
        return None, _failure("FILE_NOT_FOUND", "允许范围内的目标文件不存在")

    return candidate, None


def _read_allowed_text(path: Any) -> Tuple[Optional[str], Optional[dict], Optional[str]]:
    file_path, error = _resolve_allowed_file(path)#调用前面一个写好的函数
    if error is not None:
        return None, error, None

    assert file_path is not None
    try:
        if file_path.stat().st_size > MAX_FILE_BYTES:
            return None, _failure("FILE_TOO_LARGE", "文件超过 256 KiB 限制"), None
        text = file_path.read_text(encoding="utf-8")
        display_path = file_path.relative_to(ROOT).as_posix()
        return text, None, display_path
    except UnicodeError:
        return None, _failure("READ_ERROR", "文件不是可读取的 UTF-8 文本"), None
    except OSError:
        return None, _failure("READ_ERROR", "读取文件失败"), None


def _result_size(result: dict) -> int:
    return len(json.dumps(result, ensure_ascii=False, separators=(",", ":")))


def _fit_result_size(result: dict) -> dict:
    """限制成功结果大小，同时保留真实的行号和匹配顺序。"""
    if _result_size(result) <= MAX_RESULT_CHARS:
        return result

    result["truncated"] = True

    if "content" in result:
        original_content = result["content"]
        low, high = 0, len(original_content)
        best = ""
        while low <= high:
            middle = (low + high) // 2
            candidate = dict(result)
            candidate["content"] = original_content[:middle]
            if _result_size(candidate) <= MAX_RESULT_CHARS:
                best = candidate["content"]
                low = middle + 1
            else:
                high = middle - 1
        result["content"] = best
        return result

    if "matches" in result:
        result["matches"] = list(result["matches"])
        while _result_size(result) > MAX_RESULT_CHARS and result["matches"]:
            largest = max(
                result["matches"],
                key=lambda match: len(match.get("text", "")),
            )
            text = largest.get("text", "")
            if text:
                largest["text"] = text[: max(0, len(text) // 2)]
            else:
                result["matches"].pop()
        return result

    return result


def _valid_line_number(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 1


def read_file(path: str, start_line: int = 1, end_line: int = 80) -> dict:
    """读取允许清单中的文件片段，行号从 1 开始且包含起止行。"""
    if not _valid_line_number(start_line) or not _valid_line_number(end_line):
        return _failure("INVALID_ARGUMENT", "行号必须是大于或等于 1 的整数")
    if end_line < start_line:
        return _failure("INVALID_RANGE", "结束行不能小于起始行")
    if end_line - start_line + 1 > MAX_READ_LINES:
        return _failure("INVALID_RANGE", "一次最多读取 80 行")

    text, error, display_path = _read_allowed_text(path)
    if error is not None:
        return error

    assert text is not None
    assert display_path is not None
    lines = text.splitlines()

    if not lines:
        return {
            "ok": True,
            "path": display_path,
            "start_line": None,
            "end_line": None,
            "content": "",
            "truncated": False,
        }

    if start_line > len(lines):
        return _failure("INVALID_RANGE", "起始行超过文件实际行数")

    actual_end = min(end_line, len(lines))
    content = "\n".join(
        f"{number}: {lines[number - 1]}"
        for number in range(start_line, actual_end + 1)
    )
    result = {
        "ok": True,
        "path": display_path,
        "start_line": start_line,
        "end_line": actual_end,
        "content": content,
        "truncated": False,
    }
    return _fit_result_size(result)


def list_files() -> dict:
    """列出允许清单中实际存在的普通文件。"""
    files: List[str] = []
    for relative_path in sorted(ALLOWED_FILES):
        file_path = ROOT / relative_path
        if file_path.is_symlink():
            continue
        if file_path.is_file():
            files.append(relative_path)

    return {"ok": True, "files": files, "truncated": False}


def search_text(query: str) -> dict:
    """在允许清单中进行区分大小写的字面字符串搜索。"""
    if not isinstance(query, str) or not query.strip():
        return _failure("INVALID_ARGUMENT", "query 必须是非空字符串")

    matches: List[dict] = []
    truncated = False
    for relative_path in sorted(ALLOWED_FILES):
        text, error, display_path = _read_allowed_text(relative_path)
        if error is not None:
            return error

        assert text is not None
        assert display_path is not None
        for line_number, line in enumerate(text.splitlines(), start=1):
            if query in line:
                if len(matches) >= MAX_SEARCH_MATCHES:
                    truncated = True
                    break
                matches.append(
                    {
                        "path": display_path,
                        "line": line_number,
                        "text": line,
                    }
                )
        if truncated:
            break

    result = {"ok": True, "matches": matches, "truncated": truncated}
    return _fit_result_size(result)


Tool = Callable[..., dict]
TOOL_REGISTRY: Dict[str, Tool] = {
    "read_file": read_file,
    "list_files": list_files,
    "search_text": search_text,
}


def dispatch_tool(request_json: str) -> dict:
    """解析一条 JSON 调用，并根据 name 执行已注册工具。"""
    if not isinstance(request_json, str):
        return _failure("INVALID_ARGUMENT", "工具调用必须是 JSON 字符串")

    try:
        request = json.loads(request_json)
    except json.JSONDecodeError:
        return _failure("INVALID_ARGUMENT", "工具调用不是合法的 JSON")

    if not isinstance(request, dict):
        return _failure("INVALID_ARGUMENT", "JSON 顶层必须是对象")

    call_id = request.get("id")
    name = request.get("name")
    arguments = request.get("arguments")

    if not isinstance(call_id, str) or not call_id.strip():
        return _failure("INVALID_ARGUMENT", "id 必须是非空字符串")
    if not isinstance(name, str) or not name.strip():
        return {
            "ok": False,
            "call_id": call_id,
            **_failure("INVALID_ARGUMENT", "name 必须是非空字符串"),
        }
    if name not in TOOL_REGISTRY:
        return {
            "ok": False,
            "call_id": call_id,
            **_failure("UNKNOWN_TOOL", f"未知工具：{name}"),
        }
    if not isinstance(arguments, dict):
        return {
            "ok": False,
            "call_id": call_id,
            **_failure("INVALID_ARGUMENT", "arguments 必须是对象"),
        }

    tool = TOOL_REGISTRY[name]
    try:
        tool_result = tool(**arguments)
    except TypeError:
        return {
            "ok": False,
            "call_id": call_id,
            **_failure("INVALID_ARGUMENT", "工具参数不正确"),
        }

    if not tool_result.get("ok", False):
        return {
            "ok": False,
            "call_id": call_id,
            "error": tool_result.get("error", "TOOL_ERROR"),
            "message": tool_result.get("message", "工具执行失败"),
            "result": tool_result,
        }

    return {"ok": True, "call_id": call_id, "result": tool_result}
