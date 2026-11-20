# -*- coding: utf-8 -*-
"""Настройки приложения: config.json рядом с программой."""
import json, os, sys, copy, threading

def base_dir():
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

BASE = base_dir()
CONFIG_PATH = os.path.join(BASE, "config.json")

import threat_defaults
DEFAULT_THREAT_WORDS = threat_defaults.HARD

DEFAULT_CATEGORIES = [
    {"key": "complaint", "name": "Жалобы", "words": [
        "туалет", "уборк", "грязн", "грязь", "сломан", "не работает", "воняет", "запах", "хамил", "нагрубил", "очеред", 
        "жалоб", "претензи", "недоволь", "ужасн", "безобрази", "хамств", "грубо", "обман", "вернуть деньги", "возврат",
        "complaint", "refund", "terrible", "rude", "unacceptable", "shikoyat", "e'tiroz", "qaytar", "yomon", "dag'al"]},
    {"key": "security", "name": "Безопасность", "words": [
        "охран", "безопасн", "кража", "украл", "подозрительн", "драка", "инцидент", "видеонаблюд", "камер",
        "security", "theft", "stolen", "incident", "suspicious", "xavfsizlik", "o'g'ri", "kamera", "janjal"]},
    {"key": "lost", "name": "Потерянные вещи", "words": [
        "утерян", "забытые вещи", "нашли", 
        "потерял", "потеряла", "забыл", "забыла", "находк", "оставил", "кошел", "бюро находок", "пропал",
        "lost", "found", "left my", "forgot", "yo'qot", "yoqot", "unut", "topib", "qoldir"]},
    {"key": "parking", "name": "Парковка", "words": [
        "машин", "авто ", "эвакуат", "номер авто", 
        "парков", "паркинг", "стоянк", "шлагбаум", "въезд", "парковочн", "автомобил",
        "parking", "car park", "avtoturargoh", "mashina", "to'xtash", "shlagbaum"]},
    {"key": "rent", "name": "Аренда", "words": ['аренд', 'арендатор', 'субаренд', 'помещени', 'торговое мест', 'торговая точк', 'торговую точк', 'точку продаж', 'юнит', 'бутик', 'островок', 'островн', 'корнер', 'киоск', 'павильон', 'открыть магазин', 'открыть кафе', 'открыть ресторан', 'открыть точку', 'открыть кофейн', 'открытие магазин', 'разместить магазин', 'разместить точку', 'ставк', 'арендн плат', 'стоимость аренд', 'кв.м', 'кв. м', 'квадратн', 'м2', 'м²', 'договор аренд', 'франшиз', 'франчайз', 'pop-up', 'поп-ап', 'попап', 'поп ап', 'ритейл', 'ритейлер', 'сеть магазинов', 'якорн', 'свободные площади', 'свободное помещение', 'брокер', 'девелопер', 'ijara', 'ijarachi', 'ijaraga', "do'kon ochish", 'do‘kon ochish', 'dokon ochish', 'savdo joyi', 'savdo maydon', 'savdo nuqta', 'kvadrat', 'franshiza', 'franchayzing', 'butik', 'korner', 'ижара', 'ижарачи', 'дўкон очиш', 'дукон очиш', 'савдо жойи', 'савдо майдон', 'савдо нуқта', 'франшиза', 'франчайзинг', 'rent', 'rental', 'lease', 'leasing', 'tenant', 'landlord', 'premises', 'retail space', 'commercial space', 'space for', 'square meter', 'square metre', 'sqm', 'm2', 'kiosk', 'franchise', 'franchising', 'store opening', 'open a store', 'open a shop', 'open a cafe', 'pop-up store', 'pop-up shop', 'anchor tenant', 'retail unit', 'shop unit', 'торговая площад', 'торговую площад', 'торговых площад', 'торговые площад', 'площадь под', 'площади под', 'площадку под', 'свободную площадь', 'площадь в аренду', 'вакантные площади', 'вакантное помещение', 'vacant space', 'vacant unit', 'шоурум', 'снять помещение', 'сдаете в аренду', 'сдаёте в аренду', 'условия размещения', 'разместить бренд', 'открыть бутик', 'открыть салон', 'открыть филиал', 'открыть шоурум', 'открыть островок', 'открыть фудкорт', 'место на фудкорте', 'коммерческая недвижимость', 'коммерческую недвижимость', 'торговый остров', 'вендинг', 'установить банкомат', 'установить автомат', 'установить терминал', 'отдел аренды', 'выход на рынок узбекистана', 'storefront', 'shop space', 'store space', 'food court space', 'vending', 'open a boutique', 'open a restaurant', 'open a branch', 'open a showroom', 'open an outlet', 'commercial property', 'commercial real estate', 'available unit', 'sublease', 'sublet', 'leasing department', 'leasing manager', 'leasing team', 'brand expansion', 'expand our brand', 'enter the uzbekistan market']},
    {"key": "marketing", "name": "Маркетинг", "words": ['маркетинг', 'маркетолог', 'реклам', 'пиар', 'pr-', 'pr ', 'smm', 'таргет', 'продвижени', 'продвинуть', 'мероприяти', 'мероприят', 'ивент', 'эвент', 'промоакци', 'промо', 'промоутер', 'розыгрыш', 'конкурс', 'призов', 'дегустаци', 'сэмплинг', 'семплинг', 'раздача', 'флаер', 'листовк', 'фотозон', 'фотосесси', 'фотосъемк', 'фотосъёмк', 'видеосъемк', 'видеосъёмк', 'съемк', 'съёмк', 'снять ролик', 'клип', 'блогер', 'блоггер', 'инфлюенсер', 'амбассадор', 'коллаборац', 'спонсор', 'выставк', 'экспозици', 'ярмарк', 'маркет выходного', 'фестивал', 'концерт', 'выступлени', 'мастер-класс', 'мастер класс', 'флешмоб', 'новогодн', 'оформление к празднику', 'брендинг', 'брендирован', 'бренд-зон', 'баннер', 'билборд', 'лайтбокс', 'медиафасад', 'led-экран', 'led экран', 'соцсет', 'инстаграм', 'телеграм-канал', 'тикток', 'пресс-релиз', 'интервью', 'сми', 'журналист', 'анонс', 'программа лояльности', 'лояльност', 'презентация бренда', 'запуск бренда', 'открытие коллекции', 'marketing', 'reklama', 'tadbir', 'aksiya', 'tanlov', 'konkurs', 'festival', 'konsert', "ko'rgazma", 'ko‘rgazma', 'korgazma', 'yarmarka', 'homiy', 'homiylik', 'bloger', "targ'ibot", 'fotosessiya', 'suratga olish', 'brend', 'реклама', 'тадбир', 'акция', 'танлов', 'кўргазма', 'ярмарка', 'ҳомий', 'хомий', 'тарғибот', 'таргибот', 'advertis', 'advert', 'promo', 'promotion', 'campaign', 'event', 'sponsor', 'collab', 'influencer', 'blogger', 'brand activation', 'exhibition', 'festival', 'concert', 'giveaway', 'contest', 'photoshoot', 'photo shoot', 'filming', 'video shoot', 'billboard', 'banner', 'press release', 'social media', 'instagram', 'tiktok', 'smm', 'media partner', 'маркетингов', 'рекламн', 'праздничное мероприят', 'праздничную программ', 'шоу-программ', 'шоу программ', 'арт-объект', 'инсталляци', 'сотрудничество с блогер', 'рекламное сотрудничество', 'информационное партнерство', 'информационное партнёрство', 'media partnership', 'brand collaboration', 'co-branding', 'кобрендинг', 'акции', 'акцию', 'акцией', 'акциях', 'акциям', 'медиакит', 'медиа-кит', 'кросс-промо', 'кросс-маркетинг', 'пресс-служб', 'пресс-тур', 'пресс-конференц', 'репортаж', 'телеканал', 'съемочн', 'съёмочн', 'выступить', 'показ мод', 'модный показ', 'дефиле', 'кастинг', 'благотворительн', 'спецпроект', 'подкаст', 'отдел маркетинга', 'промостойк', 'промо-стойк', 'наружная реклама', 'разрешение на съемку', 'разрешение на съёмку', 'sponsorship', 'press inquiry', 'media inquiry', 'media kit', 'press kit', 'media interview', 'press interview', 'commercial shoot', 'film crew', 'filming permit', 'permission to film', 'permission to shoot', 'shoot a video', 'product launch', 'brand launch', 'launch event', 'sampling', 'tasting', 'flyer', 'leaflet', 'roadshow', 'road show', 'workshop', 'master class', 'masterclass', 'flash mob', 'flashmob', 'fashion show', 'digital screen', 'led screen', 'outdoor advertising', 'ad placement', 'ad space', 'cross-promo', 'cross promotion', 'loyalty program', 'ambassador', 'charity', 'fundraising', 'public relations', 'telegram channel', 'content creator', 'marketing department', 'marketing team']},
    {"key": "hr", "name": "Вакансии", "words": [
        "работать у вас", "кадров", "отдел кадров", 
        "вакансия", "вакансии", "резюме", "трудоустрой", "на работу", "стажировк",
        "vacancy", "job", "resume", "internship", "ish o'rni", "rezyume", "ishga"]},
    {"key": "general", "name": "Общие вопросы", "words": [
        "режим работы", "часы работы", "во сколько", "где находится", "подарочн", "кино", "сеанс", "магазин", "экскурси", "детск",
        "opening hours", "gift card", "cinema", "where is", "ish vaqti", "soat", "sovg'a", "kinoteatr", "do'kon", "bolalar"]},
]

