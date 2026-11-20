# -*- coding: utf-8 -*-
"""Пробный запуск собранного ReceptionMail.exe в демо-режиме: программа стартует, экран отвечает, письма
разбираются, смена открывается и закрывается. Запуск: python build_tools/smoke_test.py <папка установки>"""
import json, os, shutil, subprocess, sys, tempfile, threading, time, urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

src = os.path.abspath(sys.argv[1])
work = os.path.join(tempfile.mkdtemp(prefix="rm-smoke-"), "ReceptionMail")
shutil.copytree(src, work)
cfg_path = os.path.join(work, "config.json")
with open(cfg_path, encoding="utf-8") as f:
    cfg = json.load(f)
cfg.update({"mode": "demo", "port": 8795, "sound": False, "toast": False, "manager_email": "boss@example.uz",
            "ai_enabled": True, "ai_api_key": "smoke-test-key"})
with open(cfg_path, "w", encoding="utf-8") as f:
    json.dump(cfg, f, ensure_ascii=False)

def call(path, body=None):
    req = urllib.request.Request("http://127.0.0.1:8795" + path,
                                 data=json.dumps(body).encode("utf-8") if body is not None else None,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as r:
        return r.read()

# Поддельный Claude API на этом же компьютере: проверяем, что в собранной программе работает весь путь запроса
# к ИИ (библиотека, сеть, разбор ответа) — без настоящего ключа и без выхода в интернет.
AI_CALLS = []

class FakeClaude(BaseHTTPRequestHandler):
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        key = self.headers.get("x-api-key") or (self.headers.get("Authorization") or "").replace("Bearer ", "")
        AI_CALLS.append({"path": self.path, "key": key, "model": body.get("model")})
        # ИИ — заключительный фильтр по угрозам: поддельный ИИ узнаёт угрозы демо-писем, иначе снял бы их тревогу
        text = json.dumps(body.get("messages") or [], ensure_ascii=False).lower()
        danger = any(w in text for w in ("бомб", "б о м б а", "bomba", "gunman", "взорв"))
        answer = {"category": "security" if danger else "general", "confidence": "high", "category_reason": "пробный ответ",
                  "reason": "пробный ответ", "threat": "explicit" if danger else "none",
                  "threat_reason": "пробный ответ", "keywords": []}
        out = json.dumps({"id": "msg_smoke", "type": "message", "role": "assistant", "model": body.get("model"),
                          "content": [{"type": "text", "text": json.dumps(answer, ensure_ascii=False)}],
                          "stop_reason": "end_turn", "stop_sequence": None,
                          "usage": {"input_tokens": 10, "output_tokens": 10}}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def log_message(self, *a):
        pass

fake = ThreadingHTTPServer(("127.0.0.1", 0), FakeClaude)
threading.Thread(target=fake.serve_forever, daemon=True).start()
env = dict(os.environ, ANTHROPIC_BASE_URL="http://127.0.0.1:%d" % fake.server_address[1], NO_PROXY="127.0.0.1,localhost",
           no_proxy="127.0.0.1,localhost")

exe = os.path.join(work, "ReceptionMail.exe")
if os.path.exists(exe):
    # самопроверка exe: даты из COM читаются (без модуля win32timezone каждое письмо Outlook было «ошибкой чтения»)
    st_file = os.path.join(work, "selftest.txt")
    subprocess.run([exe, "--selftest", st_file], cwd=work, timeout=60)
    with open(st_file, encoding="utf-8") as f:
        st_text = f.read()
    print(st_text)
    assert st_text.startswith("OK"), "самопроверка exe не прошла:\n" + st_text
cmd = [exe] if os.path.exists(exe) else [sys.executable, os.path.join(work, "app", "main.py")]   # без exe — из исходников
proc = subprocess.Popen(cmd + ["--no-browser"], cwd=work, env=env)
try:
    state = None
    for _ in range(60):
        time.sleep(1)
        try:
            state = json.loads(call("/api/state"))
            if state.get("last_poll"):
                break
        except Exception:
            pass
    assert state and state.get("last_poll"), "программа не ответила за 60 с: %r" % (state,)
    with open(os.path.join(work, "app", "core.py"), encoding="utf-8") as f:      # исходники лежат в пакете рядом с exe
        want = f.read().split('VERSION = "', 1)[1].split('"', 1)[0]
    assert state["version"] == want, "версия %s, ожидалась %s" % (state["version"], want)
    assert state["mode"] == "demo" and state["counts"]["queue"] > 0, state["counts"]
    assert b"<html" in call("/").lower(), "экран не отдаётся"
    # второй фильтр (ИИ): библиотека в сборке есть, письма демо-режима дошли до (поддельного) Claude и разобраны
    assert state["ai"]["sdk"] and state["ai"]["state"] == "ok", state["ai"]
    done = []
    for _ in range(40):
        time.sleep(1)
        state = json.loads(call("/api/state"))
        done = [m for m in state["queue"] if m.get("ai_state") == "done"]
        if done:
            break
    assert done, "ни одно письмо не разобрано ИИ: %r; обращений к ИИ: %r" % (state["ai"], AI_CALLS[:3])
    assert AI_CALLS and AI_CALLS[0]["key"] == "smoke-test-key" and "/v1/messages" in AI_CALLS[0]["path"], AI_CALLS[:2]
    assert json.loads(call("/api/demo", {"action": "threat"}))["ok"]
    for _ in range(20):
        time.sleep(1)
        state = json.loads(call("/api/state"))
        if state["threats"]:
            break
    assert state["threats"], "демо-угроза не подняла тревогу"
    assert json.loads(call("/api/open-shift", {"staff": ["Тест"]})).get("ok")
    rep = json.loads(call("/api/close-shift", {"by": "Тест"}))
    assert rep.get("file") and not rep.get("error"), rep
    assert os.path.exists(os.path.join(work, "reports", rep["file"]))
    assert os.path.exists(os.path.join(work, "data", "mail.db"))
    print("Пробный запуск: OK — версия %s, в очереди %d, ИИ разобрал писем: %d (обращений %d), тревога поднята, отчёт %s"
          % (state["version"], state["counts"]["queue"], len(done), len(AI_CALLS), rep["file"]))
finally:
    proc.kill()
