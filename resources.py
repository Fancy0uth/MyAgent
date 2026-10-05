"""L23：显式加载本地 Skill 和固定 Prompt Template。

这个模块只读取项目内预先放置的文本资源。它不会 import Python 文件、执行
Shell，也不会因为读取了 Skill 就自动增加工具或权限。
"""

from __future__ import annotations

import re
from pathlib import Path, PureWindowsPath
from typing import Any, Optional


DEFAULT_MAX_CONTENT_CHARS = 12_000
_SAFE_NAME = re.compile(r"^[a-z][a-z0-9_-]*$")
_PLACEHOLDER = re.compile(r"{{\s*([A-Za-z_][A-Za-z0-9_]*)\s*}}")
_FORBIDDEN_DIRECTIVES = (
    "ignore all previous instructions",
    "忽略之前的指令",
    "execute shell",
    "执行 shell",
    "运行任意 python",
    "授予自己权限",
)


class ResourceLoadError(ValueError):
    """资源无法安全读取或渲染时抛出的稳定错误。"""

    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(f"{code}: {message}")


class ResourceCatalog:
    """从固定项目目录发现和加载文本资源。"""

    def __init__(
        self,
        project_root: Optional[Path | str] = None,
        *,
        max_content_chars: int = DEFAULT_MAX_CONTENT_CHARS,
    ) -> None:
        if (
            isinstance(max_content_chars, bool)
            or not isinstance(max_content_chars, int)
            or max_content_chars <= 0
        ):
            raise ResourceLoadError(
                "INVALID_LIMIT",
                "max_content_chars 必须是大于 0 的整数",
            )

        base = Path(__file__).resolve().parent if project_root is None else Path(project_root)
        self.project_root = base.resolve()
        self.skills_root = (self.project_root / "resources" / "skills").resolve()
        self.prompts_root = (self.project_root / "prompts").resolve()
        self.max_content_chars = max_content_chars

    def list_skills(self) -> list[dict]:
        """只返回 Skill 的元数据，不把全文放进结果。"""
        records = self._discover_skill_records()
        result = []
        seen_names: set[str] = set()
        for path in records:
            raw = self._read_text(path, self.skills_root)
            metadata, _body = _parse_skill_document(raw)
            _validate_skill_metadata(metadata)
            name = metadata["name"]
            if name in seen_names:
                raise ResourceLoadError(
                    "DUPLICATE_RESOURCE",
                    f"发现重复的 Skill 名称：{name}",
                )
            seen_names.add(name)
            result.append(
                {
                    "name": name,
                    "purpose": metadata["purpose"],
                    "allowed_paths": metadata["allowed_paths"],
                    "source": self._relative_source(path),
                }
            )
        return sorted(result, key=lambda item: item["name"])

    def load_skill(self, name: str) -> dict:
        """显式读取一份 Skill，并返回可放入上下文的参考资料。"""
        _validate_resource_name(name)
        path = self._find_skill_path(name)
        raw = self._read_text(path, self.skills_root)
        metadata, body = _parse_skill_document(raw)
        _validate_skill_metadata(metadata)
        if metadata["name"] != name:
            raise ResourceLoadError(
                "RESOURCE_NOT_FOUND",
                f"没有找到名为 {name!r} 的 Skill",
            )
        if len(body) > self.max_content_chars:
            raise ResourceLoadError(
                "RESOURCE_TOO_LARGE",
                f"Skill 内容不能超过 {self.max_content_chars} 个字符",
            )
        _reject_override_directives(body)

        source = self._relative_source(path)
        content = body.strip()
        return {
            "name": metadata["name"],
            "purpose": metadata["purpose"],
            "allowed_paths": metadata["allowed_paths"],
            "source": source,
            "content": content,
            # trusted=False 很重要：Skill 只能作为参考资料，不能改写权限。
            "context_instruction": {
                "path": source,
                "content": content,
                "trusted": False,
            },
            "context_added": {
                "resource": metadata["name"],
                "source": source,
                "content_chars": len(content),
            },
        }

    def render_prompt_template(self, name: str, values: dict[str, str]) -> dict:
        """渲染固定 ``{{placeholder}}``，不执行模板中的 Python 表达式。"""
        _validate_resource_name(name)
        if not isinstance(values, dict):
            raise ResourceLoadError("INVALID_TEMPLATE_VALUES", "模板变量必须是对象")

        path = self.prompts_root / f"{name}.txt"
        if not path.is_file():
            raise ResourceLoadError(
                "RESOURCE_NOT_FOUND",
                f"没有找到名为 {name!r} 的 Prompt Template",
            )
        raw = self._read_text(path, self.prompts_root)
        if len(raw) > self.max_content_chars:
            raise ResourceLoadError(
                "RESOURCE_TOO_LARGE",
                f"Prompt Template 不能超过 {self.max_content_chars} 个字符",
            )

        placeholder_names = []
        for match in _PLACEHOLDER.finditer(raw):
            placeholder = match.group(1)
            if placeholder not in placeholder_names:
                placeholder_names.append(placeholder)
        required = set(placeholder_names)
        provided = set(values)
        missing = sorted(required - provided)
        extra = sorted(provided - required)
        if missing:
            raise ResourceLoadError(
                "TEMPLATE_VARIABLE_MISSING",
                f"缺少模板变量：{', '.join(missing)}",
            )
        if extra:
            raise ResourceLoadError(
                "TEMPLATE_VARIABLE_UNKNOWN",
                f"存在模板未声明的变量：{', '.join(extra)}",
            )
        if any(not isinstance(value, str) for value in values.values()):
            raise ResourceLoadError(
                "INVALID_TEMPLATE_VALUES",
                "模板变量的值必须是字符串",
            )

        rendered = _PLACEHOLDER.sub(
            lambda match: values[match.group(1)],
            raw,
        )
        return {
            "name": name,
            "source": self._relative_source(path),
            "placeholders": placeholder_names,
            "content": rendered,
            "context_added": {
                "resource": name,
                "source": self._relative_source(path),
                "content_chars": len(rendered),
            },
        }

    def _discover_skill_records(self) -> list[Path]:
        if not self.skills_root.is_dir():
            return []
        paths = sorted(self.skills_root.rglob("SKILL.md"))
        for path in paths:
            self._ensure_within(path, self.skills_root)
        return paths

    def _find_skill_path(self, name: str) -> Path:
        for path in self._discover_skill_records():
            raw = self._read_text(path, self.skills_root)
            metadata, _body = _parse_skill_document(raw)
            _validate_skill_metadata(metadata)
            if metadata["name"] == name:
                return path
        raise ResourceLoadError(
            "RESOURCE_NOT_FOUND",
            f"没有找到名为 {name!r} 的 Skill",
        )

    def _read_text(self, path: Path, root: Path) -> str:
        safe_path = self._ensure_within(path, root)
        if not safe_path.is_file():
            raise ResourceLoadError(
                "RESOURCE_NOT_FOUND",
                f"资源文件不存在：{self._relative_source(safe_path)}",
            )
        try:
            return safe_path.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise ResourceLoadError(
                "INVALID_RESOURCE_ENCODING",
                "资源必须是 UTF-8 文本",
            ) from exc
        except OSError as exc:
            raise ResourceLoadError("RESOURCE_READ_FAILED", "读取资源失败") from exc

    def _ensure_within(self, path: Path, root: Path) -> Path:
        resolved = path.resolve()
        try:
            resolved.relative_to(root.resolve())
        except ValueError as exc:
            raise ResourceLoadError(
                "RESOURCE_PATH_ESCAPE",
                "资源路径不能离开固定资源目录",
            ) from exc
        return resolved

    def _relative_source(self, path: Path) -> str:
        return path.resolve().relative_to(self.project_root).as_posix()


