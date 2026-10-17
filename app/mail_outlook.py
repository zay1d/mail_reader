# -*- coding: utf-8 -*-
"""Чтение почты из классического Outlook (COM).
Ящик ищется в таком порядке: учётная запись с нужным адресом -> хранилище с таким именем ->
общий ящик (делегирование) -> ящик по умолчанию. Работает с Exchange/Microsoft 365, IMAP и POP."""
from datetime import datetime, timedelta

PR_SMTP = "http://schemas.microsoft.com/mapi/proptag/0x39FE001F"
PR_SENDER_SMTP = "http://schemas.microsoft.com/mapi/proptag/0x5D01001F"
PR_MSGID = "http://schemas.microsoft.com/mapi/proptag/0x1035001F"
PR_HEADERS = "http://schemas.microsoft.com/mapi/proptag/0x007D001F"
PR_ATT_HIDDEN = "http://schemas.microsoft.com/mapi/proptag/0x7FFE000B"
PR_ATT_CID = "http://schemas.microsoft.com/mapi/proptag/0x3712001F"
OL_INBOX, OL_SENT, OL_JUNK, OL_MAIL = 6, 5, 23, 43
# кроме писем во «Входящих» бывают приглашения/отмены/ответы на встречи (53–57) и отчёты о недоставке (46):
# их тоже показываем — в списке «Не попали в очередь» (отчёт о недоставке = ответ клиенту не дошёл)
OTHER_CLASSES = {53: "приглашение на встречу (Outlook)", 54: "отмена встречи (Outlook)",
                 55: "ответ на приглашение (Outlook)", 56: "ответ на приглашение (Outlook)",
                 57: "ответ на приглашение (Outlook)", 46: "отчёт о доставке / недоставке письма (Outlook)",
                 49: "запрос задачи (Outlook)", 50: "запрос задачи (Outlook)", 51: "ответ на задачу (Outlook)",
                 52: "ответ на задачу (Outlook)", 104: "приглашение к общему доступу (Outlook)",
                 181: "уведомление о пересылке встречи (Outlook)", 45: "сообщение папки (Outlook)", 26: "встреча календаря (Outlook)"}
IN_CLASSES = None        # во «Входящих» читаем ВСЕ элементы: всё, что не письмо, — в «Не попали в очередь» с причиной
SENT_NAMES = ("отправленные", "sent items", "sent", "отправленные элементы", "jo'natilganlar", "[gmail]/отправленные")

def _dt(v):
    """COM-дата -> naive datetime (локальное время)."""
    try:
        d = datetime(v.year, v.month, v.day, v.hour, v.minute, v.second)
        if d.year < 1990:
            return None
        return d
    except Exception:
        return None

def _iso(d):
    return d.strftime("%Y-%m-%dT%H:%M:%S") if d else ""

def _err(e):
    try:
        return "%s %s" % (e.args[0], e.args[1])
    except Exception:
        return str(e)[:160]

