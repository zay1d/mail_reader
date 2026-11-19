# -*- coding: utf-8 -*-
"""Язык письма, направление (категория), приоритет — по правилам, без внешних сервисов."""
import re

_UZ_CYR = re.compile(r"[ўқғҳ]", re.I)
_UZ_CYR_WORDS = re.compile(r"\b(салом|рахмат|илтимос|керак|қандай|бўлади|ишлайди|соат|ассалому)\b", re.I)
_UZ_LAT = re.compile(r"(\b(assalomu|alaykum|salom|rahmat|iltimos|kerak|qanday|bo['’`]?l\w*|ishlaydi|soat|uchun|bilan|haqida|mumkin|yoki|ham|men|siz|bu|bor|yo['’`]?q|hamma|savdo|markaz\w*|chiqib|ketsin|xat|bugun|ertaga|kecha|javob|qaysi|qachon|nima|qilish|qiling|edi|emas)\b|[og]['’`])", re.I)

# русский латиницей (транслит): «Zdravstvuyte, podskazhite…» — отвечать по-русски
_RU_TRANSLIT = re.compile(r"\b(zdravstvu\w*|zdrastvu\w*|dobr(?:yy|iy|ij|yj|oe|ogo)|privet|podskazh\w*|podskaj\w*|pozhalu\w*|pojalu\w*|"
                          r"spasibo|skazhite|skajite|khochu|hochu|mozhno|mojno|skolko|skol'ko|rabotae\w*|kogda|gde|pochemu|nuzhno|"
                          r"nujno|vopros|est')(?![a-z])", re.I)
# ссылки, адреса почты, домены: латиница в них — не язык человека
# (?<!…) — поиск начинается только с начала слова: без этого длинная строка без пробелов (код, «a.b.c.d…» на
# 20 000 знаков) разбиралась 5–15 с и задерживала весь опрос почты
_LINKS = re.compile(r"<[^<>\s]+>|(?:https?://|www\.)\S+|(?<![\w.+\-])[\w.+\-]+@[\w\-]+(?:\.[\w\-]+)+|"
                    r"(?<![\w.\-])[\w\-]+(?:\.[\w\-]+)*\.(?:uz|ru|com|org|net|info|io|biz|me|co)\b(?:/\S*)?", re.I)
# начало цитаты переписки / пересланного письма
_QUOTE = re.compile(r"^[ \t]*(?:>|-{2,}\s*(?:original message|forwarded message|исходное сообщение|пересылаемое сообщение|"
                    r"переадресованное сообщение)|_{8,}|(?:from|от|sent|отправлено|date|дата|kimdan)\s*:|on .{3,120} wrote:|"
                    r".{0,160}\d{1,2}:\d{2}.{0,160}<[^<>\s]+@[^<>\s]+>.{0,20}:\s*$)", re.I | re.M)   # Gmail/Яндекс/Mail.ru: «…10:12, Имя <адрес>:»
# подпись и дисклеймер компании
_SIGN = re.compile(r"^[ \t]*(?:--\s*$|с уважением|с наилучшими|best regards|kind regards|warm regards|regards\b|sincerely|cheers\b|"
                   r"hurmat bilan|ҳурмат билан|sent from my|отправлено (?:с|из)|this e-?mail|this message|disclaimer|"
                   r"confidential|конфиденциальн|the information contained)", re.I | re.M)
# заявка с сайта: текст клиента — в поле «Сообщение»; язык страницы — в ссылке tcmall.uz/ru|uz|en
_FORM_MSG = re.compile(r"^[ \t]*(?:сообщение|комментарий|текст сообщения|message|comment|your message|xabar|izoh|хабар|изоҳ)\s*[:：]\s*(.*)",
                       re.I | re.M | re.S)
_SITE = re.compile(r"tcmall\.uz/(ru|uz|oz|en)\b", re.I)

