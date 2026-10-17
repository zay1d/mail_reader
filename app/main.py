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

def main():
    base = config.BASE
    cfg = config.load()
    port = int(cfg.get("port") or 8791)
    url = "http://127.0.0.1:%d/" % port
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
            srv = serve(engine, ui_dir, reports_dir, port)
        except OSError as e:
            raise RuntimeError("Порт %d занят другой программой (%s). Закройте её или смените порт в config.json." % (port, e))
    except Exception as e:
        _fatal("Ошибка запуска: %s\n%s" % (e, traceback.format_exc()[-800:]))
        return
    threading.Thread(target=engine.loop, daemon=True).start()
    threading.Thread(target=srv.serve_forever, daemon=True).start()
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
