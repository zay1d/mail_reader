# -*- coding: utf-8 -*-
"""Смены и отчёт по смене."""
import os, html, base64
import donut
from datetime import datetime, timedelta

def _hm(s):
    """«09:00», «9:00», «9.00», «24:00» -> минуты; ошибка в Настройках не должна останавливать программу."""
    import re
    m = re.match(r"^\s*(\d{1,2})\s*[:.]\s*(\d{2})\s*$", str(s or ""))
    if not m:
        raise ValueError("время смены «%s» — нужно ЧЧ:ММ" % s)
    return min(24 * 60, int(m.group(1)) * 60 + int(m.group(2)))

def valid_shifts(cfg):
    """Смены из настроек без ошибочных строк; если все с ошибками — стандартные 09:00–17:00 и 16:00–24:00."""
    out = []
    for s in cfg.get("shifts") or []:
        try:
            _hm(s.get("start")); _hm(s.get("end"))
            out.append(s)
        except Exception:
            continue
    return out or [{"name": "Смена 1", "start": "09:00", "end": "17:00"}, {"name": "Смена 2", "start": "16:00", "end": "24:00"}]

def _iso(d):
    return d.strftime("%Y-%m-%dT%H:%M:%S")

def _p(iso):
    try:
        return datetime.strptime(iso[:19], "%Y-%m-%dT%H:%M:%S")
    except Exception:
        return None

def _fmt_dt(iso):
    d = _p(iso)
    return d.strftime("%d.%m.%Y %H:%M") if d else ""

def _fmt_t(iso):
    d = _p(iso)
    return d.strftime("%H:%M") if d else ""

