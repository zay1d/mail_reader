# -*- coding: utf-8 -*-
"""Автозапуск программы при входе в Windows и ярлык на рабочем столе.
Только для текущего пользователя (HKCU), без прав администратора. Работает для ReceptionMail.exe;
при запуске из исходников (python app/main.py) ничего не меняет."""
import os, sys

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
NAME = "ReceptionMail"
LNK = "Почта ресепшн.lnk"

def exe_path():
    return sys.executable if getattr(sys, "frozen", False) else ""

def _set(name, exe, enabled):
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
        if enabled:
            winreg.SetValueEx(k, name, 0, winreg.REG_SZ, '"%s"' % exe)
        else:
            try:
                winreg.DeleteValue(k, name)
            except FileNotFoundError:
                pass

def is_enabled(name=NAME):
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            v, _ = winreg.QueryValueEx(k, name)
        return (v or "").strip('"').lower() == (exe_path() or "?").lower()
    except Exception:
        return False

def _shell():
    import pythoncom, win32com.client
    pythoncom.CoInitialize()
    return win32com.client.Dispatch("WScript.Shell")

def _drop_startup_shortcuts(exe):
    """Ярлык из папки «Автозагрузка» (его ставил install_autostart.bat прежних версий) убираем,
    иначе программа стартует дважды и открывает два окна браузера."""
    sh = _shell()
    d = sh.SpecialFolders("Startup")
    for f in os.listdir(d):
        if f.lower().endswith(".lnk"):
            p = os.path.join(d, f)
            try:
                if os.path.basename(sh.CreateShortcut(p).TargetPath or "").lower() == os.path.basename(exe).lower():
                    os.remove(p)
            except Exception:
                pass

def _desktop_shortcut(exe):
    sh = _shell()
    p = os.path.join(sh.SpecialFolders("Desktop"), LNK)
    s = sh.CreateShortcut(p)
    if os.path.exists(p) and (s.TargetPath or "").lower() == exe.lower():
        return
    s.TargetPath = exe
    s.WorkingDirectory = os.path.dirname(exe)
    s.Description = "Почта ресепшн — контроль входящих писем"
    s.Save()

TASK = "ReceptionMail Watchdog"

def _watchdog(exe, enabled):
    """Сторож в Планировщике Windows: каждые 5 минут запускает ReceptionMail.exe --watchdog.
    Если программа работает — сторож тихо выходит; если её закрыли (Диспетчер задач, сбой) —
    запускает заново. Задача для текущего пользователя, без прав администратора."""
    import subprocess
    flags = 0x08000000
    if enabled:
        cmd = ["schtasks", "/Create", "/F", "/SC", "MINUTE", "/MO", "5", "/TN", TASK,
               "/TR", '"%s" --watchdog' % exe]
        r = subprocess.run(cmd, capture_output=True, creationflags=flags, timeout=30)
        if r.returncode != 0:
            raise RuntimeError((r.stderr or r.stdout or b"").decode("cp866", "replace").strip()[:150])
        return "сторож (перезапуск каждые 5 мин) включён"
    r = subprocess.run(["schtasks", "/Delete", "/F", "/TN", TASK], capture_output=True, creationflags=flags, timeout=30)
    return "сторож выключен"

def watchdog_exists():
    import subprocess
    r = subprocess.run(["schtasks", "/Query", "/TN", TASK], capture_output=True, creationflags=0x08000000, timeout=30)
    return r.returncode == 0

def apply(enabled):
    """Вернёт строку для журнала."""
    exe = exe_path()
    if not exe or os.name != "nt":
        return "Автозапуск: запуск из исходников, не настраивается"
    out = []
    try:
        _set(NAME, exe, bool(enabled))
        out.append("Автозапуск при входе в Windows: " + ("включён" if enabled else "выключен"))
    except Exception as e:
        out.append("Автозапуск не настроен: " + str(e)[:150])
    try:
        out.append(_watchdog(exe, bool(enabled)))
    except Exception as e:
        out.append("сторож не настроен: " + str(e)[:150])
    try:
        _drop_startup_shortcuts(exe)
        _desktop_shortcut(exe)
    except Exception as e:
        out.append("ярлык на рабочем столе не создан: " + str(e)[:150])
    return "; ".join(out)
