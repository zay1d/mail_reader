# -*- coding: utf-8 -*-
"""Работа программы целиком на поддельной почте: очередь, автоответ, пересылка в отдел, угрозы, ответы."""
import unittest
import helpers
from helpers import make_engine, cleanup, set_forward
import config, shifts
from datetime import datetime, timedelta


class EngineCase(unittest.TestCase):
    overrides = None

    def setUp(self):
        self.eng, self.src, self.tmp = make_engine(self.overrides)

    def tearDown(self):
        cleanup(self.eng, self.tmp)

    def queue_ids(self):
        return [m["id"] for m in self.eng.store.queue(True)]

    def rec(self, m):
        return self.eng.store.get(m["id"])


class AutoReply(EngineCase):
    def test_new_letter_goes_to_queue_and_gets_auto_reply(self):
        m = self.src.add("client@mail.ru", "Вопрос", "Здравствуйте! До скольки вы работаете?")
        self.eng.poll()
        self.assertEqual(self.queue_ids(), [m["id"]])
        self.assertEqual(len(self.src.replies), 1)
        r = self.src.replies[0]
        self.assertEqual(r["to"], "client@mail.ru")
        self.assertTrue(r["subject"].startswith("Ваше обращение получено: "))
        self.assertIn("+998 00 000-00-00", r["text"])
        self.assertTrue(self.rec(m)["auto_replied_at"])

    def test_auto_reply_is_sent_once(self):
        self.src.add("client@mail.ru", "Вопрос", "Здравствуйте! До скольки вы работаете?")
        for _ in range(3):
            self.eng.poll()
        self.assertEqual(len(self.src.replies), 1)

    def test_auto_reply_language(self):
        self.src.add("a@example.com", "Savol", "Assalomu alaykum. Bolalar maydonchasi qaysi soatlarda ishlaydi? Rahmat.")
        self.src.add("b@example.com", "Question", "Hello, what are your opening hours on Sunday?")
        self.eng.poll()
        by_to = {r["to"]: r for r in self.src.replies}
        self.assertTrue(by_to["a@example.com"]["subject"].startswith("Murojaatingiz qabul qilindi: "))
        self.assertTrue(by_to["b@example.com"]["subject"].startswith("We received your message: "))

    def test_same_thread_from_same_sender_gets_one_auto_reply(self):
        self.src.add("client@mail.ru", "Вопрос по карте", "Можно ли купить подарочную карту?", minutes_ago=5)
        self.eng.poll()
        self.src.add("client@mail.ru", "RE: Вопрос по карте", "Напоминаю о своём вопросе.")
        self.eng.poll()
        self.assertEqual(len(self.src.replies), 1)
        self.assertEqual(len(self.queue_ids()), 2)          # оба письма ждут ответа

    def test_no_auto_reply_to_own_domain_and_no_forward(self):
        self.eng.apply_settings({"categories": set_forward(self.eng.cfg["categories"], "rent", "rent@dept.uz")})
        m = self.src.add("k.sabitova@tcmall.uz", "RE: аренда бутика", "Коллеги, ответила клиенту по аренде.")
        self.eng.poll()
        self.assertEqual(self.src.replies, [])
        self.assertEqual(self.src.forwards, [])
        self.assertEqual(self.queue_ids(), [m["id"]])

    def test_newsletters_and_auto_mail_are_hidden_without_reply(self):
        self.src.add("noreply@shop.uz", "Скидки недели", "Только до воскресенья!")
        self.src.add("news@shop.uz", "Новости", "Рассылка", bulk=True)
        self.src.add("x@y.uz", "Автоматический ответ: нет на месте", "Я в отпуске")
        self.eng.poll()
        self.assertEqual(self.queue_ids(), [])
        self.assertEqual(self.src.replies, [])

    def test_old_letters_get_no_auto_reply(self):
        m = self.src.add("client@mail.ru", "Вопрос", "До скольки вы работаете?", minutes_ago=17 * 60)
        self.eng.poll()
        self.assertEqual(self.queue_ids(), [m["id"]])       # в очереди есть
        self.assertEqual(self.src.replies, [])              # но автоответ не уходит: письму больше 16 часов

    def test_auto_reply_is_retried_when_outlook_is_busy(self):
        self.src.add("client@mail.ru", "Вопрос", "До скольки вы работаете?")
        self.src.fail_reply = 2
        self.eng.poll(); self.eng.poll()
        self.assertEqual(self.src.replies, [])
        self.eng.poll()
        self.assertEqual(len(self.src.replies), 1)

    def test_auto_reply_gives_up_after_three_failures(self):
        self.src.add("client@mail.ru", "Вопрос", "До скольки вы работаете?")
        self.src.fail_reply = 3
        for _ in range(6):
            self.eng.poll()
        self.assertEqual(self.src.replies, [])

    def test_no_more_than_five_auto_replies_per_hour_to_one_address(self):
        for i in range(8):
            self.src.add("robot@other.uz", "Тема %d" % i, "Текст письма номер %d" % i)
        self.eng.poll()
        self.assertEqual(len(self.src.replies), 5)

    def test_auto_reply_can_be_switched_off(self):
        self.eng.apply_settings({"auto_reply_enabled": False})
        self.src.add("client@mail.ru", "Вопрос", "До скольки вы работаете?")
        self.eng.poll()
        self.assertEqual(self.src.replies, [])

    def test_site_form_auto_reply_goes_to_client_address(self):
        body = "Имя: Тестова Анна\nEmail: anna.test@example.uz\nСообщение: Работает ли каток в выходные?"
        m = self.src.add("support@o-d.one", "Новая заявка от Тестова Анна", body, reply_to=["contact@tcmall.uz"])
        self.eng.poll()
        self.assertEqual([r["to"] for r in self.src.replies], ["anna.test@example.uz"])
        self.assertEqual(self.rec(m)["client_email"], "anna.test@example.uz")

    def test_site_form_without_client_address_gets_no_auto_reply(self):
        m = self.src.add("support@o-d.one", "Новая заявка", "Сообщение: позвоните мне", reply_to=["contact@tcmall.uz"])
        self.eng.poll()
        self.assertEqual(self.src.replies, [])
        self.assertEqual(self.queue_ids(), [m["id"]])


