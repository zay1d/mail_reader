# -*- coding: utf-8 -*-
"""Ядро: опрос почты, сопоставление ответов, категории, приоритеты, автоответ,
угрозы, напоминания, смены."""
import os, threading, time, traceback, webbrowser, html
from datetime import datetime, timedelta

import config, notify, threats, matcher, shifts, classify, telegram, autostart, ai
from store import Store

VERSION = "1.35"
# метки нашего письма-сигнала в СБ; письмо с [0] в теме вернулось в ящик ресепшн (автоответ, группа, пересылка) — без тревоги
SB_MARKS = ("УГРОЗА: письмо на почту ресепшн", "Сработал сигнал угрозы в почте ресепшн")

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
        self.store = Store(os.path.join(base, "data", "mail.db"))
        self.reports_dir = os.path.join(base, "reports")
        self.lock = threading.RLock()
        self.shift_lock = threading.Lock()   # закрытие/открытие смены: две вкладки экрана не отправят отчёт дважды
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
        self.after_clear = []
        self.action_next = {}         # id письма -> когда снова пробовать автоответ/пересылку, если не удалось         # письма, по которым сняли «возможную угрозу»: догнать автоответ и пересылку
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
        self.url = "http://127.0.0.1:%d/" % int(self.cfg.get("port") or 8791)
        self.log = []
        self.ai = None
        self._reload_patterns()
        self._make_source()
        self._apply_count_from()
        self._log("Почта ресепшн, версия " + VERSION)
        self._first_outlook_start()
        self._autostart()
        if self.cfg.get("_config_error"):
            self._log("ВНИМАНИЕ: " + self.cfg["_config_error"])
            notify.toast("Почта ресепшн: настройки слетели", "Проверьте Настройки — config.json был повреждён")

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
        old, self.ai = self.ai, ai.AiClassifier(self.cfg, self._log)
        if old:
            old.close()
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
        values = dict(values or {})
        values.pop("ai_api_key_set", None)
        # ключ API в Настройках не показывается: пустое поле = оставить сохранённый, «Удалить ключ» = стереть
        if values.pop("ai_api_key_clear", False):
            values["ai_api_key"] = ""
        elif not (values.get("ai_api_key") or "").strip():
            values.pop("ai_api_key", None)
        else:
            values["ai_api_key"] = values["ai_api_key"].strip()
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
        ai_jobs = self._ai_start(incoming)
        ai_deadline = time.time() + self.AI_WAIT_SECONDS
        touched = []                  # уже известные письма: «видели сейчас» — одной записью в базу за опрос
        for m in incoming:
            try:   # одно «битое» письмо не должно останавливать разбор остальных (и их автоответ/пересылку)
                if self.store.has(m["id"]):
                    touched.append(m)
                    if m["id"] not in self.backfilled:      # письма, пришедшие до v1.30: дописать «Отвечать на» и адрес клиента
                        self.backfilled.add(m["id"])
                        rec = self.store.get(m["id"])
                        if rec and not rec.get("reply_to") and m.get("reply_to"):
                            self._client_info(m)
                            self.store.set_client(m)
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
                if level and (ign == "своё письмо" or SB_MARKS[0].lower() in (m.get("subject") or "").lower()):
                    # наш же сигнал в СБ вернулся (автоответ «нет на месте», группа СБ с contact@, недоставка) или письмо
                    # самого ящика — тревогу НЕ поднимаем, иначе каждый сигнал порождал бы новый (петля)
                    if level == 2:
                        self._log("Письмо «%s» — возврат нашего сигнала в СБ или своё письмо, тревога не поднимается" % (m.get("subject") or ""))
                    level, found = 0, []
                if level == 2 and ign:
                    self._log("Явная угроза от отправителя из списка «не считать входящими» (%s) — тревога всё равно" % ign)
                    ign = ""
                if level == 1 and ign:
                    level, found = 0, []    # «возможная угроза» в рассылке: экрана нет — и в отчёте её быть не должно
                # явная угроза — без ожидания ИИ: тревога не должна ждать ответа внешнего сервиса
                air = self._ai_result(ai_jobs.get(m["id"]), ai_deadline) if not ign and level != 2 else None
                if air and air["threat"] and not level:
                    # ИИ увидел угрозу, которую не поймали ключевые слова: жёлтый экран, решает сотрудник (красный — только по словам)
                    level, found = 1, ["ИИ: " + (air["threat_reason"] or "возможная угроза")]
                lang = (air and air["lang"]) or classify.detect_lang(m.get("body") or "", m.get("subject") or "")   # язык самого человека, не подписи/цитаты
                cat = classify.categorize(m.get("subject"), m.get("body"), self.cfg.get("categories"), level == 2)
                if air and air["category"] and level != 2:
                    cat = air["category"]
                pri = 1 if level else classify.prioritize(m.get("subject"), m.get("body"), m.get("sender"),
                                                          bool(m.get("importance_high")), self.cfg.get("priority") or {}, cat)
                if air and not level:
                    pri = ai.merge_priority(air["priority"], pri, bool(m.get("importance_high")), m.get("sender"),
                                            self.cfg.get("priority") or {}, cat)
                rd = _p(m.get("received_at")) or datetime.now()
                key = shifts.shift_key_for(rd, self.cfg)
                if self.store.upsert_in(m, ign, found, key, lang, cat, pri, level):
                    if air:
                        self.store.set_ai_summary(m["id"], air["summary"])
                        self._log("ИИ: «%s» → %s, %s, %s%s" % (m.get("subject"), self.cat_name(cat),
                                  ((self.cfg.get("priority") or {}).get("names") or {}).get(str(pri), pri), lang,
                                  (" | " + air["summary"]) if air["summary"] else ""))
                    if not ign:
                        new_letters.append(m)
                    elif not self.first_poll:
                        self._log("Письмо «%s» от %s не показывается: %s" % (m.get("subject"), m.get("sender"), ign))
                    if level:
                        self._log("%s: %s | %s | %s" % ("УГРОЗА" if level == 2 else "ВОЗМОЖНАЯ УГРОЗА",
                                                        m.get("sender"), m.get("subject"), ", ".join(found)))
            except Exception:
                self._log("Ошибка разбора письма «%s» от %s: %s" % (m.get("subject"), m.get("sender"), traceback.format_exc()[-300:]))
        self.store.touch_many(touched)
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
                    bad = self._self_target(o, cands, only_form=not to_self)
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
        self._match_late(new_letters, own)
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
        fresh = [m for m in new_letters if (_p(m.get("received_at")) or datetime.now()) >= backlog]
        if len(fresh) < len(new_letters):
            self._log("Старых писем без автоответа и пересылки (старше %s ч): %d" % (self.cfg.get("auto_reply_backlog_hours") or 16, len(new_letters) - len(fresh)))
        cleared, self.after_clear = self.after_clear, []
        for mid in cleared:            # «Угрозы нет» по жёлтому сигналу: письмо обрабатывается как обычное
            rec = self.store.get(mid)
            if rec and (_p(rec.get("received_at")) or datetime.now()) >= backlog:
                fresh.append(rec)
        fresh += rescued               # из «Нежелательной» во «Входящие» — обрабатываются при любом возрасте
        # + письма, у которых автоответ или пересылка не удались раньше (Outlook был занят, окно-вопрос, нет сети):
        # повтор с паузой 1, 2, 4… мин, до 8 попыток, пока письмо не старше «auto_reply_backlog_hours»
        work = {m["id"]: m for m in fresh}
        for r in self.store.pending_actions(_iso(backlog)):
            if time.time() >= self.action_next.get(r["id"], 0):
                work.setdefault(r["id"], r)
        for mid, m in work.items():    # сначала направление и пересылка в отдел, затем автоответ (уходит всем)
            rec = self.store.get(mid)
            if not rec or self._threat_blocks(rec):
                continue               # угроза: ни автоответа, ни пересылки; «Угрозы нет» вернёт письмо сюда
            try:
                ok_f = self._auto_forward(m)
                ok_r = self._auto_reply(m)
                ok = ok_f and ok_r
            except Exception as e:
                ok = False
                self._log("Автоответ/пересылка: ошибка " + str(e)[:200])
            tries = self.store.set_actions(mid, ok)
            if ok:
                self.action_next.pop(mid, None)
            else:
                self.action_next[mid] = time.time() + 60 * min(30, 2 ** max(0, tries - 1))
        self._mark_vanished(since)
        self.first_poll = False
        self.last_poll = datetime.now()
        self.status = src.status()

    def _match_late(self, new_letters, own):
        """Ответ мог попасть в «Отправленные» раньше, чем программа прочитала само письмо (сбой чтения, письмо
        поставили в очередь вручную). Такой ответ уже записан в базу и сам письмо не закроет — без этого письму
        ушёл бы автоответ после ответа сотрудника, а карточка висела бы в очереди вечно."""
        if not new_letters:
            return
        ids = {m["id"] for m in new_letters}
        cands = [c for c in self.store.unanswered_candidates() if c["id"] in ids]
        if not cands:
            return
        lo = min(c.get("received_at") or "" for c in cands)
        for o in self.store.outgoing_between(lo, _iso(datetime.now() + timedelta(days=1))):
            if matcher.is_forward(o.get("subject") or ""):
                continue
            got = matcher.match_reply_all(dict(o, sent_at=o.get("received_at")), cands, own)
            for mid in got:
                self.store.mark_answered(mid, o["id"], o.get("received_at"))
            if got:
                cands = [c for c in cands if c["id"] not in got]
                self._log("Ответ найден в «Отправленных» раньше письма: %s -> %s" % (o.get("subject"), ", ".join(got)))
            if not cands:
                break

    AI_MAX_PER_POLL = 40      # защита от расходов: больше писем за один опрос — остальные по ключевым словам

    def _ai_start(self, incoming):
        """Запустить ИИ-сортировку новых писем параллельно (до 4 одновременно), пока идёт разбор остальных.
        Не отправляются в ИИ: уже известные, рассылки, служебные, «Нежелательная почта», из списка «не считать
        входящими» и старые (старше «auto_reply_backlog_hours»: их не пересылают, а платить за них незачем)."""
        jobs = {}
        if not (self.ai and self.ai.enabled):
            return jobs
        old = datetime.now() - timedelta(hours=int(self.cfg.get("auto_reply_backlog_hours") or 16))
        for m in incoming:
            if len(jobs) >= self.AI_MAX_PER_POLL:
                break
            if (m.get("junk") or m.get("bulk") or (m.get("item_class") or 43) != 43 or self.store.has(m["id"])
                    or (_p(m.get("received_at")) or datetime.now()) < old
                    or matcher.is_ignored(m.get("sender"), m.get("sender_name"), m.get("subject"), self.cfg)):
                continue
            f = self.ai.classify_async(m)
            if f is not None:
                jobs[m["id"]] = f
        return jobs

    AI_WAIT_SECONDS = 25      # сколько опрос ждёт ИИ: не успел — письмо по ключевым словам, тревоги и экран не ждут

    @staticmethod
    def _ai_result(f, deadline):
        if f is None:
            return None
        try:
            return f.result(timeout=max(0.0, deadline - time.time()))
        except Exception:
            return None

    def test_ai(self):
        return self.ai.test() if self.ai else (False, "ИИ не настроен")

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
            self.store.mark_no_reply(m["id"], "удалено из Outlook сотрудником")
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
        if not self.cfg.get("auto_reply_enabled"):
            return True
        rec = self.store.get(m["id"])
        if not rec or rec.get("ignored") or self._threat_blocks(rec) or rec.get("auto_replied_at") or rec.get("answered_at"):
            return True
        if rec.get("junk"):
            self._log("Автоответ не отправлен (письмо в «Нежелательной почте»; если оно настоящее — в Outlook «Не нежелательное»): %s"
                      % (rec.get("subject") or ""))
            return True
        if rec.get("reply_self") and not rec.get("client_email"):
            self._log("Автоответ не отправлен: заявка с сайта «%s» — «Ответить» ушло бы на наш ящик, адреса клиента в тексте нет"
                      % (rec.get("subject") or ""))
            return True
        # кому отвечаем: адрес клиента (заявка с сайта, Reply-To) или отправитель
        sender = (rec.get("client_email") or rec.get("sender") or "").lower()
        if not sender or "@" not in sender:
            return True
        own = self._own()
        if sender in own or (rec.get("sender") or "").lower() in own:
            return True  # не отвечаем самому ящику ресепшн
        # коллеги своего домена (ответ отдела «всем» с contact@ в копии) автоответ не получают — решение Олега 18.09
        own_domains = {a.split("@", 1)[1] for a in own}
        if self.cfg.get("auto_reply_skip_own_domain", True) and any(
                "@" in a and a.split("@", 1)[1] in own_domains for a in (sender, (rec.get("sender") or "").lower())):
            self._log("Автоответ не отправлен (%s — свой домен): %s" % (sender, rec.get("subject")))
            return True
        # правило Олега (17.09): автоответ на КАЖДОЕ письмо; исключение — та же тема от того же
        # отправителя (продолжение переписки): автоответ по теме уходит один раз
        key = self._thread_key(rec.get("subject"))
        prev = self.store.auto_replied_to(sender)
        if any(self._thread_key(p.get("subject")) == key for p in prev):
            self._log("Автоответ не отправлен (по теме «%s» %s уже получал автоответ)" % (rec.get("subject"), sender))
            return True
        # защита от переписки двух автоответчиков: наш автоответ в теме («[Ticket #1] Re: Ваше обращение получено: …»)
        # — это ответ чужого автоответчика или тикет-системы; не больше 5 автоответов одному адресу в час и 10 в сутки
        subj_l = (rec.get("subject") or "").lower()
        if any(p.lower() in subj_l for p in self._auto_prefixes()):
            self._log("Автоответ не отправлен (в теме наш автоответ — ответ автоответчика или тикет-системы): %s" % rec.get("subject"))
            return True
        hour_ago = _iso(datetime.now() - timedelta(hours=1))
        day_ago = _iso(datetime.now() - timedelta(days=1))
        if (sum(1 for p in prev if (p.get("auto_replied_at") or "") >= hour_ago) >= 5
                or sum(1 for p in prev if (p.get("auto_replied_at") or "") >= day_ago) >= 10):
            self._log("Автоответ не отправлен (%s: слишком много автоответов за час/сутки — похоже на автоответчик)" % sender)
            return True
        lang = rec.get("lang") or "ru"
        tpls = (self.cfg.get("auto_reply_templates") or {}).get(rec.get("category") or "general") or {}
        tpl = tpls.get(lang) or tpls.get("ru") or ((self.cfg.get("auto_reply_templates") or {}).get("general") or {}).get(lang) or ""
        if not tpl.strip():
            return True
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
            return True
        except Exception as e:
            self._log("Автоответ не отправлен (повтор позже): " + str(e)[:200])
            return False

    def forward_addr(self, category):
        for c in self.cfg.get("categories") or []:
            if c.get("key") == category:
                return (c.get("forward_to") or "").strip()
        return ""

    def _auto_forward(self, m):
        rec = self.store.get(m["id"])
        if not rec or rec.get("ignored") or self._threat_blocks(rec) or rec.get("forwarded_at") or rec.get("answered_at") or rec.get("junk"):
            return True
        own = {x.strip().lower() for x in (self.cfg.get("own_addresses") or []) if "@" in (x or "")}
        sender = (rec.get("sender") or "").lower()
        if self.cfg.get("auto_reply_skip_own_domain", True) and "@" in sender and sender.split("@", 1)[1] in {x.split("@", 1)[1] for x in own}:
            self._log("Пересылка не выполнена (%s — свой домен, письмо остаётся на ресепшн): %s" % (sender, rec.get("subject")))
            return True   # ответ отдела «всем» не гоняем обратно в отдел
        to = self.forward_addr(rec.get("category"))
        if not to:
            self._log("Письмо «%s» — направление «%s», пересылка для него не настроена" % (rec.get("subject"), self.cat_name(rec.get("category"))))
            return True
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
            return True
        except Exception as e:
            self._log("Пересылка не удалась (повтор позже): " + str(e)[:200])
            return False

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
                    try:   # каждому получателю отдельно: не дошло одному — повтор только ему
                        done = st.setdefault("tg_ok", set())
                        sent, errs = telegram.send_each(self.cfg.get("telegram_bot_token"), self.cfg.get("telegram_chat_id"),
                                                        telegram.threat_text(m, self.cfg.get("reception_phone")), skip=done)
                        done.update(sent)
                        if sent:
                            self._log("Тревога отправлена в Telegram: " + ", ".join(sent))
                        if errs:
                            self._log("Telegram: тревога НЕ дошла (повтор через 2 мин): " + "; ".join(errs)[:300])
                        else:
                            st["tg"] = True
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
                self.check_threats()
                if now >= next_minute:
                    next_minute = now + 60
                    self.check_overdue()
                    self._daily_backup()
                    # смены открываются и закрываются ТОЛЬКО сотрудниками вручную (решение Олега 17.09)
            except Exception:
                self._log(traceback.format_exc()[-500:])
            time.sleep(2)

    # ---------- состояние для экрана ----------
    def _pub(self, m, conv=None, brief=False):
        keys = ("id", "sender", "sender_name", "subject", "body_preview", "received_at", "answered_at",
                "threat_words", "threat_ack_at", "threat_level", "shift_key", "lang", "category", "priority", "auto_replied_at", "attachments",
                "forwarded_to", "forwarded_at", "junk", "no_reply_reason", "ignored", "client_email", "client_name",
                "reply_self", "self_reply_at", "self_reply_to", "unignored_at", "no_reply_at", "ai_summary")
        d = {k: m.get(k) for k in keys}
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
        telegram.send_message(self.cfg.get("telegram_bot_token"), self.cfg.get("telegram_chat_id"),
                              "✅ Проверка связи: почта ресепшн TCM подключена к группе. Сюда будут приходить сигналы об угрозах.")
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
            "ai": {"enabled": bool(self.ai and self.ai.enabled), "model": self.ai.model if self.ai else "",
                   "error": (self.ai.last_error if self.ai else "") or "", "classified": self.ai.ok_count if self.ai else 0},
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
        if not self.shift_lock.acquire(timeout=40):
            return {"error": "Смена сейчас закрывается — подождите несколько секунд"}
        try:
            return self._open_shift(staff)
        finally:
            self.shift_lock.release()

    def _open_shift(self, staff):
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
        rec = self.store.get(mid)
        if not rec or (rec.get("threat_level") or 2) != 1:
            return False                  # явную угрозу (красный) так снять нельзя — только «Я сообщил в СБ»
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
        if ok:
            self.store.set_actions(mid, True)     # вручную в очередь: без автоответа и пересылки
            self._match_late([{"id": mid}], self._own())
        if ok and rec:
            self._log("Письмо «%s» (%s) поставлено в очередь вручную (было: %s)" % (rec.get("subject"), rec.get("sender"), rec.get("ignored")))
        return ok

    def close_shift(self, by):
        if not self.shift_lock.acquire(blocking=False):
            return {"error": "Смена уже закрывается (нажали в другом окне) — подождите"}
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
                    c["forward_to"] = "marketing-demo@example.uz"
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
