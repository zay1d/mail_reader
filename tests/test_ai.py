# -*- coding: utf-8 -*-
"""Второй фильтр (ИИ): направление — двойная проверка со словарём, угрозы — ИИ может только поднять тревогу.
Обращений к Claude в тестах нет: ответы ИИ задаются в тесте (helpers.FakeAI)."""
import threading, time, unittest
import helpers
from helpers import make_engine, cleanup, set_forward, FakeAI, ai_wait
import ai, pii


class AICase(unittest.TestCase):
    overrides = {"security_email": "sb@tcmall.uz"}

    def setUp(self):
        self.eng, self.src, self.tmp = make_engine(self.overrides)
        cats = set_forward(self.eng.cfg["categories"], "rent", "rent@dept.uz")
        cats = set_forward(cats, "marketing", "marketing@dept.uz")
        self.eng.apply_settings({"categories": cats, "ai_enabled": True, "ai_api_key": "test-key"})
        self.ai = self.eng.ai = FakeAI()

    def tearDown(self):
        self.eng.stop = True
        cleanup(self.eng, self.tmp)

    def rec(self, m):
        return self.eng.store.get(m["id"])

    def queue_ids(self):
        return [m["id"] for m in self.eng.store.queue(True)]

    def journal(self):
        return "\n".join(self.eng.log)