class Forwarding(EngineCase):
    def setUp(self):
        super().setUp()
        cats = set_forward(self.eng.cfg["categories"], "rent", "rent@dept.uz")
        cats = set_forward(cats, "marketing", "marketing@dept.uz")
        self.eng.apply_settings({"categories": cats})

    def test_letter_is_forwarded_to_department_once_and_leaves_queue(self):
        m = self.src.add("client@mail.ru", "Аренда", "Хотим взять в аренду помещение под бутик, 40 кв.м.")
        for _ in range(3):
            self.eng.poll()
        self.assertEqual([(f["to"], f["id"]) for f in self.src.forwards], [("rent@dept.uz", m["id"])])
        self.assertEqual(len(self.src.replies), 1)          # автоответ клиенту тоже ушёл
        self.assertEqual(self.queue_ids(), [])              # пересланное снимается с очереди ресепшн
        self.assertEqual(self.rec(m)["forwarded_to"], "rent@dept.uz")

    def test_other_directions_stay_at_reception(self):
        m = self.src.add("client@mail.ru", "Потеряла кошелёк", "Вчера потеряла кошелёк у фудкорта.")
        self.eng.poll()
        self.assertEqual(self.src.forwards, [])
        self.assertEqual(self.queue_ids(), [m["id"]])

    def test_forward_is_retried_when_outlook_is_busy(self):
        self.src.add("client@mail.ru", "Реклама", "Хотим провести рекламную акцию и розыгрыш призов.")
        self.src.fail_forward = 1
        self.eng.poll()
        self.assertEqual(self.src.forwards, [])
        self.eng.poll()
        self.assertEqual([f["to"] for f in self.src.forwards], ["marketing@dept.uz"])


