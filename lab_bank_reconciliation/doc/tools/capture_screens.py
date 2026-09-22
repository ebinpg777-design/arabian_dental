"""Capture the bank reconciliation screens for the guide; remove every record the walk creates.
Your own statements are only ever viewed."""
import json
import os
from collections import defaultdict

from playwright.sync_api import sync_playwright

URL = "http://localhost:8069"
IMG = "/home/ebin-pg/odoo/projects/arabian_dental/lab_bank_reconciliation/doc/images"
NOTE = "Not in the passbook: ask the branch"
os.makedirs(IMG, exist_ok=True)
for name in os.listdir(IMG):
    os.remove(os.path.join(IMG, name))

RPC = """async ([model, method, args, kwargs]) => {
    const r = await fetch('/web/dataset/call_kw', {method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({jsonrpc: '2.0', method: 'call', params: {model, method, args, kwargs: kwargs || {}}})});
    const j = await r.json(); if (j.error) throw new Error(JSON.stringify(j.error.data.message)); return j.result; }"""
errors = []


def rpc(pg, model, method, args, kwargs=None):
    return pg.evaluate(RPC, [model, method, args, kwargs or {}])


def shot(pg, name):
    pg.screenshot(path=f"{IMG}/{name}.png")
    print("  saved", name)


def settle(pg, ms=900):
    pg.wait_for_timeout(ms)


def scroll_to(pg, selector, block="start"):
    pg.evaluate("([s, b]) => document.querySelector(s)?.scrollIntoView({block: b})", [selector, block])
    settle(pg, 400)


def progress(pg):
    return pg.locator(".o_bank_rec_progress_l").inner_text().strip()


def toast(pg):
    texts = pg.locator(".o_notification").all_inner_texts()
    return texts[-1].replace("\n", " ") if texts else ""


def close_toasts(pg):
    for btn in pg.locator(".o_notification .o_notification_close, .o_notification button.btn-close").all():
        try:
            btn.click(timeout=2000)
        except Exception:
            pass
    settle(pg, 400)


def cents(value):
    return int(round(value * 100))


