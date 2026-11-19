# -*- coding: utf-8 -*-
"""Звук (winsound) и всплывающие уведомления Windows."""
import os, math, struct, wave, subprocess, threading

try:
    import winsound
except ImportError:  # не Windows
    winsound = None

_paths = {}
_enabled = {"sound": True, "toast": True}
_siren_on = False
_lock = threading.Lock()

def _write_wav(path, samples, rate=22050):
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"".join(struct.pack("<h", int(max(-1, min(1, s)) * 32000)) for s in samples))

def _tone(freq, dur, rate=22050, vol=0.6, fade=0.02):
    n = int(rate * dur)
    out = []
    for i in range(n):
        t = i / rate
        env = min(1, t / fade, (dur - t) / fade) if fade else 1
        out.append(vol * env * math.sin(2 * math.pi * freq * t))
    return out

def _silence(dur, rate=22050):
    return [0.0] * int(rate * dur)

def prepare(sounds_dir):
    os.makedirs(sounds_dir, exist_ok=True)
    chime = os.path.join(sounds_dir, "new_mail.wav")
    overdue = os.path.join(sounds_dir, "overdue.wav")
    siren = os.path.join(sounds_dir, "siren.wav")
    if not os.path.exists(chime):
        _write_wav(chime, _tone(880, 0.18) + _silence(0.05) + _tone(1320, 0.28))
    if not os.path.exists(overdue):
        s = []
        for _ in range(3):
            s += _tone(660, 0.16, vol=0.7) + _silence(0.1)
        _write_wav(overdue, s)
    if not os.path.exists(siren):
        rate = 22050
        dur = 2.0
        n = int(rate * dur)
        out = []
        for i in range(n):
            t = i / rate
            f = 600 + 600 * (0.5 - 0.5 * math.cos(2 * math.pi * t / dur))
            out.append(0.85 * math.sin(2 * math.pi * f * t))
        _write_wav(siren, out)
    _paths.update(chime=chime, overdue=overdue, siren=siren)

def configure(sound=True, toast=True):
    _enabled["sound"] = bool(sound)
    _enabled["toast"] = bool(toast)

def _play(path, loop=False):
    if not winsound or not _enabled["sound"] or not path:
        return
    flags = winsound.SND_FILENAME | winsound.SND_ASYNC
    if loop:
        flags |= winsound.SND_LOOP
    try:
        winsound.PlaySound(path, flags)
    except Exception:
        pass

def chime():
    with _lock:
        if not _siren_on:
            _play(_paths.get("chime"))

def overdue():
    with _lock:
        if not _siren_on:
            _play(_paths.get("overdue"))

def siren_start():
    global _siren_on
    with _lock:
        if not _siren_on:
            _siren_on = True
            _play(_paths.get("siren"), loop=True)

def siren_stop():
    global _siren_on
    with _lock:
        _siren_on = False
        if winsound:
            try:
                winsound.PlaySound(None, 0)
            except Exception:
                pass

def _find_windows(title_part):
    import ctypes
    from ctypes import wintypes
    u = ctypes.windll.user32
    found = []
    proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def cb(hwnd, lp):
        if u.IsWindowVisible(hwnd):
            n = u.GetWindowTextLengthW(hwnd)
            if n:
                buf = ctypes.create_unicode_buffer(n + 1)
                u.GetWindowTextW(hwnd, buf, n + 1)
                if title_part in buf.value:
                    found.append(hwnd)
        return True
    u.EnumWindows(proc(cb), 0)
    return found

def window_exists(title_part):
    if os.name != "nt":
        return False
    try:
        return bool(_find_windows(title_part))
    except Exception:
        return False

def is_foreground(title_part):
    if os.name != "nt":
        return False
    try:
        import ctypes
        u = ctypes.windll.user32
        h = u.GetForegroundWindow()
        n = u.GetWindowTextLengthW(h)
        buf = ctypes.create_unicode_buffer(n + 1)
        u.GetWindowTextW(h, buf, n + 1)
        return title_part in buf.value
    except Exception:
        return False

def _swp(hwnd, after, flags):
    """SetWindowPos с правильными 64-битными аргументами (HWND_TOPMOST = -1 должен уйти как указатель, а не int)."""
    import ctypes
    from ctypes import wintypes
    f = ctypes.windll.user32.SetWindowPos
    f.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_uint]
    f.restype = wintypes.BOOL
    return bool(f(hwnd, ctypes.c_void_p(after).value, 0, 0, 0, 0, flags))

def set_topmost(hwnd, on):
    """Закрепить окно поверх всех (on=True) или снять закрепление."""
    if os.name != "nt" or not hwnd:
        return False
    try:
        import ctypes
        return _swp(hwnd, -1 if on else -2, 0x0001 | 0x0002 | 0x0010)
    except Exception:
        return False

