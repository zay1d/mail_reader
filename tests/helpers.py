# -*- coding: utf-8 -*-
"""Общее для тестов: программа во временной папке и поддельный источник почты вместо Outlook."""
import os, sys, json, shutil, tempfile
from datetime import datetime, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "app"))

import config, notify            # noqa: E402

def iso(d):
    return d.strftime("%Y-%m-%dT%H:%M:%S")

def ago(minutes=0):
    return iso(datetime.now() - timedelta(minutes=minutes))


class FakeSource:
    """Источник почты для тестов: письма кладём руками, отправленное программой копится в списках."""
    name = "fake"

    def __init__(self):
        self.incoming, self.outgoing = [], []
        self.replies, self.forwards, self.mails = [], [], []
        self.fail_reply = 0          # сколько следующих автоответов «Outlook не отправил»
        self.fail_forward = 0
        self.last_complete = True
        self.n = 0

    def add(self, sender, subject, body, minutes_ago=1, name="", **extra):
        self.n += 1
        m = {"id": "in-%d" % self.n, "entry_id": "e-%d" % self.n, "conv_id": "c-%d" % self.n,
             "sender": sender, "sender_name": name or sender, "subject": subject, "body": body,
             "received_at": ago(minutes_ago), "importance_high": False, "bulk": False,
             "attachments": [], "reply_to": []}
        m.update(extra)
        self.incoming.append(m)
        return m

    def add_out(self, to, subject, conv_id="", minutes_ago=0):
        self.n += 1
        o = {"id": "out-%d" % self.n, "entry_id": "", "conv_id": conv_id, "recipients": list(to),
             "subject": subject, "sent_at": ago(minutes_ago)}
        self.outgoing.append(o)
        return o

    def status(self):
        return "ok", "тест"

    def fetch(self, since):
        return list(self.incoming), list(self.outgoing)

    def send_reply(self, msg, subject, text, to=None):
        if self.fail_reply:
            self.fail_reply -= 1
            raise OSError("Outlook занят")
        self.replies.append({"to": to or msg.get("sender"), "subject": subject, "text": text, "id": msg.get("id")})
        return True

    def forward(self, msg, to, note):
        if self.fail_forward:
            self.fail_forward -= 1
            raise OSError("Outlook занят")
        self.forwards.append({"to": to, "note": note, "id": msg.get("id")})
        return True

    def send_mail(self, to, cc, subject, html, inline=None):
        self.mails.append({"to": to, "cc": cc, "subject": subject, "html": html})
        return True


def make_engine(overrides=None):
    """Движок программы во временной папке (настройки — из config.json репозитория, режим демо, без звука).
    Возвращает (engine, source, tmpdir). Папку удалить через cleanup(tmpdir)."""
    tmp = tempfile.mkdtemp(prefix="rm-test-")
    os.makedirs(os.path.join(tmp, "data"))
    os.makedirs(os.path.join(tmp, "reports"))
    with open(os.path.join(ROOT, "config.json"), encoding="utf-8") as f:
        cfg = json.load(f)
    cfg.update({"mode": "demo", "sound": False, "toast": False, "count_from": "",
                "own_addresses": ["contact@tcmall.uz"], "mailbox_address": "",
                "telegram_bot_token": "", "security_email": "", "reception_phone": "+998 00 000-00-00"})
    cfg.update(overrides or {})
    config.CONFIG_PATH = os.path.join(tmp, "config.json")
    config.BASE = tmp
    with open(config.CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False)
    config._cfg = None
    config.load()
    from core import Engine
    eng = Engine(tmp)
    src = FakeSource()
    eng.source = src
    return eng, src, tmp


def cleanup(eng, tmp):
    try:
        eng.store.con.close()
    except Exception:
        pass
    notify.siren_stop()
    shutil.rmtree(tmp, ignore_errors=True)


def set_forward(cfg_categories, key, addr):
    cats = json.loads(json.dumps(cfg_categories))
    for c in cats:
        if c.get("key") == key:
            c["forward_to"] = addr
    return cats


class FakeAI:
    """Поддельный второй фильтр: ответы задаются в тесте по теме письма, обращений к Claude нет."""
    def __init__(self, answers=None, recheck=None, threat_recheck=None):
        self.answers = answers or {}        # тема письма -> ответ analyze() или исключение
        self.rechecks = recheck or {}       # тема письма -> ответ recheck() или исключение
        self.threat_rechecks = threat_recheck or {}   # тема письма -> ответ recheck_threat() или исключение
        self.threat_rechecked = []
        self.sent = []
        self.on = True

    def enabled(self):
        return self.on

    def why_off(self):
        return "" if self.on else "ИИ выключен в Настройках"

    def prepare(self, subject, body, attachments=(), names=()):
        import pii
        mk = pii.Masker(names)
        text = "Тема: %s\nТекст письма:\n%s" % (mk.mask(subject), mk.mask(body))
        self.sent.append({"subject": subject, "text": text})
        self._subject = subject
        return text, mk

    def _answer(self, table):
        a = table.get(self._subject)
        if isinstance(a, Exception):
            raise a
        return a

    def analyze(self, text):
        a = self._answer(self.answers)
        if a is None:
            raise AssertionError("нет ответа ИИ для письма «%s»" % self._subject)
        d = {"category": "general", "confidence": "high", "category_reason": "причина", "threat": "none",
             "threat_reason": "", "keywords": []}
        d.update(a)
        return d

    def recheck_threat(self, text, words, level, ai_reason):
        self.threat_rechecked.append({"subject": self._subject, "words": list(words), "level": level})
        a = self._answer(self.threat_rechecks)
        if a is None:
            raise AssertionError("нет ответа перепроверки угрозы для письма «%s»" % self._subject)
        return dict({"reason": "после перепроверки"}, **a)

    def recheck(self, text, rule_cat, rule_words, ai_cat, ai_reason):
        a = self._answer(self.rechecks) or {"category": ai_cat, "confidence": "high"}
        return dict({"reason": "после перепроверки"}, **a)


def ai_wait(eng, polls=1, timeout=10):
    """Дождаться, пока поток второго фильтра разберёт очередь, и выполнить опрос(ы) почты."""
    import time
    t0 = time.time()
    while (eng.ai_jobs or not eng.ai_queue.empty()) and time.time() - t0 < timeout:
        time.sleep(0.01)
    assert not eng.ai_jobs, "ИИ не разобрал очередь за %d с" % timeout
    for _ in range(polls):
        eng.poll()
