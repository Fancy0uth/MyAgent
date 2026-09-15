from pathlib import Path
from tools import *



def main() -> None:
    print("main.py 已启动")

    # 调用 tools.py 中的函数，读取 README.md 的第 1—3 行
    result = read_file(
        "README.md",
        start_line=1,
        end_line=3,
    )

    # 把工具返回的结果显示到终端
    print(result)


if __name__ == "__main__":
    main()
"""if __name__ == "__main__":
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
        raise SystemExit(1)"""
