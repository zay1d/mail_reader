# -*- coding: utf-8 -*-
"""Правила без почты: язык письма, направление, срочность, угрозы, сопоставление ответа с письмом."""
import unittest
import helpers                                   # noqa: F401  (настраивает путь к app/)
import json, os
import classify, threats, matcher, config

with open(os.path.join(helpers.ROOT, "config.json"), encoding="utf-8") as _f:
    CFG = json.load(_f)
for _k, _v in config.DEFAULTS.items():
    CFG.setdefault(_k, _v)
config._merge_defaults(CFG)          # как при запуске программы: новые стандартные слова дописываются к сохранённым
DET = threats.Detector(CFG)


def level(text):
    return DET.analyze(text)[0]


def cat(subject, body, is_threat=False):
    return classify.categorize(subject, body, CFG["categories"], is_threat)


class Language(unittest.TestCase):
    def test_russian(self):
        self.assertEqual(classify.detect_lang("Здравствуйте! Подскажите, до скольки вы работаете?", "Вопрос"), "ru")

    def test_uzbek_latin(self):
        self.assertEqual(classify.detect_lang("Assalomu alaykum. Bolalar maydonchasi qaysi soatlarda ishlaydi? Rahmat.", "Savol"), "uz")

    def test_uzbek_cyrillic(self):
        self.assertEqual(classify.detect_lang("Ассалому алайкум. Савдо маркази қачон очилади? Раҳмат, жавоб керак.", ""), "uz")

    def test_english(self):
        self.assertEqual(classify.detect_lang("Hello, I was charged twice for parking on Saturday.", "Parking"), "en")

    def test_russian_translit(self):
        self.assertEqual(classify.detect_lang("Zdravstvuyte, podskazhite pozhaluysta, kogda rabotaet katok?", ""), "ru")

    def test_quote_and_signature_do_not_change_language(self):
        body = ("Добрый день, когда будет ответ?\n\nС уважением,\nIvan Petrov\nSales Manager, Best Company LLC\n"
                "> On Monday John wrote:\n> Please find attached our offer")
        self.assertEqual(classify.detect_lang(body, "RE: Offer"), "ru")

    def test_empty_letter_is_russian(self):
        self.assertEqual(classify.detect_lang("", ""), "ru")

    def test_long_line_without_spaces_is_fast(self):
        import time
        t0 = time.time()
        classify.detect_lang("a.b.c.d." * 5000, "x")
        self.assertLess(time.time() - t0, 2.0)


class Category(unittest.TestCase):
    def test_keywords(self):
        self.assertEqual(cat("Потеряла кошелёк в фудкорте", "Вчера вечером потеряла кошелёк, коричневый."), "lost")
        self.assertEqual(cat("Parking fee question", "I was charged twice for parking."), "parking")
        self.assertEqual(cat("Резюме", "Отправляю резюме на вакансию администратора."), "hr")
        self.assertEqual(cat("Вопрос", "Хотим взять в аренду помещение под бутик, 40 кв.м."), "rent")
        self.assertEqual(cat("Вопрос", "Хотим провести рекламную акцию и розыгрыш призов."), "marketing")

    def test_department_named_in_subject_wins(self):
        self.assertEqual(cat("Для маркетинга", "Добрый день, по поводу аренды и парковки."), "marketing")
        self.assertEqual(cat("В отдел аренды", "Вопрос про рекламу."), "rent")

    def test_complaint_has_priority(self):
        self.assertEqual(cat("Жалоба", "Жалоба: на парковке нагрубил охранник."), "complaint")

    def test_no_keywords_is_general(self):
        self.assertEqual(cat("Вопрос", "Здравствуйте, хотел уточнить одну вещь."), "general")

    def test_threat_is_security(self):
        self.assertEqual(cat("Аренда", "текст", is_threat=True), "security")


class Priority(unittest.TestCase):
    def pri(self, subject, body, category="general", high=False, sender="a@b.c"):
        return classify.prioritize(subject, body, sender, high, CFG["priority"], category)

    def test_urgent_word(self):
        self.assertEqual(self.pri("Срочно!", "Нужен ответ"), 1)

    def test_outlook_high_importance(self):
        self.assertEqual(self.pri("Вопрос", "текст", high=True), 1)

    def test_complaint_is_urgent(self):
        self.assertEqual(self.pri("Отзыв", "текст", category="complaint"), 1)

    def test_commercial_offer_is_low(self):
        self.assertEqual(self.pri("Коммерческое предложение", "Наша компания предлагает прайс"), 3)

    def test_ordinary(self):
        self.assertEqual(self.pri("Вопрос", "До скольки работаете?"), 2)


