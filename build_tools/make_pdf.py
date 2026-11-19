# -*- coding: utf-8 -*-
"""INSTRUKCIYA.md -> INSTRUKCIYA.html (для печати в PDF: docs/Instrukciya_Pochta_Resepshn.pdf).
Запуск: python build_tools/make_pdf.py <куда положить html>. PDF печатается из html браузером (Chromium)."""
import html, os, re, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

def inline(t):
    t = html.escape(t, quote=False)
    t = re.sub(r"`([^`]+)`", r"<code>\1</code>", t)
    t = re.sub(r"\*\*([^*]+)\*\*", r"<b>\1</b>", t)
    return t

def convert(md):
    out, para, lst = [], [], None          # lst: None | "ul" | "ol"; элементы списка копятся в item
    item = []
    def flush_para():
        if para:
            out.append("<p>%s</p>" % inline(" ".join(para)))
            para.clear()
    def flush_item():
        if item:
            out.append("<li>%s</li>" % inline(" ".join(item)))
            item.clear()
    def close_list():
        nonlocal lst
        flush_item()
        if lst:
            out.append("</%s>" % lst)
            lst = None
    for line in md.splitlines():
        s = line.strip()
        m_h = re.match(r"^(#{1,3})\s+(.*)$", s)
        m_ul = re.match(r"^-\s+(.*)$", s)
        m_ol = re.match(r"^\d+\.\s+(.*)$", s)
        if not s:
            flush_para(); close_list()
        elif m_h:
            flush_para(); close_list()
            n = len(m_h.group(1))
            out.append("<h%d>%s</h%d>" % (n, inline(m_h.group(2)), n))
        elif m_ul or m_ol:
            flush_para()
            kind = "ul" if m_ul else "ol"
            if lst != kind:
                close_list()
                out.append("<%s>" % kind)
                lst = kind
            flush_item()
            item.append((m_ul or m_ol).group(1))
        elif lst and line.startswith(" "):
            item.append(s)                 # продолжение пункта списка
        else:
            close_list()
            para.append(s)
    flush_para(); close_list()
    return "\n".join(out)

CSS = """
@page { size: A4; margin: 16mm 15mm 16mm 15mm; }
body { font-family: "DejaVu Sans", "Segoe UI", Arial, sans-serif; font-size: 10.5pt; line-height: 1.42; color: #1f2430; }
h1 { font-size: 19pt; color: #1B2A4A; margin: 0 0 8pt; padding-bottom: 6pt; border-bottom: 2pt solid #1B2A4A; }
h2 { font-size: 13pt; color: #1B2A4A; margin: 15pt 0 5pt; break-after: avoid; }
p { margin: 0 0 6pt; }
ul, ol { margin: 0 0 6pt; padding-left: 17pt; }
li { margin: 0 0 3.5pt; break-inside: avoid; }
code { font-family: "DejaVu Sans Mono", Consolas, monospace; font-size: 9.3pt; background: #EEF0F4; padding: 0 2.5pt; border-radius: 2pt; }
"""

if __name__ == "__main__":
    with open(os.path.join(ROOT, "INSTRUKCIYA.md"), encoding="utf-8") as f:
        body = convert(f.read())
    dst = sys.argv[1]
    with open(dst, "w", encoding="utf-8") as f:
        f.write("<!doctype html><html lang='ru'><head><meta charset='utf-8'><title>Почта ресепшн — инструкция</title>"
                "<style>%s</style></head><body>%s</body></html>" % (CSS, body))
    print(dst)
