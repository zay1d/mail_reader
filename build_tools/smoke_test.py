# -*- coding: utf-8 -*-
"""Пробный запуск собранного ReceptionMail.exe в демо-режиме: программа стартует, экран отвечает, письма
разбираются, смена открывается и закрывается. Запуск: python build_tools/smoke_test.py <папка установки>"""
import json, os, shutil, subprocess, sys, tempfile, time, urllib.request

src = os.path.abspath(sys.argv[1])
work = os.path.join(tempfile.mkdtemp(prefix="rm-smoke-"), "ReceptionMail")
shutil.copytree(src, work)
cfg_path = os.path.join(work, "config.json")
with open(cfg_path, encoding="utf-8") as f:
    cfg = json.load(f)
cfg.update({"mode": "demo", "port": 8795, "sound": False, "toast": False, "manager_email": "boss@example.uz"})
with open(cfg_path, "w", encoding="utf-8") as f:
    json.dump(cfg, f, ensure_ascii=False)

def call(path, body=None):
    req = urllib.request.Request("http://127.0.0.1:8795" + path,
                                 data=json.dumps(body).encode("utf-8") if body is not None else None,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as r:
        return r.read()

exe = os.path.join(work, "ReceptionMail.exe")
cmd = [exe] if os.path.exists(exe) else [sys.executable, os.path.join(work, "app", "main.py")]   # без exe — из исходников
proc = subprocess.Popen(cmd + ["--no-browser"], cwd=work)
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
    print("Пробный запуск: OK — версия %s, в очереди %d, тревога поднята, отчёт %s"
          % (state["version"], state["counts"]["queue"], rep["file"]))
finally:
    proc.kill()