class Direction(AICase):
    def test_same_opinion_letter_goes_to_department(self):
        m = self.src.add("client@mail.ru", "Аренда", "Хотим взять в аренду помещение под бутик, 40 кв.м.")
        self.ai.answers["Аренда"] = {"category": "rent"}
        self.eng.poll()
        self.assertEqual(self.rec(m)["ai_state"], "pending")
        ai_wait(self.eng)
        r = self.rec(m)
        self.assertEqual((r["ai_state"], r["category"], r["forwarded_to"]), ("done", "rent", "rent@dept.uz"))
        self.assertEqual(len(self.src.replies), 1)
        self.assertFalse(r["ai_note"])
        ai_wait(self.eng, polls=2)
        self.assertEqual((len(self.src.forwards), len(self.src.replies)), (1, 1))      # повторно ничего не уходит

    def test_nothing_is_sent_before_ai_answers(self):
        gate = threading.Event()
        orig = self.ai.analyze
        self.ai.analyze = lambda text: (gate.wait(5), orig(text))[1]
        self.src.add("client@mail.ru", "Аренда", "Хотим взять в аренду помещение под бутик.")
        self.ai.answers["Аренда"] = {"category": "rent"}
        self.eng.poll(); self.eng.poll()
        self.assertEqual((self.src.forwards, self.src.replies), ([], []))             # ждём ИИ
        gate.set()
        ai_wait(self.eng)
        self.assertEqual((len(self.src.forwards), len(self.src.replies)), (1, 1))

    def test_ai_corrects_dictionary_after_recheck(self):
        # словарь не нашёл слов (general), ИИ понял по смыслу — аренда; после перепроверки уверен
        m = self.src.add("client@mail.ru", "Запрос", "Мы сеть кофеен, хотим узнать, есть ли у вас свободные места на первом этаже.")
        self.ai.answers["Запрос"] = {"category": "rent", "keywords": ["сеть кофеен", "свободные места", "+998 90"]}
        self.ai.rechecks["Запрос"] = {"category": "rent", "confidence": "high", "reason": "ищут место под кофейню"}
        self.eng.poll(); ai_wait(self.eng)
        r = self.rec(m)
        self.assertEqual((r["category"], r["rule_category"], r["forwarded_to"]), ("rent", "general", "rent@dept.uz"))
        self.assertIn("вместо «Общие вопросы» по словарю", r["ai_note"])
        self.assertIsNone(r["cat_conflict"])
        words = {s["word"] for s in self.eng.ai_suggestions()}                         # слова предложены в словарь
        self.assertEqual(words, {"сеть кофеен", "свободные места"})

    def test_unsure_ai_leaves_choice_to_staff(self):
        m = self.src.add("client@mail.ru", "Вопрос", "Хотим поставить стойку в атриуме на выходные.")
        self.ai.answers["Вопрос"] = {"category": "marketing", "confidence": "medium"}
        self.ai.rechecks["Вопрос"] = {"category": "marketing", "confidence": "medium", "reason": "может быть и аренда"}
        self.eng.poll(); ai_wait(self.eng)
        r = self.rec(m)
        self.assertEqual((r["category"], r["cat_conflict"]), ("general", "marketing"))
        self.assertEqual(self.src.forwards, [])                                        # в отдел — только после выбора
        self.assertEqual(len(self.src.replies), 1)                                     # автоответ клиенту уходит сразу
        self.assertEqual(self.queue_ids(), [m["id"]])
        pub = [x for x in self.eng.state()["queue"] if x["id"] == m["id"]][0]
        self.assertEqual(pub["cat_conflict_name"], "Маркетинг")
        self.assertTrue(self.eng.set_category(m["id"], "marketing"))
        self.eng.poll()
        r = self.rec(m)
        self.assertEqual((r["category"], r["cat_conflict"], r["forwarded_to"]), ("marketing", None, "marketing@dept.uz"))
        self.assertEqual(len(self.src.replies), 1)

    def test_staff_can_keep_dictionary_direction(self):
        m = self.src.add("client@mail.ru", "Вопрос", "Хотим поставить стойку в атриуме на выходные.")
        self.ai.answers["Вопрос"] = {"category": "marketing", "confidence": "low"}
        self.ai.rechecks["Вопрос"] = {"category": "marketing", "confidence": "low"}
        self.eng.poll(); ai_wait(self.eng)
        self.eng.set_category(m["id"], "general")
        self.eng.poll()
        self.assertEqual(self.src.forwards, [])
        self.assertEqual(self.queue_ids(), [m["id"]])
        self.assertFalse(self.eng.set_category(m["id"], "нет такого"))

    def test_unsure_but_forwarding_is_the_same_takes_ai_direction(self):
        # оба направления остаются на ресепшн (пересылка не настроена) — спрашивать сотрудника незачем
        m = self.src.add("client@mail.ru", "Вопрос", "Вчера оставил у вас зонт, кажется.")
        self.ai.answers["Вопрос"] = {"category": "lost", "confidence": "medium"}
        self.ai.rechecks["Вопрос"] = {"category": "lost", "confidence": "medium"}
        self.eng.poll(); ai_wait(self.eng)
        r = self.rec(m)
        self.assertEqual((r["category"], r["cat_conflict"]), ("lost", None))

    def test_ai_agrees_with_dictionary_after_recheck(self):
        m = self.src.add("client@mail.ru", "Аренда", "Хотим взять в аренду помещение под бутик.")
        self.ai.answers["Аренда"] = {"category": "marketing"}
        self.ai.rechecks["Аренда"] = {"category": "rent", "confidence": "high"}
        self.eng.poll(); ai_wait(self.eng)
        r = self.rec(m)
        self.assertEqual((r["category"], r["forwarded_to"]), ("rent", "rent@dept.uz"))
        self.assertIn("согласился со словарём", r["ai_note"])

    def test_priority_follows_final_direction(self):
        m = self.src.add("client@mail.ru", "Вчерашний визит", "Эскалатор стоял, столы никто не убирал. Больше не придём.")
        self.ai.answers["Вчерашний визит"] = {"category": "complaint"}
        self.eng.poll(); ai_wait(self.eng)
        r = self.rec(m)
        self.assertEqual((r["category"], r["priority"]), ("complaint", 1))             # жалоба — срочно