DEFAULT_PRIORITY = {
    "urgent_words": ["срочно", "срочный", "немедленно", "сегодня же", "травм", "скорая", "полиц",
                     "суд", "прокуратур", "штраф", "сми", "журналист", "телеканал", "пожар", "дым", "затопил", "нет света", "лифт застрял",
                     "urgent", "asap", "immediately", "emergency", "police", "injur", "ambulance", "lost child", "missing child", "press", "journalist",
                     "shoshilinch", "zudlik", "tezda", "bola yo'qol", "bolam yo'qol", "yo'qolgan bola", "militsiya", "politsiya", "jarohat", "tez yordam",
                     "потерялся ребенок", "потерялся ребёнок", "пропал ребенок", "пропал ребёнок", "ребенок потерялся", "ребёнок потерялся",
                     "бола йўқол", "болам йўқол", "йўқолган бола"],
    "low_words": ["коммерческое предложение", "прайс", "прайс-лист", "предлагаем сотрудничество", "наша компания предлагает", "рассылк", "вебинар", "скидк",
                  "price list", "commercial offer", "we offer", "our company offers", "newsletter", "webinar", "discount",
                  "narxlar", "taklif qilamiz", "hamkorlik taklifi"],
    "vip_senders": [],
    "urgent_categories": ["complaint", "security"],
    "low_categories": ["hr"],
    "sla": {"1": 15, "2": 60, "3": 240},
    "names": {"1": "Срочно", "2": "Обычно", "3": "Низкий"},
}

