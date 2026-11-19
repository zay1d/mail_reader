# -*- coding: utf-8 -*-
"""Словари детектора угроз (значения по умолчанию, правятся в config.json / Настройках)."""

# --- Явная угроза (красный экран): регулярные выражения по нормализованному тексту ---
HARD = [
    # русский
    r"\bбомб(а|ы|у|е|ой|ах|ами|ам)\b", r"\bвзрыв(а|у|ом|е|ы|ов|ам)?\b", r"\bвзрывн\w* устройств",
    r"\bвзорв", r"\bвзрывчат", r"\bзаминир", r"\bминирован", r"\bмина заложен", r"\bзаложена мина",
    r"\bстрельб", r"\bстреля(ть|ю|ем|ет|ют)\b", r"\bзастрел", r"\bрасстрел", r"\bперестрел",
    r"\bтеракт", r"\bтеррор", r"\bзаложник", r"\bзахват(им|ить|или|ываем|ил|ит|ят|чик)",
    r"\bотрав(им|ить|или|лю|лен|ят|ит|ы)\b", r"\bубь(ю|ем|ёт|ет|ют)\b", r"\bубий(ств|ц)", r"\bубит(ь|ы|ый|ые)\b",
    r"\bподо?ж(ог|ечь|жем|гу|гем|ги|гут|жет)\b", r"\bсожг", r"\bсожж", r"\bгранат(а|ы|у|ой|ами)\b",   # подожгу / поджог
    r"\bтротил", r"\bдетонат", r"\bсмертник", r"\bшахид", r"\bджихад", r"\bоружи", r"\bпистолет", r"\bрезн(я|ю|и|ей)\b",
    r"\bвсе умр(ете|ёте|ут)\b", r"\bвы умр(ете|ёте)\b",
    # узбекский латиница
    r"\bbomba", r"\bportla", r"\bmina bor\b", r"\bmina qo['’`]?y", r"\bminalar\b", r"\bminala", r"\botish(ma)?\b", r"\bterror", r"\bterakt",
    r"\bgarovga", r"\bo['’`]?ldir", r"\buldir", r"\bzaharla", r"\bgranata", r"\bqurol",
    r"\bto['’`]?pponcha", r"\bhujum qil", r"\bshahid", r"\bhammangiz o['’`]?lasiz",
    r"\bo['’`]?t qo['’`]?y(aman|amiz|ib)", r"\byondirib yubor",          # поджог
    # узбекский кириллица
    r"\bпортла", r"\bотишма", r"\bгаровга", r"\bўлдир", r"\bулдир", r"\bзаҳарла", r"\bзахарла",
    r"\bқурол", r"\bкурол", r"\bҳужум қил", r"\bтўппонча", r"\bҳаммангиз ўласиз",
    r"\bўт қў(яман|ямиз|йиб|йаман|йамиз)", r"\b[её]ндириб юбор",          # поджог
    # английский
    r"\bbombs?\b", r"\bexplosi(on|ve)s?\b", r"\bexplode", r"\bdetonat", r"\bshooting\b", r"\bshooter\b",
    r"\bshoot (you|them|everyone|people|all)\b", r"\bguns?\b", r"\bgunman", r"\bhostage",
    r"\bkill (you|them|everyone|all|people)\b", r"\bpoison (the|you|your)\b", r"\bgrenade", r"\bmassacre",
    r"\bweapon", r"\bpistol", r"\brifle", r"\bjihad", r"\bterrorist", r"\bblow (it |you |them |everything )?up\b",
    r"\b(will|gonna|going to) (all )?die\b",
    r"\bburn (it |you |them |everything |the \w+ |your \w+ )?down\b", r"\bset (it |you |them |the \w+ |your \w+ )?on fire\b",
    r"\bset fire\b", r"\barson\b",                                          # поджог
]

# --- Исключения: если фраза стоит рядом с найденным словом, тревога не поднимается ---
EXCEPTIONS = [
    "взрывной рост", "взрывные продажи", "взрывной спрос", "взрыв продаж", "взрыв эмоций", "бомбическ",
    "убийственн", "гранатов", "мина-", "photo shoot", "photoshoot", "video shoot", "killer offer",
    "killer deal", "killer price", "die-cut", "hot shot", "bomb.com", "bath bomb", "explosion of flavour",
    "explosion of flavor", "sales explosion", "оружие массового", "тир ", "лазертаг", "пейнтбол", "airsoft",
    # сленг-похвала и реклама (v1.33): оборот гасит только «своё» слово — «Это бомба! … В здании бомба» тревогу поднимет
    "это бомба!", "просто бомба", "was the bomb", "cherry bomb", "взорвал зал", "взорвали зал", "взорвал танцпол",
    "взорвали танцпол", "взорвал интернет", "взорвали интернет", "взрыв вкуса", "взрыв скидок", "взрыв цен",
    "aksiya bombasi", "bomba aksiya", "bomba narx", "narxlar bomba", "bomba chegirma", "chegirmalar bomba",
    "акция бомбаси", "бомба нарх", "нархлар бомба", "бомба чегирма",
]