class Failures(AICase):
    def test_ai_error_falls_back_to_dictionary(self):
        m = self.src.add("client@mail.ru", "Аренда", "Хотим взять в аренду помещение под бутик.")
        self.ai.answers["Аренда"] = ai.AIError("network", "нет связи с Claude API")
        self.eng.poll(); ai_wait(self.eng)
        r = self.rec(m)
        self.assertEqual((r["ai_state"], r["category"], r["forwarded_to"]), ("failed", "rent", "rent@dept.uz"))
        self.assertEqual(len(self.src.replies), 1)
        self.assertIn("обработано по словарю", self.journal())

    def test_after_three_errors_ai_is_paused_and_dictionary_works_alone(self):
        for i in range(3):
            self.src.add("c%d@mail.ru" % i, "Письмо %d" % i, "Текст")
            self.ai.answers["Письмо %d" % i] = ai.AIError("server", "сбой Claude API (529)")
        self.eng.poll(); ai_wait(self.eng)
        self.assertTrue(self.eng._ai_paused())
        self.assertEqual(self.eng.ai_status()["state"], "error")
        n = len(self.ai.sent)
        m = self.src.add("late@mail.ru", "Позже", "Текст")
        self.eng.poll()
        self.assertEqual(len(self.ai.sent), n)                                         # в ИИ не отправлено
        self.assertIsNone(self.rec(m)["ai_state"])
        self.assertIn("late@mail.ru", [r["to"] for r in self.src.replies])            # автоответ ушёл сразу

    def test_bad_key_pauses_immediately(self):
        self.src.add("client@mail.ru", "Вопрос", "Текст")
        self.ai.answers["Вопрос"] = ai.AIError("key", "ключ Claude API не принят")
        self.eng.poll(); ai_wait(self.eng)
        self.assertTrue(self.eng._ai_paused())
        self.assertEqual(len(self.src.replies), 1)

    def test_no_answer_in_time_falls_back_to_dictionary(self):
        gate = threading.Event()
        orig = self.ai.analyze
        self.ai.analyze = lambda text: (gate.wait(10), orig(text))[1]
        m = self.src.add("client@mail.ru", "Аренда", "Хотим взять в аренду помещение под бутик.")
        self.ai.answers["Аренда"] = {"category": "marketing"}
        self.eng.poll()
        self.eng.ai_jobs[m["id"]]["t0"] -= 10000                                       # прошло больше отведённого времени
        self.eng.poll()
        r = self.rec(m)
        self.assertEqual((r["ai_state"], r["forwarded_to"]), ("failed", "rent@dept.uz"))
        gate.set()
        time.sleep(0.2); self.eng.poll()
        self.assertEqual(len(self.src.forwards), 1)                                    # опоздавший ответ ничего не меняет
        self.assertEqual(self.rec(m)["category"], "rent")

    def test_job_lost_on_restart_is_sent_again(self):
        m = self.src.add("client@mail.ru", "Аренда", "Хотим взять в аренду помещение под бутик.")
        self.ai.answers["Аренда"] = {"category": "rent"}
        self.eng.store.upsert_in(m, "", [], "k", "ru", "rent", 2, 0)
        self.eng.store.set_fields(m["id"], ai_state="pending", rule_category="rent")   # так было до перезапуска
        self.eng.poll(); ai_wait(self.eng)
        self.assertEqual(self.rec(m)["ai_state"], "done")
        self.assertEqual(len(self.src.forwards), 1)

    def test_ai_switched_off_program_works_as_before(self):
        self.ai.on = False
        m = self.src.add("client@mail.ru", "Аренда", "Хотим взять в аренду помещение под бутик.")
        self.eng.poll()
        self.assertEqual((self.rec(m)["ai_state"], self.rec(m)["forwarded_to"]), (None, "rent@dept.uz"))
        self.assertEqual(self.ai.sent, [])