# Универсальный автоответ (вариант 2, утверждён Олегом 14.09.2026) — один текст для всех
# направлений, чтобы письмо с неверно определённым направлением не получило ответ не по теме.
# Строка с {phone} автоматически убирается, если телефон ресепшн не задан.
_UNIVERSAL = {
    "ru": "Здравствуйте!\n\nВаше письмо получено и передано ответственному сотруднику. Мы ответим вам в ближайшее время.\n"
          "Если вопрос срочный, позвоните на ресепшн: {phone}.\n\nС уважением,\nАдминистрация Tashkent City Mall",
    "uz": "Assalomu alaykum!\n\nXatingiz qabul qilindi va mas'ul xodimga uzatildi. Yaqin vaqt ichida sizga javob beramiz.\n"
          "Agar masala shoshilinch bo'lsa, resepshnga qo'ng'iroq qiling: {phone}.\n\nHurmat bilan,\nTashkent City Mall ma'muriyati",
    "en": "Hello!\n\nYour message has been received and passed to the responsible staff member. We will reply shortly.\n"
          "If the matter is urgent, please call the reception desk: {phone}.\n\nKind regards,\nTashkent City Mall Administration",
}
DEFAULT_TEMPLATES = {k: dict(_UNIVERSAL) for k in ("general", "lost", "parking", "rent", "marketing", "complaint", "hr")}
DEFAULT_TEMPLATES["security"] = {"ru": "", "uz": "", "en": ""}
DEFAULT_AUTO_SUBJECT = {"ru": "Ваше обращение получено: ", "uz": "Murojaatingiz qabul qilindi: ", "en": "We received your message: "}

