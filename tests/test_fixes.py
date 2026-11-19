# -*- coding: utf-8 -*-
"""Ошибки, найденные проверкой 19.11 и исправленные: тихий неполный опрос Outlook, Telegram «дошло не всем»,
сирена, сигнал в СБ некуда отправить, дата учёта писем, резервная копия базы, демо-адрес пересылки."""
import os, unittest, sqlite3
from datetime import datetime, timedelta
import helpers
from helpers import make_engine, cleanup, set_forward
import notify, telegram, store as store_mod
from mail_outlook import OutlookSource


# ---------- поддельная папка Outlook ----------
class _Item:
    Class = 43
    def __init__(self, when):
        self.ReceivedTime = when

class _Items:
    def __init__(self, items, fail_first=False, fail_next_at=None):
        self.items, self.i = items, 0
        self.fail_first, self.fail_next_at = fail_first, fail_next_at
    def Sort(self, field, desc):
        pass
    def GetFirst(self):
        if self.fail_first:
            raise OSError("Outlook занят")
        self.i = 0
        return self.items[0] if self.items else None
    def GetNext(self):
        self.i += 1
        if self.fail_next_at is not None and self.i == self.fail_next_at:
            raise OSError("Outlook занят")
        return self.items[self.i] if self.i < len(self.items) else None

class _Folder:
    FolderPath = "\\\\contact\\Входящие"
    def __init__(self, items):
        self.Items = items


class IncompleteOutlookRead(unittest.TestCase):
    """Папка прочитана не до конца — опрос должен считаться неполным: иначе письма очереди через 2 минуты
    снимались как «удалено из Outlook», а статус оставался «Outlook подключён»."""
    def scan(self, items):
        src = OutlookSource({})
        got = list(src._iter(_Folder(items), "ReceivedTime", datetime.now() - timedelta(days=7)))
        return got, src.scan[_Folder.FolderPath]

    def letters(self, n):
        now = datetime.now()
        return [_Item(now - timedelta(minutes=i)) for i in range(n)]

    def test_normal_read_has_no_errors(self):
        got, st = self.scan(_Items(self.letters(5)))
        self.assertEqual((len(got), st["errors"]), (5, 0))

    def test_folder_that_cannot_be_opened_is_an_error(self):
        got, st = self.scan(_Items(self.letters(5), fail_first=True))
        self.assertEqual(len(got), 0)
        self.assertEqual(st["errors"], 1)

    def test_read_broken_in_the_middle_is_an_error(self):
        got, st = self.scan(_Items(self.letters(5), fail_next_at=2))
        self.assertEqual(len(got), 2)
        self.assertEqual(st["errors"], 1)

    def test_reading_limit_is_an_error(self):
        got, st = self.scan(_Items(self.letters(2001)))
        self.assertEqual(len(got), 2000)
        self.assertEqual(st["errors"], 1)
        self.assertIn("предел", st["error"])


class TelegramPartial(unittest.TestCase):
    def setUp(self):
        self._orig = telegram._send_one
        self.sent = []
        def fake(token, cid, text, timeout):
            if cid == "bad":
                raise RuntimeError("chat not found, url /bot%s/sendMessage" % token)
            self.sent.append(cid)
        telegram._send_one = fake

    def tearDown(self):
        telegram._send_one = self._orig

    def test_all_delivered(self):
        self.assertEqual(telegram.send_message("TOKEN123", "1, 2", "x"), {})
        self.assertEqual(self.sent, ["1", "2"])

    def test_partial_failure_is_reported_without_token(self):
        failed = telegram.send_message("TOKEN123", "1, bad", "x")
        self.assertEqual(list(failed), ["bad"])
        self.assertNotIn("TOKEN123", failed["bad"])

    def test_all_failed_raises_without_token(self):
        with self.assertRaises(RuntimeError) as c:
            telegram.send_message("TOKEN123", "bad", "x")
        self.assertNotIn("TOKEN123", str(c.exception))


class _Sound:
    SND_FILENAME, SND_ASYNC, SND_LOOP = 1, 2, 4
    def __init__(self):
        self.fail, self.started = 1, 0
    def PlaySound(self, path, flags):
        if path is None:
            return
        if self.fail:
            self.fail -= 1
            raise RuntimeError("нет звукового устройства")
        self.started += 1


class Siren(unittest.TestCase):
    def setUp(self):
        self.orig = (notify.winsound, dict(notify._enabled), dict(notify._paths))
        notify.winsound = self.snd = _Sound()
        notify._enabled.update(sound=True)
        notify._paths["siren"] = "siren.wav"
        notify.siren_stop()

    def tearDown(self):
        notify.siren_stop()
        notify.winsound = self.orig[0]
        notify._enabled.update(self.orig[1])
        notify._paths.clear(); notify._paths.update(self.orig[2])

    def test_siren_is_retried_after_failed_start(self):
        notify.siren_start()                 # звук не запустился
        self.assertEqual(self.snd.started, 0)
        notify.siren_start()                 # следующий круг (через 2 с) — запускается
        self.assertEqual(self.snd.started, 1)
        notify.siren_start()                 # уже звучит — не перезапускать
        self.assertEqual(self.snd.started, 1)

    def test_siren_starts_when_sound_is_switched_on_during_alarm(self):
        self.snd.fail = 0
        notify.configure(sound=False, toast=False)
        notify.siren_start()
        self.assertEqual(self.snd.started, 0)
        notify.configure(sound=True, toast=False)
        notify.siren_start()
        self.assertEqual(self.snd.started, 1)


