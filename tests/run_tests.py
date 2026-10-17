# -*- coding: utf-8 -*-
"""Проверка без Outlook и без интернета: копия программы во временной папке, демо-режим.
ИИ подменяется заглушкой, которая отвечает в формате Claude API. Запуск: python tests/run_tests.py"""
import json, os, shutil, subprocess, sys, tempfile, textwrap

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

CHILD = textwrap.dedent(r'''
import sys, os, json, io, contextlib
base, mode = sys.argv[1], sys.argv[2]
sys.path.insert(0, os.path.join(base, "app"))
cfgp = os.path.join(base, "config.json")
c = json.load(open(cfgp, encoding="utf-8"))
c.update(mode="demo", ai_enabled=(mode != "off"), ai_api_key="sk-test", autostart=False)
json.dump(c, open(cfgp, "w", encoding="utf-8"), ensure_ascii=False)
import httpx2, anthropic, ai
calls = []
def handler(req):
    body = json.loads(req.content)
    calls.append(body)
    if mode == "fail":
        return httpx2.Response(401, json={"type": "error", "error": {"type": "authentication_error", "message": "bad key"}})
    u = body["messages"][0]["content"].lower()
    out = {"category": "marketing" if "коммерческое предложение" in u else ("rent" if "аренд" in u else
                                                                         ("complaint" if "жалоба" in u else "general")),
           "priority": "low" if "коммерческое предложение" in u else "normal", "lang": "ru",
           "threat": "none", "threat_reason": "", "summary": "кратко"}
    return httpx2.Response(200, json={"id": "msg_1", "type": "message", "role": "assistant", "model": body["model"],
        "content": [{"type": "text", "text": json.dumps(out, ensure_ascii=False)}],
        "stop_reason": "end_turn", "stop_sequence": None, "usage": {"input_tokens": 1, "output_tokens": 1}})
ai.AiClassifier._get_client = lambda self: anthropic.Anthropic(api_key="x", max_retries=0,
    http_client=httpx2.Client(transport=httpx2.MockTransport(handler)))
import mail_demo
fail = {"on": mode == "retry"}
for name in ("send_reply", "forward"):
    orig = getattr(mail_demo.DemoSource, name)
    def wrap(self, *a, _o=orig, **k):
        if fail["on"]:
            raise RuntimeError("Outlook занят")
        return _o(self, *a, **k)
    setattr(mail_demo.DemoSource, name, wrap)
from core import Engine
with contextlib.redirect_stdout(io.StringIO()):
    e = Engine(base); e.poll(); e.poll()
    if mode == "retry":
        before = len(e.store._rows("SELECT 1 FROM messages WHERE auto_replied_at IS NOT NULL"))
        fail["on"] = False
        e.action_next.clear()          # не ждать паузу между попытками
        e.poll()
rows = e.store._rows("SELECT subject, category, priority, ai_summary, ignored, auto_replied_at, forwarded_to FROM messages WHERE kind='in'")
st = e.state()
print(json.dumps({"calls": len(calls), "req": calls[0] if calls else None, "rows": rows, "ai": st["ai"],
                  "queue": len(st["queue"]), "before": locals().get("before")}, ensure_ascii=False, default=str))
''')

def run(mode):
    tmp = tempfile.mkdtemp(prefix="rm_test_")
    try:
        for p in ("app", "ui", "sounds"):
            shutil.copytree(os.path.join(ROOT, p), os.path.join(tmp, p))
        shutil.copy(os.path.join(ROOT, "config.json"), tmp)
        os.makedirs(os.path.join(tmp, "data"))
        os.makedirs(os.path.join(tmp, "reports"))
        script = os.path.join(tmp, "_child.py")
        open(script, "w", encoding="utf-8").write(CHILD)
        r = subprocess.run([sys.executable, script, tmp, mode], capture_output=True, text=True, encoding="utf-8")
        if r.returncode:
            raise SystemExit("ОШИБКА (%s):\n%s" % (mode, r.stderr[-3000:]))
        return json.loads(r.stdout.strip().splitlines()[-1])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

def check(cond, msg):
    print(("OK   " if cond else "FAIL ") + msg)
    if not cond:
        check.failed = True
check.failed = False

off = run("off")
by = {r["subject"]: r for r in off["rows"]}
check(off["calls"] == 0, "ИИ выключен — запросов к API нет")
check(by["Аренда площадки под промо-акцию"]["category"] == "rent", "ключевые слова: аренда → rent")
check(by["Срочно! Жалоба на уборку туалетов 2 этаж"]["priority"] == 1, "ключевые слова: «срочно» → приоритет 1")
check(off["queue"] > 0, "очередь на экране не пустая")