# --- Скрытая угроза (жёлтый экран): запугивание и намерение без явных слов ---
SOFT = [
    # русский
    "пожалеете", "пожалеешь", "найдем вас", "найдём вас", "найдем тебя", "найдём тебя", "мало не покажется",
    "последнее предупреждение", "будет плохо", "сдохн", "кровь прольется", "кровь прольётся", "отомст", "месть",
    "покараем", "накажем вас", "готовьтесь", "молитесь", "прощайтесь", "это конец", "ждите сюрприз", "сюрприз ждет",
    "сюрприз ждёт", "недолго осталось", "вам конец", "тикают часы", "обратный отсчет", "обратный отсчёт",
    "никто не выйдет", "никто не уйдет", "никто не уйдёт", "будете гореть", "сгорите", "похорон", "гроб",
    "вас найдем", "вас найдём", "тебя найдем", "тебя найдём", "знаем, где вы", "знаем где вы", "знаем, где ты", "знаем где ты",
    # узбекский латиница
    "pushaymon bo", "kutib turing", "topamiz sizni", "sizni topamiz", "qasos", "jazolaymiz", "o'lim", "olim kutmoqda",
    "oxirgi ogohlantirish", "hech kim chiqmaydi", "tayyor bo'ling", "tayyor boling", "seni topamiz",
    # узбекский кириллица
    "пушаймон бў", "кутиб туринг", "сизни топамиз", "қасос", "жазолаймиз", "ўлим", "охирги огоҳлантириш",
    "ҳеч ким чиқмайди", "тайёр бўлинг", "сени топамиз",
    # английский
    "you will regret", "you'll regret", "we will find you", "you will pay", "pay for it", "pay for this",
    "last warning", "final warning", "revenge", "you are dead", "you're dead", "wait and see", "say goodbye",
    "countdown", "nobody leaves", "no one leaves", "you will burn", "will suffer", "time is running out",
    "we know where you", "we'll find you",
]

# --- Признаки обычного делового письма: при них комбинация «место + время» НЕ считается угрозой ---
POLITE = [
    "здравствуйте", "добрый день", "доброе утро", "добрый вечер", "уважаем", "с уважением", "спасибо", "благодар",
    "пожалуйста", "подскажите", "просим", "прошу", "хотим", "хотели бы", "ждем вас", "ждём вас", "ваш заказ", "assalomu", "salom", "hurmat", "rahmat",
    "iltimos", "ассалому", "ҳурмат", "раҳмат", "илтимос", "hello", "dear", "good morning", "good afternoon",
    "kind regards", "best regards", "thank you", "thanks", "please", "could you", "would like",
    # приглашения на события (v1.33): «сегодня в 18:00 в ТРЦ… приглашаем» — не угроза
    "приглашаем", "приглашаю", "приглашени", "будем рады", "рады видеть", "рады вас", "welcome", "invite", "invitation",
    "taklif qilamiz", "taklif etamiz", "marhamat", "xursand bo'lamiz", "таклиф қиламиз", "марҳамат",
]

# --- Комбинация «место + время»: без явных слов, но с указанием объекта и срока → жёлтый экран ---
PLACE = [
    "торговый центр", "торговом центре", "торговый центр", "тц ", "трц", "ваше здание", "вашем здании", "в здании",
    "этаже", "паркинг", "парковк", "фудкорт", "кинотеатр", "посетител", "покупател", "молл",
    "savdo markaz", "binoda", "binongiz", "qavat", "avtoturargoh", "mall", "your building", "in the building",
    "floor", "food court", "visitors", "shoppers", "car park", "parking",
]
TIME = [
    "через час", "через полчаса", "через 30 минут", "через 2 часа", "через два часа", "через 15 минут",
    "сегодня в ", "сегодня вечером", "сегодня ночью", "завтра в ", "завтра утром", "ровно в ", "в полдень",
    "bir soatdan", "yarim soatdan", "bugun soat", "bugun kechqurun", "ertaga soat", "ertaga ertalab",
    "бир соатдан", "ярим соатдан", "бугун соат", "бугун кечқурун", "эртага соат",
    "in an hour", "within an hour", "in 30 minutes", "in two hours", "tonight", "tomorrow at", "today at", "at noon",
]