def _mins(a, b):
    da, db = _p(a), _p(b)
    return int((db - da).total_seconds() // 60) if da and db else None

def _dur(m):
    if m is None:
        return ""
    m = max(0, int(m))
    return "%d:%02d" % (m // 60, m % 60)

def shifts_of_day(day, cfg):
    """Список смен даты day с окнами учёта писем."""
    base = datetime(day.year, day.month, day.day)
    lst = valid_shifts(cfg)
    out = []
    for i, s in enumerate(lst):
        start = base + timedelta(minutes=_hm(s["start"]))
        end = base + timedelta(minutes=_hm(s["end"]))
        win_start = base if i == 0 else start
        win_end = base + timedelta(days=1) if i == len(lst) - 1 else base + timedelta(minutes=_hm(lst[i + 1]["start"]))
        out.append({
            "key": "%s_%d" % (base.strftime("%Y-%m-%d"), i + 1), "idx": i + 1,
            "name": s.get("name") or ("Смена %d" % (i + 1)),
            "start": start, "end": end, "win_start": win_start, "win_end": win_end,
            "label": "%s %s–%s" % (s.get("name") or "", s["start"], s["end"]),
        })
    return out

def shift_key_for(dt, cfg):
    for s in shifts_of_day(dt, cfg):
        if s["win_start"] <= dt < s["win_end"]:
            return s["key"]
    return dt.strftime("%Y-%m-%d") + "_1"

def sla_start(dt, cfg):
    """С какого момента считать срок ответа: письмо в рабочие часы — с получения;
    ночное (вне смен) — с начала ближайшей смены."""
    for d in (dt, dt + timedelta(days=1)):
        for s in shifts_of_day(d, cfg):
            if s["start"] <= dt < s["end"]:
                return dt
            if s["start"] > dt:
                return s["start"]
    return dt

def is_night(dt, cfg):
    return sla_start(dt, cfg) != dt

def active_shifts(now, cfg):
    return [s for s in shifts_of_day(now, cfg) if s["start"] <= now < s["end"]]

def shift_name_at(dt, cfg):
    """Название смены — только подпись: смена расписания, чьё начало ближе всего к моменту открытия."""
    lst = shifts_of_day(dt, cfg)
    if not lst:
        return "Смена", 1
    s = min(lst, key=lambda x: abs((x["start"] - dt).total_seconds()))
    return s["name"], s["idx"]

def session_shift(ses, prev_close, cfg, now=None):
    """Смена = от кнопки «Открыть смену» до кнопки «Закрыть смену» (решение Олега 17.09, часы не участвуют).
    Письма считаются с момента закрытия предыдущей смены: пришедшее между сменами достаётся следующей."""
    now = now or datetime.now()
    opened = _p(ses.get("opened_at") or "") or now
    closed = _p(ses.get("closed_at") or "")
    ws = _p(prev_close or "") or datetime(opened.year, opened.month, opened.day)
    if ws > opened:
        ws = opened
    name, idx = shift_name_at(opened, cfg)
    when = opened.strftime("%H:%M") if opened.date() == now.date() else opened.strftime("%d.%m %H:%M")
    return {"key": "s%d" % int(ses.get("id") or 0), "idx": idx, "name": name, "start": opened, "end": closed or now,
            "win_start": ws, "win_end": closed or (now + timedelta(days=3650)),
            "label": "%s · открыта в %s" % (name, when), "opened_label": when}

# ---------- статистика и отчёт ----------
def shift_stats(shift, store, cfg, upto=None):
    """Цифры отчёта = цифры экрана (аудит 25.09): «Пришло» — письма, полученные за смену;
    «Отвечено» — ответы, отправленные за смену (в т.ч. на письма прошлых смен); «Без ответа» —
    очередь на момент закрытия (в т.ч. перешедшие с прошлых смен)."""
    ws, we = _iso(shift["win_start"]), _iso(shift["win_end"])
    cut = min(we, _iso(upto)) if upto else we
    rm_fw = bool(cfg.get("forward_removes_from_queue", True))
    letters = store.incoming_between(ws, cut)
    auto = [m for m in store.incoming_between(ws, cut, include_ignored=True) if m.get("ignored")]
    forwarded = store.forwarded_between(ws, cut)       # как на экране: переслано за смену
    # сняты с очереди без ответа за смену (удалено в Outlook и т.п.); угрозы — в своей таблице
    closed = [m for m in store.no_reply_between(ws, cut) if not m.get("threat_words")]
    hidden = store.hidden_between(ws, cut)              # рассылки и автописьма — «Не попали в очередь»
    answered = store.answered_between(ws, cut)
    unanswered = store.queue(rm_fw)
    no_reply = [m for m in letters if not m.get("answered_at") and m.get("no_reply_reason")]
    need = len(answered) + len(unanswered)
    rate = round(100.0 * len(answered) / need, 1) if need > 0 else None
    def _base(m):
        d = _p(m["received_at"])
        return _iso(sla_start(d, cfg)) if d else m["received_at"]
    times = [_mins(_base(m), m["answered_at"]) for m in answered]
    times = [max(0, t) for t in times if t is not None]
    night = [m for m in letters if _p(m["received_at"]) and is_night(_p(m["received_at"]), cfg)]
    carried_done = [m for m in answered if (m.get("received_at") or "") < ws]
    carried_open = [m for m in unanswered if (m.get("received_at") or "") < ws]
    outgoing = store.outgoing_between(ws, cut)
    threats = store.threats_between(ws, cut)
    sla = int(cfg.get("sla_overdue_minutes") or 60)
    in_sla = sum(1 for m in answered
                 if max(0, _mins(_base(m), m["answered_at"]) or 0) <= sla_for(cfg, m.get("priority")))
    cats = []
    for c in cfg.get("categories") or []:
        k = c.get("key")
        of = lambda lst: [m for m in lst if (m.get("category") or "general") == k]
        rc, ak, uk, fk = of(letters), of(answered), of(unanswered), of(forwarded)
        if rc or ak or uk or fk:
            # «Переслано» по направлениям — как итог «Переслано в отделы»: пересланные за смену
            cats.append({"name": c.get("name"), "received": len(rc), "answered": len(ak),
                         "forwarded": len(fk), "unanswered": len(uk)})
    pri = {p: sum(1 for m in letters if (m.get("priority") or 2) == p) for p in (1, 2, 3)}
    return {
        "received": len(letters), "answered": len(answered), "no_reply": len(no_reply),
        "unanswered": len(unanswered), "rate": rate, "need": need,
        "avg_min": round(sum(times) / len(times)) if times else None,
        "max_min": max(times) if times else None,
        "in_sla": in_sla, "sla": sla,
        "auto": len(auto), "outgoing": len(outgoing), "threats": len(threats),
        "auto_replied": sum(1 for m in letters if m.get("auto_replied_at")),
        "carried_done": len(carried_done), "carried_open": len(carried_open),
        "categories": cats, "priorities": pri, "conv": store.conv_counts(),
        "with_attachments": sum(1 for m in letters if m.get("attachments")),
        "forwarded": len(forwarded), "night": len(night), "closed": len(closed), "hidden": len(hidden),
        "lists": {"unanswered": unanswered, "answered": answered, "no_reply": no_reply,
                  "threats": threats, "carried_open": carried_open, "forwarded": forwarded, "closed": closed},
    }

def _att(m):
    a = m.get("attachments") or []
    return ", ".join(x.get("name", "") for x in a) if a else "—"

def _fsize(n):
    n = int(n or 0)
    return "%d КБ" % round(n / 1024) if n < 1048576 else "%s МБ" % str(round(n / 1048576, 1)).replace(".", ",")

def sla_for(cfg, priority):
    m = (cfg.get("priority") or {}).get("sla") or {}
    return int(m.get(str(priority or 2)) or cfg.get("sla_overdue_minutes") or 60)

def pri_name(cfg, priority):
    m = (cfg.get("priority") or {}).get("names") or {}
    return m.get(str(priority or 2)) or {1: "Срочно", 2: "Обычно", 3: "Низкий"}.get(priority or 2, "")

def cat_name(cfg, key):
    for c in cfg.get("categories") or []:
        if c.get("key") == key:
            return c.get("name") or key
    return "Общие вопросы"

_CSS = ('font-family:"Segoe UI",Inter,Arial,sans-serif;color:#2D2D2D;font-size:13px;')

def _table(rows, cols):
    if not rows:
        return "<p style='color:#6B7280;margin:6px 0 14px'>— нет —</p>"
    th = "".join("<th style='text-align:left;padding:6px 8px;background:#1B2A4A;color:#fff;font-weight:600;font-size:12px'>%s</th>" % c[0] for c in cols)
    trs = []
    for i, r in enumerate(rows):
        bg = "#fff" if i % 2 == 0 else "#F4F6FA"
        tds = "".join("<td style='padding:5px 8px;border-bottom:1px solid #E5E7EB;background:%s'>%s</td>" % (bg, html.escape(str(c[1](r) or ""))) for c in cols)
        trs.append("<tr>" + tds + "</tr>")
    return "<table cellspacing='0' cellpadding='0' style='border-collapse:collapse;width:100%%;margin:4px 0 10px'><tr>%s</tr>%s</table>" % (th, "".join(trs))

def _who(m):
    n, a = m.get("sender_name") or "", m.get("sender") or ""
    return "%s <%s>" % (n, a) if n and a and n.lower() != a.lower() else (n or a)

def build_report(shift, stats, cfg, closed_by, now, donut_src=None):
    st = stats
    date = shift["start"].strftime("%d.%m.%Y")
    title = "Отчёт ресепшн по почте: %s, %s" % (shift["name"], date)
    rate = ("%s%%" % str(st["rate"]).replace(".", ",")) if st["rate"] is not None else "—"
    kpi = [
        ("Пришло писем", st["received"]), ("Отвечено", st["answered"]),
        ("Без ответа", st["unanswered"]), ("Переслано в отделы", st["forwarded"]), ("Доля ответов", rate),
        ("Среднее время ответа, ч:мм", _dur(st["avg_min"]) or "—"),
        ("Ответ позже срока", st["answered"] - st["in_sla"] if st["answered"] else 0),
    ]
    P = lambda m: pri_name(cfg, m.get("priority"))
    C = lambda m: cat_name(cfg, m.get("category"))
    conv = st.get("conv") or {}
    N = lambda m: conv.get(m.get("conv_id") or "", 1)
    kpi_html = "".join(
        "<td style='padding:7px 6px;border:1px solid #E5E7EB;text-align:center;width:14%%'>"
        "<div style='font-size:19px;font-weight:700;color:%s'>%s</div><div style='font-size:11px;line-height:1.25;color:#6B7280'>%s</div></td>"
        % ("#C0392B" if (k == "Без ответа" and v) else "#1B2A4A", v, k) for k, v in kpi)
    if donut_src:
        kpi_html = ("<td style='padding:4px 10px;border:1px solid #E5E7EB;width:150px;vertical-align:middle'>"
                    "<table cellspacing='0' cellpadding='0'><tr>"
                    "<td style='padding:0 10px 0 0;vertical-align:middle'><img src='%s' width='72' height='72' alt='' style='display:block'></td>"
                    "<td style='vertical-align:middle'><div style='font-size:22px;font-weight:700;color:#27AE60;line-height:1'>%s</div>"
                    "<div style='font-size:12px;color:#6B7280;margin-top:4px'>отвечено</div></td>"
                    "</tr></table></td>"
                    % (donut_src, rate)) + kpi_html
    verdict = ("<p style='margin:10px 0;padding:8px 12px;background:#EAF4EE;border-left:4px solid #2E7D52'>"
               "<b>Все письма смены обработаны.</b></p>" if st["unanswered"] == 0 else
               "<p style='margin:10px 0;padding:8px 12px;background:#FBEDEB;border-left:4px solid #C0392B'>"
               "<b>Без ответа осталось писем: %d.</b> Они переданы следующей смене.</p>" % st["unanswered"])
    L = st["lists"]
    threat_html = ""
    if st["threats"]:
        threat_html = ("<p style='margin:10px 0;padding:8px 12px;background:#FBEDEB;border-left:4px solid #C0392B'>"
                       "<b>Сигналов угрозы: %d</b> (явных %d, возможных %d). Список ниже.</p>"
                       % (st["threats"], sum(1 for m in L["threats"] if (m.get("threat_level") or 2) == 2),
                          sum(1 for m in L["threats"] if (m.get("threat_level") or 2) == 1)))
    body = "".join([
        "<div style='%s'>" % _CSS,
        "<div style='background:#1B2A4A;color:#fff;padding:12px 18px'><div style='font-size:16px;font-weight:700'>%s</div>"
        "<div style='font-size:12px;opacity:.85;margin-top:2px'>Tashkent City Mall · смена открыта %s · отчёт на %s · учтены письма, полученные с %s%s%s%s</div></div>"
        % (html.escape(title), shift["start"].strftime("%d.%m.%Y %H:%M"), now.strftime("%d.%m.%Y %H:%M"),
           shift["win_start"].strftime("%d.%m %H:%M"),
           (" · с прошлых смен: отвечено %d, ждут ответа %d" % (st["carried_done"], st["carried_open"])) if (st["carried_done"] or st["carried_open"]) else "",
           (" · рассылок и автописем (не требуют ответа): %d" % st.get("hidden", 0)) if st.get("hidden") else "",
           " · на смене: " + html.escape(closed_by) if closed_by else ""),
        "<div style='padding:12px 18px 6px'>",
        "<table cellspacing='0' cellpadding='0' style='border-collapse:collapse;width:100%%'><tr>%s</tr></table>" % kpi_html,
        threat_html,
        "<h3 style='font-size:14px;margin:12px 0 4px;color:#1B2A4A'>По направлениям</h3>",
        _table(st["categories"], [("Направление", lambda c: c["name"]), ("Пришло", lambda c: c["received"]),
                                  ("Отвечено", lambda c: c["answered"]), ("Переслано", lambda c: c["forwarded"]),
                                  ("Без ответа", lambda c: c["unanswered"])]),
    ])
    if L["forwarded"]:
        body += "<h3 style='font-size:14px;margin:12px 0 4px;color:#1B2A4A'>Переслано в отделы (%d)</h3>" % len(L["forwarded"])
        body += _table(L["forwarded"], [("Получено", lambda m: _fmt_t(m["received_at"])), ("Направление", C),
                                        ("От кого", _who), ("Тема", lambda m: m.get("subject")),
                                        ("Кому переслано", lambda m: m.get("forwarded_to")),
                                        ("Когда", lambda m: _fmt_t(m.get("forwarded_at") or ""))])
    if L.get("closed"):
        body += "<h3 style='font-size:14px;margin:12px 0 4px;color:#1B2A4A'>Сняты с очереди без ответа, кроме угроз (%d)</h3>" % len(L["closed"])
        body += _table(L["closed"], [("Получено", lambda m: _fmt_t(m["received_at"])), ("От кого", _who),
                                     ("Тема", lambda m: m.get("subject")), ("Причина", lambda m: m.get("no_reply_reason")),
                                     ("Когда", lambda m: _fmt_t(m.get("no_reply_at") or ""))])
    if L["threats"]:
        body += "<h3 style='font-size:14px;margin:12px 0 4px;color:#C0392B'>Сигналы угрозы (%d)</h3>" % len(L["threats"])
        def _lvl(m):
            return "Явная" if (m.get("threat_level") or 2) == 2 else "Возможная"
        def _res(m):
            if not m.get("threat_ack_at"):
                return "не обработано"
            if (m.get("threat_level") or 2) == 2:
                return "передано в СБ " + _fmt_t(m.get("threat_ack_at"))
            return "проверено, угрозы нет " + _fmt_t(m.get("threat_ack_at"))
        body += _table(L["threats"], [("Получено", lambda m: _fmt_t(m["received_at"])), ("Уровень", _lvl), ("От кого", _who),
                                      ("Тема", lambda m: m.get("subject")),
                                      ("Слова", lambda m: ", ".join(m.get("threat_words") or [])),
                                      ("Итог", _res)])
    body += "</div></div>"
    page = ("<!doctype html><html><head><meta charset='utf-8'><title>%s</title></head>"
            "<body style='margin:0;background:#EEF0F4;padding:28px 16px'>"
            "<div style='max-width:860px;margin:0 auto;background:#fff;border-radius:14px;overflow:hidden;"
            "box-shadow:0 6px 24px rgba(27,42,74,.14)'>%s</div></body></html>" % (html.escape(title), body))
    return title, body, page

def close_shift(shift, store, cfg, source, reports_dir, closed_by, now=None):
    now = now or datetime.now()
    stats = shift_stats(shift, store, cfg, upto=now)
    os.makedirs(reports_dir, exist_ok=True)
    fname = "%s_%s_smena-%d.html" % (shift["start"].strftime("%Y-%m-%d_%H%M"), shift["key"], shift["idx"])
    path = os.path.join(reports_dir, fname)
    png_name = fname[:-5] + "_donut.png"
    png_path = os.path.join(reports_dir, png_name)
    rate = (stats["rate"] / 100.0) if stats.get("rate") is not None else None
    donut.render(rate, path=png_path)
    title, body_file, page = build_report(shift, stats, cfg, closed_by, now, donut_src=png_name)
    title, body_mail, _ = build_report(shift, stats, cfg, closed_by, now, donut_src="cid:donut")
    with open(path, "w", encoding="utf-8") as f:
        f.write(page)
    sent_to, err = "", ""
    to = (cfg.get("manager_email") or "").strip()
    if to:
        try:
            source.send_mail(to, (cfg.get("report_cc") or "").strip(), title, body_mail, inline=[("donut", png_path)])
            sent_to = to
        except Exception as e:
            err = "Отчёт сохранён, но не отправлен: " + str(e)[:200]
    store.shift_close(shift["key"], shift["name"], _iso(shift["start"]), _iso(now),
                      closed_by, fname, sent_to)
    return {"file": fname, "sent_to": sent_to, "error": err, "stats": {k: v for k, v in stats.items() if k != "lists"}}