DEFAULTS = {
    "mode": "demo",                 # demo | outlook
    "mailbox_address": "",          # пусто = ящик по умолчанию в Outlook; иначе адрес общего ящика
    "port": 8791,
    "poll_seconds": 20,
    "lookback_days": 7,
    "count_from": "",               # «учитывать письма начиная с» (ГГГГ-ММ-ДДTЧЧ:ММ); более ранние игнорируются полностью
    "sla_warn_minutes": 30,
    "sla_overdue_minutes": 60,
    "repeat_alert_minutes": 10,
    "shifts": [
        {"name": "Смена 1", "start": "09:00", "end": "17:00"},
        {"name": "Смена 2", "start": "16:00", "end": "24:00"},
    ],
    "manager_email": "",
    "report_cc": "",
    "security_email": "",
    "security_phone": "",
    "telegram_bot_token": "",
    "telegram_chat_id": "",
    "own_addresses": [],
    "ignore_senders": ["noreply", "no-reply", "donotreply", "do-not-reply", "mailer-daemon",
                       "postmaster", "notification", "notifications", "newsletter", "bounce"],
    "ignore_subjects": ["Автоматический ответ", "Automatic reply", "Undeliverable",
                        "Не доставлено", "Delivery Status", "Out of office", "Нет на месте"],
    "threat_words": DEFAULT_THREAT_WORDS,
    "threat_exceptions": threat_defaults.EXCEPTIONS,
    "threat_soft_words": threat_defaults.SOFT,
    "threat_place_words": threat_defaults.PLACE,
    "threat_time_words": threat_defaults.TIME,
    "threat_polite_words": threat_defaults.POLITE,
    "sound": True,
    "toast": True,
    "words_removed": {},             # стандартные слова направлений, которые администратор удалил (не возвращать)
    "threat_removed": {},            # стандартные слова словарей угроз, которые администратор удалил (не возвращать)
    "admin_pin": "0000",            # PIN для страницы «Настройки», журнала и остановки программы
    "autostart": True,              # запускать программу при входе в Windows (HKCU Run)
    "categories": DEFAULT_CATEGORIES,
    "priority": DEFAULT_PRIORITY,
    "auto_reply_enabled": True,
    "auto_reply_templates": DEFAULT_TEMPLATES,
    "auto_reply_subject": DEFAULT_AUTO_SUBJECT,
    "reception_phone": "",
    "auto_reply_skip_own_domain": True,   # письма коллег @свой-домен без автоответа (ответ отдела «всем»)
    "auto_reply_backlog_hours": 16,   # при запуске отвечать/пересылать письма, пришедшие за последние N часов
    "staff": [],                          # сотрудники ресепшн (Настройки → Смены)
    "forward_removes_from_queue": True,   # пересланное в отдел письмо снимается с очереди ресепшн
    # второй фильтр — ИИ (Claude API): проверяет направление письма и угрозу после словаря
    "ai_enabled": False,
    "ai_api_key": "",                     # ключ Claude API (console.anthropic.com)
    "ai_model": "claude-opus-5-5",
    "ai_effort": "low",                   # глубина разбора: low | medium | high
    "ai_hide_personal": True,             # перед отправкой в ИИ заменять личные данные метками
    "ai_wait_seconds": 180,               # сколько ждать ответа ИИ, прежде чем обработать письмо по словарю
    "ai_final_threat": True,              # ИИ — заключительный фильтр по угрозам: после перепроверки может снять тревогу словаря
    "ai_threat_wait_seconds": 30,         # сколько секунд тревога словаря ждёт решения ИИ (потом — тревога по словарю)
    "forward_note": "Переслано с почты ресепшн Tashkent City Mall. Направление: {category}. Просьба ответить отправителю напрямую.",
}

_lock = threading.Lock()
_cfg = None

