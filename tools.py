from pathlib import Path
import json
ROOT = Path(__file__).resolve().parent / "sample_repo"

def read_file(path, start_line=1, end_line=80):
    file_path = ROOT / path
    with open(file_path, 'r', encoding='utf-8') as f:
        lines = f.readlines()
        return ''.join(lines[start_line - 1:end_line])

def list_files():
    return [f.name for f in ROOT.iterdir() if f.is_file()]

"""def search_text(text:str):
    files = list_files()
    result = []
    for file in files:
        file_path = ROOT / file
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
            if text in content:
                result.append(file)
    return result"""   



def search_text(path, start_line=1, end_line=None):
    try:
        path = Path(path)
        lines = path.read_text(encoding="utf-8").splitlines()

        if end_line is None:
            end_line = len(lines)

        selected_lines = lines[start_line - 1:end_line]

        result = {
            "ok": True,
            "path": str(path),
            "start_line": start_line,
            "end_line": end_line,
            "content": "\n".join(
                f"{i}: {line}"
                for i, line in enumerate(
                    selected_lines,
                    start=start_line
                )
            )
        }

    except FileNotFoundError:
        result = {
            "ok": False,
            "error": "FILE_NOT_FOUND",
            "message": "指定文件不存在"
        }

    except UnicodeError:
        result = {
            "ok": False,
            "error": "FILE_ENCODING_ERROR",
            "message": "文件编码无法识别"
        }

    except OSError as exc:
        result = {
            "ok": False,
            "error": "FILE_READ_ERROR",
            "message": str(exc)
        }

    return result
