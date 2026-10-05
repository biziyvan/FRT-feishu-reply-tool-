"""多行回复输入能力实测（不会真正发送消息）

背景：代码题回复是多行、带缩进的文本，而此前所有回复都是单行。
飞书输入框是 Lark 富文本编辑器，实测确认 CDP 输入能否原样保留
换行与缩进 —— 这决定代码题回复能不能正常发出去。

对照两种方案：
  A. 一次性 Input.insertText，文本自带 \n
  B. 逐行 insertText，行间派发 Shift+Enter（软换行）

运行：.venv\\Scripts\\python.exe test_multiline.py
"""

import io
import sys
import time

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from playwright.sync_api import sync_playwright

import step2_auto_reply as S

CFG = S.CFG

SAMPLE = """```solidity
function transfer(address to, uint amount) public {
    require(balances[msg.sender] >= amount);
    balances[msg.sender] -= amount;
    balances[to] += amount;
}
```
require 校验余额是否足够，后两行分别扣减付款方、增加收款方。"""

EXPECT_LINES = [ln for ln in SAMPLE.split("\n")]

DUMP_JS = """() => {
    const els = document.querySelectorAll('[contenteditable="true"]');
    const box = els[els.length - 1];
    if (!box) return null;
    return {
        text: box.innerText,
        html: box.innerHTML,
        ws: getComputedStyle(box).whiteSpace,
        kids: Array.from(box.children).map(c => c.tagName).join(','),
    };
}"""


def dump(page):
    try:
        return page.evaluate(DUMP_JS)
    except Exception as e:
        return {"text": "", "html": f"<evaluate error {e}>", "ws": "?", "kids": ""}


def norm_lines(text):
    """规范化：去掉 Lark 行尾零宽空格(ZWS)与尾部空行。

    Lark 的 ace-line 每行末尾都会带一个 \\u200b 占位符，真人手打多行
    也一样，属编辑器内部结构，不是我们插入的内容。
    """
    lines = [ln.replace("\u200b", "").replace("\r", "") for ln in text.split("\n")]
    while lines and not lines[-1].strip():
        lines.pop()
    return lines


def report(label, page):
    info = dump(page)
    if not info:
        print(f"  {label}: 读不到输入框")
        return False
    lines = norm_lines(info["text"])
    print(f"  [{label}] 行数={len(lines)} (期望 {len(EXPECT_LINES)})  "
          f"white-space={info['ws']}  子节点={info['kids']}")
    ok = lines == EXPECT_LINES
    for i, (got, want) in enumerate(zip(lines, EXPECT_LINES)):
        if got != want:
            print(f"      差异 行{i}: 得到 {got!r}  期望 {want!r}")
    if ok:
        print("      OK 换行与缩进完全一致")
    return ok


def insert_by_shift_enter(page, cdp, text):
    """逐行插入，行间用 Shift+Enter 软换行（不动输入框已有内容）。"""
    for i, ln in enumerate(text.split("\n")):
        if i > 0:
            for ev in ("keyDown", "keyUp"):
                cdp.send("Input.dispatchKeyEvent", {
                    "type": ev, "key": "Enter", "code": "Enter",
                    "modifiers": 8,
                    "windowsVirtualKeyCode": 13, "nativeVirtualKeyCode": 13,
                })
            time.sleep(0.06)
        if ln:
            cdp.send("Input.insertText", {"text": ln})
        time.sleep(0.04)


def main():
    print("=" * 72)
    print("  多行输入能力实测（不会发送任何消息）")
    print("=" * 72)

    with sync_playwright() as p:
        ctx = S.launch_browser(p)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto(CFG["feishu"]["base_url"], wait_until="domcontentloaded", timeout=60000)
        if not S.wait_for_chat_list(page):
            print("会话列表加载失败")
            ctx.close()
            sys.exit(1)
        S.open_target_chat(page, CFG["feishu"]["target_chat_name"])
        time.sleep(3)

        before = S._count_my_messages(page)
        print(f"发送前「我发出的消息数」= {before}")

        box = page.locator('[contenteditable="true"]').last
        cdp = page.context.new_cdp_session(page)

        # ---------- 方案 A：一次性 insertText ----------
        print("\n[方案 A] 一次性 Input.insertText（文本自带 \\n）")
        S._clear_input_box(page)
        time.sleep(0.3)
        try:
            box.click(timeout=5000)
        except Exception:
            pass
        time.sleep(0.3)
        cdp.send("Input.insertText", {"text": SAMPLE})
        time.sleep(0.8)
        a_ok = report("A", page)

        # ---------- 方案 B：逐行 + Shift+Enter ----------
        print("\n[方案 B] 逐行 insertText + Shift+Enter 软换行")
        S._clear_input_box(page)
        time.sleep(0.3)
        try:
            box.click(timeout=5000)
        except Exception:
            pass
        time.sleep(0.3)
        insert_by_shift_enter(page, cdp, SAMPLE)
        time.sleep(0.8)
        b_ok = report("B", page)

        # ---------- 收尾 ----------
        print("\n[收尾] 清空输入框（不发送）")
        S._clear_input_box(page)
        time.sleep(0.5)
        left = S._read_input_box(page)
        print(f"  清空后残留: {left!r}")

        after = S._count_my_messages(page)
        print(f"  消息数 {before} -> {after}  ({'OK 未发送任何消息' if before == after else '异常！消息数变了'})")

        print("\n" + "=" * 72)
        print(f"结论：方案A {'可用' if a_ok else '不可用'} ｜ 方案B {'可用' if b_ok else '不可用'}")
        print("=" * 72)
        ctx.close()


if __name__ == "__main__":
    main()