class EngineFixes(unittest.TestCase):
    def tearDown(self):
        cleanup(self.eng, self.tmp)

    def journal(self):
        return "\n".join(self.eng.log)

    def test_warning_when_security_has_no_channels(self):
        self.eng, self.src, self.tmp = make_engine()          # ни Telegram, ни почты СБ
        self.src.add("anon@example.com", "", "В здании бомба.")
        self.eng.poll(); self.eng.check_threats()
        self.assertIn("в службу безопасности НЕ отправлен", self.journal())
        self.assertEqual(len(self.eng.store.threats_pending()), 1)      # экран тревоги при этом остаётся

    def test_telegram_alarm_is_repeated_to_those_who_did_not_get_it(self):
        self.eng, self.src, self.tmp = make_engine({"telegram_bot_token": "TOKEN123", "telegram_chat_id": "group, me"})
        calls, ok = [], {"group": False}
        orig = telegram._send_one
        def fake(token, cid, text, timeout):
            calls.append(cid)
            if cid == "group" and not ok["group"]:
                raise RuntimeError("bot was kicked from the group chat")
        telegram._send_one = fake
        try:
            m = self.src.add("anon@example.com", "", "В здании бомба.")
            self.eng.poll(); self.eng.check_threats()
            self.assertEqual(calls, ["group", "me"])
            self.assertIn("дошла не всем", self.journal())
            self.assertFalse(self.eng.store.get(m["id"])["threat_notified_at"])
            ok["group"] = True
            self.eng.sec_sent[m["id"]]["next"] = 0             # прошло 2 минуты
            self.eng.check_threats()
            self.assertEqual(calls, ["group", "me", "group"])  # повтор только тому, кто не получил
            self.assertTrue(self.eng.store.get(m["id"])["threat_notified_at"])
        finally:
            telegram._send_one = orig

    def test_telegram_check_fails_when_not_everyone_got_it(self):
        self.eng, self.src, self.tmp = make_engine({"telegram_bot_token": "TOKEN123", "telegram_chat_id": "group, me"})
        orig = telegram._send_one
        def fake(token, cid, text, timeout):
            if cid == "group":
                raise RuntimeError("chat not found")
        telegram._send_one = fake
        try:
            with self.assertRaises(RuntimeError):
                self.eng.test_telegram()
        finally:
            telegram._send_one = orig

    def test_demo_forward_address_does_not_survive_into_outlook_mode(self):
        self.eng, self.src, self.tmp = make_engine()
        self.eng.apply_settings({"categories": set_forward(self.eng.cfg["categories"], "marketing", "")})
        self.src.simulate_marketing = lambda: None
        self.eng.demo("forward_demo")                          # демо-кнопка «Письмо маркетингу (пересылка)»
        self.assertEqual(self.eng.forward_addr("marketing"), "marketing-demo@example.uz")
        self.eng.cfg["mode"] = "outlook"                       # тот же адрес в рабочем режиме не используется
        self.assertEqual(self.eng.forward_addr("marketing"), "")
        self.eng.cfg["mode"] = "demo"


class Backup(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.tmp = tempfile.mkdtemp(prefix="rm-bak-")
        self.db = os.path.join(self.tmp, "data", "mail.db")
        self.bak = os.path.join(self.tmp, "data", "backup")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def letter(self, s, i):
        s.upsert_in({"id": "m%d" % i, "sender": "a@b.c", "subject": "s", "body": "b", "received_at": "2026-11-01T10:00:00"},
                    "", [], "k", "ru", "general", 2, 0)

    def test_failed_backup_leaves_no_file(self):
        s = store_mod.Store(self.db)
        self.letter(s, 1)
        class Broken:
            def backup(self, dst):
                raise sqlite3.DatabaseError("disk I/O error")
        good, s.con = s.con, Broken()
        with self.assertRaises(sqlite3.DatabaseError):
            s.backup(self.bak)
        s.con = good
        self.assertEqual(os.listdir(self.bak), [])             # пустой «копии» не осталось
        self.assertTrue(s.backup(self.bak))                    # и в тот же день копия делается заново
        s.con.close()

    def test_restore_skips_empty_copy_and_takes_good_one(self):
        s = store_mod.Store(self.db)
        for i in range(3):
            self.letter(s, i)
        s.backup(self.bak)
        s.con.close()
        good = [f for f in os.listdir(self.bak) if f.endswith(".db")][0]
        os.rename(os.path.join(self.bak, good), os.path.join(self.bak, "mail-2026-01-01.db"))
        open(os.path.join(self.bak, "mail-2026-01-02.db"), "w").close()      # пустой файл от неудавшейся копии
        with open(self.db, "wb") as f:
            f.write(b"this is not a database" * 100)                          # база повреждена
        s2, note = store_mod.open_checked(self.db, self.bak)
        self.assertIn("mail-2026-01-01.db", note)
        self.assertEqual(len(s2._rows("SELECT id FROM messages")), 3)
        s2.con.close()


if __name__ == "__main__":
    unittest.main()