class OutlookSource:
    name = "outlook"

    def __init__(self, cfg):
        self.cfg = cfg
        self.app = None
        self.ns = None
        self.inbox = None
        self.sent = None
        self.junk = None
        self.account = None        # учётная запись Outlook, от имени которой отправляем
        self.how = ""              # как найден ящик (для диагностики)
        self.error = ""
        self.warn = ""
        self._cache_in = {}
        self._cache_out = {}
        self.scan = {}             # как читались папки (для диагностики): путь -> сведения
        self.full_scan = set()     # папки, где Outlook не соблюдает порядок «новые сверху» — читать целиком
        self.last_complete = False # последний опрос прочитал все папки и письма без единой ошибки

    # ---------- поиск ящика ----------
    def _target(self):
        a = (self.cfg.get("mailbox_address") or "").strip().lower()
        if a:
            return a, True
        own = [x.strip().lower() for x in (self.cfg.get("own_addresses") or []) if "@" in (x or "")]
        return (own[0] if own else ""), False

    def _accounts(self):
        out = []
        try:
            for i in range(1, self.ns.Accounts.Count + 1):
                try:
                    out.append(self.ns.Accounts.Item(i))
                except Exception:
                    pass
        except Exception:
            pass
        return out

    def _stores(self):
        out = []
        try:
            for i in range(1, self.ns.Stores.Count + 1):
                try:
                    out.append(self.ns.Stores.Item(i))
                except Exception:
                    pass
        except Exception:
            pass
        return out

    def _find_sent(self, store):
        try:
            return store.GetDefaultFolder(OL_SENT)
        except Exception:
            pass
        try:
            root = store.GetRootFolder()
            for i in range(1, root.Folders.Count + 1):
                f = root.Folders.Item(i)
                if (f.Name or "").strip().lower() in SENT_NAMES:
                    return f
        except Exception:
            pass
        return None

    def _use_store(self, store, how):
        self.inbox = store.GetDefaultFolder(OL_INBOX)
        self.sent = self._find_sent(store)
        try:
            self.junk = store.GetDefaultFolder(OL_JUNK)
        except Exception:
            self.junk = None
        self.how = how

    def connect(self):
        import pythoncom, win32com.client
        pythoncom.CoInitialize()
        step = "запуск Outlook"
        try:
            self.app = win32com.client.Dispatch("Outlook.Application")
            self.ns = self.app.GetNamespace("MAPI")
            self.inbox = self.sent = self.junk = self.account = None
            target, explicit = self._target()
            step = "поиск ящика " + (target or "по умолчанию")
            if target:
                for acc in self._accounts():                       # 1) учётная запись с таким адресом
                    try:
                        if (acc.SmtpAddress or "").strip().lower() == target:
                            self.account = acc
                            self._use_store(acc.DeliveryStore, "учётная запись " + target)
                            break
                    except Exception:
                        continue
                if self.inbox is None:                              # 2) хранилище с таким именем
                    for st in self._stores():
                        try:
                            if target in (st.DisplayName or "").strip().lower():
                                self._use_store(st, "хранилище «%s»" % st.DisplayName)
                                break
                        except Exception:
                            continue
                if self.inbox is None and explicit:                 # 3) общий ящик (делегирование)
                    try:
                        rcp = self.ns.CreateRecipient(target)
                        rcp.Resolve()
                        self.inbox = self.ns.GetSharedDefaultFolder(rcp, OL_INBOX)
                        self.inbox.Items.Count      # Outlook отдаёт папку «лениво»: проверяем, что она реально открывается
                        try:
                            self.sent = self.ns.GetSharedDefaultFolder(rcp, OL_SENT)
                        except Exception:
                            self.sent = None
                        self.how = "общий ящик " + target
                    except Exception as e:
                        names = ", ".join((a.SmtpAddress or a.DisplayName or "?") for a in self._accounts()) or "нет"
                        raise RuntimeError("ящик %s не найден в Outlook. Учётные записи в Outlook: %s. "
                                           "Очистите поле «Адрес ящика ресепшн в Outlook» или впишите один из этих адресов (%s)"
                                           % (target, names, _err(e)))
            if self.inbox is None:                                  # 4) ящик по умолчанию
                step = "ящик по умолчанию"
                self.inbox = self.ns.GetDefaultFolder(OL_INBOX)
                try:
                    self.sent = self.ns.GetDefaultFolder(OL_SENT)
                except Exception:
                    self.sent = self._find_sent(self.inbox.Store)
                try:
                    self.junk = self.inbox.Store.GetDefaultFolder(OL_JUNK)
                except Exception:
                    self.junk = None
                self.how = "ящик по умолчанию"
                if target:
                    try:
                        dflt = self.inbox.Store.DisplayName
                    except Exception:
                        dflt = "?"
                    if target not in (dflt or "").lower():
                        self.how += " «%s» (ящик %s в Outlook не найден)" % (dflt, target)
            self.warn = "" if self.sent is not None else "папка «Отправленные» не найдена — ответы не будут учитываться"
            if "не найден" in self.how:
                self.warn = ("читается «%s», а не %s — проверьте, что ящик ресепшн добавлен в Outlook" % (dflt, target)
                             + ("; " + self.warn if self.warn else ""))
            self.error = ""
        except RuntimeError:
            raise
        except Exception as e:
            raise RuntimeError("шаг «%s»: %s" % (step, _err(e)))

    def status(self):
        if self.error:
            return "error", self.error
        if self.inbox is None:
            return "off", "Outlook не подключён"
        try:
            self.inbox.Name
            bad = getattr(self, "read_problems", 0)
            warn = self.warn
            if bad:   # письмо, которое не прочиталось, на экране не появится — сотрудник должен это видеть
                warn = ("сбоев чтения Outlook: %d, часть писем может не показываться — Настройки → «Проверить подключение к Outlook»" % bad) + ("; " + warn if warn else "")
            return "ok", "Outlook подключён" + ((" · " + warn) if warn else "")
        except Exception:
            return "error", "Потеряна связь с Outlook"

    def diagnose(self):
        """Текстовая диагностика для страницы настроек (адреса ящиков, без содержимого писем)."""
        lines = []
        try:
            self.connect()
            lines.append("Подключение: OK (%s)" % self.how)
        except Exception as e:
            lines.append("Подключение: ОШИБКА — %s" % e)
        try:
            lines.append("Учётные записи Outlook:")
            for a in self._accounts():
                try:
                    lines.append("  • %s (тип %s)" % (a.SmtpAddress or a.DisplayName, {0: "Exchange", 1: "IMAP", 2: "POP3", 3: "HTTP", 4: "EAS", 5: "другое"}.get(int(a.AccountType), a.AccountType)))
                except Exception:
                    lines.append("  • (не удалось прочитать)")
            lines.append("Хранилища: " + ", ".join((s.DisplayName or "?") for s in self._stores()))
        except Exception as e:
            lines.append("Не удалось перечислить учётные записи: %s" % _err(e))
        for title, f in (("Входящие", self.inbox), ("Отправленные", self.sent), ("Спам", self.junk)):
            try:
                lines.append("%s: %s — писем %d" % (title, f.FolderPath, f.Items.Count) if f is not None else "%s: не найдена" % title)
            except Exception as e:
                lines.append("%s: ошибка %s" % (title, _err(e)))
        if self.inbox is not None:
            try:
                inc, out = self.fetch(datetime.now() - timedelta(days=1))
                lines.append("Пробное чтение за сутки: входящих %d, отправленных %d%s" % (len(inc), len(out), (" · " + self.warn) if self.warn else ""))
            except Exception as e:
                lines.append("Пробное чтение: ОШИБКА — %s" % e)
            lines.append("Сейчас на этом компьютере: %s" % datetime.now().strftime("%d.%m.%Y %H:%M"))
            for title, f in (("Входящие", self.inbox), ("Отправленные", self.sent)):
                try:
                    st = self.scan.get(f.FolderPath) if f is not None else None
                except Exception:
                    st = None
                if not st:
                    continue
                cls = ", ".join("%s:%d" % (k, v) for k, v in sorted(st["classes"].items(), key=lambda x: -x[1]))
                lines.append("%s — просмотрено %d, найдено за сутки %d, самое новое письмо %s, первые по порядку: %s; "
                             "режим: %s; ошибок чтения %d; типы (43=письмо): %s%s"
                             % (title, st["seen"], st["found"],
                                st["newest"].strftime("%d.%m.%Y %H:%M") if st["newest"] else "—",
                                ", ".join(st["first"]) or "—",
                                "вся папка (Outlook не соблюдает порядок)" if st["full"] else ("по дате" if st["sorted"] else "без сортировки"),
                                st["errors"], cls or "—", ("; " + st["error"]) if st["error"] else ""))
        return "\n".join(lines)

    # ---------- чтение ----------
    def _sender_smtp(self, item):
        try:
            v = item.PropertyAccessor.GetProperty(PR_SENDER_SMTP)
            if v and "@" in v:
                return v.lower()
        except Exception:
            pass
        try:
            if item.SenderEmailType == "EX":
                u = item.Sender.GetExchangeUser()
                if u and u.PrimarySmtpAddress:
                    return u.PrimarySmtpAddress.lower()
        except Exception:
            pass
        try:
            return (item.SenderEmailAddress or "").lower()
        except Exception:
            return ""

    def _rcpt_smtp(self, r):
        try:
            v = r.PropertyAccessor.GetProperty(PR_SMTP)
            if v and "@" in v:
                return v.lower()
        except Exception:
            pass
        try:
            ae = r.AddressEntry
            if ae and ae.Type == "EX":
                u = ae.GetExchangeUser()
                if u and u.PrimarySmtpAddress:
                    return u.PrimarySmtpAddress.lower()
        except Exception:
            pass
        try:
            return (r.Address or "").lower()
        except Exception:
            return ""

    def _msg_id(self, item):
        try:
            v = item.PropertyAccessor.GetProperty(PR_MSGID)
            if v:
                return "mid:" + v.strip()
        except Exception:
            pass
        return "eid:" + item.EntryID

    def _conv(self, item):
        try:
            return item.ConversationID or ""
        except Exception:
            return ""

    def _attachments(self, item):
        """Настоящие вложения (без встроенных картинок подписи)."""
        out = []
        try:
            n = item.Attachments.Count
        except Exception:
            return out
        for i in range(1, n + 1):
            try:
                a = item.Attachments.Item(i)
                try:
                    if a.PropertyAccessor.GetProperty(PR_ATT_HIDDEN):
                        continue
                except Exception:
                    pass
                name = a.FileName or a.DisplayName or ""
                if not name:
                    continue
                size = int(a.Size or 0)
                if name.lower().endswith((".png", ".gif", ".jpg", ".jpeg")) and size < 60000:
                    continue  # картинки подписи/логотипы
                out.append({"name": name, "size": size})
            except Exception:
                pass
        return out

    def _is_bulk(self, item):
        """Рассылка: заголовки List-Unsubscribe / Precedence: bulk / Auto-Submitted."""
        try:
            h = (item.PropertyAccessor.GetProperty(PR_HEADERS) or "")[:6000].lower()
        except Exception:
            return ""
        # причина — словами для списка «Не попали в очередь» на экране
        for mark, why in (("list-unsubscribe:", "есть ссылка «отписаться»"), ("precedence: bulk", "Precedence: bulk"),
                          ("precedence: list", "Precedence: list"), ("auto-submitted: auto-", "Auto-Submitted"),
                          ("x-auto-response-suppress:", "X-Auto-Response-Suppress")):
            if mark in h:
                return why
        return ""

    def _iter(self, folder, field, since, classes=(OL_MAIL,)):
        """Письма папки новее since. Сортируем «новые сверху» и останавливаемся после 40 старых писем подряд.
        Если Outlook порядок не соблюдает (так бывает: вид папки, режим работы с сервером) — читаем папку
        целиком, до 5000 элементов, и запоминаем это для следующих опросов."""
        try:
            path = folder.FolderPath
        except Exception:
            path = "?"
        st = {"sorted": True, "full": path in self.full_scan, "seen": 0, "errors": 0, "classes": {},
              "first": [], "newest": None, "found": 0, "error": ""}
        self.scan[path] = st
        items = folder.Items
        if not st["full"]:
            try:
                items.Sort("[" + field + "]", True)
            except Exception as e:
                st["sorted"] = False
                st["error"] = "сортировка: " + _err(e)
        full = st["full"] or not st["sorted"]
        try:
            it = items.GetFirst()
        except Exception as e:
            st["error"] = "GetFirst: " + _err(e)
            return
        n = old_run = 0
        prev = None
        order_ok = True
        limit = 5000 if full else 2000
        while it is not None and n < limit:
            n += 1
            try:
                cls = it.Class
                st["classes"][cls] = st["classes"].get(cls, 0) + 1
                if classes is None or cls in classes:
                    if cls == OL_MAIL:
                        d = _dt(getattr(it, field))
                    else:                  # приглашение, отчёт о недоставке, задача…: у отчёта нет ReceivedTime
                        try:
                            d = _dt(getattr(it, field))
                        except Exception:
                            d = _dt(it.CreationTime)
                    if len(st["first"]) < 3:
                        st["first"].append(d.strftime("%d.%m.%Y %H:%M") if d else "без даты")
                    if d is not None:
                        if st["newest"] is None or d > st["newest"]:
                            st["newest"] = d
                        if cls == OL_MAIL:         # порядок проверяем только по письмам: у отчёта о недоставке дата другая
                            if prev is not None and d > prev + timedelta(seconds=1):
                                order_ok = False   # более новое письмо ниже старого — порядок не соблюдается
                            prev = d
                    if d is not None and d < since:
                        old_run += 1
                        if not full and old_run >= 40:
                            if order_ok:
                                break
                            # порядок нарушен — дальше читаем всё, и в следующие опросы тоже
                            full = True
                            limit = 5000
                            self.full_scan.add(path)
                            st["full"] = True
                    else:
                        old_run = 0
                        st["found"] += 1
                        yield it, d
            except Exception:
                st["errors"] += 1
            try:
                it = items.GetNext()
            except Exception:
                break
        st["seen"] = n

    def _subfolders(self, folder, depth=3):
        """Папка и её подпапки (правила Outlook могут раскладывать почту по подпапкам)."""
        out = [folder]
        if depth <= 0:
            return out
        try:
            for i in range(1, folder.Folders.Count + 1):
                out += self._subfolders(folder.Folders.Item(i), depth - 1)
        except Exception:
            pass
        return out

    def _reply_to(self, item):
        """Адреса «Отвечать на» (Reply-To). У писем формы сайта там стоит наш же ящик — «Ответить» ушло бы нам самим."""
        out = []
        try:
            rr = item.ReplyRecipients
            for i in range(1, rr.Count + 1):
                a = self._rcpt_smtp(rr.Item(i))
                if a and "@" in a and a not in out:
                    out.append(a)
        except Exception:
            pass
        if not out:                                    # запасной путь: строка Reply-To в заголовках письма
            try:
                import re
                h = item.PropertyAccessor.GetProperty(PR_HEADERS) or ""
                mt = re.search(r"^reply-to:(.*(?:\r?\n[ \t].*)*)", h[:20000], re.I | re.M)
                if mt:
                    for a in re.findall(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)+", mt.group(1)):
                        a = a.lower()
                        if a not in out:
                            out.append(a)
            except Exception:
                pass
        return out

    def _parse_in(self, it, d, junk=False):
        try:
            body = it.Body or ""
        except Exception:
            body = ""
        try:
            imp = int(it.Importance) == 2
        except Exception:
            imp = False
        def safe(attr):
            try:
                return getattr(it, attr) or ""
            except Exception:
                return ""
        try:
            cls = int(it.Class)
        except Exception:
            cls = OL_MAIL
        return {
            "id": self._msg_id(it), "entry_id": it.EntryID, "conv_id": self._conv(it),
            "sender": self._sender_smtp(it), "sender_name": safe("SenderName"),
            "subject": safe("Subject"), "received_at": _iso(d), "body": body[:20000],
            "importance_high": imp, "bulk": self._is_bulk(it), "attachments": self._attachments(it),
            "junk": junk, "reply_to": self._reply_to(it), "item_class": cls,
        }

    def fetch(self, since):
        if self.inbox is None:
            self.connect()
        incoming, outgoing = [], []
        seen_in, seen_out = set(), set()
        self.scan = {}
        problems = 0                                   # сбои чтения: папки или отдельные письма
        try:
            folders = [(f, False) for f in self._subfolders(self.inbox)]
            if self.junk is not None:
                folders.append((self.junk, True))     # «Нежелательная почта» — тоже в очередь, с пометкой
            for idx, (folder, is_junk) in enumerate(folders):
                try:
                    for it, d in self._iter(folder, "ReceivedTime", since, IN_CLASSES):
                        if d is None:
                            continue
                        try:                           # одно «битое» письмо не должно останавливать чтение папки
                            eid = it.EntryID
                            seen_in.add(eid)
                            m = self._cache_in.get(eid)
                            if m is None:
                                m = self._parse_in(it, d, is_junk)
                                self._cache_in[eid] = m
                            incoming.append(m)
                        except Exception:
                            problems += 1
                except Exception as e:
                    if idx == 0:
                        raise RuntimeError("шаг «чтение Входящих»: " + _err(e))
                    problems += 1                      # подпапка или спам не читаются — основная почта важнее
            if self.sent is not None:
                try:
                    for it, d in self._iter(self.sent, "SentOn", since):
                        if d is None:
                            continue
                        try:
                            eid = it.EntryID
                            seen_out.add(eid)
                            o = self._cache_out.get(eid)
                            if o is None:
                                rcpts = []
                                try:
                                    for i in range(1, it.Recipients.Count + 1):
                                        a = self._rcpt_smtp(it.Recipients.Item(i))
                                        if a:
                                            rcpts.append(a)
                                except Exception:
                                    pass
                                try:
                                    subj = it.Subject or ""
                                except Exception:
                                    subj = ""
                                o = {"id": self._msg_id(it), "entry_id": eid, "conv_id": self._conv(it),
                                     "recipients": rcpts, "subject": subj, "sent_at": _iso(d)}
                                self._cache_out[eid] = o
                            outgoing.append(o)
                        except Exception:
                            problems += 1
                except Exception as e:
                    self.warn = "не читается папка «Отправленные»: " + _err(e)
                    problems += 1
            problems += sum(st.get("errors", 0) for st in self.scan.values())
            self.last_complete = problems == 0
            self.read_problems = problems
            for k in [k for k in self._cache_in if k not in seen_in]:
                del self._cache_in[k]
            for k in [k for k in self._cache_out if k not in seen_out]:
                del self._cache_out[k]
            self.error = ""
        except Exception as e:
            self.error = "Ошибка чтения Outlook: " + str(e)[:220]
            self.inbox = None
            self.last_complete = False
            raise
        return incoming, outgoing

    # ---------- действия ----------
    def _session(self):
        import pythoncom, win32com.client
        pythoncom.CoInitialize()
        app = win32com.client.Dispatch("Outlook.Application")
        return app, app.GetNamespace("MAPI")

    def _account_for(self, ns):
        """Учётная запись для отправки (в потоке вызова — объекты COM нельзя делить между потоками)."""
        target, _ = self._target()
        if not target:
            return None
        try:
            for i in range(1, ns.Accounts.Count + 1):
                acc = ns.Accounts.Item(i)
                if (acc.SmtpAddress or "").strip().lower() == target:
                    return acc
        except Exception:
            pass
        return None

    def _from(self, ns, mail):
        acc = self._account_for(ns)
        if acc is not None:
            try:
                mail._oleobj_.Invoke(64209, 0, 8, 0, acc)      # SendUsingAccount = acc
                return
            except Exception:
                pass
        addr = (self.cfg.get("mailbox_address") or "").strip()
        if addr:
            try:
                mail.SentOnBehalfOfName = addr
            except Exception:
                pass

    def open_item(self, entry_id):
        """Показать письмо в ГЛАВНОМ окне Outlook: нужная папка, письмо выделено — сотрудник
        отвечает в самом Outlook. Отдельное окно письма — только если главное окно недоступно."""
        app, ns = self._session()
        item = ns.GetItemFromID(entry_id)
        try:
            folder = item.Parent
            exp = None
            try:
                exp = app.ActiveExplorer()
                if exp is None and app.Explorers.Count:
                    exp = app.Explorers.Item(1)
            except Exception:
                exp = None
            if exp is None:                       # Outlook запущен без окна — открыть окно на папке письма
                exp = folder.GetExplorer()
                exp.Display()
            try:
                if exp.CurrentFolder.EntryID != folder.EntryID:
                    exp.CurrentFolder = folder
            except Exception:
                exp.CurrentFolder = folder
            try:
                exp.ClearSelection()
                exp.AddToSelection(item)
            except Exception:
                pass                              # письмо скрыто фильтром/поиском вида — окно всё равно откроется на папке
            self._to_front(exp)
            return True
        except Exception:
            item.Display()
            return True

    def _to_front(self, exp):
        """Развернуть и вывести окно Outlook поверх браузера."""
        try:
            if int(exp.WindowState) == 1:          # свернуто -> развернуть на весь экран
                exp.WindowState = 0
        except Exception:
            pass
        try:
            exp.Activate()
        except Exception:
            pass
        try:
            import ctypes
            u = ctypes.windll.user32
            hwnd = u.FindWindowW("rctrl_renwnd32", None)      # класс главного окна Outlook
            cap = ""
            try:
                cap = exp.Caption or ""
            except Exception:
                pass
            if cap:
                h2 = u.FindWindowW("rctrl_renwnd32", cap)
                hwnd = h2 or hwnd
            if hwnd:
                if u.IsIconic(hwnd):
                    u.ShowWindow(hwnd, 9)          # SW_RESTORE
                # Windows не даёт фоновой программе забрать фокус: короткое нажатие Alt снимает запрет
                u.keybd_event(0x12, 0, 0, 0); u.keybd_event(0x12, 0, 2, 0)
                u.SetForegroundWindow(hwnd)
        except Exception:
            pass

    def changed(self):
        """Быстрая проверка (раз в 2 с): изменилось ли число писем во «Входящих» и «Отправленных».
        Изменилось — сразу полный опрос: отвеченное письмо уходит с экрана через 2–5 с после отправки."""
        if self.inbox is None:
            return False
        sig = []
        for f in (self.inbox, self.sent):
            try:
                sig.append(f.Items.Count if f is not None else -1)
            except Exception:
                sig.append(-2)
        sig = tuple(sig)
        prev, self._quick_sig = getattr(self, "_quick_sig", None), sig
        return prev is not None and sig != prev

    def _own(self):
        own = {x.strip().lower() for x in (self.cfg.get("own_addresses") or []) if "@" in (x or "")}
        a = (self.cfg.get("mailbox_address") or "").strip().lower()
        if "@" in a:
            own.add(a)
        return own

    def _readdress(self, r, to):
        """Оставить в «Кому» только адрес клиента (письмо формы сайта: Reply-To — наш же ящик)."""
        while r.Recipients.Count:
            r.Recipients.Remove(1)
        r.Recipients.Add(to)
        try:
            r.Recipients.ResolveAll()
        except Exception:
            pass

    def _rcpts(self, item):
        out = []
        try:
            for i in range(1, item.Recipients.Count + 1):
                a = self._rcpt_smtp(item.Recipients.Item(i))
                if a:
                    out.append(a)
        except Exception:
            pass
        return out

    def send_reply(self, msg, subject, text, to=None):
        """Автоответ: ответ в той же цепочке, текст над цитатой оригинала. to — адрес клиента вместо Reply-To."""
        app, ns = self._session()
        item = ns.GetItemFromID(msg["entry_id"])
        r = item.Reply()
        if to:
            self._readdress(r, to)
        rc = self._rcpts(r)
        own = self._own()
        if not rc or all(a in own for a in rc):
            r.Close(1)                                  # olDiscard: черновик не сохранять
            raise RuntimeError("ответ ушёл бы на наш же ящик (%s) — адрес клиента не найден" % (", ".join(rc) or "нет адреса"))
        r.Subject = subject
        try:
            r.Body = text + "\n\n" + (r.Body or "")
        except Exception:
            r.Body = text
        self._from(ns, r)
        r.Send()
        return True

    def open_reply(self, entry_id, to):
        """«Ответить в Outlook» для заявки с сайта: окно ответа, в «Кому» уже адрес клиента (а не наш ящик)."""
        app, ns = self._session()
        item = ns.GetItemFromID(entry_id)
        r = item.Reply()
        self._readdress(r, to)
        self._from(ns, r)
        r.Display()
        try:
            self._to_front(r.GetInspector)
        except Exception:
            pass
        return True

    def forward(self, msg, to, note):
        """Переслать письмо в отдел с пометкой сверху (вложения уходят вместе с письмом)."""
        app, ns = self._session()
        item = ns.GetItemFromID(msg["entry_id"])
        f = item.Forward()
        f.Recipients.Add(to)
        try:
            f.Recipients.ResolveAll()
        except Exception:
            pass
        try:
            f.Body = (note or "") + "\n\n" + (f.Body or "")
        except Exception:
            pass
        self._from(ns, f)
        f.Send()
        return True

    def send_mail(self, to, cc, subject, html, inline=None):
        app, ns = self._session()
        m = app.CreateItem(0)
        m.To = to
        if cc:
            m.CC = cc
        for cid, path in (inline or []):
            try:
                att = m.Attachments.Add(path)
                att.PropertyAccessor.SetProperty(PR_ATT_CID, cid)
                att.PropertyAccessor.SetProperty(PR_ATT_HIDDEN, True)
            except Exception:
                pass
        self._from(ns, m)
        m.Subject = subject
        m.HTMLBody = html
        m.Send()
        return True
