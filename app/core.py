# -*- coding: utf-8 -*-
"""Ядро: опрос почты, сопоставление ответов, категории, приоритеты, автоответ,
угрозы, напоминания, смены."""
import os, threading, time, traceback, webbrowser, html, queue, collections
from datetime import datetime, timedelta

import config, notify, threats, matcher, shifts, classify, telegram, autostart, ai
from store import Store, open_checked

VERSION = "1.40"
DEMO_FORWARD = "marketing-demo@example.uz"    # адрес пересылки демо-кнопки; в режиме Outlook на него ничего не уходит
# метки нашего письма-сигнала в СБ; письмо с [0] в теме вернулось в ящик ресепшн (автоответ, группа, пересылка) — без тревоги
SB_MARKS = ("УГРОЗА: письмо на почту ресепшн", "Сработал сигнал угрозы в почте ресепшн")
GONE = "удалено из Outlook сотрудником"       # причина снятия с очереди: письмо пропало из папок Outlook
REPORT_MARK = "Отчёт ресепшн по почте"       # начало темы отчёта смены (shifts.build_report)

def _iso(d):
    return d.strftime("%Y-%m-%dT%H:%M:%S")

def _p(iso):
    try:
        return datetime.strptime(iso[:19], "%Y-%m-%dT%H:%M:%S")
    except Exception:
        return None

