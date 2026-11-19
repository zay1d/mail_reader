# -*- coding: utf-8 -*-
"""Почта ресепшн — точка входа. Запуск: python app/main.py или ReceptionMail.exe
Ключи: --no-browser (не открывать экран), --watchdog (сторож: если программа уже работает — тихо выйти,
иначе запустить; так её перезапускает планировщик Windows каждые 5 минут)."""
import os, sys, threading, time, webbrowser, socket, traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config, notify
from core import Engine
from server import serve

def _alive(port):
    s = socket.socket()
    try:
        s.settimeout(0.5)
        s.connect(("127.0.0.1", port))
        return True
    except Exception:
        return False
    finally:
        s.close()

def _fatal(msg):
    """Программа без консоли: причину падения пишем в data/error.log и показываем окно."""
    try:
        os.makedirs(os.path.join(config.BASE, "data"), exist_ok=True)
        with open(os.path.join(config.BASE, "data", "error.log"), "a", encoding="utf-8") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + msg + "\n")
    except Exception:
        pass
    if os.name == "nt" and "--watchdog" not in sys.argv:
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(0, msg[:900], "Почта ресепшн — не запустилась", 0x10)
        except Exception:
            pass

_MUTEX = None

def _single_instance(port):
    """Именованный мьютекс Windows: вторая копия программы сразу понимает, что первая уже работает
    (проверка порта не успевает: первая копия несколько секунд запускается)."""
    global _MUTEX
    if os.name != "nt":
        return True
    try:
        import ctypes
        k = ctypes.windll.kernel32
        _MUTEX = k.CreateMutexW(None, False, r"Local\TCM_ReceptionMail_%d" % port)
        return k.GetLastError() != 183          # ERROR_ALREADY_EXISTS
    except Exception:
        return True

def _restart():
    """Запустить новую копию программы и завершить эту (зависший вызов Outlook иначе не прервать)."""
    args = [sys.executable] if getattr(sys, "frozen", False) else [sys.executable, os.path.abspath(__file__)]
    args += ["--no-browser", "--after-restart"]
    try:
        import subprocess
        flags = 0x00000008 | 0x00000200 if os.name == "nt" else 0     # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
        subprocess.Popen(args, close_fds=True, creationflags=flags, cwd=config.BASE,
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception as e:
        _fatal("Перезапуск после зависания не удался: %s" % e)
        return
    os._exit(0)

def _watch(engine):
    """Сторож зависшего опроса: Outlook не отвечает 3 мин — звук и уведомление, 5 мин — перезапуск программы."""
    last = time.time()
    while True:
        time.sleep(15)
        now = time.time()
        if now - last > 90:               # компьютер спал: это не зависание, отсчёт заново
            engine.heartbeat = now
        last = now
        try:
            if engine.watch_stall(now):
                _restart()
        except Exception:
            pass

def main():
    base = config.BASE
    cfg = config.load()
    try:
        port = int(cfg.get("port") or 8791)
    except Exception:
        port = 8791
    url = "http://127.0.0.1:%d/" % port
    if "--after-restart" in sys.argv:     # перезапуск после зависания: дождаться, пока прежняя копия освободит порт
        for _ in range(60):
            if not _alive(port):
                break
            time.sleep(0.5)
        time.sleep(1.5)
    if not _single_instance(port) or _alive(port):   # уже запущено (двойной клик, сторож, автозапуск)
        if "--watchdog" not in sys.argv:
            for _ in range(20):           # первая копия ещё запускается — дождаться экрана
                if _alive(port):
                    break
                time.sleep(0.5)
            webbrowser.open(url)          # просто показать экран
        return
    try:
        notify.prepare(os.path.join(base, "sounds"))
        engine = Engine(base)
        ui_dir = os.path.join(base, "ui")
        reports_dir = os.path.join(base, "reports")
        os.makedirs(reports_dir, exist_ok=True)
        if not os.path.isfile(os.path.join(ui_dir, "index.html")):
            raise RuntimeError("Не найдена папка ui рядом с программой (%s). Восстановите папку из установочного архива." % ui_dir)
        try:
            srv = None
            # после перезапуска порт прежней копии освобождается не мгновенно — ждём до 90 с
            for attempt in range(45 if "--after-restart" in sys.argv else 1):
                try:
                    srv = serve(engine, ui_dir, reports_dir, port)
                    break
                except OSError as e:
                    err = e
                    time.sleep(2)
            if srv is None:
                raise err
        except OSError as e:
            raise RuntimeError("Порт %d занят другой программой (%s). Закройте её или смените порт в config.json." % (port, e))
    except Exception as e:
        _fatal("Ошибка запуска: %s\n%s" % (e, traceback.format_exc()[-800:]))
        return
    threading.Thread(target=engine.loop, daemon=True).start()
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    threading.Thread(target=_watch, args=(engine,), daemon=True).start()
    try:
        print("Почта ресепшн: " + url)
    except Exception:
        pass
    if "--no-browser" not in sys.argv:
        webbrowser.open(url)
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass

if __name__ == "__main__":
    main()