on = run("on")
by = {r["subject"]: r for r in on["rows"]}
n_new = sum(1 for r in on["rows"] if not r["ignored"])
check(on["calls"] == n_new, "в ИИ отправлены только письма для очереди (%d из %d)" % (on["calls"], len(on["rows"])))
req = on["req"]
check(req["output_config"]["format"]["type"] == "json_schema" and req["output_config"]["effort"] == "low", "запрос: JSON-схема, effort low")
check(req.get("fallbacks") == "default", "запрос: серверная замена модели при отказе")
k = "Коммерческое предложение: клининговое оборудование"
check(by[k]["category"] == "marketing" and by[k]["priority"] == 3, "ответ ИИ применён: направление и приоритет")
j = by["Срочно! Жалоба на уборку туалетов 2 этаж"]
check(j["category"] == "complaint" and j["priority"] == 1, "ИИ сказал «обычно», но правило «Жалобы — всегда срочно» сохраняется")
check(all(r["ai_summary"] == "кратко" for r in on["rows"] if not r["ignored"]), "краткое содержание сохранено")
check(on["ai"]["enabled"] and on["ai"]["classified"] == n_new, "статус ИИ на экране")

fail = run("fail")
check([r["category"] for r in fail["rows"]] == [r["category"] for r in off["rows"]], "ИИ с ошибкой — сортировка по ключевым словам как без ИИ")
check("ключ" in (fail["ai"]["error"] or ""), "ошибка ИИ видна в статусе: " + (fail["ai"]["error"] or ""))

rt = run("retry")
# с повтором сравниваем прогон «on» (ИИ-заглушка включена в обоих)
n_on = sum(1 for r in on["rows"] if r["auto_replied_at"])
check(rt["before"] == 0, "Outlook занят: автоответы не ушли")
check(sum(1 for r in rt["rows"] if r["auto_replied_at"]) == n_on and n_on > 0,
      "после повтора автоответы ушли всем (%d)" % n_on)
check([r["forwarded_to"] for r in rt["rows"]] == [r["forwarded_to"] for r in on["rows"]]
      and any(r["forwarded_to"] for r in rt["rows"]), "после повтора пересылка в отдел выполнена")

# детектор угроз: настоящие угрозы ловятся, безобидные обороты — нет
sys.path.insert(0, os.path.join(ROOT, "app"))
import config, threats
det = threats.Detector(config.DEFAULTS)
RED = ["Я живу в квартире рядом с ТЦ. Завтра будет стрельба", "Hammangizni oʻldiraman", "binoda to‘pponcha bor",
       "Savdo markazga oʻt qoʻyaman", "I will kill\r\nyou all", "заложено взрывное\r\nустройство",
       "В здании бом\u00adба", "бом\u200bба", "Всех зарежу в вашем ТЦ", "Ваше здание взлетит на воздух",
       "I will murder you all", "I will shoot up the mall", "В вашем ТЦ заложена бомба"]
NONE = ["Стрельба в тире завтра", "Это просто бомба! Скидки", "photo shoot in the mall", "Порежу торт",
        "Хотим арендовать помещение 40 кв.м", "Скидки взлетят до небес", "Murder mystery quest для детей"]
for t in RED:
    check(det.analyze(t)[0] == 2, "угроза: " + t.replace("\r\n", " ")[:40])
for t in NONE:
    check(det.analyze(t)[0] == 0, "не угроза: " + t)
check(det.analyze("мы найдем\r\nвас")[0] == 1, "возможная угроза через перенос строки")

# сроки: вечерняя смена до 00:00 и ночная смена
import shifts
from datetime import datetime as _dt
c2 = dict(config.DEFAULTS, shifts=[{"name": "1", "start": "09:00", "end": "17:00"}, {"name": "2", "start": "16:00", "end": "00:00"}])
check(shifts.sla_start(_dt(2026, 10, 17, 18, 30), c2) == _dt(2026, 10, 17, 18, 30), "смена до 00:00: письмо в 18:30 не «ночное»")
check(shifts.sla_start(_dt(2026, 10, 18, 3, 0), c2) == _dt(2026, 10, 18, 9, 0), "письмо в 03:00 — срок с 09:00")
c3 = dict(config.DEFAULTS, shifts=[{"name": "н", "start": "20:00", "end": "08:00"}])
check(shifts.sla_start(_dt(2026, 10, 18, 3, 0), c3) == _dt(2026, 10, 18, 3, 0), "ночная смена 20–08: письмо в 03:00 в рабочее время")

sys.exit(1 if check.failed else 0)