class Engine:
    def __init__(self, base):
        self.base = base
        self.log_lock = threading.Lock()
        self.journal = os.path.join(base, "data", "journal.log")   # журнал на диске: переживает перезапуск
        self.cfg = config.get()
        self.log = []
        self.no_auto_before = None    # база восстановлена после сбоя: письмам до этого момента автоответ и пересылку не повторять
        self.store, db_note = open_checked(os.path.join(base, "data", "mail.db"), os.path.join(base, "data", "backup"))
        if db_note:
            self.no_auto_before = datetime.now()
            self._log("ВНИМАНИЕ: " + db_note)
        self.reports_dir = os.path.join(base, "reports")
        self.lock = threading.RLock()
        self.shift_lock = threading.Lock()   # открытие/закрытие смены — по одному (две вкладки, двойное нажатие)
        self.retry = {}               # (вид, id письма) -> число попыток: автоответ/пересылка, не ушедшие из-за сбоя Outlook
        self.source = None
        self.status = ("off", "Запуск…")
        self.first_poll = True
        self.last_poll = None
        self.last_alert = {}
        self.threat_opened = set()
        self.stop = False
        self.poll_now = True
        self.boost_until = 0          # после «Ответить в Outlook» почта опрашивается каждые 3 с
        self.loop_running = False
        self.after_clear = []         # письма, по которым сняли «возможную угрозу»: догнать автоответ и пересылку
        self.sec_sent = {}            # id угрозы -> {"tg": bool, "mail": bool, "next": time} — повтор сигнала в СБ
        self.front_next = 0           # когда снова выводить экран тревоги поверх всех окон
        self.skip_logged = set()      # письма, о пропуске которых уже написано в журнале
        self.complete_since = None    # с какого момента все опросы подряд прочитали Outlook без ошибок
        self.started = datetime.now()
        self.backfilled = set()       # письма из базы, для которых уже проверены «Отвечать на» и адрес клиента
        self.self_fix_done = False    # v1.30: разовая проверка ответов, ушедших только на наш ящик
        self.fronted = set()          # по каким сигналам экран уже выводился
        self.browser_opened_at = 0
        self.pinned_hwnd = 0          # окно тревоги, закреплённое поверх всех окон (красный сигнал)
        self.pin_pause_until = 0      # «Открыть письмо в Outlook» на красном экране — открепить на 90 с
        self.heartbeat = time.time()  # когда цикл опроса в последний раз подавал признаки жизни
        # второй фильтр — ИИ: письма ждут его ответа в отдельном потоке, опрос почты и тревоги не задерживаются
        self.ai = ai.Analyzer(lambda: self.cfg)
        self.ai_queue = queue.Queue()
        self.ai_jobs = {}             # id письма -> задание (письмо с текстом), пока ИИ не ответил
        self.ai_ready = []            # письма, по которым ИИ ответил: догнать пересылку и автоответ
        self.ai_lock = threading.Lock()
        self.ai_thread = None
        self.ai_fail = {"n": 0, "until": 0, "msg": ""}     # сбои подряд; пауза, во время которой работает только словарь
        self.ai_last_ok = None
        self.ai_log = collections.deque(maxlen=30)          # что уходило в ИИ (для Настроек), только в памяти
        self.stall_warned = 0         # когда в последний раз был сигнал «почта не проверяется»
        self.url = "http://127.0.0.1:%d/" % int(self.cfg.get("port") or 8791)
        self._reload_patterns()
        self._make_source()
        self._apply_count_from()
        self._log("Почта ресепшн, версия " + VERSION)
        self._first_outlook_start()
        self._autostart()
        if self.cfg.get("_config_error"):
            self._log("ВНИМАНИЕ: " + self.cfg["_config_error"])
            notify.toast("Почта ресепшн: настройки слетели", "Проверьте Настройки — config.json был повреждён")
        if db_note:
            notify.toast("Почта ресепшн: база писем восстановлена", "Файл базы был повреждён. Подробности — Настройки → журнал")

    def _first_outlook_start(self):
        """Чистая установка (пустая база, режим Outlook, дата учёта не задана): учитывать письма с этой минуты —
        иначе в очередь попали бы письма за 7 дней, а свежим из них ушли бы автоответы и пересылки."""
        if self.cfg.get("mode") != "outlook" or (self.cfg.get("count_from") or "").strip():
            return
        if self.store._rows("SELECT 1 FROM messages WHERE kind='in' AND id NOT LIKE 'demo-%' LIMIT 1"):
            return
        now = datetime.now().strftime("%Y-%m-%dT%H:%M")
        self.cfg = config.save({"count_from": now})
        self._log("Первый запуск с Outlook: учитываются письма, пришедшие с %s (Настройки → «Учитывать письма начиная с»)"
                  % now.replace("T", " "))

    def count_from(self):
        v = (self.cfg.get("count_from") or "").strip()
        if not v:
            return None
        v = v.replace(" ", "T")
        if len(v) == 16:
            v += ":00"
        return v if _p(v) else None

    def _apply_count_from(self):
        cf = self.count_from()
        if cf:
            self.store.ignore_before(cf)
        else:
            self.store.ignore_before("0000-00-00T00:00:00")   # снять прежние пометки

    # ---------- настройка ----------
    def _autostart(self):
        if self.cfg.get("mode") != "outlook":
            self._log("Автозапуск: демо-режим, не настраивается (включится после подключения к Outlook)")
            return
        self._log(autostart.apply(self.cfg.get("autostart", True)))

    def _log(self, msg):
        line = datetime.now().strftime("%d.%m %H:%M:%S ") + msg
        try:
            print(line)          # консоли может не быть или у неё кодировка без «→», «№» — журнал важнее
        except Exception:
            pass
        self.log.append(line)
        del self.log[:-200]
        try:
            with self.log_lock:
                if os.path.exists(self.journal) and os.path.getsize(self.journal) > 2000000:
                    os.replace(self.journal, self.journal[:-4] + ".old.log")   # хранится текущий + один прошлый
                with open(self.journal, "a", encoding="utf-8") as f:
                    f.write(datetime.now().strftime("%d.%m.%Y %H:%M:%S ") + msg + "\n")
        except Exception:
            pass

    def log_tail(self, n=300):
        """Журнал для Настроек: последние строки из файла (видно и то, что было до перезапуска)."""
        try:
            with self.log_lock:
                lines = []
                for p in (self.journal[:-4] + ".old.log", self.journal):
                    if os.path.exists(p):
                        with open(p, encoding="utf-8", errors="replace") as f:
                            lines += f.read().splitlines()
            return lines[-n:] if lines else self.log[-n:]
        except Exception:
            return self.log[-n:]

    def _reload_patterns(self):
        self.detector = threats.Detector(self.cfg)
        notify.configure(self.cfg.get("sound", True), self.cfg.get("toast", True))

    def _make_source(self):
        with self.lock:
            if self.cfg.get("mode") == "outlook":
                from mail_outlook import OutlookSource
                self.source = OutlookSource(self.cfg)
            else:
                from mail_demo import DemoSource
                self.source = DemoSource(self.cfg, self.base)
            self.first_poll = True
            self.status = ("off", "Подключение…")

    def apply_settings(self, values):
        old_mode = self.cfg.get("mode")
        old_auto = self.cfg.get("autostart", True)
        old = {k: self.cfg.get(k) for k in ("mailbox_address", "own_addresses")}
        self.cfg = config.save(values)
        if self.cfg.get("autostart", True) != old_auto or self.cfg.get("mode") != old_mode:
            self._autostart()
        self._reload_patterns()
        self._apply_count_from()
        if self.cfg.get("mode") == old_mode == "outlook" and any(
                (values.get(k) is not None and values.get(k) != old.get(k)) for k in ("mailbox_address", "own_addresses")):
            self._make_source()                      # новый адрес ящика — подключиться заново, без перезапуска
            self._log("Адрес ящика изменён — переподключение к Outlook")
        if self.cfg.get("mode") != old_mode:
            if old_mode == "demo":
                cats = self.cfg.get("categories") or []
                if any((c.get("forward_to") or "").strip().lower() == DEMO_FORWARD for c in cats):
                    for c in cats:              # адрес, записанный демо-кнопкой «Письмо маркетингу», убрать из настроек
                        if (c.get("forward_to") or "").strip().lower() == DEMO_FORWARD:
                            c["forward_to"] = ""
                    self.cfg = config.save({"categories": cats})
                    self._log("Демо-адрес пересылки убран из направлений")
                self.store.purge_demo()
                self.threat_opened.clear(); self.last_alert.clear()
                self._log("Режим изменён на Outlook: демо-письма и тестовые смены удалены")
                self._first_outlook_start()
                self._apply_count_from()
            self._make_source()
        self.poll_now = True

    def sla_for(self, priority):
        sla = (self.cfg.get("priority") or {}).get("sla") or {}
        return int(sla.get(str(priority or 2)) or self.cfg.get("sla_overdue_minutes") or 60)

    def cat_name(self, key):
        for c in self.cfg.get("categories") or []:
            if c.get("key") == key:
                return c.get("name") or key
        return "Общие вопросы"

    def _auto_prefixes(self):
        """Префиксы темы автоответа (без пробелов по краям: Outlook может обрезать пробел у письма без темы)."""
        vals = list((self.cfg.get("auto_reply_subject") or {}).values()) + list(config.DEFAULT_AUTO_SUBJECT.values())
        out = []
        for v in vals:
            v = (v or "").strip()
            if v and v not in out:
                out.append(v)
        return out

    def _own(self):
        """Адреса самого ящика ресепшн (contact@…)."""
        own = {a.strip().lower() for a in (self.cfg.get("own_addresses") or []) if "@" in (a or "")}
        a = (self.cfg.get("mailbox_address") or "").strip().lower()
        if "@" in a:
            own.add(a)
        return own

    def _client_info(self, m):
        """Куда на самом деле отвечать. Заявка с сайта: «Отвечать на» — наш же ящик (Outlook «Ответить» ушло бы
        нам самим), адрес клиента берём из текста («Email: …»). Reply-To на другой адрес — отвечать туда."""
        own = self._own()
        rt = [(a or "").lower() for a in (m.get("reply_to") or []) if "@" in (a or "")]
        m["reply_self"] = bool(rt) and all(a in own for a in rt)
        m["client_email"], m["client_name"] = "", ""
        if m["reply_self"]:
            m["client_email"], m["client_name"] = classify.form_contact(m.get("body"), own, m.get("sender"))
        else:
            other = [a for a in rt if a not in own and a != (m.get("sender") or "").lower()]
            if other:
                m["client_email"] = other[0]
        return m

    def _self_target(self, o, cands, only_form=False):
        """На какое письмо был ответ, не дошедший до клиента: та же переписка Outlook, сначала та же тема
        (заявки формы с одинаковой темой могут оказаться в одной переписке). only_form — только заявки с сайта."""
        sent = o.get("sent_at") or ""
        conv = o.get("conv_id") or ""
        if not conv:
            return []
        same = [c for c in cands if c.get("conv_id") == conv and (c.get("received_at") or "") <= sent
                and (not only_form or c.get("reply_self"))]
        key = self._thread_key(o.get("subject"))
        pick = [c for c in same if key and self._thread_key(c.get("subject")) == key] or (same if len(same) == 1 else [])
        if not pick:
            return []
        first = sorted(pick, key=lambda c: c.get("received_at") or "")[0]
        cli = (first.get("client_email") or "").lower()
        return [c["id"] for c in pick if (c.get("client_email") or "").lower() == cli]

    def _fix_self_answers(self):
        """До v1.32 «ответом» засчитывалось письмо, не дошедшее до клиента: ушедшее только на наш ящик или, у заявки
        с сайта, на contact@ и коллег без адреса клиента. Такие письма возвращаются в очередь с красной пометкой,
        если позже не было настоящего ответа клиенту."""
        own = self._own()
        if not own:
            return
        since = _iso(datetime.now() - timedelta(days=int(self.cfg.get("lookback_days") or 7)))
        outs = self.store._rows("SELECT * FROM messages WHERE kind='out' AND ignored IS NULL AND received_at>=? ORDER BY received_at", (since,))
        by_id = {o["id"]: o for o in outs}
        selfish = set()
        for o in outs:
            rc = [(a or "").lower() for a in (o.get("recipients") or []) if a]
            if rc and all(a in own for a in rc):
                selfish.add(o["id"])
                self.store._exec("UPDATE messages SET ignored='ответ себе' WHERE id=?", (o["id"],))
        back = []
        for m in self.store._rows("SELECT * FROM messages WHERE kind='in' AND answered_at IS NOT NULL AND received_at>=?", (since,)):
            o = by_id.get(m.get("answer_id"))
            if not o:
                continue
            rc = [(a or "").lower() for a in (o.get("recipients") or []) if a]
            if o["id"] in selfish or (m.get("reply_self") and rc and not matcher.reaches(m, set(rc), own)):
                self.store._exec("UPDATE messages SET answered_at=NULL, answer_id=NULL, self_reply_at=?, self_reply_to=? WHERE id=?",
                                 (o.get("received_at"), ", ".join(rc), m["id"]))
                back.append(self.store.get(m["id"]))
        if not back:
            return
        cands = back
        for o in outs:                     # настоящий ответ клиенту мог быть позже — засчитать его
            if o["id"] in selfish:
                continue
            ids = matcher.match_reply_all(dict(o, sent_at=o.get("received_at")), cands, own)
            for mid in ids:
                self.store.mark_answered(mid, o["id"], o.get("received_at"))
            cands = [c for c in cands if c["id"] not in ids]
        for m in cands:
            self._log("Письмо «%s» (%s) возвращено в очередь: ответ ушёл не клиенту, клиент его не получил"
                      % (m.get("subject"), m.get("client_email") or m.get("sender")))

    # ---------- опрос ----------
    def poll(self):
        src = self.source
        since = datetime.now() - timedelta(days=int(self.cfg.get("lookback_days") or 3))
        cf = self.count_from()
        if cf and _p(cf) > since:
            since = _p(cf)
        t_fetch = datetime.now()
        incoming, outgoing = src.fetch(since)
        if getattr(src, "last_complete", True):
            if self.complete_since is None:
                self.complete_since = t_fetch
        else:
            self.complete_since = None             # был сбой чтения — «пропавшие» письма пока не снимаем
        if cf:   # всё, что пришло или отправлено до даты запуска, не учитываем совсем
            for m in incoming:
                if (m.get("received_at") or "") < cf and m["id"] not in self.skip_logged and not self.first_poll:
                    self.skip_logged.add(m["id"])
                    self._log("Письмо «%s» от %s (получено %s) не учитывается: раньше даты «Учитывать письма начиная с» (%s)"
                              % (m.get("subject"), m.get("sender"), (m.get("received_at") or "").replace("T", " ")[:16], cf.replace("T", " ")[:16]))
            incoming = [m for m in incoming if (m.get("received_at") or "") >= cf]
            outgoing = [o for o in outgoing if (o.get("sent_at") or "") >= cf]
        new_letters = []
        rescued = []
        junk_ids = self.store.junk_ids()
        gone_ids = {r["id"] for r in self.store._rows(
            "SELECT id FROM messages WHERE kind='in' AND no_reply_reason=? AND answered_at IS NULL", (GONE,))}
        seen = []                          # письма, уже известные базе: «видели сейчас» пишется одним сохранением
        for m in incoming:
            try:
                if self.store.has(m["id"]):
                    seen.append(m)
                    if m["id"] not in self.backfilled:      # письма, пришедшие до v1.30: дописать «Отвечать на» и адрес клиента
                        self.backfilled.add(m["id"])
                        rec = self.store.get(m["id"])
                        if rec and not rec.get("reply_to") and m.get("reply_to"):
                            self._client_info(m)
                            self.store.set_client(m)
                    if m["id"] in gone_ids:                   # письмо восстановили (из «Удалённых», из архива)
                        self.store.undo_no_reply(m["id"])
                        self._log("Письмо «%s» (%s) снова видно в Outlook — возвращено в очередь"
                                  % (m.get("subject") or "", m.get("sender") or ""))
                    if m["id"] in junk_ids and not m.get("junk"):
                        self.store.set_junk(m["id"], False)       # сотрудник нажал «Не нежелательное»
                        rescued.append(m)
                        self._log("Письмо «%s» перенесено из «Нежелательной почты» во «Входящие» — автоответ и пересылка"
                                  % (m.get("subject") or ""))
                    continue
                ign = matcher.is_ignored(m.get("sender"), m.get("sender_name"), m.get("subject"), self.cfg)
                if not ign and m.get("bulk"):
                    ign = ("рассылка: " + m["bulk"]) if isinstance(m.get("bulk"), str) else "рассылка (заголовки письма)"
                self._client_info(m)
                if m["reply_self"] and not ign:
                    self._log("Заявка с сайта «%s»: «Отвечать на» — наш ящик; %s" % (
                        m.get("subject"), ("адрес клиента из текста: " + m["client_email"]) if m["client_email"]
                        else "адреса клиента в тексте нет — ответить можно только по телефону/вручную"))
                cls = m.get("item_class") or 43
                if not ign and cls != 43:               # приглашение на встречу, отчёт о недоставке — в «Не попали в очередь»
                    from mail_outlook import OTHER_CLASSES
                    ign = OTHER_CLASSES.get(cls, "служебный элемент Outlook (тип %s)" % cls)
                text = (m.get("subject") or "") + "\n" + (m.get("body") or "")
                # отчёт о недоставке цитирует наше же письмо (в т.ч. сигнал угрозы в СБ) — тревогу по нему не поднимаем
                level, found = self.detector.analyze(text) if cls != 46 else (0, [])
                if m.get("junk"):
                    # «Нежелательная почта»: письмо в очереди с пометкой (клиентские письма с Gmail часто попадают туда);
                    # явная угроза — тревога; «возможная угроза» из спама жёлтый экран не поднимает (там много «last warning»)
                    if level == 2:
                        ign = ""
                    elif level == 1:
                        level, found = 0, []
                if level and self._own_echo(m, ign):
                    # наш же сигнал в СБ вернулся (автоответ «нет на месте», группа СБ с contact@, недоставка) или наш
                    # отчёт смены — тревогу НЕ поднимаем, иначе каждый сигнал порождал бы новый (петля)
                    if level == 2:
                        self._log("Письмо «%s» — возврат нашего сигнала в СБ или отчёта смены, тревога не поднимается" % (m.get("subject") or ""))
                    level, found = 0, []
                elif level == 2 and ign == "своё письмо":
                    # адрес отправителя подделывается: «заложена бомба» от имени нашего же ящика — тревога (решение 19.11)
                    self._log("Явная угроза в письме с адресом нашего ящика (адрес отправителя мог быть подделан) — тревога")
                if level == 2 and ign:
                    self._log("Явная угроза от отправителя из списка «не считать входящими» (%s) — тревога всё равно" % ign)
                    ign = ""
                if level == 1 and ign:
                    level, found = 0, []    # «возможная угроза» в рассылке: экрана нет — и в отчёте её быть не должно
                lang = classify.detect_lang(m.get("body") or "", m.get("subject") or "")   # язык самого человека, не подписи/цитаты
                cat = classify.categorize(m.get("subject"), m.get("body"), self.cfg.get("categories"), level == 2)
                pri = 1 if level else classify.prioritize(m.get("subject"), m.get("body"), m.get("sender"),
                                                          bool(m.get("importance_high")), self.cfg.get("priority") or {}, cat)
                rd = _p(m.get("received_at")) or datetime.now()
                key = shifts.shift_key_for(rd, self.cfg)
                if self.store.upsert_in(m, ign, found, key, lang, cat, pri, level):
                    if not ign:
                        new_letters.append(m)
                    elif not self.first_poll:
                        self._log("Письмо «%s» от %s не показывается: %s" % (m.get("subject"), m.get("sender"), ign))
                    if level:
                        self._log("%s: %s | %s | %s" % ("УГРОЗА" if level == 2 else "ВОЗМОЖНАЯ УГРОЗА",
                                                        m.get("sender"), m.get("subject"), ", ".join(found)))
            except Exception as e:      # сбой разбора одного письма не останавливает всю почту
                self._letter_failed(m, e)
        self.store.touch_many(seen)
        if not self.self_fix_done:
            self.self_fix_done = True
            try:
                self._fix_self_answers()
            except Exception as e:
                self._log("Проверка ответов самим себе не выполнена: " + str(e)[:200])
        cands = self.store.unanswered_candidates()
        prefixes = self._auto_prefixes()
        own = self._own()
        for o in outgoing:
            try:
                subj = o.get("subject") or ""
                is_auto = any(subj.strip().lower().startswith(p.lower()) for p in prefixes)
                rc = [(a or "").lower() for a in (o.get("recipients") or []) if a]
                to_self = not is_auto and bool(rc) and all(a in own for a in rc)
                if self.store.upsert_out(o, "автоответ" if is_auto else ("ответ себе" if to_self else None)):
                    if is_auto:
                        continue
                    ids = [] if to_self else matcher.match_reply_all(o, cands, own)
                    if not ids and not matcher.is_forward(subj):
                        # ответ не дошёл до клиента: «Ответить» на заявку с сайта ушло на contact@ (и коллегам) —
                        # письмо остаётся в очереди с красной пометкой; обычному письму — только если ответ ушёл себе
                        # то же — «Ответить» с заменой адресата на коллегу (все получатели своего домена)
                        internal = bool(rc) and bool(own) and not matcher.external(rc, own)
                        bad = self._self_target(o, cands, only_form=not (to_self or internal))
                        for mid in bad:
                            self.store.mark_self_reply(mid, o.get("sent_at"), ", ".join(rc))
                        if bad:
                            self._log("Ответ «%s» ушёл на %s, а не клиенту — клиент его НЕ получил, письмо остаётся в очереди"
                                      % (subj, ", ".join(rc)))
                        continue
                    if to_self:
                        continue
                    for mid in ids:
                        self.store.mark_answered(mid, o["id"], o.get("sent_at"))
                    if ids:
                        cands = [c for c in cands if c["id"] not in ids]
                        self._log("Ответ: %s -> %s" % (subj, ", ".join(ids)))
            except Exception as e:      # сбой на одном исходящем письме не останавливает учёт остальных
                self._out_failed(o, e)
        if new_letters and not self.first_poll:
            n = len(new_letters)
            if n == 1:
                m = new_letters[0]
                notify.toast("Новое письмо на ресепшн", "%s: %s" % (m.get("sender_name") or m.get("sender"), m.get("subject")))
            else:
                notify.toast("Новые письма на ресепшн", "Пришло писем: %d. Нужно ответить." % n)
        # автоответ и пересылка в отдел — только письмам не старше backlog_hours (в т.ч. пришедшим, пока ПК был
        # выключен). Старые письма, ставшие «новыми» позже (сдвинули дату учёта, Outlook досинхронизировал
        # архив), автоответ и пересылку не получают
        backlog = datetime.now() - timedelta(hours=int(self.cfg.get("auto_reply_backlog_hours") or 16))
        if self.no_auto_before and self.no_auto_before > backlog:
            backlog = self.no_auto_before      # база восстановлена после сбоя: прежним письмам автоответ уже уходил
        fresh = [m for m in new_letters if (_p(m.get("received_at")) or datetime.now()) >= backlog]
        if len(fresh) < len(new_letters):
            self._log("Старых писем без автоответа и пересылки (старше %s ч): %d" % (self.cfg.get("auto_reply_backlog_hours") or 16, len(new_letters) - len(fresh)))
        cleared, self.after_clear = self.after_clear, []
        for mid in cleared:            # «Угрозы нет» по жёлтому сигналу: письмо обрабатывается как обычное
            rec = self.store.get(mid)
            if rec and (_p(rec.get("received_at")) or datetime.now()) >= backlog:
                fresh.append(rec)
        fresh += rescued               # из «Нежелательной» во «Входящие» — обрабатываются при любом возрасте
        by_id = {m["id"]: m for m in incoming}
        fresh += self._ai_collect(by_id, {m["id"] for m in fresh})   # ИИ ответил (или не дождались) — догнать
        have = {m["id"] for m in fresh}
        for mid in {k[1] for k in self.retry} - have:    # в прошлый опрос Outlook не отправил (был занят) — повторить
            rec = self.store.get(mid)
            if rec:
                fresh.append(rec)
        self.retry = {k: v for k, v in self.retry.items() if k[1] in {m["id"] for m in fresh}}
        for m in fresh:               # сначала направление и пересылка в отдел, затем автоответ (уходит всем)
            self.heartbeat = time.time()
            try:
                if self._ai_hold(m, by_id):      # письмо ждёт второго фильтра (ИИ): пересылка и автоответ — после него
                    continue
                self._auto_forward(m)
                self._auto_reply(m)
            except Exception as e:
                self._log("Автоответ и пересылка письма «%s» не выполнены: %s" % (m.get("subject"), str(e)[:200]))
        try:
            self._mark_vanished(since)
        except Exception as e:
            self._log("Проверка удалённых писем не выполнена: " + str(e)[:200])
        self.first_poll = False
        self.last_poll = datetime.now()
        self.status = src.status()
        self._log_read_problems(src)

    def _log_read_problems(self, src):
        """Сбои чтения Outlook — в журнал с текстом первой ошибки (раз в 10 минут, пока не пройдут)."""
        bad = getattr(src, "read_problems", 0) or 0
        if not bad:
            self.read_logged = 0
            return
        if time.time() - getattr(self, "read_logged", 0) < 600:
            return
        self.read_logged = time.time()
        first = ""
        for path, st in (getattr(src, "scan", {}) or {}).items():
            if st.get("item_error") or st.get("error"):
                first = "%s: %s" % (path, st.get("item_error") or st.get("error"))
                break
        self._log("ВНИМАНИЕ: сбоев чтения Outlook: %d%s%s" % (
            bad, (" — " + src.read_broken) if getattr(src, "read_broken", "") else "",
            ("; первая ошибка — " + first) if first else ""))

    def _own_echo(self, m, ign):
        """Письмо — возврат нашего же сигнала в СБ или нашего отчёта смены (в них цитируются слова угрозы).
        Узнаётся по нашей теме, И отправитель должен быть «своим»: наш ящик или домен, почта СБ, автоотправитель
        (mailer-daemon, автоответ). Чужое письмо с такой темой тревогу не гасит."""
        subj = (m.get("subject") or "").lower()
        if SB_MARKS[0].lower() not in subj and REPORT_MARK.lower() not in subj:
            return False
        s = (m.get("sender") or "").lower()
        own = self._own()
        own_dom = {a.split("@", 1)[1] for a in own}
        sec = {x.strip().lower() for x in (self.cfg.get("security_email") or "").replace(",", ";").split(";") if x.strip()}
        return (not s or s in own or s in sec or ("@" in s and s.split("@", 1)[1] in own_dom)
                or (ign or "").startswith(("автоотправитель", "автописьмо", "своё письмо")))

    def _letter_failed(self, m, e):
        """Письмо, на котором споткнулся разбор, не останавливает остальную почту и не теряется: встаёт в очередь
        как обычное (без автоответа и пересылки); угроза в нём проверяется отдельно."""
        mid = m.get("id")
        if mid not in self.skip_logged:
            self.skip_logged.add(mid)
            self._log("Письмо «%s» от %s разобрано с ошибкой (%s) — поставлено в очередь без автоответа и пересылки"
                      % (m.get("subject"), m.get("sender"), str(e)[:150]))
        try:
            if self.store.has(mid):
                return
            level, found = 0, []
            try:
                if (m.get("item_class") or 43) != 46 and not self._own_echo(
                        m, matcher.is_ignored(m.get("sender"), m.get("sender_name"), m.get("subject"), self.cfg)):
                    level, found = self.detector.analyze((m.get("subject") or "") + "\n" + (m.get("body") or ""))
                    if level == 1 and m.get("junk"):
                        level, found = 0, []
            except Exception:
                level, found = 0, []
            rd = _p(m.get("received_at")) or datetime.now()
            self.store.upsert_in(m, "", found, rd.strftime("%Y-%m-%d") + "_1", "ru",
                                 "security" if level == 2 else "general", 1 if level else 2, level)
        except Exception as e2:
            self._log("Письмо «%s» не записано в базу: %s" % (m.get("subject"), str(e2)[:150]))

    def _out_failed(self, o, e):
        oid = o.get("id")
        if oid not in self.skip_logged:
            self.skip_logged.add(oid)
            self._log("Отправленное письмо «%s» учтено с ошибкой: %s" % (o.get("subject"), str(e)[:150]))

    def _retry(self, kind, mid, e, tries):
        """Outlook не отправил автоответ/пересылку (занят, перезапускается): повторить в следующие опросы, всего
        3 попытки. RuntimeError — отказ по правилам (ответ ушёл бы на наш же ящик): повторять незачем.
        tries — сколько попыток уже было (запись о повторе снимается в начале каждой попытки)."""
        if isinstance(e, RuntimeError):
            return ""
        n = tries + 1
        if n >= 3:
            return " (попытка %d, больше не повторяется)" % n
        self.retry[(kind, mid)] = n
        return " (попытка %d, повтор при следующем опросе)" % n

    def _mark_vanished(self, since):
        """Письмо удалили или перенесли из Outlook (в «Удалённые», архив): в очереди ему висеть нельзя.
        Снимаем, если 2 минуты подряд его не было при ПОЛНОМ чтении Outlook (все папки и письма без ошибок) —
        сбой чтения не может «удалить» настоящее письмо."""
        if self.cfg.get("mode") != "outlook" or self.first_poll or self.complete_since is None:
            return
        now = datetime.now()
        # край окна чтения «N дней назад» сдвигается — письма на краю не считаем удалёнными; дата учёта не сдвигается
        guard = now - timedelta(days=int(self.cfg.get("lookback_days") or 3)) + timedelta(minutes=5)
        cf = _p(self.count_from() or "")
        if cf and cf > guard:
            guard = cf
        for m in self.store.vanished(_iso(guard), _iso(now - timedelta(minutes=2))):
            gone_from = max(_p(m.get("last_seen")) or now, self.complete_since)
            if now - gone_from < timedelta(minutes=2):
                continue
            self.store.mark_no_reply(m["id"], GONE)
            self._log("Письмо «%s» (%s) больше не видно в Outlook — снято с очереди" % (m.get("subject"), m.get("sender")))

    def _thread_key(self, subject):
        """Тема без RE:/FW: и без префикса нашего автоответа — одна переписка = один ключ."""
        s = matcher.norm_subject(subject)
        prefixes = [p.strip().lower() for p in self._auto_prefixes() if p.strip()]
        prev = None
        while prev != s:
            prev = s
            for p in prefixes:
                if s.startswith(p):
                    s = matcher.norm_subject(s[len(p):])
        return s

    @staticmethod
    def _threat_blocks(rec):
        """Угроза — без автоответа и пересылки; «возможная угроза», которую сотрудник снял («Угрозы нет»), — обычное письмо."""
        if not rec.get("threat_words"):
            return False
        return not ((rec.get("threat_level") or 2) == 1 and rec.get("threat_ack_at"))

    def _auto_reply(self, m):
        tries = self.retry.pop(("reply", m["id"]), 0)
        if not self.cfg.get("auto_reply_enabled"):
            return
        rec = self.store.get(m["id"])
        if not rec or rec.get("ignored") or self._threat_blocks(rec) or rec.get("auto_replied_at") or rec.get("answered_at"):
            return
        if rec.get("junk"):
            self._log("Автоответ не отправлен (письмо в «Нежелательной почте»; если оно настоящее — в Outlook «Не нежелательное»): %s"
                      % (rec.get("subject") or ""))
            return
        if rec.get("reply_self") and not rec.get("client_email"):
            self._log("Автоответ не отправлен: заявка с сайта «%s» — «Ответить» ушло бы на наш ящик, адреса клиента в тексте нет"
                      % (rec.get("subject") or ""))
            return
        # кому отвечаем: адрес клиента (заявка с сайта, Reply-To) или отправитель
        sender = (rec.get("client_email") or rec.get("sender") or "").lower()
        if not sender or "@" not in sender:
            return
        own = self._own()
        if sender in own or (rec.get("sender") or "").lower() in own:
            return  # не отвечаем самому ящику ресепшн
        # коллеги своего домена (ответ отдела «всем» с contact@ в копии) автоответ не получают — решение Олега 18.09
        own_domains = {a.split("@", 1)[1] for a in own}
        if self.cfg.get("auto_reply_skip_own_domain", True) and any(
                "@" in a and a.split("@", 1)[1] in own_domains for a in (sender, (rec.get("sender") or "").lower())):
            self._log("Автоответ не отправлен (%s — свой домен): %s" % (sender, rec.get("subject")))
            return
        # правило Олега (17.09): автоответ на КАЖДОЕ письмо; исключение — та же тема от того же
        # отправителя (продолжение переписки): автоответ по теме уходит один раз
        key = self._thread_key(rec.get("subject"))
        prev = self.store.auto_replied_to(sender)
        if any(self._thread_key(p.get("subject")) == key for p in prev):
            self._log("Автоответ не отправлен (по теме «%s» %s уже получал автоответ)" % (rec.get("subject"), sender))
            return
        # защита от переписки двух автоответчиков: не больше 5 автоответов одному адресу в час
        hour_ago = _iso(datetime.now() - timedelta(hours=1))
        if sum(1 for p in prev if (p.get("auto_replied_at") or "") >= hour_ago) >= 5:
            self._log("Автоответ не отправлен (%s: больше 5 автоответов за час — похоже на автоответчик)" % sender)
            return
        lang = rec.get("lang") or "ru"
        tpls = (self.cfg.get("auto_reply_templates") or {}).get(rec.get("category") or "general") or {}
        tpl = tpls.get(lang) or tpls.get("ru") or ((self.cfg.get("auto_reply_templates") or {}).get("general") or {}).get(lang) or ""
        if not tpl.strip():
            return
        phone = (self.cfg.get("reception_phone") or "").strip()
        if not phone:
            tpl = "\n".join(line for line in tpl.split("\n") if "{phone}" not in line)
        text = classify.render_template(tpl, phone=phone, name=rec.get("sender_name") or "",
                                        sla=self.sla_for(rec.get("priority")))
        prefix = ((self.cfg.get("auto_reply_subject") or {}).get(lang) or config.DEFAULT_AUTO_SUBJECT.get(lang)
                  or config.DEFAULT_AUTO_SUBJECT["ru"])
        subject = prefix + (rec.get("subject") or "")
        try:
            if rec.get("client_email"):
                self.source.send_reply(rec, subject, text, to=rec["client_email"])
            else:
                self.source.send_reply(rec, subject, text)
            self.store.set_auto_replied(rec["id"])
            self._log("Автоответ (%s/%s): %s" % (rec.get("category"), lang, sender))
        except Exception as e:
            self._log("Автоответ не отправлен%s: %s" % (self._retry("reply", rec["id"], e, tries), str(e)[:200]))

    def forward_addr(self, category):
        for c in self.cfg.get("categories") or []:
            if c.get("key") == category:
                to = (c.get("forward_to") or "").strip()
                if to.lower() == DEMO_FORWARD and self.cfg.get("mode") != "demo":
                    return ""       # демо-кнопка записала этот адрес в настройки: настоящие письма на него не пересылаем
                return to
        return ""

    def _auto_forward(self, m):
        tries = self.retry.pop(("forward", m["id"]), 0)
        rec = self.store.get(m["id"])
        if not rec or rec.get("ignored") or self._threat_blocks(rec) or rec.get("forwarded_at") or rec.get("answered_at") or rec.get("junk"):
            return
        if rec.get("cat_conflict"):
            self._log("Письмо «%s»: словарь и ИИ разошлись в направлении («%s» или «%s») — в отдел не пересылается, "
                      "направление выбирает сотрудник на карточке" % (rec.get("subject"), self.cat_name(rec.get("category")),
                                                                    self.cat_name(rec.get("cat_conflict"))))
            return
        own = self._own()
        sender = (rec.get("sender") or "").lower()
        if self.cfg.get("auto_reply_skip_own_domain", True) and "@" in sender and sender.split("@", 1)[1] in {x.split("@", 1)[1] for x in own}:
            self._log("Пересылка не выполнена (%s — свой домен, письмо остаётся на ресепшн): %s" % (sender, rec.get("subject")))
            return   # ответ отдела «всем» не гоняем обратно в отдел
        to = self.forward_addr(rec.get("category"))
        if not to:
            self._log("Письмо «%s» — направление «%s», пересылка для него не настроена" % (rec.get("subject"), self.cat_name(rec.get("category"))))
            return
        note = classify.render_template(self.cfg.get("forward_note") or "", category=self.cat_name(rec.get("category")),
                                        phone=self.cfg.get("reception_phone") or "")
        if rec.get("client_email"):        # заявка с сайта: «Ответить» в отделе ушло бы на ящик ресепшн
            note += "\nАдрес клиента для ответа: %s%s" % (rec["client_email"], (" (" + rec["client_name"] + ")") if rec.get("client_name") else "")
        elif rec.get("reply_self"):
            note += "\nВнимание: «Ответить» на это письмо уйдёт на ящик ресепшн — адрес клиента ищите в тексте заявки."
        try:
            self.source.forward(rec, to, note)
            self.store.mark_forwarded(rec["id"], to)
            self._log("Переслано в отдел (%s): %s -> %s" % (rec.get("category"), rec.get("subject"), to))
        except Exception as e:
            self._log("Пересылка не удалась%s: %s" % (self._retry("forward", rec["id"], e, tries), str(e)[:200]))

    def _daily_backup(self):
        """Раз в день копия базы и config.json в data/backup (защита от порчи и случайного удаления)."""
        try:
            folder = os.path.join(self.base, "data", "backup")
            p = self.store.backup(folder)
            if p:
                import shutil
                if os.path.exists(config.CONFIG_PATH):
                    shutil.copy2(config.CONFIG_PATH, os.path.join(folder, "config-%s.json" % datetime.now().strftime("%Y-%m-%d")))
                self._log("Резервная копия базы: " + os.path.basename(p))
        except Exception as e:
            self._log("Резервная копия не сделана: " + str(e)[:150])

    def check_overdue(self):
        rep = int(self.cfg.get("repeat_alert_minutes") or 10)
        now = datetime.now()
        due = []
        for m in self.store.queue(bool(self.cfg.get("forward_removes_from_queue", True))):
            rd = _p(m["received_at"])
            if rd:
                rd = shifts.sla_start(rd, self.cfg)
            if not rd or (now - rd).total_seconds() < self.sla_for(m.get("priority")) * 60:
                continue
            last = self.last_alert.get(m["id"])
            if last is None or (now - last).total_seconds() >= rep * 60:
                due.append(m)
        if due:
            for m in due:
                self.last_alert[m["id"]] = now
            if len(due) == 1:
                m = due[0]
                notify.toast("Письмо ждёт ответа больше %d мин" % self.sla_for(m.get("priority")),
                             "%s: %s" % (m.get("sender_name") or m.get("sender"), m.get("subject")))
            else:
                notify.toast("Просрочены ответы: %d писем" % len(due), "Откройте экран «Почта ресепшн»")

    def check_threats(self):
        pending = self.store.threats_pending()
        if not pending:
            notify.siren_stop()
            self._unpin()
            return
        red = [m for m in pending if (m.get("threat_level") or 2) == 2]
        if red:
            notify.siren_start()
        else:
            notify.siren_stop()
        self._threat_to_front(bool(red), pending)
        for m in pending:
            lvl = m.get("threat_level") or 2
            if m["id"] not in self.threat_opened:
                self.threat_opened.add(m["id"])
                if lvl == 2:
                    notify.toast("ВНИМАНИЕ! УГРОЗА", "Письмо: %s. Откройте экран ресепшн." % (m.get("subject") or ""))
                else:
                    notify.toast("Возможная угроза — проверьте письмо", "Письмо: %s" % (m.get("subject") or ""))
                    notify.chime()
            if lvl == 2 and not m.get("threat_notified_at"):
                st = self.sec_sent.setdefault(m["id"], {"tg": False, "mail": False, "next": 0})
                if time.time() < st["next"]:
                    continue
                st["next"] = time.time() + 120          # не получилось — повтор через 2 минуты
                use_tg = bool((self.cfg.get("telegram_bot_token") or "").strip())
                to = (self.cfg.get("security_email") or "").strip()
                if use_tg and not st["tg"]:
                    try:
                        failed = telegram.send_message(self.cfg.get("telegram_bot_token"),
                                                       st.get("tg_left") or self.cfg.get("telegram_chat_id"),
                                                       telegram.threat_text(m, self.cfg.get("reception_phone")))
                        if not failed:
                            st["tg"] = True
                            self._log("Тревога отправлена в Telegram СБ")
                        else:            # части получателей не дошло (бот удалён из группы, ошибка в ID) — повторить им
                            st["tg_tries"] = st.get("tg_tries", 0) + 1
                            who = "; ".join("%s: %s" % kv for kv in failed.items())[:300]
                            if st["tg_tries"] >= 5:
                                st["tg"] = True
                                self._log("ВНИМАНИЕ: тревога в Telegram дошла НЕ ВСЕМ получателям, повторы прекращены. "
                                          "Не получили: " + who)
                            else:
                                st["tg_left"] = ",".join(failed)
                                self._log("ВНИМАНИЕ: тревога в Telegram дошла не всем (повтор им через 2 мин). Не получили: " + who)
                    except Exception as e:
                        self._log("Telegram: не удалось отправить тревогу (повтор через 2 мин): " + str(e)[:200])
                if to and not st["mail"]:
                    try:
                        body = ("<p><b>" + SB_MARKS[1] + ".</b></p>"
                                "<p>От: %s &lt;%s&gt;<br>Тема: %s<br>Получено: %s<br>Найдено: %s</p><p>%s</p>"
                                % (html.escape(m.get("sender_name") or ""), html.escape(m.get("sender") or ""),
                                   html.escape(m.get("subject") or ""), (m.get("received_at") or "").replace("T", " "),
                                   html.escape(", ".join(m.get("threat_words") or [])),
                                   html.escape(m.get("body_preview") or "")))
                        self.source.send_mail(to, "", SB_MARKS[0] + " — " + (m.get("subject") or ""), body)
                        st["mail"] = True
                        self._log("Письмо в СБ отправлено: " + to)
                    except Exception as e:
                        self._log("Не удалось отправить письмо в СБ (повтор через 2 мин): " + str(e)[:200])
                if (st["tg"] or not use_tg) and (st["mail"] or not to):
                    if not use_tg and not to:
                        self._log("ВНИМАНИЕ: сигнал об угрозе в службу безопасности НЕ отправлен — в Настройках не заданы "
                                  "ни Telegram, ни почта СБ. Тревога только на экране ресепшн: звоните в СБ")
                    self.store.set_threat_notified(m["id"])
                    self.sec_sent.pop(m["id"], None)

    def _unpin(self):
        if self.pinned_hwnd:
            notify.set_topmost(self.pinned_hwnd, False)
            self.pinned_hwnd = 0

    def _threat_to_front(self, red, pending):
        """Экран тревоги поверх любых программ.
        Красный сигнал: окно закреплено поверх всех окон и возвращается (свернули, закрыли вкладку, перекрыли)
        каждые 2 с, пока сотрудник не нажмёт «Я сообщил»; «Открыть письмо в Outlook» — пауза 90 с.
        Жёлтый: окно выводится поверх один раз при появлении."""
        now = time.time()
        title = "— Почта ресепшн"      # заголовок вкладки с экраном тревоги (не «Настройки · …», не «Отчёты · …»)
        if red:
            if now < self.pin_pause_until:
                self._unpin()
                return
            if notify.pinned_ok(self.pinned_hwnd, title):
                return
            hwnd = notify.bring_to_front(title, pin=True)
            if hwnd:
                if hwnd != self.pinned_hwnd:
                    self._unpin()
                    self.pinned_hwnd = hwnd
                self.fronted.update(m["id"] for m in pending)
                return
            if now >= self.browser_opened_at + 30:  # вкладку закрыли или переключили — открыть экран снова
                self.browser_opened_at = now
                try:
                    webbrowser.open(self.url)
                except Exception:
                    pass
            return
        self._unpin()
        new = [m["id"] for m in pending if m["id"] not in self.fronted]
        if not new and (not red or now < self.front_next):   # новый сигнал — сразу; красный — повтор раз в минуту
            return
        self.fronted.update(m["id"] for m in pending)
        if notify.is_foreground(title):
            self.front_next = now + 60
            return
        if notify.bring_to_front(title):
            self.front_next = now + 60
            return
        if now >= self.browser_opened_at + 120:     # окна программы нет — открыть экран в браузере
            self.browser_opened_at = now
            try:
                webbrowser.open(self.url)
            except Exception:
                pass
        self.fronted.difference_update(new)       # окно ещё открывается — повторить через 3 с
        self.front_next = now + 3

    def loop(self):
        self.loop_running = True
        next_poll = 0
        next_minute = 0
        last_tick = time.time()
        while not self.stop:
            try:
                now = time.time()
                self.heartbeat = now
                if now - last_tick > 90:      # ноутбук спал (закрыли крышку): связь с Outlook устанавливаем заново
                    self._log("Компьютер проснулся после сна (%d мин) — переподключение к Outlook" % ((now - last_tick) // 60))
                    try:
                        self.source.inbox = None
                    except Exception:
                        pass
                    self.poll_now = True
                last_tick = now
                interval = 5 if self.cfg.get("mode") == "demo" else max(5, int(self.cfg.get("poll_seconds") or 20))
                if now < self.boost_until:
                    interval = min(interval, 3)     # сотрудник отвечает на письмо — ждём его ответ в «Отправленных»
                    next_poll = min(next_poll, now + interval)
                # каждые 2 с: число писем во «Входящих»/«Отправленных» изменилось — опрос сразу
                # (отвеченное письмо уходит с экрана через 2–5 с после отправки, а не через 20 с)
                if not self.poll_now and self.cfg.get("mode") == "outlook" and hasattr(self.source, "changed"):
                    try:
                        if self.source.changed():
                            self.poll_now = True
                    except Exception:
                        pass
                if self.poll_now or now >= next_poll:
                    self.poll_now = False
                    next_poll = now + interval
                    try:
                        t0 = time.time()
                        self.poll()
                        if time.time() - t0 > 3:
                            self._log("Опрос почты занял %.1f с" % (time.time() - t0))
                    except Exception as e:
                        self.status = ("error", "Нет связи с Outlook: " + str(e)[:400])
                        self._log("Ошибка опроса: " + str(e)[:300])
                        try:
                            self.source.inbox = None
                        except Exception:
                            pass
                self.heartbeat = time.time()
                self.check_threats()
                if now >= next_minute:
                    next_minute = now + 60
                    self.check_overdue()
                    self._daily_backup()
                    # смены открываются и закрываются ТОЛЬКО сотрудниками вручную (решение Олега 17.09)
            except Exception:
                self._log(traceback.format_exc()[-500:])
            last_tick = time.time()       # сон компьютера меряем по паузе между кругами: долгий опрос — не «сон»
            time.sleep(2)

    # ---------- второй фильтр: ИИ ----------
    def _ai_paused(self):
        return time.time() < self.ai_fail["until"]

    def ai_status(self):
        """Для экрана: включён ли ИИ и работает ли он."""
        c = self.cfg
        if not c.get("ai_enabled"):
            return {"on": False, "state": "off", "text": "", "sdk": ai.sdk_ok()}
        why = self.ai.why_off()
        if why:
            return {"on": True, "state": "error", "text": "ИИ не работает: %s. Письма разбирает словарь." % why, "sdk": ai.sdk_ok()}
        if self._ai_paused():
            return {"on": True, "state": "error", "sdk": True,
                    "text": "ИИ временно не отвечает (%s). Письма разбирает словарь; следующая попытка в %s."
                            % (self.ai_fail["msg"], datetime.fromtimestamp(self.ai_fail["until"]).strftime("%H:%M"))}
        return {"on": True, "state": "ok", "text": "ИИ проверяет направление писем и угрозы", "sdk": True,
                "pending": len(self.ai_jobs)}

    def _ai_wants(self, rec):
        return self._ai_skip_reason(rec) is None

    def _ai_skip_reason(self, rec):
        """None — письмо отправляется на второй фильтр; иначе причина, почему нет ("" — причина не для показа:
        рассылка, уже отвечено, уже проверено ИИ). Явная угроза по словарю (красный экран) в ИИ не уходит: тревога
        уже поднята, и снять её ИИ не может. Письма коллег своего домена не отправляются (внутренняя переписка)."""
        if not rec or rec.get("kind") != "in" or rec.get("ignored") or rec.get("ai_state") or rec.get("answered_at"):
            return ""
        if rec.get("no_reply_reason"):
            return ""
        if (rec.get("threat_level") or 0) == 2:
            return "явная угроза по словарю — красный экран уже поднят, ИИ его не снимает"
        if rec.get("threat_ack_at"):
            return ""
        if not self.ai.enabled():
            if not self.cfg.get("ai_enabled"):
                return ("в Настройках не стоит галочка «Включить проверку писем ИИ» (или настройки не сохранены "
                        "кнопкой «Сохранить настройки»)")
            return self.ai.why_off() or "ИИ не настроен"
        if self._ai_paused():
            return "ИИ временно на паузе после сбоев (%s)" % self.ai_fail["msg"]
        s = (rec.get("sender") or "").lower()
        own_dom = {a.split("@", 1)[1] for a in self._own()}
        if "@" in s and s.split("@", 1)[1] in own_dom:
            return "письмо коллеги своего домена (@%s) — внутренняя переписка в ИИ не отправляется" % s.split("@", 1)[1]
        return None

    def _ai_note_skip(self, rec, why):
        """Письмо не ушло в ИИ: причина — в списке «что отправлялось в ИИ» и в журнале (если ИИ вообще используется)."""
        if not why or not (self.cfg.get("ai_enabled") or (self.cfg.get("ai_api_key") or "").strip()):
            return
        self.ai_log.append({"at": _iso(datetime.now()), "sent": "Тема: " + (rec.get("subject") or "(без темы)"),
                            "hidden": {}, "result": "НЕ отправлено в ИИ: " + why, "skipped": True})
        self._log("Письмо «%s» (%s) в ИИ не отправлено: %s — обработано по словарю"
                  % (rec.get("subject") or "", rec.get("sender") or "", why))

    def _ai_hold(self, m, by_id):
        """True — письмо отправлено (или уже ждёт) на второй фильтр: пересылка и автоответ откладываются."""
        rec = self.store.get(m["id"])
        if not rec:
            return False
        if rec.get("ai_state") == "pending":
            return True
        why = self._ai_skip_reason(rec)
        if why is not None:
            self._ai_note_skip(rec, why)
            return False
        self._ai_submit(rec, by_id.get(rec["id"]) or m)
        return True

    def _ai_submit(self, rec, full):
        """full — письмо с полным текстом (из опроса Outlook); нет — в ИИ уйдёт начало письма из базы."""
        job = {"id": rec["id"], "t0": time.time(), "subject": rec.get("subject") or "",
               "body": (full or {}).get("body") or rec.get("body_preview") or "",
               "attachments": rec.get("attachments") or [], "sender": rec.get("sender") or "",
               "names": [x for x in (rec.get("sender_name"), rec.get("client_name")) if x],
               "importance_high": bool((full or {}).get("importance_high"))}
        self.ai_jobs[rec["id"]] = job
        self.store.set_fields(rec["id"], ai_state="pending", rule_category=rec.get("category") or "general")
        self.ai_queue.put(job)
        if self.ai_thread is None or not self.ai_thread.is_alive():
            self.ai_thread = threading.Thread(target=self._ai_worker, daemon=True)
            self.ai_thread.start()

    def _ai_collect(self, by_id, have):
        """Письма, которым пора догнать пересылку и автоответ: ИИ ответил; ИИ не ответил за отведённое время;
        задание потерялось (программу перезапустили) — такое письмо отправляется в ИИ заново."""
        out = []
        with self.ai_lock:
            ready, self.ai_ready = self.ai_ready, []
        wait = int(self.cfg.get("ai_wait_seconds") or 180)
        now = time.time()
        for rec in self.store.ai_pending():
            job = self.ai_jobs.get(rec["id"])
            if job is None:
                if self.ai.enabled() and not self._ai_paused() and rec["id"] in by_id:
                    self._ai_submit(dict(rec, ai_state=None), by_id[rec["id"]])
                    continue
            elif now - job["t0"] < wait:
                continue
            # не дождались ИИ — письмо обрабатывается по словарю
            self.ai_jobs.pop(rec["id"], None)
            self.store.set_fields(rec["id"], ai_state="failed", ai_note="", ai_at=_iso(datetime.now()))
            self._log("Письмо «%s»: ИИ не ответил вовремя — обработано по словарю" % (rec.get("subject") or ""))
            ready.append(rec["id"])
        for mid in dict.fromkeys(ready):
            if mid in have:
                continue
            rec = self.store.get(mid)
            if rec:
                out.append(rec)
        return out

    def _ai_worker(self):
        while not self.stop:
            try:
                job = self.ai_queue.get(timeout=5)
            except queue.Empty:
                continue
            try:
                if self.ai_jobs.get(job["id"]) is job:       # задание не снято по времени
                    self._ai_run(job)
            except Exception:
                self._log("Сбой второго фильтра (ИИ): " + traceback.format_exc()[-400:])
                try:
                    self.store.set_fields(job["id"], ai_state="failed", ai_at=_iso(datetime.now()))
                except Exception:
                    pass
            finally:
                if self.ai_jobs.get(job["id"]) is job:
                    self.ai_jobs.pop(job["id"], None)
                with self.ai_lock:
                    self.ai_ready.append(job["id"])
                self.poll_now = True

    def _ai_failed(self, e):
        f = self.ai_fail
        f["n"] += 1
        f["msg"] = str(e)[:120]
        if e.kind in ("key", "off") or f["n"] >= 3:
            f["until"] = time.time() + (600 if e.kind == "key" else 300)
            f["n"] = 0
            self._log("ВНИМАНИЕ: ИИ не работает (%s) — письма разбирает только словарь; повторная попытка через %d мин"
                      % (f["msg"], 10 if e.kind == "key" else 5))

    def _ai_run(self, job):
        mid = job["id"]
        text, masker = self.ai.prepare(job["subject"], job["body"], job["attachments"], job["names"])
        entry = {"at": _iso(datetime.now()), "sent": text, "hidden": masker.counts(), "result": ""}
        self.ai_log.append(entry)
        rec = self.store.get(mid)
        if not rec:
            return
        rule_cat = rec.get("rule_category") or rec.get("category") or "general"
        try:
            v = self.ai.analyze(text)
        except ai.AIError as e:
            entry["result"] = "нет ответа: " + str(e)
            if e.kind == "refusal":
                # модель отказалась разбирать письмо из-за его содержания — это само по себе повод показать письмо человеку
                self._ai_threat(mid, job, "possible", "ИИ отказался разбирать это письмо из-за его содержания — прочитайте письмо")
                self.store.set_fields(mid, ai_state="done", ai_threat="possible", ai_at=_iso(datetime.now()))
                return
            self._ai_failed(e)
            self.store.set_fields(mid, ai_state="failed", ai_at=_iso(datetime.now()))
            self._log("Письмо «%s»: ИИ не ответил (%s) — обработано по словарю" % (job["subject"], str(e)[:160]))
            return
        self.ai_fail["n"] = 0
        self.ai_last_ok = datetime.now()
        fields = {"ai_state": "done", "ai_at": _iso(datetime.now()), "ai_threat": v["threat"], "ai_conf": v["confidence"]}
        # --- угроза: срабатывает тот фильтр, который увидел больше опасности; снять тревогу словаря ИИ не может
        level = self._ai_threat(mid, job, v["threat"], masker.unmask(v["threat_reason"]))
        if self.ai_jobs.get(mid) is not job:
            # ответ опоздал: письмо уже обработано по словарю (переслано, автоответ ушёл) — направление не трогаем,
            # но угрозу, которую увидел ИИ, показываем и с опозданием
            entry["result"] = "ответ опоздал; угроза: %s" % v["threat"]
            return
        if v["threat"] == "none" and level == 1:
            fields["ai_threat_note"] = "ИИ считает, что угрозы нет" + ((": " + masker.unmask(v["threat_reason"])) if v["threat_reason"] else "")
        # --- направление: совпало со словарём — готово; разошлось — ИИ перепроверяет с обоими мнениями
        ai_cat, conf, reason = v["category"], v["confidence"], masker.unmask(v["category_reason"]).rstrip(" .")
        final, conflict, note = rule_cat, None, ""
        if level == 2:
            ai_cat = final = "security"
        elif ai_cat != rule_cat:
            words = classify.explain(job["subject"], job["body"], self.cfg.get("categories"), rule_cat)
            try:
                r = self.ai.recheck(text, rule_cat, words, ai_cat, v["category_reason"])
                final, conf, reason = r["category"], r["confidence"], masker.unmask(r["reason"]).rstrip(" .")
            except ai.AIError as e:
                self._ai_failed(e)
                final, conf = ai_cat, "low"
            entry["result"] = "словарь: %s; ИИ: %s; после перепроверки: %s (%s)" % (rule_cat, ai_cat, final, conf)
            if final == rule_cat:
                note = "ИИ сначала предложил «%s», после перепроверки согласился со словарём" % self.cat_name(ai_cat)
            elif conf != "high" and self.forward_addr(final) != self.forward_addr(rule_cat):
                # уверенности нет, а от направления зависит пересылка — решает сотрудник
                conflict, note = final, ("Словарь: «%s», ИИ: «%s»%s"
                                         % (self.cat_name(rule_cat), self.cat_name(final), (" — " + reason) if reason else ""))
                final = rule_cat
            else:
                note = "ИИ: направление «%s» вместо «%s» по словарю%s" % (
                    self.cat_name(final), self.cat_name(rule_cat), (" — " + reason) if reason else "")
                self._ai_suggest(final, v.get("keywords"), job)
        fields.update(ai_category=conflict or final, ai_note=note, cat_conflict=conflict, category=final)
        if not entry["result"]:
            entry["result"] = "направление: %s (%s); угроза: %s" % (v["category"], v["confidence"], v["threat"])
        if final != (rec.get("category") or "general") and level == 0:
            fields["priority"] = classify.prioritize(job["subject"], job["body"], job["sender"], job["importance_high"],
                                                     self.cfg.get("priority") or {}, final)
        self.store.set_fields(mid, **fields)
        if note:
            self._log("Письмо «%s»: %s" % (job["subject"], note))

    def _ai_threat(self, mid, job, threat, reason):
        """Применить мнение ИИ об угрозе. Возвращает итоговый уровень (0, 1, 2)."""
        rec = self.store.get(mid) or {}
        level = rec.get("threat_level") or 0
        if threat == "none" or rec.get("threat_ack_at"):
            return level
        mark = "ИИ: " + (reason or ("явная угроза" if threat == "explicit" else "возможная угроза"))
        words = [w for w in (rec.get("threat_words") or []) if not w.startswith("ИИ: ")] + [mark]
        if level == 0:
            # словарь ничего не нашёл, ИИ видит угрозу — жёлтый экран: письмо читает сотрудник
            self.store.set_fields(mid, threat_level=1, threat_words=words, priority=1)
            self._log("ВОЗМОЖНАЯ УГРОЗА (ИИ): %s | %s | %s" % (job["sender"], job["subject"], reason))
            return 1
        if level == 1 and threat == "explicit":
            # словарь — «возможная», ИИ — «явная»: красный экран, сигнал в СБ
            self.store.set_fields(mid, threat_level=2, threat_words=words, priority=1, category="security")
            self.threat_opened.discard(mid)
            self._log("УГРОЗА (словарь: возможная, ИИ: явная): %s | %s | %s" % (job["sender"], job["subject"], reason))
            return 2
        self.store.set_fields(mid, threat_words=words)
        return level

    def _ai_suggest(self, category, keywords, job):
        """ИИ исправил направление: слова письма, по которым это видно, предлагаются в словарь (решает администратор)."""
        text = (job["subject"] + "\n" + job["body"]).lower()
        have = []
        for c in self.cfg.get("categories") or []:
            if c.get("key") == category:
                have = [(w or "").strip().lower() for w in c.get("words") or []]
        for k in keywords or []:
            k = " ".join(str(k).lower().split())
            if not 4 <= len(k) <= 40 or len(k.split()) > 3 or any(ch.isdigit() or ch in "[]@" for ch in k):
                continue
            if k not in text or any(w and (k.startswith(w) or w in k.split()) for w in have):
                continue
            try:
                self.store.suggest_add(category, k, job["subject"])
            except Exception:
                pass

    def set_category(self, mid, key):
        """Сотрудник выбрал направление письма, в котором словарь и ИИ разошлись."""
        rec = self.store.get(mid)
        if not rec or key not in [c.get("key") for c in self.cfg.get("categories") or []]:
            return False
        self.store.set_fields(mid, category=key, cat_conflict=None,
                              ai_note="Направление выбрано сотрудником: «%s»" % self.cat_name(key))
        self._log("Письмо «%s»: направление «%s» выбрано сотрудником" % (rec.get("subject"), self.cat_name(key)))
        with self.ai_lock:
            self.ai_ready.append(mid)
        self.poll_now = True
        return True

    def ai_test(self):
        text = self.ai.test()
        if not self.cfg.get("ai_enabled"):
            text += (" НО проверка писем ВЫКЛЮЧЕНА: поставьте галочку «Включить проверку писем ИИ» и нажмите "
                     "«Сохранить настройки» — иначе письма в ИИ не отправляются.")
        elif self._ai_paused():
            text += " ИИ был на паузе после сбоев — пауза снята, следующие письма уйдут в ИИ."
            self.ai_fail.update(n=0, until=0)
        else:
            text += " Проверка писем включена: новые письма будут уходить в ИИ."
        return text

    def ai_sent(self):
        """Что уходило в ИИ (последние письма, после скрытия личных данных) — для Настроек."""
        return list(self.ai_log)[::-1]

    def ai_suggestions(self):
        out = self.store.suggestions()
        for s in out:
            s["category_name"] = self.cat_name(s["category"])
        return out

    def ai_suggestion(self, sid, action):
        """«Добавить» — слово дописывается в словарь направления; «Отклонить» — больше не предлагается."""
        s = self.store.suggestion(sid)
        if not s or s.get("state"):
            return False
        if action == "add":
            cats = self.cfg.get("categories") or []
            for c in cats:
                if c.get("key") == s["category"] and s["word"] not in (c.get("words") or []):
                    c.setdefault("words", []).append(s["word"])
            self.cfg = config.save({"categories": cats})
            self.store.suggest_set(sid, "added")
            self._log("Слово «%s» добавлено в словарь направления «%s» (предложено ИИ)" % (s["word"], self.cat_name(s["category"])))
        else:
            self.store.suggest_set(sid, "rejected")
        return True

    # ---------- сторож зависшего опроса ----------
    STALL_WARN, STALL_RESTART = 180, 300          # секунд без признаков жизни: сигнал / перезапуск программы

    def stalled_for(self, now=None):
        """Сколько секунд цикл опроса не подаёт признаков жизни (Outlook завис, ждёт ответа в окне-вопросе): вызов
        Outlook не возвращается, а вместе с опросом стоят проверка угроз и напоминания. 0 — всё в порядке.
        Долгое, но идущее чтение большой папки зависанием не считается (источник отмечает каждое прочитанное письмо)."""
        if self.cfg.get("mode") != "outlook" or not self.loop_running or self.stop:
            return 0
        now = now or time.time()
        last = max(self.heartbeat, getattr(self.source, "progress", 0) or 0)
        return max(0, now - last)

    def watch_stall(self, now=None):
        """Вызывается сторожем раз в 15 с. Возвращает True, когда программу пора перезапустить."""
        now = now or time.time()
        lag = self.stalled_for(now)
        if lag < self.STALL_WARN:
            return False
        if now - self.stall_warned >= 120:
            self.stall_warned = now
            self._log("ВНИМАНИЕ: почта не проверяется %d мин — Outlook не отвечает (проверьте, не открыт ли в Outlook "
                      "вопрос или окно с ошибкой)" % (lag // 60))
            notify.toast("Почта ресепшн: почта НЕ проверяется", "Outlook не отвечает. Проверьте, нет ли в Outlook окна с вопросом.")
            notify.overdue()
        if lag >= self.STALL_RESTART:
            self._log("Опрос почты завис на %d мин — программа перезапускается" % (lag // 60))
            return True
        return False

    # ---------- состояние для экрана ----------
    def _pub(self, m, conv=None, brief=False):
        keys = ("id", "sender", "sender_name", "subject", "body_preview", "received_at", "answered_at",
                "threat_words", "threat_ack_at", "threat_level", "shift_key", "lang", "category", "priority", "auto_replied_at", "attachments",
                "forwarded_to", "forwarded_at", "junk", "no_reply_reason", "ignored", "client_email", "client_name",
                "reply_self", "self_reply_at", "self_reply_to", "unignored_at", "no_reply_at",
                "ai_state", "ai_category", "ai_conf", "ai_threat", "ai_note", "ai_threat_note", "rule_category", "cat_conflict")
        d = {k: m.get(k) for k in keys}
        if m.get("cat_conflict"):
            d["cat_conflict_name"] = self.cat_name(m.get("cat_conflict"))
        d["can_open"] = bool(m.get("entry_id")) and self.cfg.get("mode") == "outlook"
        d["sla"] = self.sla_for(m.get("priority"))
        d["category_name"] = self.cat_name(m.get("category"))
        d["thread_count"] = (conv or {}).get(m.get("conv_id") or "", 1)
        rd = _p(m.get("received_at"))
        base = shifts.sla_start(rd, self.cfg) if rd else None
        d["sla_from"] = _iso(base) if base else m.get("received_at")
        d["night"] = bool(rd and base and base != rd)
        if brief:                      # списки под очередью показывают тему, текст письма не нужен (экран обновляется раз в 2 с)
            d.pop("body_preview", None)
        return d

    def outlook_diag(self):
        """Диагностика подключения к Outlook (для кнопки в настройках)."""
        try:
            from mail_outlook import OutlookSource
            return OutlookSource(self.cfg).diagnose()
        except Exception as e:
            return "Диагностика не выполнена: " + str(e)[:300]

    def test_telegram(self):
        failed = telegram.send_message(self.cfg.get("telegram_bot_token"), self.cfg.get("telegram_chat_id"),
                                       "✅ Проверка связи: почта ресепшн TCM подключена к группе. Сюда будут приходить сигналы об угрозах.")
        if failed:                 # кому-то не дошло — проверка не должна показывать «успех»
            raise RuntimeError("сообщение дошло не всем получателям. Не получили: "
                               + "; ".join("%s: %s" % kv for kv in failed.items()))
        return True

    def state(self):
        now = datetime.now()
        cl = self._current_shift(now)
        # окно счёта: от закрытия предыдущей смены (кнопкой) до закрытия этой; часы не участвуют
        if cl:
            ws_dt = cl["win_start"]
        else:
            ws_dt = _p(self.store.session_prev_close() or "") or datetime(now.year, now.month, now.day)
        ws, we = _iso(ws_dt), _iso(now + timedelta(days=1))
        received = self.store.incoming_between(ws, we)
        answered = self.store.answered_between(ws, we)
        queue = self.store.queue(bool(self.cfg.get("forward_removes_from_queue", True)))
        forwarded = self.store.forwarded_between(ws, we)
        hidden = self.store.hidden_between(ws, we)
        closed = self.store.no_reply_between(ws, we)   # сняты без ответа: угроза передана в СБ, удалено из Outlook
        conv = self.store.conv_counts()
        # все письма смены для пилюль: пришедшие за смену + перешедшие (ждут или отвечены в эту смену) — их число на пилюле
        letters = {}
        for m in received + queue + answered:
            letters.setdefault(m["id"], m)
        letters = sorted(letters.values(), key=lambda m: m.get("received_at") or "", reverse=True)
        # разбивка по направлениям за смену
        cats = []
        for c in self.cfg.get("categories") or []:
            k = c.get("key")
            rc = [m for m in received if (m.get("category") or "general") == k]
            qk = [m for m in queue if (m.get("category") or "general") == k]
            ak = [m for m in answered if (m.get("category") or "general") == k]      # отвечено в эту смену
            # перешло с прошлых смен: ещё открытые + отвеченные уже в эту смену
            carried_k = sum(1 for m in qk if m.get("received_at", "") < ws) + sum(1 for m in ak if m.get("received_at", "") < ws)
            cats.append({"key": k, "name": c.get("name"), "received": len(rc), "carried": carried_k,
                         "answered": len(ak),
                         "forwarded": sum(1 for m in forwarded if (m.get("category") or "general") == k),
                         "open": len(qk)})
        overdue = 0
        for m in queue:
            rd = _p(m["received_at"]) or now
            rd = shifts.sla_start(rd, self.cfg)
            if (now - rd).total_seconds() >= self.sla_for(m.get("priority")) * 60:
                overdue += 1
        st, msg = self.status
        if self.cfg.get("mode") == "outlook":            # опрос почты завис (Outlook ждёт ответа в окне-вопросе и т.п.)
            ref = self.last_poll or self.started
            lag = (now - ref).total_seconds()
            if lag > max(180, 3 * int(self.cfg.get("poll_seconds") or 20)):
                st, msg = "error", ("Почта не проверялась %d мин — проверьте, не открыт ли в Outlook вопрос или окно "
                                    "с ошибкой; если не помогло — перезапустите программу" % (lag // 60))
        pn = (self.cfg.get("priority") or {}).get("names") or {}
        return {
            "now": _iso(now), "version": VERSION, "mode": self.cfg.get("mode"), "status": st, "status_text": msg,
            "last_poll": _iso(self.last_poll) if self.last_poll else None,
            "next_shift": shifts.shift_name_at(now, self.cfg)[0],
            "closable": {"key": cl["key"], "name": cl["name"], "label": cl["label"], "opened": cl["opened_label"]} if cl else None,
            "counts": {"queue": len(queue), "received": len(received), "answered": len(answered), "overdue": overdue,
                       "urgent": sum(1 for m in queue if m.get("priority") == 1),
                       "forwarded": len(forwarded),
                       "carried": sum(1 for m in queue if m.get("received_at", "") < ws)
                                  + sum(1 for m in answered if m.get("received_at", "") < ws),
                       "auto_replied": sum(1 for m in received if m.get("auto_replied_at")),
                       "hidden": len(hidden), "closed": len(closed)},
            "categories": cats,
            "queue": [dict(self._pub(m, conv), carried=(cl is not None and (_p(m["received_at"]) or now) < cl["win_start"])) for m in queue],
            "answered": [self._pub(m, conv, True) for m in answered],          # без обрезки: каждое письмо смены видно
            "forwarded": [self._pub(m, conv, True) for m in forwarded],
            "letters": [self._pub(m, conv, True) for m in letters],
            "hidden": [self._pub(m, conv, True) for m in hidden],
            "closed": [self._pub(m, conv, True) for m in closed],
            "threats": [self._pub(m) for m in self.store.threats_pending()],
            "session": self._session_pub(),
            "ai": self.ai_status(),
            "staff": list(self.cfg.get("staff") or []) or (["Рахимова Дильноза", "Каримов Азиз", "Юсупова Малика", "Алимов Сардор"] if self.cfg.get("mode") == "demo" else []),
            "cfg": {"security_phone": self.cfg.get("security_phone") or "",
                    "manager_email": self.cfg.get("manager_email") or "",
                    "auto_reply": bool(self.cfg.get("auto_reply_enabled")),
                    "priority_names": {"1": pn.get("1", "Срочно"), "2": pn.get("2", "Обычно"), "3": pn.get("3", "Низкий")}},
        }

    def _current_shift(self, now=None):
        """Смена, по которой идёт учёт: от кнопки «Открыть смену» до кнопки «Закрыть смену».
        Нет открытой смены — None (по часам смены не открываются и не закрываются)."""
        ses = self.store.session_current()
        if not ses:
            return None
        return shifts.session_shift(ses, self.store.session_prev_close(ses.get("id")), self.cfg, now)

    def _session_pub(self):
        ses = self.store.session_current()
        if not ses:
            return None
        return {"opened_at": ses.get("opened_at"), "staff": ses.get("staff") or [], "shift_key": ses.get("shift_key")}

    def open_shift(self, staff):
        staff = [str(x).strip() for x in (staff or []) if str(x).strip()]
        if not staff:
            return {"error": "Выберите хотя бы одного сотрудника"}
        with self.shift_lock:
            if self.store.session_current():
                return {"error": "Смена уже открыта. Сначала закройте её."}
            self.store.session_open("", staff, self.cfg.get("mode"))
        self._log("Смена открыта: " + ", ".join(staff))
        return {"ok": True}

    # ---------- действия ----------
    def open_item(self, mid, view=False):
        """«Ответить в Outlook». view=True — только показать письмо (строка в списке отвеченных/скрытых)."""
        if self.pinned_hwnd:                      # красный экран закреплён — дать открыть письмо в Outlook
            self.pin_pause_until = time.time() + 90
            self._unpin()
        m = self.store.get(mid)
        if not m or not m.get("entry_id"):
            return False
        self.boost_until = time.time() + 600
        try:
            if (not view and m.get("reply_self") and m.get("client_email") and not m.get("answered_at")
                    and hasattr(self.source, "open_reply")):
                # заявка с сайта: «Ответить» в Outlook ушло бы нам самим — открываем ответ, где в «Кому» адрес клиента
                self._log("Ответ на заявку с сайта «%s»: открыто окно ответа на %s" % (m.get("subject"), m["client_email"]))
                return bool(self.source.open_reply(m["entry_id"], m["client_email"]))
            return bool(self.source.open_item(m["entry_id"]))
        except Exception as e:
            self._log("Не удалось открыть письмо: " + str(e)[:200])
            return False

    def ack_threat(self, mid):
        self.store.ack_threat(mid)
        # письмо-угроза не требует ответа по почте: убираем из очереди, в отчёте останется в разделе угроз
        self.store.mark_no_reply(mid, "Угроза: передано в службу безопасности")
        self._log("Сигнал угрозы подтверждён сотрудником: " + str(mid))
        if not self.store.threats_pending():
            notify.siren_stop()
        return True

    def threat_escalate(self, mid):
        """Жёлтый -> красный: сотрудник прочитал письмо и подтвердил угрозу."""
        self.store.set_threat_level(mid, 2)
        self.threat_opened.discard(mid)
        self._log("Сотрудник поднял тревогу по письму: " + str(mid))
        return True

    def threat_clear(self, mid):
        """Жёлтый: сотрудник проверил, угрозы нет. Письмо остаётся в очереди на ответ."""
        self.store.ack_threat(mid)
        self.after_clear.append(mid)
        self.poll_now = True
        self._log("Возможная угроза проверена, угрозы нет: " + str(mid))
        if not self.store.threats_pending():
            notify.siren_stop()
        return True

    def no_reply(self, mid, reason):
        self.store.mark_no_reply(mid, reason or "не требует ответа")
        return True

    def undo_no_reply(self, mid):
        self.store.undo_no_reply(mid)
        return True

    def unignore(self, mid):
        """«Нужен ответ» в списке «Не попали в очередь»: письмо встаёт в очередь (автоответ и пересылка не уходят)."""
        rec = self.store.get(mid)
        ok = self.store.unignore(mid)
        if ok and rec:
            self._log("Письмо «%s» (%s) поставлено в очередь вручную (было: %s)" % (rec.get("subject"), rec.get("sender"), rec.get("ignored")))
        return ok

    def close_shift(self, by):
        # закрытие ждёт свежий опрос почты до 30 с: второе нажатие (другая вкладка) дало бы второй отчёт руководителю
        if not self.shift_lock.acquire(blocking=False):
            return {"error": "Смена уже закрывается — подождите несколько секунд"}
        try:
            return self._close_shift(by)
        finally:
            self.shift_lock.release()

    def _close_shift(self, by):
        s = self._current_shift()
        if not s:
            return {"error": "Нет открытой смены для закрытия"}
        ses = self.store.session_current()
        who = ", ".join(ses.get("staff") or []) if ses and ses.get("staff") else (by or "сотрудник ресепшн")
        if self.loop_running:
            t0 = datetime.now()
            self.poll_now = True
            for _ in range(15):        # до 30 с ждём свежий опрос почты
                if self.last_poll and self.last_poll >= t0:
                    break
                time.sleep(2)
        now = datetime.now()
        r = shifts.close_shift(s, self.store, self.cfg, self.source, self.reports_dir, who, now)
        self.store.session_close(who, _iso(now))
        self._log("Смена закрыта вручную (%s): %s -> %s" % (who, s["key"], r["file"]))
        r["shift"] = s["label"]
        return r

    def preview_report(self):
        s = self._current_shift()
        if not s:
            return "<p>Нет открытой смены</p>"
        stats = shifts.shift_stats(s, self.store, self.cfg, upto=datetime.now())
        import base64, donut
        rate = (stats["rate"] / 100.0) if stats.get("rate") is not None else None
        src = "data:image/png;base64," + base64.b64encode(donut.render(rate)).decode("ascii")
        return shifts.build_report(s, stats, self.cfg, "", datetime.now(), donut_src=src)[2]

    def reports(self):
        out = []
        try:
            for f in sorted(os.listdir(self.reports_dir), reverse=True):
                if f.endswith(".html") and not f.startswith("demo-"):
                    out.append(f)
        except Exception:
            pass
        return out

    # ---------- демо ----------
    def demo(self, action):
        if self.cfg.get("mode") != "demo":
            return False
        if action == "new":
            self.source.simulate_new()
        elif action == "threat":
            self.source.simulate_threat()
        elif action == "suspicious":
            self.source.simulate_suspicious()
        elif action == "form":
            self.source.simulate_form()
        elif action == "self_reply":             # для проверки: «Ответить» на заявку ушло на наш же ящик
            q = [m for m in self.store.queue() if m.get("reply_self")]
            if q:
                self.source.simulate_self_reply(q[0])
        elif action == "forward_demo":
            # включить демо-адрес для маркетинга и прислать письмо этого направления
            cats = self.cfg.get("categories") or []
            for c in cats:
                if c.get("key") == "marketing" and not c.get("forward_to"):
                    c["forward_to"] = DEMO_FORWARD
            self.apply_settings({"categories": cats})
            self.source.simulate_marketing()
        elif action == "reply":
            q = self.store.queue()
            if q:
                self.source.simulate_reply(q[0])
        elif action == "reset":
            self.store.clear_all()
            self.threat_opened.clear()
            self.last_alert.clear()
            self._make_source()
        self.poll_now = True
        return True
