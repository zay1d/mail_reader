# -*- coding: utf-8 -*-
"""Отправка тревоги в группу Telegram службы безопасности (Bot API, только stdlib).
Единственный выход программы в интернет; используется только для сигнала об угрозе."""
import json, urllib.request, urllib.parse, urllib.error

def _ids(chat_id):
    return [x.strip() for x in str(chat_id or "").replace(";", ",").split(",") if x.strip()]

def send_each(token, chat_id, text, skip=(), timeout=10):
    """Отправить каждому получателю (кроме уже получивших — skip). -> (кому ушло, [ошибки])."""
    token = (token or "").strip()
    ids = _ids(chat_id)
    if not token or not ids:
        raise RuntimeError("Не задан токен бота или получатели")
    sent, errors = [], []
    for cid in ids:
        if cid in skip:
            continue
        try:
            _send_one(token, cid, text, timeout)
            sent.append(cid)
        except Exception as e:
            errors.append("%s: %s" % (cid, str(e)[:120]))
    return sent, errors

def send_message(token, chat_id, text, timeout=10):
    """chat_id — один ID или несколько через запятую (группа СБ + личные чаты).
    Ошибка, если не дошло хотя бы одному получателю (кнопка проверки должна это показать)."""
    sent, errors = send_each(token, chat_id, text, timeout=timeout)
    if errors:
        raise RuntimeError(("Дошло: %s. " % ", ".join(sent) if sent else "") + "Не дошло: " + "; ".join(errors))
    return True

def list_chats(token, timeout=10):
    """Кто писал боту (для подбора ID получателей): [{chat_id, name, type}]."""
    token = (token or "").strip()
    if not token:
        raise RuntimeError("Не задан токен бота")
    url = "https://api.telegram.org/bot%s/getUpdates?limit=100" % token
    with urllib.request.urlopen(url, timeout=timeout) as r:
        body = json.loads(r.read().decode("utf-8"))
    seen, out = set(), []
    for u in body.get("result") or []:
        msg = u.get("message") or u.get("my_chat_member", {}).get("chat") and {"chat": u["my_chat_member"]["chat"]} or {}
        chat = (msg or {}).get("chat") or {}
        cid = str(chat.get("id") or "")
        if not cid or cid in seen:
            continue
        seen.add(cid)
        name = chat.get("title") or " ".join(x for x in (chat.get("first_name"), chat.get("last_name")) if x) or chat.get("username") or cid
        out.append({"chat_id": cid, "name": name, "type": chat.get("type") or ""})
    return out

def _send_one(token, chat_id, text, timeout):
    url = "https://api.telegram.org/bot%s/sendMessage" % token
    data = urllib.parse.urlencode({"chat_id": chat_id, "text": text[:4000], "parse_mode": "HTML",
                                   "disable_web_page_preview": "true"}).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:     # причина отказа Telegram — в теле ответа («chat not found», «bot was kicked»)
        try:
            desc = json.loads(e.read().decode("utf-8")).get("description")
        except Exception:
            desc = None
        raise RuntimeError(desc or str(e))
    if not body.get("ok"):
        raise RuntimeError(str(body.get("description") or "Telegram отклонил сообщение"))
    return True

def threat_text(m, phone):
    import html
    words = ", ".join(m.get("threat_words") or [])
    return ("🚨 <b>ВНИМАНИЕ! УГРОЗА — почта ресепшн TCM</b>\n"
            "Получено: %s\nОт: %s &lt;%s&gt;\nТема: %s\nНайдено: <b>%s</b>\n\n%s\n\n"
            "Письмо не удалять. Ресепшн: %s"
            % ((m.get("received_at") or "").replace("T", " ")[:16], html.escape((m.get("sender_name") or "")[:100]),
               html.escape((m.get("sender") or "")[:200]), html.escape((m.get("subject") or "")[:300]), html.escape(words[:300]),
               html.escape((m.get("body_preview") or "")[:300]), html.escape(phone or "")))