def load():
    global _cfg
    with _lock:
        cfg = copy.deepcopy(DEFAULTS)
        if os.path.exists(CONFIG_PATH):
            try:
                with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                    user = json.load(f)
                for k, v in user.items():
                    cfg[k] = v
                _sanitize(cfg)
                _merge_defaults(cfg)
            except Exception as e:
                try:
                    print("config.json не прочитан:", e)
                except Exception:
                    pass
                cfg = copy.deepcopy(DEFAULTS)
                try:                       # битый файл откладываем, чтобы было видно, что настройки слетели
                    os.replace(CONFIG_PATH, CONFIG_PATH + ".broken")
                    cfg["_config_error"] = "config.json повреждён и переименован в config.json.broken: %s" % str(e)[:120]
                except Exception:
                    pass
                # без настроек программа ушла бы в демо-режим и перестала читать почту — берём ежедневную копию
                user, name = _from_backup()
                if user is not None:
                    cfg = copy.deepcopy(DEFAULTS)
                    cfg.update(user)
                    _sanitize(cfg)
                    _merge_defaults(cfg)
                    cfg["_config_error"] = ("config.json был повреждён (отложен как config.json.broken); настройки "
                                            "восстановлены из копии %s — проверьте Настройки" % name)
                    try:
                        _write(cfg)
                    except Exception:
                        pass
        _cfg = cfg
        return cfg

def _from_backup():
    """Последняя читаемая ежедневная копия настроек (data/backup/config-ГГГГ-ММ-ДД.json) или (None, "")."""
    folder = os.path.join(os.path.dirname(CONFIG_PATH), "data", "backup")
    try:
        names = sorted((f for f in os.listdir(folder) if f.startswith("config-") and f.endswith(".json")), reverse=True)
    except Exception:
        return None, ""
    for f in names:
        try:
            with open(os.path.join(folder, f), "r", encoding="utf-8") as fh:
                user = json.load(fh)
            if isinstance(user, dict) and user:
                return user, f
        except Exception:
            continue
    return None, ""

def _write(cfg):
    """Запись через временный файл: при выключении компьютера посреди сохранения config.json остаётся целым
    (обрезанный файл = настройки сброшены, программа в демо-режиме)."""
    tmp = CONFIG_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({k: v for k, v in cfg.items() if not k.startswith("_")}, f, ensure_ascii=False, indent=2)
        f.flush()
        try:
            os.fsync(f.fileno())
        except Exception:
            pass
    os.replace(tmp, CONFIG_PATH)

_INT_KEYS = {"port": (1, 65535), "poll_seconds": (5, 3600), "lookback_days": (1, 60), "repeat_alert_minutes": (1, 1440),
             "sla_warn_minutes": (1, 100000), "sla_overdue_minutes": (1, 100000), "auto_reply_backlog_hours": (1, 720),
             "ai_wait_seconds": (30, 3600), "ai_threat_wait_seconds": (5, 120)}

def _sanitize(cfg):
    """Значения не того вида (ручная правка config.json: «20 сек», пусто, строка вместо списка) заменяются
    стандартными — иначе программа спотыкалась бы на каждом опросе или показывала пустой экран."""
    try:
        _sanitize_raw(cfg)
    except Exception:
        pass                                # проверка не должна сама стать причиной сброса настроек
    return cfg

def _sanitize_raw(cfg):
    for k, d in DEFAULTS.items():
        v = cfg.get(k)
        if isinstance(d, list) and not isinstance(v, list):
            cfg[k] = [v] if isinstance(v, str) and v.strip() else copy.deepcopy(d)
        elif isinstance(d, dict) and not isinstance(v, dict):
            cfg[k] = copy.deepcopy(d)
        elif isinstance(d, str) and not isinstance(v, str):
            cfg[k] = "" if v is None else str(v)
    for k, (lo, hi) in _INT_KEYS.items():
        try:
            cfg[k] = min(hi, max(lo, int(cfg.get(k))))
        except Exception:
            cfg[k] = DEFAULTS[k]
    cfg["categories"] = [c for c in cfg["categories"] if isinstance(c, dict) and c.get("key")] or copy.deepcopy(DEFAULT_CATEGORIES)
    for c in cfg["categories"]:
        if not isinstance(c.get("words"), list):
            c["words"] = []
    cfg["shifts"] = [s for s in cfg["shifts"] if isinstance(s, dict)]
    pr = cfg["priority"]
    for k, d in DEFAULT_PRIORITY.items():
        if k in pr and not isinstance(pr[k], type(d)):
            pr[k] = copy.deepcopy(d)
    for p, v in list((pr.get("sla") or {}).items()):
        try:
            pr["sla"][p] = max(1, int(v))
        except Exception:
            pr["sla"][p] = DEFAULT_PRIORITY["sla"].get(p, 60)
    for k in ("auto_reply_templates", "auto_reply_subject"):
        for kk, vv in list(cfg[k].items()):
            if k == "auto_reply_templates" and not isinstance(vv, dict):
                cfg[k][kk] = {}
            elif k == "auto_reply_subject" and not isinstance(vv, str):
                cfg[k][kk] = ""