class ThreatInsurance(AICase):
    """ИИ может только поднять тревогу."""
    def test_dictionary_calm_ai_sees_threat_yellow_screen(self):
        m = self.src.add("anon@example.com", "", "На третьем этаже оставлен рюкзак с сюрпризом. Скоро узнаете.")
        self.ai.answers[""] = {"category": "security", "threat": "explicit", "threat_reason": "сообщает об оставленном опасном предмете"}
        self.eng.poll()
        self.assertEqual(self.rec(m)["threat_level"], 0)
        ai_wait(self.eng); self.eng.check_threats()
        r = self.rec(m)
        self.assertEqual(r["threat_level"], 1)                                         # жёлтый: письмо читает сотрудник
        self.assertEqual(r["threat_words"], ["ИИ: сообщает об оставленном опасном предмете"])
        self.assertEqual(r["priority"], 1)
        self.assertEqual([t["id"] for t in self.eng.store.threats_pending()], [m["id"]])
        self.assertEqual(self.src.replies, [])                                         # автоответ не уходит
        self.assertEqual(self.src.mails, [])                                           # в СБ — после решения сотрудника
        self.eng.threat_escalate(m["id"]); self.eng.check_threats()
        self.assertEqual([x["to"] for x in self.src.mails], ["sb@tcmall.uz"])

    def test_staff_clears_ai_alarm_letter_is_processed(self):
        m = self.src.add("angry@example.com", "Отзыв", "Вы у меня ещё попляшете.")
        self.ai.answers["Отзыв"] = {"category": "complaint", "threat": "possible", "threat_reason": "запугивание без подробностей"}
        self.eng.poll(); ai_wait(self.eng)
        self.assertEqual(self.src.replies, [])
        self.eng.threat_clear(m["id"]); self.eng.poll()
        self.assertEqual(len(self.src.replies), 1)
        n = len(self.ai.sent)
        self.eng.poll()
        self.assertEqual(len(self.ai.sent), n)                                         # повторно в ИИ не уходит

    def test_dictionary_yellow_ai_explicit_becomes_red(self):
        m = self.src.add("anon@example.com", "Ждите", "Вы ещё пожалеете. Здание взлетит на воздух вместе со всеми.")
        self.ai.answers["Ждите"] = {"category": "security", "threat": "explicit", "threat_reason": "обещает уничтожить здание"}
        self.eng.poll()
        self.assertEqual(self.rec(m)["threat_level"], 1)
        ai_wait(self.eng); self.eng.check_threats()
        r = self.rec(m)
        self.assertEqual((r["threat_level"], r["category"]), (2, "security"))
        self.assertIn("ИИ: обещает уничтожить здание", r["threat_words"])
        self.assertIn("пожалеете", r["threat_words"])                                  # слова словаря сохранены
        self.assertEqual([x["to"] for x in self.src.mails], ["sb@tcmall.uz"])          # сигнал в СБ
        self.assertEqual(self.src.replies, [])

    def test_dictionary_yellow_ai_calm_stays_yellow_with_note(self):
        m = self.src.add("client@mail.ru", "Отзыв", "Вы ещё пожалеете, что не открыли у себя наш магазин!")
        self.ai.answers["Отзыв"] = {"category": "rent", "threat": "none", "threat_reason": "деловое предложение, не запугивание"}
        self.eng.poll(); ai_wait(self.eng)
        r = self.rec(m)
        self.assertEqual(r["threat_level"], 1)                                         # ИИ тревогу не снимает
        self.assertEqual(len(self.eng.store.threats_pending()), 1)
        self.assertIn("ИИ считает, что угрозы нет", r["ai_threat_note"])
        self.assertEqual(self.src.replies, [])

    def test_dictionary_red_is_not_sent_to_ai(self):
        m = self.src.add("anon@example.com", "", "В вашем торговом центре заложена бомба.")
        self.eng.poll(); self.eng.poll()
        self.assertEqual(self.rec(m)["threat_level"], 2)
        self.assertEqual(self.ai.sent, [])

    def test_model_refusal_shows_letter_to_staff(self):
        m = self.src.add("anon@example.com", "Письмо", "Странный текст.")
        self.ai.answers["Письмо"] = ai.AIError("refusal", "модель отказалась разбирать письмо")
        self.eng.poll(); ai_wait(self.eng)
        r = self.rec(m)
        self.assertEqual(r["threat_level"], 1)
        self.assertIn("ИИ отказался разбирать", r["threat_words"][0])
        self.assertFalse(self.eng._ai_paused())


