# -*- coding: utf-8 -*-
"""Демо-источник: вымышленные письма, чтобы посмотреть экран и отчёт без Outlook."""
import os, random, threading
from datetime import datetime, timedelta

def _iso(d):
    return d.strftime("%Y-%m-%dT%H:%M:%S")

_SENDERS = [
    ("Азиза Каримова", "aziza.k@example.uz"),
    ("ООО «Ситимаркет»", "office@citymarket-demo.uz"),
    ("Jamshid Tursunov", "j.tursunov@example.com"),
    ("Наталья Петрова", "n.petrova@example.ru"),
    ("Event Agency Demo", "hello@eventdemo.uz"),
    ("Sardor Alimov", "sardor@example.uz"),
    ("Dilnoza Rashidova", "dilnoza.r@example.uz"),
    ("Михаил Орлов", "m.orlov@example.com"),
]
_SUBJECTS = [
    ("Потеряла кошелёк в фудкорте", "Здравствуйте! Вчера вечером потеряла кошелёк у фудкорта, коричневый. Подскажите, куда обратиться?"),
    ("Аренда площадки под промо-акцию", "Добрый день. Интересует площадка на 1 этаже на 3 дня в октябре. Пришлите условия и стоимость."),
    ("Bolalar maydonchasi ish vaqti", "Assalomu alaykum. Bolalar maydonchasi qaysi soatlarda ishlaydi? Rahmat."),
    ("Parking fee question", "Hello, I was charged twice for parking on Saturday. Could you check receipt 48213?"),
    ("Вопрос по подарочным картам", "Добрый день! Можно ли купить подарочную карту номиналом 500 000 сум и где?"),
    ("Срочно! Жалоба на уборку туалетов 2 этаж", "Сегодня в 14:00 туалет на 2 этаже был в ужасном состоянии. Прошу срочно принять меры."),
    ("Экскурсия для школьников", "Здравствуйте. Хотим привести группу 25 детей в субботу. Нужно ли согласование?"),
    ("Коммерческое предложение: клининговое оборудование", "Наша компания предлагает поломоечные машины со скидкой 15%. Прайс-лист во вложении."),
    ("Резюме на вакансию администратора", "Добрый день, отправляю резюме на вакансию администратора ресепшн."),
    ("Предложение о сотрудничестве: фотозона", "Здравствуйте! Предлагаем разместить фотозону к Новому году, спонсорское участие."),
]
_AUTO = ("Microsoft Teams", "noreply@teams-demo.com", "Автоматический ответ: Ваша заявка принята",
         "Это автоматическое уведомление, отвечать не нужно.")
_THREATS = [
    ("аноним", "anon4471@example.com", "Срочно", "В вашем торговом центре заложена б о м б а. Взорвётся через час."),
    ("no name", "xx99@example.com", "Ogohlantirish", "Savdo markazida bomba bor. Hamma chiqib ketsin."),
    ("unknown", "zz@example.com", "Warning", "There is a gunman on the third floor, everyone will be shot."),
]

_FORMS = [
    ("Тестова Анна", "anna.test@example.uz", "Здравствуйте! Работает ли каток в выходные и сколько стоит час?"),
    ("Karimov Bekzod", "bekzod.k@example.uz", "Assalomu alaykum. Kecha 2-qavatda sumkamni qoldirib ketdim, topilmadimi?"),
    ("Орлова Мария", "m.orlova@example.ru", "Добрый день. Хочу пожаловаться на грубость охранника на парковке вчера в 19:00."),
]
_BULK = ("Shop Demo Newsletter", "news@shopdemo-mail.uz", "Скидки недели: до 50% на всё",
         "Только до воскресенья! Отписаться от рассылки: ссылка внизу письма.")

_SUSPICIOUS = [
    ("аноним", "anon77@example.com", "Ждите", "Мы вас всех найдём. Ждите сюрприз завтра в 15:00 в вашем здании."),
    ("noname", "nn@example.com", "Ogohlantirish", "Pushaymon bo'lasiz. Kutib turing."),
    ("unknown", "u@example.com", "Last warning", "You will pay for it. Wait and see."),
    ("гость", "guest@example.com", "Вопрос", "Что будет в торговом центре сегодня в 18:00? Никто не выйдет."),
]