class ThreatFlow(EngineCase):
    overrides = {"security_email": "sb@tcmall.uz"}

    def test_red_threat_blocks_auto_reply_and_notifies_security(self):
        self.eng.apply_settings({"categories": set_forward(self.eng.cfg["categories"], "rent", "rent@dept.uz")})
        m = self.src.add("anon@example.com", "Аренда", "В вашем торговом центре заложена бомба. Взорвётся через час.")
        self.eng.poll()
        self.eng.check_threats()
        rec = self.rec(m)
        self.assertEqual(rec["threat_level"], 2)
        self.assertEqual(rec["category"], "security")
        self.assertEqual(rec["priority"], 1)
        self.assertEqual(self.src.replies, [])               # угрожающему автоответ не уходит
        self.assertEqual(self.src.forwards, [])
        self.assertEqual([x["to"] for x in self.src.mails], ["sb@tcmall.uz"])
        self.assertEqual([t["id"] for t in self.eng.store.threats_pending()], [m["id"]])
        self.eng.check_threats()
        self.assertEqual(len(self.src.mails), 1)             # сигнал в СБ уходит один раз

    def test_red_threat_stays_until_staff_confirms(self):
        m = self.src.add("anon@example.com", "", "В вашем торговом центре заложена бомба.")
        self.eng.poll(); self.eng.poll()
        self.assertEqual(len(self.eng.store.threats_pending()), 1)
        self.eng.ack_threat(m["id"])
        self.assertEqual(self.eng.store.threats_pending(), [])
        self.assertEqual(self.queue_ids(), [])               # ответа по почте угроза не требует
        self.assertEqual(self.src.replies, [])

    def test_threat_from_ignored_sender_still_raises_alarm(self):
        m = self.src.add("noreply@anon.com", "Внимание", "В здании бомба, взрыв через час.")
        self.eng.poll()
        self.assertEqual(self.rec(m)["threat_level"], 2)
        self.assertEqual(len(self.eng.store.threats_pending()), 1)

    def test_yellow_waits_for_staff_then_letter_is_processed_normally(self):
        m = self.src.add("angry@example.com", "Ждите", "Вы ещё пожалеете, что так со мной обошлись.")
        self.eng.poll()
        self.assertEqual(self.rec(m)["threat_level"], 1)
        self.assertEqual(self.src.replies, [])               # пока сотрудник не проверил — без автоответа
        self.assertEqual(self.src.mails, [])                 # жёлтый сигнал в СБ сам не уходит
        self.eng.threat_clear(m["id"])                       # «Угрозы нет»
        self.eng.poll()
        self.assertEqual(len(self.src.replies), 1)
        self.assertEqual(self.eng.store.threats_pending(), [])
        self.assertEqual(self.queue_ids(), [m["id"]])

    def test_yellow_escalated_by_staff_becomes_red(self):
        m = self.src.add("angry@example.com", "Ждите", "Вы ещё пожалеете, что так со мной обошлись.")
        self.eng.poll()
        self.eng.threat_escalate(m["id"])
        self.eng.check_threats()
        self.assertEqual(self.rec(m)["threat_level"], 2)
        self.assertEqual([x["to"] for x in self.src.mails], ["sb@tcmall.uz"])
        self.assertEqual(self.src.replies, [])

    def test_our_own_alarm_coming_back_does_not_raise_new_alarm(self):
        self.src.add("sb@tcmall.uz", "Автоответ: УГРОЗА: письмо на почту ресепшн — тест",
                     "Сработал сигнал угрозы. В здании бомба.")
        self.eng.poll()
        self.assertEqual(self.eng.store.threats_pending(), [])


class Answers(EngineCase):
    def test_reply_from_outlook_removes_letter_from_queue(self):
        m = self.src.add("client@mail.ru", "Вопрос", "До скольки вы работаете?", minutes_ago=10)
        self.eng.poll()
        self.src.add_out(["client@mail.ru"], "RE: Вопрос", conv_id=m["conv_id"])
        self.eng.poll()
        self.assertEqual(self.queue_ids(), [])
        self.assertTrue(self.rec(m)["answered_at"])

    def test_our_auto_reply_is_not_counted_as_answer(self):
        m = self.src.add("client@mail.ru", "Вопрос", "До скольки вы работаете?", minutes_ago=10)
        self.eng.poll()
        self.src.add_out(["client@mail.ru"], "Ваше обращение получено: Вопрос", conv_id=m["conv_id"])
        self.eng.poll()
        self.assertEqual(self.queue_ids(), [m["id"]])

    def test_site_form_reply_to_own_mailbox_keeps_letter_in_queue_with_warning(self):
        body = "Имя: Тестова Анна\nEmail: anna.test@example.uz\nСообщение: Работает ли каток?"
        m = self.src.add("support@o-d.one", "Новая заявка от Тестова Анна", body, minutes_ago=10,
                         reply_to=["contact@tcmall.uz"])
        self.eng.poll()
        self.src.add_out(["contact@tcmall.uz"], "RE: Новая заявка от Тестова Анна", conv_id=m["conv_id"], minutes_ago=5)
        self.eng.poll()
        self.assertEqual(self.queue_ids(), [m["id"]])
        self.assertTrue(self.rec(m)["self_reply_at"])        # красная пометка «ответ ушёл не клиенту»
        self.src.add_out(["anna.test@example.uz"], "RE: Новая заявка от Тестова Анна", conv_id=m["conv_id"])
        self.eng.poll()
        self.assertEqual(self.queue_ids(), [])

    def test_no_reply_and_undo(self):
        m = self.src.add("client@mail.ru", "Спасибо", "Спасибо за помощь!")
        self.eng.poll()
        self.eng.no_reply(m["id"], "не требует ответа")
        self.assertEqual(self.queue_ids(), [])
        self.eng.undo_no_reply(m["id"])
        self.assertEqual(self.queue_ids(), [m["id"]])

    def test_broken_letter_does_not_stop_the_rest(self):
        self.src.add("client1@mail.ru", "Первое", "Текст")
        bad = self.src.add("client2@mail.ru", "Сломанное", "Текст")
        bad["received_at"] = None                            # письмо без даты — разбор спотыкается
        self.src.add("client3@mail.ru", "Третье", "Текст")
        self.eng.poll()
        self.assertEqual(len(self.queue_ids()), 3)


