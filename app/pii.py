# -*- coding: utf-8 -*-
"""Скрытие личных данных перед отправкой письма в ИИ. Адреса почты, телефоны, номера карт, документов и машин,
ссылки, длинные номера и имя отправителя заменяются метками ([ПОЧТА_1], [ТЕЛ_1], [ИМЯ_1]…). Таблица замен
остаётся в программе: по ней метки в ответе ИИ превращаются обратно в настоящие значения."""
import re

_EMAIL = re.compile(r"(?<![A-Za-z0-9._%+\-])[A-Za-z0-9._%+\-]+@[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)+")
_URL = re.compile(r"(?:https?://|www\.)[^\s<>\"']+", re.I)
# номер карты: 16 цифр группами по 4 (или подряд), 13–19 цифр подряд
_CARD = re.compile(r"(?<!\d)(?:\d{4}[ \-]?){3}\d{4}(?!\d)|(?<!\d)\d{13}(?!\d)|(?<!\d)\d{15,19}(?!\d)")
_PINFL = re.compile(r"(?<!\d)\d{14}(?!\d)")                       # ПИНФЛ — 14 цифр
_PASSPORT = re.compile(r"(?<![A-Za-zА-Яа-я0-9])[A-ZА-Я]{2}[ \-]?\d{7}(?!\d)")     # AA1234567, АВ 1234567
# номер машины (Узбекистан): 01 A 123 BC, 01A123BC, 01 123 ABC, 01 123ABC
_PLATE = re.compile(r"(?<![A-Za-z0-9])\d{2}[ \-]?(?:[A-Z][ \-]?\d{3}[ \-]?[A-Z]{2}|\d{3}[ \-]?[A-Z]{3})(?![A-Za-z0-9])")
# телефон: +998 90 123-45-67, 8 (495) 123-45-67, 90 123 45 67 — от 7 цифр с разделителями
_PHONE = re.compile(r"(?<![\w.])(?:\+|00)?\d[\d \-()]{5,18}\d(?![\w])")
_LONGNUM = re.compile(r"(?<!\d)\d{6,}(?!\d)")                     # чеки, счета, договоры
# «меня зовут Иван Петров», «my name is John Smith», «ismim Jamshid», «С уважением,\nИван Петров»
_WORD = r"[A-ZА-ЯЁЎҚҒҲ][A-Za-zА-Яа-яЁёЎўҚқҒғҲҳ'’`\-]{1,30}"
_INTRO = re.compile(r"(?:меня зовут|мо[её] имя|я\s*[—\-]\s*|my name is|i am|i'm|mening ismim|ismim|исмим)[ \t]+(" + _WORD + r"(?:[ \t]+" + _WORD + r"){0,2})", re.I)
_FIELD = re.compile(r"^[ \t]*(?:фио|ф\.\s*и\.\s*о\.?|имя|ваше имя|имя и фамилия|name|full name|your name|ism|ismi|ism[ -]familiya|f\.?i\.?sh\.?)"
                    r"[ \t]*[:：][ \t]*\r?\n?[ \t]*([^\r\n:]{2,80})", re.I | re.M)
_SIGN = re.compile(r"(?:с уважением|с наилучшими пожеланиями|best regards|kind regards|regards|sincerely|hurmat bilan|ҳурмат билан)"
                   r"[ \t]*[,.!]?[ \t]*\r?\n[ \t]*(" + _WORD + r"(?:[ \t]+" + _WORD + r"){0,2})", re.I)
# имя отправителя — не имя человека, а название: такие слова в тексте не скрываем
_ORG = {"ооо", "оао", "зао", "ао", "ип", "чп", "мчж", "llc", "ltd", "inc", "gmbh", "group", "agency", "service", "services",
        "team", "support", "info", "office", "sales", "mall", "center", "centre", "bank", "shop", "store", "market",
        "company", "компания", "группа", "центр", "банк", "магазин", "отдел", "служба", "администрация", "admin",
        "reception", "ресепшн", "noreply", "newsletter", "microsoft", "google", "teams"}


