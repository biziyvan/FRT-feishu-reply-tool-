"""DOM 顺序 vs id 排序 对比诊断"""
import sys, time
sys.path.insert(0, '.')
from playwright.sync_api import sync_playwright
import step2_auto_reply as S
from denoise import clean_text, snowflake_ts

CFG = S.CFG
with sync_playwright() as p:
    ctx = S.launch_browser(p)
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    page.goto(CFG['feishu']['base_url'], wait_until='domcontentloaded', timeout=60000)
    S.wait_for_chat_list(page)
    S.open_target_chat(page, CFG['feishu']['target_chat_name'])
    time.sleep(3)

    items = page.locator('.js-message-item')
    n = items.count()
    print(f"=== DOM 里实际消息（DOM 原始顺序），共 {n} 条 ===\n")
    raw = []
    for i in range(n):
        el = items.nth(i)
        mid = el.get_attribute('id') or ''
        cls = el.get_attribute('class') or ''
        t = clean_text(el.inner_text()).replace('\n', ' / ')[:55]
        who = '机器人' if 'message-not-self' in cls else '  我  '
        raw.append((i, mid, who, t))
        print(f"  DOM#{i:2d} | ts={snowflake_ts(mid)} | {who} | {t}")

    print(f"\n=== 按 id 数值排序（程序认定的时间序）===\n")
    for i, mid, who, t in sorted(raw, key=lambda x: int(x[1]) if x[1].isdigit() else 0):
        print(f"  DOM#{i:2d} | ts={snowflake_ts(mid)} | {who} | {t}")

    print(f"\n=== 按雪花时间戳排序 ===\n")
    for i, mid, who, t in sorted(raw, key=lambda x: snowflake_ts(x[1])):
        print(f"  DOM#{i:2d} | ts={snowflake_ts(mid)} | {who} | {t}")

    ctx.close()