def _parse_skill_document(raw: str) -> tuple[dict, str]:
    header, separator, body = raw.partition("\n---\n")
    if not separator:
        raise ResourceLoadError(
            "INVALID_RESOURCE_FORMAT",
            "Skill 必须包含元数据和单独的 --- 分隔线",
        )

    metadata: dict[str, Any] = {}
    for line in header.splitlines():
        if not line.strip():
            continue
        if ":" not in line:
            raise ResourceLoadError("INVALID_RESOURCE_FORMAT", "Skill 元数据格式无效")
        key, value = line.split(":", 1)
        key = key.strip()
        value = value.strip()
        if key in metadata:
            raise ResourceLoadError("INVALID_RESOURCE_FORMAT", f"元数据重复：{key}")
        metadata[key] = value

    required = {"name", "purpose", "allowed_paths"}
    if set(metadata) != required:
        raise ResourceLoadError(
            "INVALID_RESOURCE_FORMAT",
            "Skill 元数据必须且只能包含 name、purpose、allowed_paths",
        )
    metadata["allowed_paths"] = [
        item.strip()
        for item in metadata["allowed_paths"].split(",")
        if item.strip()
    ]
    return metadata, body


def _validate_skill_metadata(metadata: dict) -> None:
    _validate_resource_name(metadata.get("name"))
    if not isinstance(metadata.get("purpose"), str) or not metadata["purpose"].strip():
        raise ResourceLoadError("INVALID_RESOURCE_METADATA", "Skill purpose 不能为空")
    paths = metadata.get("allowed_paths")
    if not isinstance(paths, list) or not paths:
        raise ResourceLoadError(
            "INVALID_ALLOWED_PATH",
            "Skill 至少需要一个 allowed_path",
        )
    for path in paths:
        if not isinstance(path, str) or not path.strip():
            raise ResourceLoadError("INVALID_ALLOWED_PATH", "allowed_path 不能为空")
        posix_path = Path(path)
        windows_path = PureWindowsPath(path)
        if (
            posix_path.is_absolute()
            or windows_path.is_absolute()
            or bool(windows_path.drive)
            or ".." in posix_path.parts
            or ".." in windows_path.parts
        ):
            raise ResourceLoadError(
                "INVALID_ALLOWED_PATH",
                "allowed_path 必须是项目内相对路径",
            )


def _validate_resource_name(name: Any) -> None:
    if not isinstance(name, str) or _SAFE_NAME.fullmatch(name) is None:
        raise ResourceLoadError(
            "INVALID_RESOURCE_NAME",
            "资源名称只能包含小写字母、数字、下划线和连字符，且必须以字母开头",
        )


def _reject_override_directives(content: str) -> None:
    normalized = content.casefold()
    if any(marker.casefold() in normalized for marker in _FORBIDDEN_DIRECTIVES):
        raise ResourceLoadError(
            "UNSAFE_RESOURCE_CONTENT",
            "Skill 包含疑似越权指令，拒绝加载",
        )
