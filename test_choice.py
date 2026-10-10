"""选择题作答自检（只调大模型，不碰浏览器）

用途：验证两件事
  1. 上下文里带上完整 A/B/C/D 选项后，模型是否**只回一个字母**
     （而不是回一段内容、或 "B. xxx" 这种）
  2. 答案是否正确

运行：.venv\\Scripts\\python.exe test_choice.py
"""

import io
import re
import sys
import time

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

import step2_auto_reply as S

# (题干, 选项列表, 正确字母)
CASES = [
    (
        "在Solidity中，如果要计算10的16次方（即10^16），应该使用以下哪种正确的运算符语法？",
        ["10 ^ 16", "10 ** 16", "pow(10, 16)", "10 ^^ 16"],
        "B",
    ),
    (
        "在 Solidity 中，如果要判断两个字符串变量 str1 和 str2 的内容是否完全相同，"
        "以下哪种做法是正确的？",
        [
            "直接用 str1 == str2 比较",
            "使用内置的 strcmp(str1, str2) 函数",
            "把字符串转成 bytes 后比较 keccak256(str1) == keccak256(str2)",
            "用 compare(str1, str2) 方法",
        ],
        "C",
    ),
    (
        "在 Solidity 中，关于动态数组（例如 Person[] public people;），以下说法正确的是？",
        [
            "动态数组在声明时必须指定固定的长度，例如 Person[10] public people;",
            "将动态数组声明为 public 后，会自动生成一个 getter 函数，允许外部合约或账户"
            "通过索引读取数组元素，但无法直接修改数组长度或插入新元素。",
            "public 数组外部可以直接修改其内容和长度。",
            "动态数组不能用 push 方法添加元素。",
        ],
        "B",
    ),
    (
        "在 Solidity 中，关于无符号整数 uint 及其相关类型的描述，下列哪一项是错误的？",
        [
            "uint 实际上是 uint256 的别名。",
            "uint 类型可以存储负数，例如 -1。",
            "uint 表示无符号整数，取值范围从 0 开始。",
            "uint8 占用 8 位（1 字节）存储空间。",
        ],
        "B",
    ),
    (
        "在 Solidity 中，定义接口（Interface）以实现跨合约交互时，接口内的函数声明在语法上有什么特点？",
        [
            "必须包含完整的函数体",
            "在函数声明末尾用分号结束，不写函数体",
            "必须用 private 修饰",
            "接口里每个函数都必须有返回值",
        ],
        "B",
    ),
    (
        "在 Solidity 智能合约中，关于状态变量（State Variables）和内存变量（Memory Variables）"
        "的存储特性，以下说法正确的是？",
        [
            "状态变量只存在于内存中，函数执行完就消失。",
            "内存变量会被永久保存在区块链上。",
            "状态变量永久存储在区块链上，内存变量仅存在于函数执行期间，函数结束后即被销毁。",
            "两者没有区别，只是命名不同。",
        ],
        "C",
    ),
    (
        "在 Solidity 中，将状态数组声明为 public（如 Zombie[] public zombies;）后，"
        "关于该数组的访问特性，以下描述正确的是？",
        [
            "外部不仅可以读取数据，还可以直接修改数组内容。",
            "编译器会自动生成 getter 方法，外部合约只能读取数据，无法直接修改。",
            "外部无法访问该数组。",
            "public 数组只能被本合约内部访问。",
        ],
        "B",
    ),
    (
        "在 Solidity 中，每次建立一个新的智能合约项目时，源码必须包含的最基础代码结构是？",
        [
            "只需要状态变量",
            "contract 合约定义（可含状态变量与函数），以及 pragma 版本声明",
            "必须有 main 函数",
            "必须有构造函数和析构函数",
        ],
        "B",
    ),
]


def build_cards(stem, options, course="智能合约技术"):
    """按卡片真实格式拼出【历史】+【当前待答】两段。"""
    body = stem + "\n" + "\n".join(f"{c}. {o}" for c, o in zip("ABCD", options))
    return (
        f"以下是我和教学机器人「AI教学平台」的最近对话：\n\n"
        f"[机器人] AI 自适应问答\n"
        f"📚 正在学习：{course}\n"
        f"第 1 题（掌握度 0%）\n"
        f"{body}\n"
        f"💡 直接回复选项字母（如 A）或选项内容作答\n\n"
        f"↑↑↑ 上面这一条是机器人【最新】发来的消息，\n"
        f"我需要回答的就是其中这道题：第 1 题 —— {body}\n"
        f"注意：只需要回答这一道题，不要回答更早的题目。\n\n"
        f"请针对上面【最新】的这道题，给出我下一条应该发送给机器人的回复内容。"
    )


def main():
    print("=" * 74)
    print(f"选择题作答自检（模型: {S.LLM['model']}）")
    print("=" * 74)
    ok_fmt = ok_ans = 0
    for i, (stem, opts, key) in enumerate(CASES, 1):
        print(f"\n[{i}] {stem[:46]}…  正确: {key}")
        t = time.time()
        ans = (S.ask_llm(build_cards(stem, opts)) or "").strip()
        cost = time.time() - t
        stripped = re.sub(r"[^A-Za-z]", "", ans).upper()
        fmt_ok = stripped == key or (len(ans) == 1 and ans.upper() in "ABCD")
        ans_ok = stripped == key
        ok_fmt += fmt_ok
        ok_ans += ans_ok
        flag = "✓" if ans_ok else ("格式OK/答案错" if fmt_ok else "✗ 格式不合规")
        print(f"    模型输出: {ans!r}   ({cost:.1f}s)  {flag}")
    n = len(CASES)
    print("\n" + "=" * 74)
    print(f"只回字母（格式合规）: {ok_fmt}/{n}     答案正确: {ok_ans}/{n}")
    print("=" * 74)


if __name__ == "__main__":
    main()
