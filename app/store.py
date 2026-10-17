# -*- coding: utf-8 -*-
"""Локальная база SQLite: письма и смены."""
import sqlite3, json, os, threading
from datetime import datetime

SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
  id TEXT PRIMARY KEY,
  entry_id TEXT,
  conv_id TEXT,
  kind TEXT,                -- in | out
  sender TEXT,
  sender_name TEXT,
  recipients TEXT,          -- json list (для исходящих)
  subject TEXT,
  body_preview TEXT,
  received_at TEXT,         -- ISO, локальное время (для out = время отправки)
  ignored TEXT,             -- причина, если письмо не требует учёта
  answered_at TEXT,
  answer_id TEXT,
  no_reply_reason TEXT,
  no_reply_at TEXT,
  threat_words TEXT,        -- json list или NULL
  threat_ack_at TEXT,
  threat_notified_at TEXT,
  shift_key TEXT,
  first_seen TEXT,
  lang TEXT,
  category TEXT,
  priority INTEGER,
  auto_replied_at TEXT
);
CREATE INDEX IF NOT EXISTS ix_kind_recv ON messages(kind, received_at);
CREATE INDEX IF NOT EXISTS ix_shift ON messages(shift_key);
CREATE TABLE IF NOT EXISTS sessions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  shift_key TEXT,
  opened_at TEXT,
  staff TEXT,               -- json list фамилий
  closed_at TEXT,
  closed_by TEXT
);
CREATE TABLE IF NOT EXISTS shifts (
  key TEXT PRIMARY KEY,
  name TEXT,
  start_at TEXT,
  end_at TEXT,
  closed_at TEXT,
  closed_by TEXT,
  report_path TEXT,
  sent_to TEXT
);
"""
_NEW_COLS = {"lang": "TEXT", "category": "TEXT", "priority": "INTEGER", "auto_replied_at": "TEXT", "attachments": "TEXT", "threat_level": "INTEGER", "forwarded_to": "TEXT", "forwarded_at": "TEXT", "last_seen": "TEXT", "junk": "INTEGER",
            "reply_to": "TEXT", "client_email": "TEXT", "client_name": "TEXT", "reply_self": "INTEGER",
            "self_reply_at": "TEXT", "self_reply_to": "TEXT", "unignored_at": "TEXT", "ai_summary": "TEXT",
            "actions_done": "INTEGER", "action_tries": "INTEGER", "answer_seen_at": "TEXT"}

def now_iso():
    return datetime.now().strftime("%Y-%m-%dT%H:%M:%S")

class Store:
    def __init__(self, path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self.lock = threading.RLock()
        self.con = sqlite3.connect(path, check_same_thread=False)
        self.con.row_factory = sqlite3.Row
        with self.lock:
            self.con.executescript(SCHEMA)
            cols = {r[1] for r in self.con.execute("PRAGMA table_info(messages)")}
            for c, t in _NEW_COLS.items():
                if c not in cols:
                    self.con.execute("ALTER TABLE messages ADD COLUMN %s %s" % (c, t))
                    if c == "actions_done":   # обновление: письма из прежних версий уже обработаны — не повторять
                        self.con.execute("UPDATE messages SET actions_done=1")
            scols = {r[1] for r in self.con.execute("PRAGMA table_info(sessions)")}
            if "mode" not in scols:          # смены, открытые в демо-режиме, отличаем от настоящих
                self.con.execute("ALTER TABLE sessions ADD COLUMN mode TEXT")
            self.con.commit()

    # ---------- служебное ----------
    def _rows(self, sql, args=()):
        with self.lock:
            cur = self.con.execute(sql, args)
            return [self._row2dict(r) for r in cur.fetchall()]

    def _row2dict(self, r):
        d = dict(r)
        for k in ("threat_words", "recipients", "attachments", "reply_to"):
            if d.get(k):
                try:
                    d[k] = json.loads(d[k])
                except Exception:
                    d[k] = []
            else:
                d[k] = []
        if not d.get("priority"):
            d["priority"] = 2
        return d

    def _exec(self, sql, args=()):
        with self.lock:
            self.con.execute(sql, args)
            self.con.commit()

    def has(self, mid):
        with self.lock:
            return self.con.execute("SELECT 1 FROM messages WHERE id=?", (mid,)).fetchone() is not None

    def touch_in(self, m):
        """Письмо уже в базе: обновить только ссылки на элемент Outlook и время, когда его видели."""
        self._exec("UPDATE messages SET entry_id=?, conv_id=?, last_seen=? WHERE id=?", (m.get("entry_id"), m.get("conv_id"), now_iso(), m["id"]))

    def vanished(self, since_iso, unseen_before_iso):
        """Письма очереди, которых Outlook давно не показывает (удалены/перемещены сотрудником)."""
        return self._rows("""SELECT * FROM messages WHERE kind='in' AND ignored IS NULL AND answered_at IS NULL
                             AND no_reply_reason IS NULL AND forwarded_at IS NULL
                             AND received_at>=? AND last_seen IS NOT NULL AND last_seen<?""",
                          (since_iso, unseen_before_iso))

    def backup(self, folder, keep=14):
        """Ежедневная копия базы: data/backup/mail-ГГГГ-ММ-ДД.db (последние keep штук)."""
        os.makedirs(folder, exist_ok=True)
        name = "mail-%s.db" % datetime.now().strftime("%Y-%m-%d")
        path = os.path.join(folder, name)
        if os.path.exists(path):
            return None
        with self.lock:
            dst = sqlite3.connect(path)
            try:
                self.con.backup(dst)
            finally:
                dst.close()
        old = sorted(f for f in os.listdir(folder) if f.startswith("mail-") and f.endswith(".db"))
        for f in old[:-keep]:
            try:
                os.remove(os.path.join(folder, f))
            except Exception:
                pass
        return path

    def purge_demo(self):
        """При переходе с демо на реальный ящик: убрать вымышленные письма и тестовые смены."""
        self._exec("DELETE FROM messages WHERE id LIKE 'demo-%'")
        self._purge_demo_sessions()

    def _purge_demo_sessions(self):
        """Удалить только смены, открытые в демо-режиме (настоящие смены и отчёты не трогаем)."""
        ids = [r["id"] for r in self._rows("SELECT id FROM sessions WHERE mode='demo'")]
        for i in ids:
            self._exec("DELETE FROM shifts WHERE key=?", ("s%d" % int(i),))
        self._exec("DELETE FROM sessions WHERE mode='demo'")

    def get(self, mid):
        rows = self._rows("SELECT * FROM messages WHERE id=?", (mid,))
        return rows[0] if rows else None

    # ---------- письма ----------
    def junk_ids(self):
        """Письма, которые пришли в «Нежелательную почту» и ещё не переехали во «Входящие»."""
        return {r["id"] for r in self._rows("SELECT id FROM messages WHERE kind='in' AND junk=1")}

    def pending_actions(self, since_iso, max_tries=8):
        """Письма в очереди, у которых автоответ/пересылка ещё не удались."""
        return self._rows("""SELECT * FROM messages WHERE kind='in' AND ignored IS NULL AND answered_at IS NULL
            AND no_reply_reason IS NULL AND COALESCE(actions_done,0)=0 AND COALESCE(action_tries,0)<?
            AND received_at>=?""", (max_tries, since_iso))

    def set_actions(self, mid, ok):
        """Отметить попытку автоответа/пересылки. -> число попыток."""
        with self.lock:
            self.con.execute("UPDATE messages SET actions_done=?, action_tries=COALESCE(action_tries,0)+1 WHERE id=?",
                             (1 if ok else 0, mid))
            self.con.commit()
            r = self.con.execute("SELECT action_tries FROM messages WHERE id=?", (mid,)).fetchone()
            return int(r[0] or 0) if r else 0

    def set_ai_summary(self, mid, summary):
        self._exec("UPDATE messages SET ai_summary=? WHERE id=?", ((summary or "")[:200] or None, mid))

    def touch_many(self, ms):
        """touch_in для многих писем одной транзакцией (было: запись на диск на каждое письмо каждые 20 с)."""
        if not ms:
            return
        t = now_iso()
        with self.lock:
            self.con.executemany("UPDATE messages SET entry_id=?, conv_id=?, last_seen=? WHERE id=?",
                                 [(m.get("entry_id"), m.get("conv_id"), t, m["id"]) for m in ms])
            self.con.commit()

    def set_junk(self, mid, junk):
        self._exec("UPDATE messages SET junk=? WHERE id=?", (1 if junk else 0, mid))

    def upsert_in(self, m, ignored, threat_words, shift_key, lang, category, priority, threat_level=0):
        """True, если письмо новое."""
        with self.lock:
            cur = self.con.execute("SELECT id FROM messages WHERE id=?", (m["id"],))
            exists = cur.fetchone() is not None
            if exists:
                self.con.execute("UPDATE messages SET entry_id=?, conv_id=? WHERE id=?",
                                 (m.get("entry_id"), m.get("conv_id"), m["id"]))
            else:
                self.con.execute(
                    """INSERT INTO messages (id, entry_id, conv_id, kind, sender, sender_name, subject,
                       body_preview, received_at, ignored, threat_words, shift_key, first_seen, lang, category, priority, attachments, threat_level, junk,
                       reply_to, client_email, client_name, reply_self)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (m["id"], m.get("entry_id"), m.get("conv_id"), "in", m.get("sender"),
                     m.get("sender_name"), m.get("subject"), (m.get("body") or "")[:400],
                     m.get("received_at"), ignored or None,
                     json.dumps(threat_words, ensure_ascii=False) if threat_words else None,
                     shift_key, now_iso(), lang, category, int(priority or 2),
                     json.dumps(m.get("attachments") or [], ensure_ascii=False) if m.get("attachments") else None,
                     int(threat_level or 0), 1 if m.get("junk") else 0,
                     json.dumps(m.get("reply_to") or [], ensure_ascii=False) if m.get("reply_to") else None,
                     m.get("client_email") or None, m.get("client_name") or None, 1 if m.get("reply_self") else 0))
            self.con.commit()
            return not exists

    def upsert_out(self, m, ignored=None):
        with self.lock:
            cur = self.con.execute("SELECT id FROM messages WHERE id=?", (m["id"],))
            if cur.fetchone() is not None:
                return False
            self.con.execute(
                """INSERT INTO messages (id, entry_id, conv_id, kind, recipients, subject, received_at, first_seen, ignored)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (m["id"], m.get("entry_id"), m.get("conv_id"), "out",
                 json.dumps(m.get("recipients") or [], ensure_ascii=False),
                 m.get("subject"), m.get("sent_at"), now_iso(), ignored))
            self.con.commit()
            return True

    def unanswered_candidates(self):
        return self._rows("""SELECT * FROM messages WHERE kind='in' AND ignored IS NULL
                             AND answered_at IS NULL ORDER BY received_at""")

    def queue(self, exclude_forwarded=True):
        """Ждут ответа: срочные выше, внутри — старые выше."""
        sql = """SELECT * FROM messages WHERE kind='in' AND ignored IS NULL
                 AND answered_at IS NULL AND no_reply_reason IS NULL"""
        if exclude_forwarded:
            sql += " AND forwarded_at IS NULL"
        return self._rows(sql + " ORDER BY COALESCE(priority,2), received_at")

    def mark_forwarded(self, mid, to):
        self._exec("UPDATE messages SET forwarded_to=?, forwarded_at=? WHERE id=?", (to, now_iso(), mid))

    def forwarded_between(self, start_iso, end_iso):
        return self._rows("""SELECT * FROM messages WHERE kind='in' AND forwarded_at>=? AND forwarded_at<?
                             ORDER BY forwarded_at DESC""", (start_iso, end_iso))

    def mark_answered(self, mid, answer_id, answered_at):
        # answer_seen_at — когда программа увидела ответ: ответ, отправленный в 16:59 и попавший в «Отправленные»
        # после закрытия смены в 17:00, засчитывается следующей смене (иначе его не было бы ни в одном отчёте)
        self._exec("UPDATE messages SET answered_at=?, answer_id=?, answer_seen_at=? WHERE id=?",
                   (answered_at, answer_id, now_iso(), mid))

    def set_client(self, m):
        """Дописать «Отвечать на» и адрес клиента письму, пришедшему до v1.30."""
        self._exec("UPDATE messages SET reply_to=?, client_email=?, client_name=?, reply_self=? WHERE id=?",
                   (json.dumps(m.get("reply_to") or [], ensure_ascii=False), m.get("client_email") or None,
                    m.get("client_name") or None, 1 if m.get("reply_self") else 0, m["id"]))

    def mark_self_reply(self, mid, at, to):
        """Ответ ушёл только на наш же ящик (Reply-To формы сайта): клиент его не получил, письмо остаётся в очереди."""
        self._exec("UPDATE messages SET self_reply_at=?, self_reply_to=? WHERE id=?", (at, to, mid))

    def hidden_between(self, start_iso, end_iso):
        """Письма, которые программа не поставила в очередь (рассылки, автописьма) — список «Не попали в очередь».
        Свои письма ящика и письма до даты запуска не показываем."""
        return self._rows("""SELECT * FROM messages WHERE kind='in' AND ignored IS NOT NULL
                             AND ignored NOT IN ('своё письмо', 'до даты запуска')
                             AND received_at>=? AND received_at<? ORDER BY received_at DESC""", (start_iso, end_iso))

    def unignore(self, mid):
        """Кнопка «Нужен ответ»: письмо из «Не попали в очередь» встаёт в очередь (без автоответа и пересылки)."""
        with self.lock:
            cur = self.con.execute("""UPDATE messages SET ignored=NULL, unignored_at=? WHERE id=? AND kind='in'
                                      AND ignored IS NOT NULL AND ignored NOT IN ('своё письмо', 'до даты запуска')""",
                                   (now_iso(), mid))
            self.con.commit()
            return cur.rowcount > 0

    def mark_no_reply(self, mid, reason):
        self._exec("UPDATE messages SET no_reply_reason=?, no_reply_at=? WHERE id=?", (reason, now_iso(), mid))

    def undo_no_reply(self, mid):
        self._exec("UPDATE messages SET no_reply_reason=NULL, no_reply_at=NULL WHERE id=?", (mid,))

    def set_auto_replied(self, mid):
        self._exec("UPDATE messages SET auto_replied_at=? WHERE id=?", (now_iso(), mid))

    def auto_replied_to(self, sender, since_iso="0000"):
        """Письма этого отправителя, на которые уже ушёл автоответ (для правила «одна тема — один автоответ»)."""
        # заявки с сайта приходят от одного адреса формы — считаем по адресу клиента, на который ушёл автоответ
        return self._rows("""SELECT id, subject, auto_replied_at FROM messages WHERE kind='in'
                             AND lower(COALESCE(NULLIF(client_email, ''), sender))=?
                             AND auto_replied_at>=?""", ((sender or "").lower(), since_iso))

    def threats_pending(self):
        """Неподтверждённые сигналы: сначала явные (2), затем возможные (1)."""
        return self._rows("""SELECT * FROM messages WHERE kind='in' AND threat_words IS NOT NULL AND ignored IS NULL
                             AND threat_ack_at IS NULL ORDER BY COALESCE(threat_level,2) DESC, received_at""")

    def set_threat_level(self, mid, level):
        """Смена уровня (эскалация жёлтого в красный): сбрасывает подтверждение и уведомления."""
        self._exec("UPDATE messages SET threat_level=?, threat_ack_at=NULL, threat_notified_at=NULL WHERE id=?", (int(level), mid))

    def ack_threat(self, mid):
        self._exec("UPDATE messages SET threat_ack_at=? WHERE id=?", (now_iso(), mid))

    def set_threat_notified(self, mid):
        self._exec("UPDATE messages SET threat_notified_at=? WHERE id=?", (now_iso(), mid))

    def incoming_between(self, start_iso, end_iso, include_ignored=False):
        sql = "SELECT * FROM messages WHERE kind='in' AND received_at>=? AND received_at<?"
        if not include_ignored:
            sql += " AND ignored IS NULL"
        return self._rows(sql + " ORDER BY received_at", (start_iso, end_iso))

    def outgoing_between(self, start_iso, end_iso, include_auto=False):
        sql = "SELECT * FROM messages WHERE kind='out' AND received_at>=? AND received_at<?"
        if not include_auto:
            sql += " AND ignored IS NULL"
        return self._rows(sql + " ORDER BY received_at", (start_iso, end_iso))

    def answered_between(self, start_iso, end_iso):
        return self._rows("""SELECT * FROM messages WHERE kind='in' AND answered_at IS NOT NULL
                             AND MAX(answered_at, COALESCE(answer_seen_at, answered_at))>=?
                             AND MAX(answered_at, COALESCE(answer_seen_at, answered_at))<?
                             ORDER BY answered_at DESC""", (start_iso, end_iso))

    def no_reply_between(self, start_iso, end_iso):
        return self._rows("""SELECT * FROM messages WHERE kind='in' AND no_reply_at>=? AND no_reply_at<?
                             ORDER BY no_reply_at DESC""", (start_iso, end_iso))

    def threats_between(self, start_iso, end_iso):
        # «возможная угроза» в рассылке (не в очереди, экрана не было) в отчёт не идёт — отчёт = экран
        return self._rows("""SELECT * FROM messages WHERE kind='in' AND threat_words IS NOT NULL
                             AND (ignored IS NULL OR COALESCE(threat_level,2)=2)
                             AND received_at>=? AND received_at<? ORDER BY received_at""", (start_iso, end_iso))

    def ignore_before(self, iso, reason="до даты запуска"):
        """Письма, пришедшие раньше даты запуска, исключаются из очереди, автоответов и статистики."""
        self._exec("UPDATE messages SET ignored=? WHERE kind='in' AND received_at<? AND ignored IS NULL", (reason, iso))
        self._exec("UPDATE messages SET ignored=NULL WHERE kind='in' AND received_at>=? AND ignored=?", (iso, reason))

    def conv_counts(self):
        """Число писем в каждой цепочке (входящие + ответы, без автоответов)."""
        with self.lock:
            cur = self.con.execute("""SELECT conv_id, COUNT(*) FROM messages
                                      WHERE conv_id IS NOT NULL AND conv_id != '' AND (kind='in' OR ignored IS NULL)
                                      GROUP BY conv_id""")
            return {r[0]: r[1] for r in cur.fetchall()}

    def clear_all(self):
        """Кнопка «Сброс» в демо: только вымышленные письма и демо-смены (реальная почта не удаляется)."""
        self._exec("DELETE FROM messages WHERE id LIKE 'demo-%'")
        self._purge_demo_sessions()

    # ---------- сессии (кто на смене) ----------
    def session_current(self):
        rows = self._rows("SELECT * FROM sessions WHERE closed_at IS NULL ORDER BY id DESC LIMIT 1")
        if not rows:
            return None
        r = rows[0]
        try:
            r["staff"] = json.loads(r.get("staff") or "[]")
        except Exception:
            r["staff"] = []
        return r

    def session_open(self, shift_key, staff, mode=None):
        self._exec("UPDATE sessions SET closed_at=? WHERE closed_at IS NULL", (now_iso(),))
        self._exec("INSERT INTO sessions (shift_key, opened_at, staff, mode) VALUES (?,?,?,?)",
                   (shift_key, now_iso(), json.dumps(list(staff or []), ensure_ascii=False), mode))

    def session_close(self, closed_by, at=None):
        self._exec("UPDATE sessions SET closed_at=?, closed_by=? WHERE closed_at IS NULL", (at or now_iso(), closed_by))

    def session_prev_close(self, before_id=None):
        """Когда закрыли предыдущую смену: с этого момента письма считаются за следующую."""
        if before_id:
            rows = self._rows("SELECT closed_at FROM sessions WHERE closed_at IS NOT NULL AND id<? ORDER BY id DESC LIMIT 1", (before_id,))
        else:
            rows = self._rows("SELECT closed_at FROM sessions WHERE closed_at IS NOT NULL ORDER BY id DESC LIMIT 1")
        return rows[0]["closed_at"] if rows else None

    # ---------- смены ----------
    def shift_get(self, key):
        rows = self._rows("SELECT * FROM shifts WHERE key=?", (key,))
        return rows[0] if rows else None

    def shift_close(self, key, name, start_at, end_at, closed_by, report_path, sent_to):
        self._exec("""INSERT OR REPLACE INTO shifts (key, name, start_at, end_at, closed_at, closed_by, report_path, sent_to)
                      VALUES (?,?,?,?,?,?,?,?)""",
                   (key, name, start_at, end_at, now_iso(), closed_by, report_path, sent_to))

    def shifts_recent(self, limit=30):
        return self._rows("SELECT * FROM shifts ORDER BY closed_at DESC LIMIT ?", (limit,))