class Threats(unittest.TestCase):
    def test_explicit_is_red(self):
        for t in ("В вашем торговом центре заложена бомба. Взорвётся через час.",
                  "Savdo markazida bomba bor. Hamma chiqib ketsin.",
                  "There is a gunman on the third floor, everyone will be shot.",
                  "I planted an explosive device in your mall."):
            self.assertEqual(level(t), 2, t)

    def test_disguised_is_red(self):
        for t in ("заложена б о м б а в тц", "заложена б.о.м.б.а", "в здании бoмба", "В центре 6омба"):
            self.assertEqual(level(t), 2, t)

    def test_possible_is_yellow(self):
        for t in ("Вы ещё пожалеете, что так со мной обошлись.",
                  "Мы вас всех найдём. Ждите сюрприз завтра в 15:00 в вашем здании.",
                  "Pushaymon bo'lasiz. Kutib turing."):
            self.assertEqual(level(t), 1, t)

    def test_ordinary_letters_are_calm(self):
        for t in ("Здравствуйте! Будет ли работать парковка завтра в 7 утра?",
                  "Наша новая акция — просто бомба! Скидки 50%.",
                  "Хотим заказать день рождения в лазертаге, стрельба по мишеням для 10 детей.",
                  "Есть ли в детском центре тир для подростков?",
                  "Потеряла кошелёк у фудкорта, подскажите, куда обратиться?",
                  "Взрывной рост продаж: предлагаем сотрудничество."):
            self.assertEqual(level(t), 0, t)

    def test_harmless_phrase_does_not_hide_real_threat(self):
        self.assertEqual(level("Ваша акция — просто бомба! А теперь серьёзно: в здании бомба, уходите."), 2)

    def test_bad_pattern_in_settings_does_not_crash(self):
        d = threats.Detector(dict(CFG, threat_words=["(незакрытая скобка", r"\bбомб"]))
        self.assertEqual(d.analyze("в здании бомба")[0], 2)


class Matching(unittest.TestCase):
    def c(self, **kw):
        base = {"id": "m1", "conv_id": "c1", "sender": "client@mail.ru", "subject": "Вопрос по аренде",
                "received_at": "2026-11-01T10:00:00", "client_email": "", "reply_self": 0}
        base.update(kw)
        return base

    def out(self, **kw):
        base = {"conv_id": "c1", "subject": "RE: Вопрос по аренде", "recipients": ["client@mail.ru"],
                "sent_at": "2026-11-01T10:30:00"}
        base.update(kw)
        return base

    def test_subject_prefixes(self):
        self.assertEqual(matcher.norm_subject("RE: FW: Отв: Вопрос  по аренде"), "вопрос по аренде")

    def test_reply_in_same_conversation(self):
        self.assertEqual(matcher.match_reply(self.out(), [self.c()]), "m1")

    def test_reply_by_subject_without_conversation(self):
        self.assertEqual(matcher.match_reply(self.out(conv_id=""), [self.c()]), "m1")

    def test_reply_sent_before_letter_does_not_count(self):
        self.assertIsNone(matcher.match_reply(self.out(sent_at="2026-11-01T09:00:00"), [self.c()]))

    def test_forward_to_department_is_not_an_answer(self):
        o = self.out(subject="FW: Вопрос по аренде", recipients=["rent@tcmall.uz"])
        self.assertIsNone(matcher.match_reply(o, [self.c()]))

    def test_site_form_needs_reply_to_client(self):
        form = self.c(sender="support@o-d.one", reply_self=1, client_email="anna@example.uz")
        own = {"contact@tcmall.uz"}
        self.assertIsNone(matcher.match_reply(self.out(recipients=["contact@tcmall.uz"]), [form], own))
        self.assertEqual(matcher.match_reply(self.out(recipients=["anna@example.uz"]), [form], own), "m1")

    def test_one_reply_closes_reminder_from_same_sender(self):
        first = self.c()
        second = self.c(id="m2", received_at="2026-11-01T10:10:00")
        self.assertEqual(sorted(matcher.match_reply_all(self.out(), [first, second])), ["m1", "m2"])

    def test_ignore_filter(self):
        self.assertTrue(matcher.is_ignored("noreply@shop.uz", "Shop", "Скидки", CFG))
        self.assertTrue(matcher.is_ignored("a@b.uz", "A", "Автоматический ответ: нет на месте", CFG))
        self.assertTrue(matcher.is_ignored("contact@tcmall.uz", "", "x", dict(CFG, own_addresses=["contact@tcmall.uz"])))
        self.assertEqual(matcher.is_ignored("client@mail.ru", "Клиент", "Вопрос", CFG), "")


class FormContact(unittest.TestCase):
    def test_client_address_from_form_text(self):
        body = "Имя: Тестова Анна\nEmail: anna.test@example.uz\nСообщение: Работает ли каток?"
        self.assertEqual(classify.form_contact(body, {"contact@tcmall.uz"}, "support@o-d.one"),
                         ("anna.test@example.uz", "Тестова Анна"))

    def test_own_address_is_not_client(self):
        body = "Email: contact@tcmall.uz\nСообщение: тест"
        self.assertEqual(classify.form_contact(body, {"contact@tcmall.uz"}, "support@o-d.one")[0], "")



class SlangPraise(unittest.TestCase):
    """«Мероприятие было бомба» — похвала (ложная красная тревога 19.11). Настоящие угрозы рядом — тревога."""
    def test_praise_is_calm(self):
        for t in ("Мероприятие было бомба!", "мероприятие было бомба, спасибо организаторам",
                  "Концерт был бомба", "Вчера всё было бомба, придём ещё", "Вчерашнее мероприятие было просто бомба"):
            self.assertEqual(level(t), 0, t)

    def test_update_adds_exception_to_saved_settings(self):
        with open(os.path.join(helpers.ROOT, "config.json"), encoding="utf-8") as f:
            saved = json.load(f)
        self.assertNotIn("было бомба", saved["threat_exceptions"])     # в настройках ПК ресепшн этого слова нет
        self.assertIn("было бомба", CFG["threat_exceptions"])          # программа допишет его при запуске

    def test_threats_still_raise_alarm(self):
        for t in ("В здании бомба.", "Мероприятие было бомба. А завтра в здании будет бомба.",
                  "Завтра на мероприятии будет бомба", "В вашем центре была бомба, и будет снова",
                  "Мероприятие — бомба заложена в зале", "Было бомбу заложить решено"):
            self.assertEqual(level(t), 2, t)


if __name__ == "__main__":
    unittest.main()