class DemoSource:
    name = "demo"

    def __init__(self, cfg, base_dir):
        self.cfg = cfg
        self.base = base_dir
        self.lock = threading.Lock()
        self.incoming = []
        self.outgoing = []
        self.n = 0
        self.tag = datetime.now().strftime("%H%M%S")
        self._seed()

    def _seed(self):
        now = datetime.now()
        offsets = [170, 95, 50, 22, 6, 12, 40]
        for i, off in enumerate(offsets):
            self._add(now - timedelta(minutes=off), *_SENDERS[i], *_SUBJECTS[i])
        first = self.incoming[0]
        self.outgoing.append({
            "id": "demo-out-%s-1" % self.tag, "entry_id": "", "conv_id": first["conv_id"],
            "recipients": [first["sender"]], "subject": "RE: " + first["subject"],
            "sent_at": _iso(now - timedelta(minutes=140)),
        })
        self._add(now - timedelta(minutes=30), _AUTO[0], _AUTO[1], _AUTO[2], _AUTO[3])
        self._add(now - timedelta(minutes=15), *_SENDERS[7], *_SUBJECTS[7])
        self._add(now - timedelta(minutes=10), *_BULK, bulk="есть ссылка «отписаться»")
        self.simulate_form(now - timedelta(minutes=8), 0)

    def _add(self, when, name, addr, subj, body, bulk=False, reply_to=None):
        self.n += 1
        att = []
        sl = subj.lower()
        if "аренд" in sl:
            att = [{"name": "Prezentaciya_promo.pdf", "size": 2400000}]
        elif "коммерческое" in sl:
            att = [{"name": "Price_list.xlsx", "size": 48000}, {"name": "KP_klining.docx", "size": 210000}]
        elif "резюме" in sl:
            att = [{"name": "Resume.pdf", "size": 180000}]
        m = {
            "id": "demo-in-%s-%d" % (self.tag, self.n), "entry_id": "demo-in-%s-%d" % (self.tag, self.n),
            "conv_id": "conv-%s-%d" % (self.tag, self.n), "sender": addr, "sender_name": name,
            "subject": subj, "received_at": _iso(when), "body": body,
            "importance_high": False, "bulk": bulk, "attachments": att, "reply_to": list(reply_to or []),
        }
        with self.lock:
            self.incoming.append(m)
        return m

    def connect(self):
        pass

    def status(self):
        return "demo", "Демо-режим: письма вымышленные"

    def fetch(self, since):
        with self.lock:
            return list(self.incoming), list(self.outgoing)

    def open_item(self, entry_id):
        return False

    def _outbox(self, to, subject, text_or_html):
        out = os.path.join(self.base, "reports", "demo-outbox.html")
        with open(out, "a", encoding="utf-8") as f:
            f.write("<hr><p><b>Кому:</b> %s<br><b>Тема:</b> %s</p><pre style='white-space:pre-wrap'>%s</pre>" % (to, subject, text_or_html))

    def send_mail(self, to, cc, subject, html, inline=None):
        self._outbox(to, subject, html)
        return True

    def send_reply(self, msg, subject, text, to=None):
        """Автоответ: в демо — запись в outbox + исходящее письмо в той же цепочке (to — адрес клиента заявки)."""
        self._outbox(to or msg.get("sender"), subject, text)
        with self.lock:
            self.outgoing.append({
                "id": "demo-auto-%s-%d" % (self.tag, len(self.outgoing) + 1), "entry_id": "",
                "conv_id": msg.get("conv_id") or "", "recipients": [to or msg.get("sender") or ""],
                "subject": subject, "sent_at": _iso(datetime.now()),
            })
        return True

    def forward(self, msg, to, note):
        self._outbox(to, "FW: " + (msg.get("subject") or ""), (note or "") + "\n\n" + (msg.get("body") or ""))
        with self.lock:
            self.outgoing.append({
                "id": "demo-fw-%s-%d" % (self.tag, len(self.outgoing) + 1), "entry_id": "",
                "conv_id": msg.get("conv_id") or "", "recipients": [to],
                "subject": "FW: " + (msg.get("subject") or ""), "sent_at": _iso(datetime.now()),
            })
        return True

    # ---------- кнопки демо ----------
    def simulate_new(self):
        i = random.randrange(len(_SENDERS))
        j = random.randrange(len(_SUBJECTS))
        return self._add(datetime.now(), *_SENDERS[i], *_SUBJECTS[j])

    def simulate_threat(self):
        t = random.choice(_THREATS)
        return self._add(datetime.now(), *t)

    def simulate_marketing(self):
        return self._add(datetime.now(), *_SENDERS[4], *_SUBJECTS[9])

    def simulate_form(self, when=None, i=None):
        """Заявка с сайта: письмо от адреса формы, «Отвечать на» — наш же ящик, адрес клиента в тексте."""
        name, email, text = _FORMS[random.randrange(len(_FORMS)) if i is None else i]
        own = [a for a in (self.cfg.get("own_addresses") or []) if "@" in (a or "")] or ["contact@tcmall.uz"]
        self.n_form = getattr(self, "n_form", 2200) + 1
        body = ("Новая заявка с сайта tcmall.uz/ru/contact\nID заявки: %d\nФИО: %s\nEmail: %s\n"
                "Телефон: +998 90 000 00 00\nСообщение: %s" % (self.n_form, name, email, text))
        return self._add(when or datetime.now(), "support@o-d.one", "support@o-d.one", "Новая заявка от " + name, body,
                         reply_to=[own[0]])

    def simulate_suspicious(self):
        t = random.choice(_SUSPICIOUS)
        return self._add(datetime.now(), *t)

    def simulate_reply(self, msg):
        with self.lock:
            self.outgoing.append({
                "id": "demo-out-%s-%d" % (self.tag, len(self.outgoing) + 2), "entry_id": "",
                "conv_id": msg.get("conv_id") or "", "recipients": [msg.get("client_email") or msg.get("sender") or ""],
                "subject": "RE: " + (msg.get("subject") or ""), "sent_at": _iso(datetime.now()),
            })
        return True

    def simulate_self_reply(self, msg):
        """Ошибка сотрудника: «Ответить» на заявку с сайта ушло на наш же ящик (для проверки предупреждения)."""
        own = [a for a in (self.cfg.get("own_addresses") or []) if "@" in (a or "")] or ["contact@tcmall.uz"]
        with self.lock:
            self.outgoing.append({
                "id": "demo-out-%s-%d" % (self.tag, len(self.outgoing) + 2), "entry_id": "",
                "conv_id": msg.get("conv_id") or "", "recipients": [own[0]],
                "subject": "RE: " + (msg.get("subject") or ""), "sent_at": _iso(datetime.now()),
            })
        return True