def pinned_ok(hwnd, title_part):
    """Окно тревоги на месте: существует, видно, не свёрнуто, закреплено поверх, на нём экран тревоги."""
    if os.name != "nt" or not hwnd:
        return False
    try:
        import ctypes
        u = ctypes.windll.user32
        if not u.IsWindow(hwnd) or not u.IsWindowVisible(hwnd) or u.IsIconic(hwnd):
            return False
        if not (u.GetWindowLongW(hwnd, -20) & 0x0008):          # WS_EX_TOPMOST
            return False
        n = u.GetWindowTextLengthW(hwnd)
        buf = ctypes.create_unicode_buffer(n + 1)
        u.GetWindowTextW(hwnd, buf, n + 1)
        return title_part in buf.value
    except Exception:
        return False

def bring_to_front(title_part, pin=False):
    """Развернуть окно с таким заголовком и вывести поверх всех программ; pin=True — оставить закреплённым
    поверх всех окон. Возвращает окно (hwnd) или 0, если окна нет."""
    if os.name != "nt":
        return 0
    try:
        import ctypes
        u = ctypes.windll.user32
        k = ctypes.windll.kernel32
        wins = _find_windows(title_part)
        if not wins:
            return 0
        hwnd = wins[0]
        if u.IsIconic(hwnd):
            u.ShowWindow(hwnd, 9)                      # SW_RESTORE — из свёрнутого
        u.ShowWindow(hwnd, 3)                          # SW_MAXIMIZE — на весь экран
        fg = u.GetForegroundWindow()
        me = k.GetCurrentThreadId()
        fg_thread = u.GetWindowThreadProcessId(fg, None)
        attached = bool(fg_thread and fg_thread != me and u.AttachThreadInput(me, fg_thread, True))
        # поверх всех окон на мгновение и обратно: окно встаёт наверх даже без права забрать фокус
        _swp(hwnd, -1, 0x0001 | 0x0002 | 0x0040)   # HWND_TOPMOST
        if not pin:
            _swp(hwnd, -2, 0x0001 | 0x0002 | 0x0040)   # HWND_NOTOPMOST — только поднять
        u.BringWindowToTop(hwnd)
        ok = u.SetForegroundWindow(hwnd)
        if attached:
            u.AttachThreadInput(me, fg_thread, False)
        if not ok or u.GetForegroundWindow() != hwnd:
            u.keybd_event(0x12, 0, 0, 0); u.keybd_event(0x12, 0, 2, 0)   # Alt снимает запрет Windows на смену фокуса
            u.SetForegroundWindow(hwnd)
        return hwnd
    except Exception:
        return 0

def test_sound():
    if winsound and not _siren_on:           # проверка звука не должна глушить сирену, если идёт тревога
        try:
            winsound.PlaySound(_paths.get("chime"), winsound.SND_FILENAME | winsound.SND_ASYNC)
        except Exception:
            pass

_PS = r'''
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] | Out-Null
$t1 = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('{TITLE_B64}'))
$t2 = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('{TEXT_B64}'))
$xml = '<toast scenario="reminder"><visual><binding template="ToastGeneric"><text>' + $t1 + '</text><text>' + $t2 + '</text></binding></visual></toast>'
$doc = New-Object Windows.Data.Xml.Dom.XmlDocument
$doc.LoadXml($xml)
$toast = New-Object Windows.UI.Notifications.ToastNotification $doc
$appId = '{APPID}'
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($appId).Show($toast)
'''

_PS_APPID = r'{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe'
_APPID = "TashkentCityMall.ReceptionMail"
_appid_ready = None

def _app_id():
    """Уведомления показываются от имени «Почта ресепшн», а не «Windows PowerShell»: имя программы
    один раз записывается в раздел текущего пользователя (HKCU). Не получилось — прежний вариант."""
    global _appid_ready
    if _appid_ready is None:
        try:
            import winreg
            k = winreg.CreateKey(winreg.HKEY_CURRENT_USER, "Software\\Classes\\AppUserModelId\\" + _APPID)
            winreg.SetValueEx(k, "DisplayName", 0, winreg.REG_SZ, "Почта ресепшн")
            winreg.CloseKey(k)
            _appid_ready = True
        except Exception:
            _appid_ready = False
    return _APPID if _appid_ready else _PS_APPID

def toast(title, text):
    if not _enabled["toast"] or os.name != "nt":
        return
    import base64
    def b64(s):
        # XML-экранирование; в код PowerShell попадает только base64 (буквы, цифры, + / =) — текст письма
        # не может выполниться как команда (раньше тема «$(команда)» исполнялась при показе уведомления)
        x = (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;").replace("'", "&apos;")
        return base64.b64encode(x.encode("utf-8")).decode("ascii")
    script = (_PS.replace("{APPID}", _app_id()).replace("{TITLE_B64}", b64(title))
              .replace("{TEXT_B64}", b64((text or "")[:200])))
    def run():
        try:
            subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden",
                            "-Command", script], creationflags=0x08000000, timeout=15,
                           capture_output=True)
        except Exception:
            pass
    threading.Thread(target=run, daemon=True).start()