_REMOVED_WORDS = {'rent': ['коммерческ', 'промо-акци', 'свободн площад', 'площадк', 'площадь', 'площад', 'вакантн', 'vacant'], 'hr': ['hr'], 'marketing': ['маркетингу', 'событи', 'праздник', 'декор', 'афиш', 'сотрудничеств', 'партнерств', 'партнёрств', 'hamkorlik', 'ҳамкорлик', 'хамкорлик', 'partnership', 'collaboration', 'activation', 'шоу']}   # слова прежних версий, дававшие ложные срабатывания

_THREAT_KEYS = ("threat_words", "threat_exceptions", "threat_soft_words", "threat_place_words",
                "threat_time_words", "threat_polite_words")

def _merge_threats(cfg):
    """Словари угроз: новые слова из обновлений программы дописываются к сохранённым (кроме удалённых
    администратором); запись, у которой при сохранении потерялся пробел в конце («тир» вместо «тир »), — как в
    программе: без пробела «тир» гасил тревогу рядом с «тираже», «завтра в» ловил «завтра вечером»."""
    rem = cfg.get("threat_removed") or {}
    for k in _THREAT_KEYS:
        cur = cfg.get(k)
        if not isinstance(cur, list):
            continue
        dflt = DEFAULTS.get(k) or []
        for w in dflt:
            if w != w.rstrip() and w not in cur and w.rstrip() in cur:
                cur[cur.index(w.rstrip())] = w
        skip = set(rem.get(k) or [])
        for w in dflt:
            if w not in cur and w not in skip:
                cur.append(w)

def _merge_defaults(cfg):
    """Новые слова из обновлений программы дописываются к сохранённым направлениям и словарям угроз,
    ничего из того, что добавил пользователь, не удаляется."""
    try:
        _merge_threats(cfg)
    except Exception:
        pass
    try:
        by_key = {d["key"]: d for d in DEFAULT_CATEGORIES}
        have = {c.get("key") for c in cfg.get("categories") or []}
        for c in cfg.get("categories") or []:
            d = by_key.get(c.get("key"))
            if d:
                cur = c.setdefault("words", [])
                for w in _REMOVED_WORDS.get(c.get("key"), []):
                    if w in cur and w not in d["words"]:
                        cur.remove(w)
                skip = set((cfg.get("words_removed") or {}).get(c.get("key")) or [])
                for w in d["words"]:
                    if w not in cur and w not in skip:
                        cur.append(w)
        for k, d in by_key.items():
            if k not in have:
                cfg.setdefault("categories", []).append(copy.deepcopy(d))
    except Exception:
        pass

def get():
    return _cfg if _cfg is not None else load()

def save(new_values: dict):
    global _cfg
    with _lock:
        cfg = _cfg or copy.deepcopy(DEFAULTS)
        for k, v in new_values.items():
            if k in DEFAULTS:
                cfg[k] = v
        if isinstance(new_values.get("categories"), list):
            # какие стандартные слова администратор убрал — запомнить, чтобы обновление их не вернуло
            rem = dict(cfg.get("words_removed") or {})
            by_key = {d["key"]: d for d in DEFAULT_CATEGORIES}
            for c in new_values["categories"]:
                d = by_key.get((c or {}).get("key"))
                if d:
                    cur = set(c.get("words") or [])
                    rem[d["key"]] = [w for w in d["words"] if w not in cur]
            cfg["words_removed"] = rem
        trem = dict(cfg.get("threat_removed") or {})
        for k in _THREAT_KEYS:
            if isinstance(new_values.get(k), list):
                # какие стандартные слова угроз администратор убрал — обновление их не вернёт
                have = {(w or "").rstrip() for w in new_values[k]}
                trem[k] = [w for w in DEFAULTS.get(k) or [] if w.rstrip() not in have]
        cfg["threat_removed"] = trem
        cfg.pop("_config_error", None)
        _sanitize(cfg)
        _write(cfg)
        _cfg = cfg
        return cfg

def public():
    """Настройки для интерфейса (секретов в конфиге нет)."""
    return copy.deepcopy(get())