class Masker:
    """Одно письмо — один Masker: одинаковые значения получают одну и ту же метку."""

    def __init__(self, names=()):
        self.map = {}          # метка -> настоящее значение
        self._rev = {}         # (вид, значение в нижнем регистре) -> метка
        self._n = {}
        self.name_tokens = []
        for n in names or ():
            self._add_name(n)

    def _add_name(self, name):
        """Имя человека (2–3 слова с заглавной буквы, не название организации) — скрывать каждое его слово."""
        toks = [t for t in re.split(r"[\s,.;:()\"«»]+", (name or "").strip()) if t]
        if not 1 <= len(toks) <= 4 or "@" in (name or ""):
            return
        if any(t.lower().strip(".") in _ORG or any(ch.isdigit() for ch in t) for t in toks):
            return
        good = [t for t in toks if len(t) >= 3 and t[0].isupper() and re.fullmatch(r"[^\W\d_]+(?:['’`\-][^\W\d_]+)*", t)]
        if len(good) < 2 and len(toks) != 1:
            return
        if len(toks) == 1 and not good:
            return
        for t in good:
            if t.lower() not in (x.lower() for x in self.name_tokens):
                self.name_tokens.append(t)

    def _tag(self, kind, value):
        key = (kind, re.sub(r"\s+", " ", value.strip().lower()))
        tag = self._rev.get(key)
        if tag is None:
            self._n[kind] = self._n.get(kind, 0) + 1
            tag = "[%s_%d]" % (kind, self._n[kind])
            self._rev[key] = tag
            self.map[tag] = value.strip()
        return tag

    def mask(self, text):
        t = text or ""
        for rx in (_INTRO, _FIELD, _SIGN):            # имена, названные в самом письме
            for m in rx.finditer(t):
                self._add_name(m.group(1))
        t = _EMAIL.sub(lambda m: self._tag("ПОЧТА", m.group(0)), t)
        t = _URL.sub(self._url, t)
        t = _CARD.sub(lambda m: self._tag("ДОКУМЕНТ", m.group(0)) if _PINFL.fullmatch(m.group(0)) else self._tag("КАРТА", m.group(0)), t)
        t = _PINFL.sub(lambda m: self._tag("ДОКУМЕНТ", m.group(0)), t)
        t = _PASSPORT.sub(lambda m: self._tag("ДОКУМЕНТ", m.group(0)), t)
        t = _PLATE.sub(lambda m: self._tag("АВТО", m.group(0)), t)
        t = _PHONE.sub(self._phone, t)
        t = _LONGNUM.sub(lambda m: self._tag("НОМЕР", m.group(0)), t)
        for tok in sorted(self.name_tokens, key=len, reverse=True):
            # слово имени целиком, с падежным окончанием («Ивана», «Петровой»); метка в квадратных скобках не трогается
            rx = re.compile(r"(?<![\w\[])" + re.escape(tok) + r"(?:а|у|е|ом|ой|ы|ым|ого|ому|ну|ning|ga|ni|dan)?(?![\w\]])", re.I)
            t = rx.sub(lambda m, tok=tok: self._tag("ИМЯ", tok), t)
        return t

    def _url(self, m):
        s = m.group(0)
        tail = len(s) - len(s.rstrip(".,;:!?)»\"'"))      # знак препинания после ссылки — не часть ссылки
        return self._tag("ССЫЛКА", s[:len(s) - tail]) + s[len(s) - tail:]

    def _phone(self, m):
        s = m.group(0)
        digits = re.sub(r"\D", "", s)
        if len(digits) < 7 or len(digits) > 15:
            return s
        # время, даты и суммы телефоном не считаем: «18:00», «25.12.2026», «500 000», «10 000 000 сум»
        if re.fullmatch(r"\d{1,3}(?: \d{3})+", s.strip()):
            return s
        return self._tag("ТЕЛ", s)

    def unmask(self, text):
        """Метки в ответе ИИ — обратно в настоящие значения (для экрана сотрудника)."""
        t = text or ""
        for tag, value in self.map.items():
            t = t.replace(tag, value)
        return t

    def counts(self):
        return dict(self._n)
