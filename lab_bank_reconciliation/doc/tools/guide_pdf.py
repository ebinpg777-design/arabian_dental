import sys
from playwright.sync_api import sync_playwright
html, pdf = sys.argv[1], sys.argv[2]
footer = """<div style="font-size:8px;color:#6b7280;width:100%;padding:0 15mm;display:flex;justify-content:space-between;">
<span>Bank Reconciliation — User Guide · lab_bank_reconciliation</span>
<span>Page <span class="pageNumber"></span> of <span class="totalPages"></span></span></div>"""
with sync_playwright() as p:
    b = p.chromium.launch(executable_path="/usr/bin/google-chrome-stable")
    pg = b.new_page()
    pg.goto("file://" + html, wait_until="load")
    pg.pdf(path=pdf, format="A4", print_background=True, display_header_footer=True,
           header_template="<div></div>", footer_template=footer,
           margin={"top": "16mm", "bottom": "18mm", "left": "15mm", "right": "15mm"}, outline=True, tagged=True)
    b.close()
print("pdf written")