class WhatIsSent(AICase):
    def test_newsletters_colleagues_and_old_letters_are_not_sent(self):
        self.src.add("noreply@shop.uz", "Скидки", "Только до воскресенья!")
        self.src.add("news@shop.uz", "Новости", "Рассылка", bulk=True)
        self.src.add("k.sabitova@tcmall.uz", "RE: аренда", "Коллеги, ответила клиенту.")
        self.src.add("old@mail.ru", "Старое", "Текст", minutes_ago=17 * 60)
        self.eng.poll(); self.eng.poll()
        self.assertEqual(self.ai.sent, [])

    def test_personal_data_is_hidden(self):
        self.src.add("alisher.k@mail.ru", "Машина", "Я Алишер Каримов. Поцарапали мою машину 01 A 777 BC на парковке. "
                     "Звоните +998 90 123-45-67, alisher.k@mail.ru, карта 8600 1234 5678 9012.", name="Алишер Каримов")
        self.ai.answers["Машина"] = {"category": "parking", "category_reason": "машину [ИМЯ_1] поцарапали"}
        self.eng.poll(); ai_wait(self.eng)
        sent = self.ai.sent[0]["text"]
        for secret in ("Алишер", "Каримов", "777", "123-45-67", "alisher.k@mail.ru", "8600"):
            self.assertNotIn(secret, sent)
        for tag in ("[ИМЯ_", "[АВТО_1]", "[ТЕЛ_1]", "[ПОЧТА_1]", "[КАРТА_1]"):
            self.assertIn(tag, sent)
        self.assertIn("парковке", sent)
        shown = self.eng.ai_sent()[0]
        self.assertEqual(shown["sent"], sent)
        self.assertEqual(shown["hidden"]["ТЕЛ"], 1)

    def test_accepting_suggestion_adds_word_to_dictionary(self):
        self.src.add("client@mail.ru", "Запрос", "Мы сеть кофеен, ищем свободные места на первом этаже.")
        self.ai.answers["Запрос"] = {"category": "rent", "keywords": ["сеть кофеен"]}
        self.eng.poll(); ai_wait(self.eng)
        s = self.eng.ai_suggestions()[0]
        self.assertEqual((s["word"], s["category_name"]), ("сеть кофеен", "Аренда"))
        self.assertTrue(self.eng.ai_suggestion(s["id"], "add"))
        rent = [c for c in self.eng.cfg["categories"] if c["key"] == "rent"][0]
        self.assertIn("сеть кофеен", rent["words"])
        self.assertEqual(self.eng.ai_suggestions(), [])
        import classify
        self.assertEqual(classify.categorize("Вопрос", "Мы сеть кофеен из Алматы.", self.eng.cfg["categories"], False), "rent")


class Masking(unittest.TestCase):
    def test_round_trip(self):
        m = pii.Masker(["Иван Петров"])
        t = "Я Иван Петров, тел. +998 90 123-45-67, паспорт AB1234567, ПИНФЛ 12345678901234. Сайт https://example.uz/a?b=1."
        x = m.mask(t)
        for secret in ("Иван", "Петров", "123-45-67", "AB1234567", "12345678901234", "example.uz"):
            self.assertNotIn(secret, x)
        self.assertEqual(m.unmask(x), t)

    def test_times_dates_and_sums_are_kept(self):
        m = pii.Masker()
        t = "Взрыв будет в 18:00 25.12.2026 на 3 этаже, ущерб 500 000 сум, бутик 214."
        self.assertEqual(m.mask(t), t)

    def test_organisation_name_is_not_masked(self):
        m = pii.Masker(["Event Agency Demo", "ООО «Ситимаркет»"])
        t = "Our event agency offers a demo for Ситимаркет."
        self.assertEqual(m.mask(t), t)

    def test_name_from_letter_text(self):
        m = pii.Masker()
        x = m.mask("Assalomu alaykum. Mening ismim Jamshid Tursunov. Jamshidning telefoni 901234567.")
        self.assertNotIn("Jamshid", x)
        self.assertNotIn("901234567", x)

    def test_long_line_is_fast(self):
        t0 = time.time()
        pii.Masker(["Иван Петров"]).mask("a.b.c.d." * 5000 + " 1 2 3 " * 3000)
        self.assertLess(time.time() - t0, 3.0)


