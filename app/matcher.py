# -*- coding: utf-8 -*-
"""Сопоставление исходящих писем с входящими (ответ или нет) и фильтр мусора."""
import re

_PREFIX = re.compile(r"^\s*(?:(?:re|fw|fwd|отв|ответ|пересл|aw|wg|tr|sv|vs)\s*(?:\[\d+\])?\s*:\s*)+", re.I)

def norm_subject(s: str) -> str:
    s = (s or "").strip()
    prev = None
    while prev != s:
        prev = s
        s = _PREFIX.sub("", s).strip()
    return re.sub(r"\s+", " ", s).lower()

def is_ignored(sender_addr: str, sender_name: str, subject: str, cfg) -> str:
    """Причина игнорирования или пустая строка."""
    a = (sender_addr or "").lower()
    n = (sender_name or "").lower()
    for own in list(cfg.get("own_addresses") or []) + [cfg.get("mailbox_address") or ""]:
        if own and own.strip().lower() == a:
            return "своё письмо"
    for pat in cfg.get("ignore_senders") or []:
        p = (pat or "").lower().strip()
        if p and (p in a or p in n):
            return "автоотправитель: " + p
    subj = (subject or "").lower()
    for pat in cfg.get("ignore_subjects") or []:
        p = (pat or "").lower().strip()
        if p and p in subj:
            return "автописьмо: " + pat
    return ""

_FWD = re.compile(r"^\s*(?:fw|fwd|пересл|tr|wg|vs)\s*(?:\[\d+\])?\s*:", re.I)

def is_forward(subject: str) -> bool:
    return bool(_FWD.match(subject or ""))

def reaches(c, rcpts, own):
    """Дошёл ли ответ до клиента заявки с сайта («Отвечать на» — наш ящик): в получателях адрес клиента;
    адреса нет в заявке — хотя бы один внешний адрес (не наш домен и не адрес самой формы)."""
    ce = (c.get("client_email") or "").lower()
    if ce:
        return ce in rcpts
    s = (c.get("sender") or "").lower()
    own_dom = {a.split("@", 1)[1] for a in (own or ()) if "@" in a}
    return any(r != s and "@" in r and r not in own and r.split("@", 1)[1] not in own_dom for r in rcpts)

def match_reply_all(outgoing: dict, candidates: list, own=()):
    """Все письма, на которые отвечает исходящее: найденное + более ранние письма того же отправителя
    в той же переписке (клиент написал и напомнил — один ответ закрывает оба)."""
    mid = match_reply(outgoing, candidates, own)
    if not mid:
        return []
    first = next(c for c in candidates if c["id"] == mid)
    sent_at = outgoing.get("sent_at") or ""
    snd = (first.get("sender") or "").lower()
    cli = (first.get("client_email") or "").lower()
    conv = first.get("conv_id") or ""
    subj = norm_subject(first.get("subject"))
    out = [mid]
    for c in candidates:
        if c["id"] == mid or (c.get("received_at") or "") > sent_at:
            continue
        if (c.get("sender") or "").lower() != snd:
            continue
        if (c.get("client_email") or "").lower() != cli:
            continue        # заявки с сайта приходят с одного адреса формы — разные клиенты не закрывают друг друга
        if c.get("reply_self") and not cli:
            continue        # заявка без адреса клиента: закрывается только своим ответом, не «заодно»
        if (conv and c.get("conv_id") == conv) or (subj and norm_subject(c.get("subject")) == subj):
            out.append(c["id"])
    return out

def match_reply(outgoing: dict, candidates: list, own=()):
    """outgoing: {conv_id, subject, recipients(list), sent_at}
    candidates: входящие без ответа [{id, conv_id, sender, subject, received_at, client_email, reply_self}]
    own: адреса нашего ящика. Возвращает id входящего или None. Сначала по цепочке Outlook, затем по теме.
    Заявка с сайта (reply_self) считается отвеченной, ТОЛЬКО если ответ ушёл клиенту (`_reaches`)."""
    rcpts = {r.lower() for r in (outgoing.get("recipients") or []) if r}
    own = {a.lower() for a in (own or ())}
    sent_at = outgoing.get("sent_at") or ""
    conv = outgoing.get("conv_id") or ""
    subj = norm_subject(outgoing.get("subject"))

    def ok_time(c):
        return (c.get("received_at") or "") <= sent_at

    def rcpt_ok(c):
        if c.get("reply_self"):
            return reaches(c, rcpts, own)
        s = (c.get("sender") or "").lower()
        ce = (c.get("client_email") or "").lower()       # адрес из Reply-To
        return (bool(s) and s in rcpts) or (bool(ce) and ce in rcpts)

    is_forward = bool(_FWD.match(outgoing.get("subject") or ""))
    if conv:
        same = [c for c in candidates if c.get("conv_id") == conv and ok_time(c)]
        pool = [c for c in same if rcpt_ok(c)] or [c for c in same if not c.get("sender") and not c.get("reply_self")]
        if not pool and not is_forward:
            # ответ в той же переписке Outlook, но на другой адрес — тоже ответ (кроме заявок с сайта:
            # там ответ засчитывается только клиенту); пересылка (FW:) в отдел ответом не считается
            pool = [c for c in same if not c.get("reply_self")]
        if pool:
            # несколько писем в одной переписке (заявки формы с одинаковой темой) — сначала с той же темой
            subj_pool = [c for c in pool if norm_subject(c.get("subject")) == subj]
            return sorted(subj_pool or pool, key=lambda c: c.get("received_at") or "")[0]["id"]
    if subj:
        same = [c for c in candidates
                if norm_subject(c.get("subject")) == subj and ok_time(c) and rcpt_ok(c)]
        if same:
            return sorted(same, key=lambda c: c.get("received_at") or "")[0]["id"]
    if not is_forward:
        # заявка с сайта: сотрудник написал клиенту новым письмом (своя тема) на адрес из заявки — это ответ
        same = [c for c in candidates if c.get("client_email") and c["client_email"].lower() in rcpts and ok_time(c)]
        if same:
            return sorted(same, key=lambda c: c.get("received_at") or "")[0]["id"]
    return None