def _lang_of(t):
    """Язык куска текста или None, если в нём нет букв."""
    t = t or ""
    cyr = len(re.findall(r"[а-яёўқғҳ]", t, re.I))
    lat = len(re.findall(r"[a-z]", t, re.I))
    if cyr + lat == 0:
        return None
    # кириллицы заметно (≥ ~30 % букв) — человек пишет кириллицей: латинские бренды, имя, e-mail его язык не меняют
    if cyr >= lat or (cyr >= 8 and cyr * 10 >= lat * 4):
        words = max(1, len(re.findall(r"[а-яёўқғҳ]+", t, re.I)))
        hits = len(_UZ_CYR.findall(t)) + len(_UZ_CYR_WORDS.findall(t))
        return "uz" if hits >= 2 and hits >= 0.03 * words else "ru"
    words = max(1, len(re.findall(r"[a-z']+", t, re.I)))
    found = _UZ_LAT.findall(t)
    wh = sum(1 for f in found if f[1])                 # узбекские слова
    hits = wh + (len(found) - wh if wh else 0)        # o'/g' считаем только рядом с узбекскими словами (не o'clock)
    if hits >= 2 and hits >= 0.03 * words:
        return "uz"
    ru = len({x.lower() for x in _RU_TRANSLIT.findall(t)})
    if ru >= 2 and ru >= 0.08 * words:
        return "ru"
    return "en"

def _own_text(t):
    """Свой текст человека: до цитаты переписки и до подписи/дисклеймера, без ссылок и адресов почты."""
    m = _QUOTE.search(t)
    if m and t[:m.start()].strip():
        t = t[:m.start()]
    m = _SIGN.search(t)
    if m and t[:m.start()].strip():
        t = t[:m.start()]
    return _LINKS.sub(" ", t)

def detect_lang(text: str, subject: str = None) -> str:
    """ru / uz / en — язык, на котором ПИШЕТ человек (на нём он прочтёт автоответ). Смотрим его собственный текст:
    без цитаты переписки, подписи, дисклеймера, ссылок и адресов почты; у заявки с сайта — поле «Сообщение» (пусто —
    язык страницы сайта). Тема — только если в тексте нет букв (тема бывает чужой: «RE: Invoice»). Узбекский — только
    если узбекских слов заметно много: английское письмо с «o'clock» или узбекское имя в русском — не узбекский.
    Букв нет совсем — ru (основной язык)."""
    body = text or ""
    if subject is None:                       # старый вызов: тема — первой строкой текста
        subject, _, body = body.partition("\n")
    m = _FORM_MSG.search(body)
    if m:
        lang = _lang_of(_LINKS.sub(" ", m.group(1)))
        if lang:
            return lang
        s = _SITE.search(body)
        if s:
            return {"ru": "ru", "uz": "uz", "oz": "uz", "en": "en"}[s.group(1).lower()]
    return (_lang_of(_own_text(body)) or _lang_of(_LINKS.sub(" ", body))
            or _lang_of(_LINKS.sub(" ", subject or "")) or "ru")

_RX = {}

def _hit(w, text):
    rx = _RX.get(w)
    if rx is None:
        rx = _RX[w] = re.compile(r"(?<![0-9a-zа-яёўқғҳ])" + re.escape(w), re.I)
    return rx.search(text) is not None

def _count(words, text):
    """Сколько слов словаря встречается в тексте (слово должно начинаться с начала слова)."""
    n = 0
    for w in words or []:
        w = (w or "").lower()
        w = w if w.endswith(" ") else w.strip()
        if w.strip() and _hit(w, text):
            n += 1
    return n

def categorize(subject: str, body: str, categories: list, is_threat: bool) -> str:
    """Возвращает key категории. Угроза -> security. Жалоба имеет приоритет при совпадении."""
    if is_threat:
        return "security"
    text = ((subject or "") + "\n" + (body or "")).lower()
    subj = (subject or "").lower()
    # отдел назван в теме письма: «Для маркетинга», «В отдел аренды», «Парковка: ...» — это важнее слов в тексте
    for c in categories or []:
        name = (c.get("name") or "").strip().lower()
        if not name or c.get("key") in ("general", "security"):
            continue
        stem = name.split()[0]
        stem = stem[:-1] if len(stem) > 5 else stem
        if stem and _hit(stem, subj):
            return c.get("key")
    best, best_score = None, 0
    for c in categories or []:
        sc = _count(c.get("words"), text) + 2 * _count(c.get("words"), subj)
        if c.get("key") == "complaint" and sc > 0:
            sc += 2
        if sc > best_score:
            best, best_score = c.get("key"), sc
    return best or "general"

