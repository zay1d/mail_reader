# -*- coding: utf-8 -*-
"""Локальный HTTP-сервер: экран ресепшн + JSON API. Только 127.0.0.1."""
import json, os, mimetypes, threading, time, traceback
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse

import config

_PIN_FAILS = []                     # время неверных попыток PIN (общий счётчик: параллельные запросы не обходят паузу)
_PIN_LOCK = threading.Lock()

mimetypes.add_type("font/woff2", ".woff2")
mimetypes.add_type("application/javascript", ".js")

def make_handler(engine, ui_dir, reports_dir):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        # ---- helpers ----
        def _json(self, obj, code=200):
            data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _html(self, text, code=200):
            data = text.encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _file(self, root, rel):
            rel = rel.replace("\\", "/").lstrip("/")
            path = os.path.normpath(os.path.join(root, rel))
            if not path.startswith(os.path.normpath(root)) or not os.path.isfile(path):
                self._html("<h1>404</h1>", 404)
                return
            ctype = mimetypes.guess_type(path)[0] or "application/octet-stream"
            if ctype.startswith("text/") or ctype.endswith("javascript"):
                ctype += "; charset=utf-8"
            with open(path, "rb") as f:
                data = f.read()
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _body(self):
            n = int(self.headers.get("Content-Length") or 0)
            if not n:
                return {}
            try:
                return json.loads(self.rfile.read(n).decode("utf-8"))
            except Exception:
                return {}

        # ---- PIN администратора: настройки, журнал, остановка программы ----
        PROTECTED = {"/api/settings", "/api/log", "/api/quit", "/api/outlook-diag", "/api/test-sound",
                     "/api/telegram-chats", "/api/test-telegram"}

        def _pin_ok(self, p):
            if p not in self.PROTECTED:
                return True
            now = time.time()
            with _PIN_LOCK:
                del _PIN_FAILS[:-50]
                recent = [t for t in _PIN_FAILS if now - t < 300]
            if len(recent) >= 5:          # 5 неверных PIN за 5 минут — вход закрыт на 5 минут (против подбора)
                self._json({"error": "pin", "message": "Слишком много неверных PIN. Подождите 5 минут."}, 403)
                return False
            want = str(engine.cfg.get("admin_pin") if engine.cfg.get("admin_pin") is not None else "0000").strip()
            got = (self.headers.get("X-Pin") or "").strip()
            if got == want:
                return True
            if got:                       # попытка подбора (запрос без PIN — не попытка)
                with _PIN_LOCK:
                    _PIN_FAILS.append(now)
            time.sleep(1)
            engine._log("Отказ по PIN: %s" % p)
            self._json({"error": "pin", "message": "Нужен PIN администратора"}, 403)
            return False

        def _local(self):
            """Запросы принимаются только с экрана самой программы (http://127.0.0.1:порт). Чужая страница, открытая
            в браузере на этом компьютере, не может «нажимать» кнопки программы и читать письма."""
            port = self.server.server_address[1]
            names = ("127.0.0.1:%d" % port, "localhost:%d" % port)
            host = (self.headers.get("Host") or "").strip().lower()
            origin = (self.headers.get("Origin") or "").strip().lower()
            if (host and host not in names) or (origin and origin not in ["http://" + n for n in names]):
                self._json({"error": "forbidden", "message": "Запрос не с экрана программы"}, 403)
                return False
            return True

        # ---- GET ----
        def do_GET(self):
            try:
                if self._local():
                    self._get()
            except (ConnectionError, TimeoutError):
                pass                          # вкладку закрыли, не дождавшись ответа, — это не ошибка программы
            except Exception as e:
                engine._log("Ошибка запроса GET %s: %s" % (self.path[:80], traceback.format_exc()[-300:]))
                try:
                    self._json({"error": "server", "message": str(e)[:200]}, 500)
                except Exception:
                    pass

        def do_POST(self):
            try:
                if self._local():
                    self._post()
            except (ConnectionError, TimeoutError):
                pass
            except Exception as e:
                engine._log("Ошибка запроса POST %s: %s" % (self.path[:80], traceback.format_exc()[-300:]))
                try:
                    self._json({"error": "server", "message": str(e)[:200]}, 500)
                except Exception:
                    pass

        def _get(self):
            p = urlparse(self.path).path
            if not self._pin_ok(p):
                return
            if p == "/":
                return self._file(ui_dir, "index.html")
            if p == "/api/state":
                return self._json(engine.state())
            if p == "/api/settings":
                return self._json(config.public())
            if p == "/api/reports":
                return self._json({"files": engine.reports(), "shifts": engine.store.shifts_recent()})
            if p == "/api/log":
                return self._json({"log": engine.log_tail(300)})
            if p == "/api/report/preview":
                return self._html(engine.preview_report())
            if p.startswith("/reports/"):
                return self._file(reports_dir, p[len("/reports/"):])
            return self._file(ui_dir, p)

        # ---- POST ----
        def _post(self):
            p = urlparse(self.path).path
            if not self._pin_ok(p):
                return
            b = self._body()
            if p == "/api/no-reply":
                return self._json({"ok": engine.no_reply(b.get("id"), b.get("reason"))})
            if p == "/api/undo-no-reply":
                return self._json({"ok": engine.undo_no_reply(b.get("id"))})
            if p == "/api/open":
                return self._json({"ok": engine.open_item(b.get("id"), bool(b.get("view")))})
            if p == "/api/unignore":
                return self._json({"ok": engine.unignore(b.get("id"))})
            if p == "/api/threat-ack":
                engine._log("threat-ack от %s | UA: %s | Referer: %s" % (self.client_address[0], (self.headers.get("User-Agent") or "")[:80], self.headers.get("Referer") or ""))
                return self._json({"ok": engine.ack_threat(b.get("id"))})
            if p == "/api/threat-escalate":
                engine._log("threat-escalate от %s" % self.client_address[0])
                return self._json({"ok": engine.threat_escalate(b.get("id"))})
            if p == "/api/threat-clear":
                engine._log("threat-clear от %s" % self.client_address[0])
                return self._json({"ok": engine.threat_clear(b.get("id"))})
            if p == "/api/open-shift":
                return self._json(engine.open_shift(b.get("staff")))
            if p == "/api/close-shift":
                return self._json(engine.close_shift(b.get("by")))
            if p == "/api/settings":
                engine.apply_settings(b)
                return self._json({"ok": True})
            if p == "/api/test-sound":
                import notify
                notify.test_sound()
                notify.toast("Почта ресепшн", "Проверка уведомлений: всё работает")
                return self._json({"ok": True})
            if p == "/api/outlook-diag":
                return self._json({"text": engine.outlook_diag()})
            if p == "/api/telegram-chats":
                try:
                    import telegram
                    return self._json({"ok": True, "chats": telegram.list_chats(engine.cfg.get("telegram_bot_token"))})
                except Exception as e:
                    return self._json({"ok": False, "error": str(e)[:300]})
            if p == "/api/test-telegram":
                try:
                    engine.test_telegram()
                    return self._json({"ok": True})
                except Exception as e:
                    return self._json({"ok": False, "error": str(e)[:300]})
            if p == "/api/demo":
                return self._json({"ok": engine.demo(b.get("action"))})
            if p == "/api/quit":
                # остановка администратором: снять сторожа, иначе через 5 минут он запустит программу снова
                try:
                    import autostart
                    if autostart.exe_path():
                        engine._log("Остановка программы администратором; " + autostart._watchdog(autostart.exe_path(), False))
                except Exception as e:
                    engine._log("Сторож не снят: " + str(e)[:150])
                self._json({"ok": True})
                threading.Thread(target=lambda: os._exit(0), daemon=True).start()
                return
            return self._json({"error": "unknown"}, 404)
    return H

class _ExclusiveServer(ThreadingHTTPServer):
    """Порт занимает только одна копия программы (на Windows SO_REUSEADDR позволял второй копии сесть на тот же порт)."""
    allow_reuse_address = False

    def server_bind(self):
        import socket
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()

def serve(engine, ui_dir, reports_dir, port):
    srv = _ExclusiveServer(("127.0.0.1", port), make_handler(engine, ui_dir, reports_dir))
    srv.daemon_threads = True
    return srv