class AnalyzerChecks(unittest.TestCase):
    """Ответ ИИ проверяется: неизвестное направление или уровень угрозы — ошибка, а не молчаливое принятие."""
    def make(self, answer):
        cfg = {"ai_enabled": True, "ai_api_key": "k", "categories": [{"key": "rent", "name": "Аренда"}, {"key": "general", "name": "Общие"}]}
        a = ai.Analyzer(lambda: cfg)
        a._ask = lambda system, user, schema, max_tokens=0: dict(answer)
        return a

    def good(self, **kw):
        return dict({"category": "rent", "confidence": "high", "category_reason": "r", "threat": "none", "threat_reason": "",
                     "keywords": ["a", "b", "c", "d"]}, **kw)

    def test_valid_answer(self):
        d = self.make(self.good()).analyze("x")
        self.assertEqual((d["category"], d["keywords"]), ("rent", ["a", "b", "c"]))

    def test_unknown_category_or_threat_is_error(self):
        for bad in ({"category": "finance"}, {"threat": "maybe"}, {"confidence": "100%"}):
            with self.assertRaises(ai.AIError):
                self.make(self.good(**bad)).analyze("x")

    def test_system_prompt_lists_directions_and_is_stable(self):
        a = self.make(self.good())
        self.assertIn('"rent" — Аренда', a._system())
        self.assertEqual(a._system(), a._system())                 # без дат и случайных значений: кэш запроса не сбивается

    def test_long_letter_is_cut_and_marked(self):
        text, _ = self.make(self.good()).prepare("Тема", "слово " * 5000)
        self.assertIn("[письмо длинное, показано начало]", text)
        self.assertLess(len(text), ai.MAX_CHARS + 200)



class SkipReasons(AICase):
    """Письмо не ушло в ИИ — причина видна в списке «что отправлялось» и в журнале."""
    def test_checkbox_off_is_explained(self):
        self.eng.apply_settings({"ai_enabled": False})
        self.ai.on = False
        self.src.add("client@mail.ru", "Аренда", "Хотим взять в аренду помещение под бутик.")
        self.eng.poll()
        item = self.eng.ai_sent()[0]
        self.assertTrue(item["skipped"])
        self.assertIn("галочка «Включить проверку писем ИИ»", item["result"])
        self.assertIn("в ИИ не отправлено", self.journal())
        self.assertEqual(len(self.src.replies), 1)                  # письмо обработано по словарю

    def test_colleague_letter_is_explained(self):
        self.src.add("k.sabitova@tcmall.uz", "Вопрос", "Коллеги, подскажите.")
        self.eng.poll()
        self.assertIn("коллеги своего домена", self.eng.ai_sent()[0]["result"])

    def test_no_ai_settings_no_noise(self):
        self.eng.apply_settings({"ai_enabled": False, "ai_api_key": ""})
        self.ai.on = False
        self.src.add("client@mail.ru", "Аренда", "Хотим взять в аренду помещение под бутик.")
        self.eng.poll()
        self.assertEqual(self.eng.ai_sent(), [])
        self.assertNotIn("в ИИ не отправлено", self.journal())

    def test_newsletter_is_not_listed(self):
        self.src.add("noreply@shop.uz", "Скидки", "Только до воскресенья!")
        self.eng.poll()
        self.assertEqual(self.eng.ai_sent(), [])

    def test_ai_test_warns_when_checking_is_off(self):
        self.eng.ai.test = lambda: "Связь с ИИ есть."
        self.eng.apply_settings({"ai_enabled": False})
        self.assertIn("ВЫКЛЮЧЕНА", self.eng.ai_test())
        self.eng.apply_settings({"ai_enabled": True})
        self.assertIn("включена", self.eng.ai_test())


if __name__ == "__main__":
    unittest.main()
