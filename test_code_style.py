"""代码题回答风格自检（只调大模型，不碰浏览器）

用途：调整 system_prompt 里【代码题】规则后，快速确认模型实际输出
是否符合「无注释、极简、一句话说明」的要求。

运行：.venv\\Scripts\\python.exe test_code_style.py
"""

import io
import re
import sys
import time

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

import step2_auto_reply as S

CASES = [
    "编写一段与「智能合约基本概念」相关的核心代码，实现其基本逻辑，并说明关键行的作用。",
    "用 Solidity 编写一个函数，实现向指定地址转账并在成功后记录事件，说明关键行的作用。",
    "编写一段 Solidity 代码，实现一个简单的计数器：可以自增、可以查询当前值，说明关键行作用。",
    "编写一段代码，读取一个整数列表并返回其中的偶数，说明关键行作用。",
]


def build(question, course="智能合约技术"):
    return (
        f"【历史对话】\n"
        f"[机器人] AI 自适应问答\n"
        f"📚 正在学习：{course}\n"
        f"第 1 题（掌握度 0%）\n"
        f"{question}\n"
        f"💡 直接回复你的答案\n\n"
        f"★当前待答题目★（机器人最新一条消息，需要回复它）：\n{question}\n"
    )


def inspect(ans):
    """粗查是否触发减配规则。"""
    body = re.sub(r"```[a-zA-Z]*", "", ans)
    issues = []
    if re.search(r"(^|\n)\s*(//|#\s|/\*|\*)", body):
        issues.append("疑似有注释行")
    if re.search(r"\b(try|except|catch|throws|require\(|revert\()", body) and "require" not in body[:0]:
        pass
    if re.search(r"\b(try:|except |catch \(|throws )", body):
        issues.append("有异常处理")
    if re.search(r"\b(if\s+.*==\s*None|if\s+not\s+isinstance|len\(.*\)\s*==\s*0)", body):
        issues.append("有参数校验")
    if re.search(r"\bdef main|if __name__|public static void main", body):
        issues.append("有 main 示例")
    return issues


def main():
    print("=" * 72)
    print("代码题回答风格自检（模型:", S.LLM["model"], "）")
    print("=" * 72)
    for i, q in enumerate(CASES, 1):
        print(f"\n{'=' * 72}\n[{i}] 题目：{q}\n{'-' * 72}")
        t = time.time()
        ans = S.ask_llm(build(q))
        cost = time.time() - t
        if not ans:
            print("!! 模型无返回")
            continue
        print(ans)
        issues = inspect(ans)
        n_lines = len([x for x in ans.splitlines() if x.strip()])
        print(f"{'-' * 72}")
        print(f"[检查] 耗时 {cost:.1f}s | 非空行 {n_lines} | 字符 {len(ans)} | "
              f"{'⚠ ' + '、'.join(issues) if issues else '✓ 未发现多余注释/异常处理/校验/main'}")


if __name__ == "__main__":
    main()