with sync_playwright() as p:
    browser = p.chromium.launch(executable_path="/usr/bin/google-chrome-stable")
    pg = browser.new_page(viewport={"width": 1440, "height": 900})
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.goto(f"{URL}/web/login", wait_until="domcontentloaded")
    if not pg.locator("input[name=login]").is_visible():
        pg.get_by_role("button", name="Log in").first.click()
        settle(pg, 800)
    pg.fill("input[name=login]", "admin")
    pg.fill("input[name=password]", "admin")
    pg.press("input[name=password]", "Enter")
    pg.wait_for_selector(".o_main_navbar", timeout=30000)
    statements_before = set(rpc(pg, "bank.reconciliation", "search", [[]]))
    try:
        pg.goto(f"{URL}/odoo/action-lab_bank_reconciliation.action_bank_reconciliation_overview", wait_until="domcontentloaded")
        pg.wait_for_selector(".o_bank_ovw_card", timeout=30000)
        settle(pg)
        print("trend lines on cards:", pg.locator(".o_bank_ovw_trend svg polyline").count())
        shot(pg, "01_overview")
        pg.locator(".o_bank_ovw_card", has_text="1002010").first.locator(".o_bank_ovw_card_f .btn-primary").click()
        pg.wait_for_selector(".o_bank_rec_line", timeout=60000)
        settle(pg, 1500)
        rec_id = rpc(pg, "bank.reconciliation", "search",
                     [[["journal_id.code", "=", "CNRB"], ["company_id.name", "=", "Arabian Dental Lab"], ["state", "=", "draft"]]],
                     {"order": "id desc", "limit": 1})[0]
        print("statement", rec_id, "(created by this walk)" if rec_id not in statements_before else "(ALREADY EXISTED)")
        shot(pg, "02_screen_first")

        pg.locator(".o_bank_rec_coach_opening .btn-primary").click()
        pg.wait_for_selector(".modal .btn-primary", timeout=10000)
        settle(pg, 500)
        shot(pg, "03_opening_confirm")
        pg.locator(".modal .btn-primary").click()
        pg.wait_for_selector(".o_bank_rec_coach_balance", timeout=30000)
        settle(pg)
        shot(pg, "04_next_step")

        # one exact entry -> ticked at once
        screen = rpc(pg, "bank.reconciliation", "get_screen", [[rec_id]])
        outstanding = [l for l in screen["deposits"] if not l["cleared"] and not l["general"]]
        counts = defaultdict(int)
        for l in outstanding:
            counts[cents(l["amount"])] += 1
        single = None
        for l in sorted((l for l in outstanding if counts[cents(l["amount"])] == 1),
                        key=lambda l: (cents(l["amount"]) % 100 == 0, -l["amount"]))[:60]:
            kinds = [r["kind"] for r in rpc(pg, "bank.reconciliation", "find_matches",
                                            [[rec_id], l["amount"], l["date"], "deposits", 7])["results"]]
            if kinds.count("single") == 1 and "batch" not in kinds:
                single = l
                break
        pg.fill(".o_bank_rec_pass input[type=date]", single["date"])
        pg.fill(".o_bank_rec_pass_amt", str(single["amount"]))
        pg.press(".o_bank_rec_pass_amt", "Enter")
        pg.wait_for_selector(".o_notification", timeout=15000)
        settle(pg, 900)
        print("auto-tick:", toast(pg), "|", progress(pg))
        shot(pg, "05_passbook_auto_tick")
        close_toasts(pg)

        # several answers
        screen = rpc(pg, "bank.reconciliation", "get_screen", [[rec_id]])
        by_day = defaultdict(list)
        for l in screen["deposits"]:
            if not l["cleared"] and not l["general"]:
                by_day[l["date"]].append(l)
        singles = {cents(l["amount"]) for l in screen["deposits"] if not l["cleared"]}
        pair = None
        for day, lines in sorted(by_day.items(), key=lambda kv: (len(kv[1]) < 2, len(kv[1]), kv[0])):
            for i in range(len(lines)):
                for j in range(i + 1, len(lines)):
                    total = round(lines[i]["amount"] + lines[j]["amount"], 2)
                    if cents(total) not in singles and lines[i]["amount"] != lines[j]["amount"]:
                        pair = (day, total)
                        break
                if pair:
                    break
            if pair:
                break
        pg.fill(".o_bank_rec_pass input[type=date]", pair[0])
        pg.fill(".o_bank_rec_pass_amt", str(pair[1]))
        pg.locator(".o_bank_rec_pass > .btn-primary").click()
        pg.wait_for_selector(".o_bank_rec_matches", timeout=30000)
        settle(pg)
        scroll_to(pg, ".o_bank_rec_pass")
        shot(pg, "06_passbook_answers")
        pg.locator(".o_bank_rec_matches .o_bank_rec_match_card .btn-primary").first.click()
        settle(pg, 1800)
        close_toasts(pg)

        # the passbook balance column catches a skipped line
        scroll_to(pg, ".o_bank_rec_head")
        pg.fill(".o_bank_rec_pass_amt", "")
        pg.fill(".o_bank_rec_pass_bal", "250000")
        pg.press(".o_bank_rec_pass_bal", "Enter")
        settle(pg, 900)
        print("balance noted:", toast(pg))
        close_toasts(pg)
        pg.fill(".o_bank_rec_pass_amt", "123456.78")
        pg.fill(".o_bank_rec_pass_bal", str(round(250000 + 123456.78 + 4500, 2)))
        pg.press(".o_bank_rec_pass_bal", "Enter")
        pg.wait_for_selector(".o_bank_rec_matches", timeout=30000)
        settle(pg, 900)
        print("balance warning:", toast(pg))
        print("coach offers the passbook balance:", pg.locator(".o_bank_rec_coach_balance .btn-primary").inner_text())
        shot(pg, "26_balance_check")
        close_toasts(pg)

        # nothing matches -> create the bank entry
        scroll_to(pg, ".o_bank_rec_pass")
        shot(pg, "07_passbook_not_found")
        pg.locator(".o_bank_rec_matches .btn-primary", has_text="Create").click()
        pg.wait_for_selector(".o_bank_rec_entry", timeout=10000)
        settle(pg, 500)
        pg.locator(".o_bank_rec_entry .o_bank_rec_selector input").first.click()
        settle(pg, 700)
        pg.keyboard.type("SREEDEVI", delay=70)
        try:
            pg.wait_for_selector(".o-autocomplete--dropdown-item", timeout=15000)
            settle(pg, 500)
            pg.locator(".o-autocomplete--dropdown-item").first.click()
        except Exception:
            print("no party suggestion appeared")
        settle(pg, 600)
        pg.fill(".o_bank_rec_entry input[placeholder='UTR / cheque no.']", "UTR 612345")
        pg.fill(".o_bank_rec_entry input[placeholder='As in the passbook']", "NEFT from DR SREEDEVI")
        scroll_to(pg, ".o_bank_rec_entry", "center")
        shot(pg, "08_new_bank_entry")
        pg.locator(".o_bank_rec_entry .btn-close").click()
        settle(pg, 400)

        # a deposit credited short: offered, not ticked (ticking would post a charge entry)
        screen = rpc(pg, "bank.reconciliation", "get_screen", [[rec_id]])
        short = None
        for l in [l for l in screen["deposits"] if not l["cleared"] and not l["general"] and l["amount"] >= 5000][:60]:
            found = rpc(pg, "bank.reconciliation", "find_matches", [[rec_id], l["amount"] - 18, l["date"], "deposits", 3])
            if any(r["kind"] == "near" for r in found["results"]):
                short = l
                break
        print("short credit line:", short and (short["date"], short["amount"]))
        if short:
            scroll_to(pg, ".o_bank_rec_head")
            pg.fill(".o_bank_rec_pass input[type=date]", short["date"])
            pg.select_option(".o_bank_rec_pass_win", "3")
            pg.fill(".o_bank_rec_pass_amt", str(round(short["amount"] - 18, 2)))
            pg.press(".o_bank_rec_pass_amt", "Enter")
            pg.wait_for_selector(".o_bank_rec_matches", timeout=30000)
            settle(pg, 900)
            print("short credit answers:", pg.locator(".o_bank_rec_matches .badge").all_inner_texts())
            scroll_to(pg, ".o_bank_rec_pass")
            shot(pg, "27_short_credit")
            pg.locator(".o_bank_rec_matches .btn-close").click()
            pg.select_option(".o_bank_rec_pass_win", "7")
            settle(pg, 300)

        # entry details, and a query raised from them
        scroll_to(pg, ".o_bank_rec_cols")
        pg.locator(".o_bank_rec_col_deposits .o_bank_rec_line", has_text="CUST.IN").first.locator(".o_bank_rec_what").click()
        pg.wait_for_selector(".o_bank_rec_drawer .o_bank_rec_dsec", timeout=30000)
        settle(pg, 900)
        shot(pg, "09_entry_details")
        pg.locator(".o_bank_rec_dsum_btns .btn", has_text="Query").click()
        settle(pg, 600)
        pg.keyboard.type(NOTE)
        pg.keyboard.press("Enter")
        settle(pg, 1800)
        shot(pg, "10_details_query")
        pg.keyboard.press("Escape")
        settle(pg, 600)
        print("details closed by Esc:", pg.locator(".o_bank_rec_drawer").count() == 0)

        # shift-click a range
        rows = pg.locator(".o_bank_rec_col_deposits .o_bank_rec_line")
        rows.nth(6).locator(".o_bank_rec_tick").click()
        settle(pg, 1500)
        rows.nth(10).locator(".o_bank_rec_tick").click(modifiers=["Shift"])
        settle(pg, 1800)
        print("range ticked:", progress(pg))

        # filters
        scroll_to(pg, ".o_bank_rec_bar")
        pg.locator(".o_bank_rec_bar .btn", has_text="Filters").click()
        settle(pg, 400)
        pg.locator(".o_bank_rec_chip_cash").click()
        settle(pg, 1800)
        scroll_to(pg, ".o_bank_rec_bar")
        shot(pg, "11_filters")
        pg.locator(".o_bank_rec_pill button").first.click()
        settle(pg, 1800)
        pg.locator(".o_bank_rec_bar .btn", has_text="Filters").click()
        settle(pg, 300)

        # day totals, a batch ticked, undo
        pg.locator(".o_bank_rec_bar .btn", has_text="Day totals").click()
        pg.wait_for_selector(".o_bank_rec_day", timeout=30000)
        settle(pg, 900)
        scroll_to(pg, ".o_bank_rec_bar")
        shot(pg, "12_day_totals")
        pg.locator(".o_bank_rec_col_deposits .o_bank_rec_day").filter(has=pg.locator(".o_bank_rec_day_g .btn-link")).first \
            .locator(".o_bank_rec_day_g .btn-link").first.click()
        pg.wait_for_selector(".modal .btn-primary", timeout=10000)
        pg.locator(".modal .btn-primary").click()
        settle(pg, 2500)
        scroll_to(pg, ".o_bank_rec_head")
        shot(pg, "13_undo")
        pg.locator(".o_bank_rec_bar .btn", has_text="Undo").click()
        settle(pg, 2500)
        close_toasts(pg)
        pg.locator(".o_bank_rec_bar .btn", has_text="Lines").click()
        settle(pg, 1500)

        dup = pg.locator(".o_bank_rec_bar .btn-outline-warning")
        if dup.count():
            dup.click()
            settle(pg, 1200)
            scroll_to(pg, ".o_bank_rec_bar")
            shot(pg, "14_duplicates")
            pg.locator(".o_bank_rec_bar .btn-warning").click()
            settle(pg, 400)

        scroll_to(pg, ".o_bank_rec_head")
        pg.locator(".o_bank_rec_summary .btn-link").click()
        settle(pg, 400)
        pg.locator(".o_bank_rec_more_wrap > .btn").click()
        settle(pg, 400)
        shot(pg, "15_statement_and_more")
        pg.keyboard.press("Escape")
        settle(pg, 300)
        pg.locator(".o_bank_rec_summary .btn-link").click()
        settle(pg, 400)

        # hints -> ready -> reconciled
        screen = rpc(pg, "bank.reconciliation", "get_screen", [[rec_id]])
        outstanding = [l for l in screen["deposits"] if not l["cleared"]]
        counts = defaultdict(int)
        for l in outstanding:
            counts[cents(l["amount"])] += 1
        unique = max((l for l in outstanding if counts[cents(l["amount"])] == 1), key=lambda l: l["amount"])
        pg.fill(".o_bank_rec_bank_in", str(round(screen["figures"]["adjusted"] + unique["amount"], 2)))
        pg.locator(".o_bank_rec_bank_in").dispatch_event("change")
        pg.wait_for_selector(".o_bank_rec_coach_hints", timeout=30000)
        settle(pg, 1200)
        shot(pg, "16_hints")
        pg.locator(".o_bank_rec_hint .btn-primary").first.click()
        pg.wait_for_selector(".o_bank_rec_coach_ready", timeout=30000)
        settle(pg, 900)
        shot(pg, "17_ready")
        pg.locator(".o_bank_rec_coach_ready .btn-primary").click()
        pg.wait_for_selector(".o_bank_rec_coach_done", timeout=30000)
        settle(pg, 1500)
        shot(pg, "18_reconciled")

        pg.locator(".o_bank_rec_crumbs .btn-link", has_text="Statement form").click()
        pg.wait_for_selector(".o_form_view", timeout=30000)
        settle(pg, 1200)
        shot(pg, "19_form")
        pg.goto(f"{URL}/odoo/action-lab_bank_reconciliation.action_bank_reconciliation", wait_until="domcontentloaded")
        pg.wait_for_selector(".o_list_view", timeout=30000)
        settle(pg)
        shot(pg, "20_statements_list")
        pg.goto(f"{URL}/odoo/action-lab_bank_reconciliation.action_bank_reconciliation_overview", wait_until="domcontentloaded")
        pg.wait_for_selector(".o_bank_ovw_card", timeout=30000)
        settle(pg)
        shot(pg, "21_overview_after")
        pg.goto(f"{URL}/report/html/lab_bank_reconciliation.report_brs/{rec_id}", wait_until="domcontentloaded", timeout=300000)
        settle(pg, 1500)
        shot(pg, "22_brs_report")
        pg.goto(f"{URL}/report/html/lab_bank_reconciliation.report_query_letter/{rec_id}", wait_until="domcontentloaded", timeout=120000)
        settle(pg, 1200)
        shot(pg, "23_query_letter")

        # carried from the last statement, on a bank that has one: viewed only
        pg.goto(f"{URL}/odoo/action-lab_bank_reconciliation.action_bank_reconciliation_overview", wait_until="domcontentloaded")
        pg.wait_for_selector(".o_bank_ovw_card", timeout=30000)
        settle(pg)
        dhb = pg.locator(".o_bank_ovw_card", has_text="DHANALAKSHMI").filter(has_text="Continue")
        if dhb.count():
            dhb.first.locator(".o_bank_ovw_card_f .btn-primary").click()
            pg.wait_for_selector(".o_bank_rec_summary", timeout=60000)
            settle(pg, 1500)
            pg.locator(".o_bank_rec_bar .btn", has_text="Filters").click()
            settle(pg, 500)
            chip = pg.locator(".o_bank_rec_chip_carried")
            print("carried chip:", chip.inner_text().replace("\n", " ") if chip.count() else None)
            if chip.count():
                chip.click()
                settle(pg, 1800)
                scroll_to(pg, ".o_bank_rec_bar")
                shot(pg, "28_carried")
        else:
            print("no DHANALAKSHMI statement in progress to show the carried filter on")

        pg.set_viewport_size({"width": 400, "height": 860})
        pg.goto(f"{URL}/odoo/action-lab_bank_reconciliation.action_bank_reconciliation_overview", wait_until="domcontentloaded")
        pg.wait_for_selector(".o_bank_ovw_card", timeout=30000)
        settle(pg)
        shot(pg, "24_phone_overview")
        pg.locator(".o_bank_ovw_card", has_text="1002010").first.locator(".o_bank_ovw_card_f .btn-primary").click()
        pg.wait_for_selector(".o_bank_rec_summary", timeout=60000)
        settle(pg, 1500)
        shot(pg, "25_phone_screen")
        print("phone screen wider than the phone:", pg.evaluate("() => document.documentElement.scrollWidth > document.documentElement.clientWidth + 2"))
    finally:
        queries = rpc(pg, "bank.line.query", "search", [[["note", "=", NOTE]]])
        if queries:
            rpc(pg, "bank.line.query", "unlink", [queries])
        made = sorted(set(rpc(pg, "bank.reconciliation", "search", [[]])) - statements_before)
        for rid in made:
            if rpc(pg, "bank.reconciliation", "read", [[rid], ["state"]])[0]["state"] == "done":
                rpc(pg, "bank.reconciliation", "action_reset_draft", [[rid]])
        clearances = rpc(pg, "bank.clearance", "search", [[["reconciliation_id", "in", made]]]) if made else []
        if clearances:
            rpc(pg, "bank.clearance", "unlink", [clearances])
        if made:
            rpc(pg, "bank.reconciliation", "unlink", [made])
        print("cleanup: statements", made, "clearances", len(clearances), "queries", len(queries))
        print("statements left:", rpc(pg, "bank.reconciliation", "search_read", [[]], {"fields": ["journal_id", "state"]}))
    print("page errors:", errors)
    browser.close()