def explain(subject: str, body: str, categories: list, key: str) -> list:
    """Слова словаря направления key, найденные в письме (для объяснения, почему словарь выбрал направление)."""
    text = ((subject or "") + "\n" + (body or "")).lower()
    subj = (subject or "").lower()
    out = []
    for c in categories or []:
        if c.get("key") != key:
            continue
        name = (c.get("name") or "").strip().lower()
        if name and key not in ("general", "security"):
            stem = name.split()[0]
            stem = stem[:-1] if len(stem) > 5 else stem
            if stem and _hit(stem, subj):
                out.append("в теме: " + stem)
        for w in c.get("words") or []:
            w = (w or "").lower()
            w = w if w.endswith(" ") else w.strip()
            if w.strip() and _hit(w, text) and w.strip() not in out:
                out.append(w.strip())
    return out

def prioritize(subject: str, body: str, sender: str, importance_high: bool, rules: dict, category: str) -> int:
    """1 — срочно, 2 — обычно, 3 — низкий."""
    text = ((subject or "") + "\n" + (body or "")).lower()
    s = (sender or "").lower()
    if importance_high:
        return 1
    for v in rules.get("vip_senders") or []:
        v = (v or "").strip().lower()
        if v and v in s:
            return 1
    if _count(rules.get("urgent_words"), text):
        return 1
    if category in (rules.get("urgent_categories") or []):
        return 1
    if _count(rules.get("low_words"), text) and category not in ("complaint", "security", "lost"):
        return 3
    if category in (rules.get("low_categories") or []):
        return 3
    return 2

_EMAIL = r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)+"
_EMAIL_RX = re.compile(r"(?<![A-Za-z0-9._%+\-])" + _EMAIL)    # с начала слова: длинная строка без пробелов — не секунды
_FORM_EMAIL = re.compile(r"(?<![a-zа-я])(?:e-?mail|email address|e-?почта|эл\.?\s*почта|электронная\s+почта|почта|pochta|elektron\s+pochta)"
                         r"\s*[:：]?\s*(?:<|\[|mailto:)?\s*(" + _EMAIL + ")", re.I)
_FORM_NAME = re.compile(r"^[ \t]*(?:фио|ф\.\s*и\.\s*о\.?|имя|ваше имя|имя и фамилия|name|full name|your name|ism|ismi|ism[ -]familiya|f\.?i\.?sh\.?)"
                        r"[ \t]*[:：][ \t]*\r?\n?[ \t]*([^\r\n:]{2,80})", re.I | re.M)

def form_contact(body: str, own=(), sender: str = ""):
    """Заявка с сайта: письмо приходит от адреса формы, «Отвечать на» — наш же ящик, а адрес клиента — в тексте
    («Email: …»). Возвращает (адрес клиента, имя клиента); не нашли — пустые строки."""
    t = body or ""
    s = (sender or "").lower()
    bad = {(a or "").strip().lower() for a in own} | {s}
    own_dom = {a.split("@", 1)[1] for a in bad if "@" in a and a != s}
    def ok(a, strict):
        dom = a.split("@", 1)[1]
        if a in bad or dom in own_dom:
            return False
        return not (strict and "@" in s and dom == s.split("@", 1)[1])   # без подписи «Email:» — не адрес самой формы
    email = ""
    for mt in _FORM_EMAIL.finditer(t):
        a = mt.group(1).lower().rstrip(".")
        if ok(a, False):
            email = a
            break
    if not email:                                     # подписи нет: берём адрес, только если он в тексте один
        found = []
        for a in _EMAIL_RX.findall(t):
            a = a.lower().rstrip(".")
            if ok(a, True) and a not in found:
                found.append(a)
        if len(found) == 1:
            email = found[0]
    mt = _FORM_NAME.search(t)
    name = mt.group(1).strip() if mt else ""
    return email, name

def render_template(tpl: str, **kw) -> str:
    out = tpl or ""
    for k, v in kw.items():
        out = out.replace("{" + k + "}", str(v or ""))
    return out
