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
from core import Engine
with contextlib.redirect_stdout(io.StringIO()):
    e = Engine(base); e.poll(); e.poll()
rows = e.store._rows("SELECT subject, category, priority, ai_summary, ignored FROM messages WHERE kind='in'")
st = e.state()
print(json.dumps({"calls": len(calls), "req": calls[0] if calls else None, "rows": rows, "ai": st["ai"],
                  "queue": len(st["queue"])}, ensure_ascii=False, default=str))
''')

def run(mode):
    tmp = tempfile.mkdtemp(prefix="rm_test_")
    try:
        for p in ("app", "ui", "sounds"):
            shutil.copytree(os.path.join(ROOT, p), os.path.join(tmp, p))
        shutil.copy(os.path.join(ROOT, "config.json"), tmp)
        os.makedirs(os.path.join(tmp, "data"))
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

sys.exit(1 if check.failed else 0)
