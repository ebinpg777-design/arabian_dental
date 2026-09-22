import base64, re, sys
from markdown_it import MarkdownIt
doc, out = sys.argv[1], sys.argv[2]
src = open(f"{doc}/USER_GUIDE.md", encoding="utf-8").read()
md = MarkdownIt("commonmark", {"html": False}).enable("table")
tokens = md.parse(src)

def slug(text):
    text = re.sub(r"[^\w\s-]", "", text.lower())
    return re.sub(r"\s+", "-", text.strip())

for i, tok in enumerate(tokens):
    if tok.type == "heading_open":
        tok.attrSet("id", slug(tokens[i + 1].content))
    if tok.type == "inline":
        for child in tok.children or []:
            if child.type == "image":
                data = base64.b64encode(open(f"{doc}/{child.attrGet('src')}", "rb").read()).decode()
                child.attrSet("src", f"data:image/png;base64,{data}")
body = md.renderer.render(tokens, md.options, {})
css = """
@page { size: A4; margin: 18mm 15mm; }
body { font-family: 'Noto Sans', 'DejaVu Sans', Arial, sans-serif; font-size: 10.5pt; color: #1f2937; line-height: 1.5; }
h1 { font-size: 26pt; margin: 0 0 4pt; color: #111827; }
h2 { font-size: 16pt; color: #0d7c8a; border-bottom: 2px solid #0d7c8a; padding-bottom: 3pt; margin-top: 22pt; break-after: avoid; }
h3 { font-size: 12.5pt; break-after: avoid; }
p, li { orphans: 3; widows: 3; }
a { color: #0d7c8a; text-decoration: none; }
code { font-family: 'DejaVu Sans Mono', monospace; font-size: 9pt; background: #f3f4f6; padding: 0 3pt; border-radius: 3pt; }
table { border-collapse: collapse; width: 100%; margin: 8pt 0; font-size: 9.5pt; break-inside: avoid; }
th, td { border: 1px solid #d1d5db; padding: 4pt 6pt; vertical-align: top; text-align: left; }
th { background: #f3f4f6; }
img { display: block; max-width: 100%; max-height: 235mm; margin: 8pt auto 4pt; border: 1px solid #d1d5db; border-radius: 4pt; break-inside: avoid; }
p:has(> img) { break-inside: avoid; text-align: center; }
blockquote { margin: 8pt 0; padding: 6pt 10pt; border-left: 4px solid #6d28d9; background: #f5f3ff; break-inside: avoid; }
blockquote p { margin: 0; }
hr { border: 0; border-top: 1px solid #e5e7eb; margin: 14pt 0; }
.cover { border-left: 6px solid #0d7c8a; padding: 6pt 0 6pt 14pt; margin-bottom: 18pt; }
h2#contents + ol { columns: 2; column-gap: 24pt; }
"""
body = re.sub(r"(<h1[^>]*>.*?</h1>\s*<p>.*?</p>)", r'<div class="cover">\1</div>', body, count=1, flags=re.S)
open(out, "w", encoding="utf-8").write(f"<!doctype html><html><head><meta charset='utf-8'><title>Bank Reconciliation — User Guide</title><style>{css}</style></head><body>{body}</body></html>")
print("html bytes", len(body))