class CountFromAndState(EngineCase):
    def test_letters_before_count_from_are_not_counted(self):
        self.eng.apply_settings({"count_from": (datetime.now() - timedelta(minutes=30)).strftime("%Y-%m-%dT%H:%M")})
        self.src.add("old@mail.ru", "Старое", "Текст", minutes_ago=120)
        new = self.src.add("new@mail.ru", "Новое", "Текст", minutes_ago=5)
        self.eng.poll()
        self.assertEqual(self.queue_ids(), [new["id"]])
        self.assertEqual([r["to"] for r in self.src.replies], ["new@mail.ru"])

    def test_state_for_screen(self):
        self.src.add("client@mail.ru", "Срочно! Жалоба", "Туалет на 2 этаже в ужасном состоянии.")
        self.src.add("anon@example.com", "", "В здании бомба.")
        self.eng.poll()
        s = self.eng.state()
        self.assertEqual(s["counts"]["queue"], 2)
        self.assertEqual(s["counts"]["urgent"], 2)
        self.assertEqual(len(s["threats"]), 1)
        self.assertEqual(s["version"], "1.35")


class Shifts(EngineCase):
    def test_open_and_close_shift_sends_report(self):
        self.eng.apply_settings({"manager_email": "boss@tcmall.uz"})
        self.assertIn("error", self.eng.open_shift([]))
        self.assertEqual(self.eng.open_shift(["Иванова"]), {"ok": True})
        self.assertIn("error", self.eng.open_shift(["Петрова"]))          # вторая смена поверх первой не открывается
        m = self.src.add("client@mail.ru", "Вопрос", "До скольки вы работаете?", minutes_ago=10)
        self.src.add("client2@mail.ru", "Другой вопрос", "Где находится кинотеатр?", minutes_ago=8)
        self.eng.poll()
        self.src.add_out(["client@mail.ru"], "RE: Вопрос", conv_id=m["conv_id"], minutes_ago=1)
        self.eng.poll()
        r = self.eng.close_shift("Иванова")
        self.assertFalse(r.get("error"))
        self.assertEqual([x["to"] for x in self.src.mails], ["boss@tcmall.uz"])
        st = r["stats"]                                                   # цифры отчёта руководителю
        self.assertEqual((st["received"], st["answered"], st["unanswered"], st["auto_replied"]), (2, 1, 1, 2))
        self.assertEqual(st["rate"], 50.0)
        import os
        with open(os.path.join(self.tmp, "reports", r["file"]), encoding="utf-8") as f:
            self.assertIn("Отчёт ресепшн по почте", f.read())            # файл отчёта сохранён в reports/
        self.assertIn("error", self.eng.close_shift("Иванова"))           # закрыть дважды нельзя

    def test_shift_window_helpers(self):
        cfg = dict(config.get(), shifts=[{"name": "Смена 1", "start": "09:00", "end": "17:00"},
                                         {"name": "Смена 2", "start": "16:00", "end": "24:00"}])
        night = datetime(2026, 11, 2, 3, 0, 0)
        self.assertTrue(shifts.is_night(night, cfg))
        self.assertEqual(shifts.sla_start(night, cfg), datetime(2026, 11, 2, 9, 0, 0))   # срок ночного письма — с утра
        day = datetime(2026, 11, 2, 11, 0, 0)
        self.assertEqual(shifts.sla_start(day, cfg), day)


if __name__ == "__main__":
    unittest.main()
