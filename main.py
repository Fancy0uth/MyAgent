from pathlib import Path

ROOT = Path(__file__).resolve().parent / "sample_repo"


def read_readme() -> str:
    return (ROOT / "README.md").read_text(encoding="utf-8")


if __name__ == "__main__":
    try:
        strings=[]
        for lines in read_readme().splitlines():
            #print(lines)
            strings.append(lines)
        
        for i, line in enumerate(strings, start=1):
            print(f"{i}: {line}")
        #print(read_readme())
    except (OSError, UnicodeError) as exc:
        print(f"读取失败：{exc}")
        raise SystemExit(1)
