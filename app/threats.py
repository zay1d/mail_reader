# -*- coding: utf-8 -*-
"""Детектор угроз. Уровни: 2 — явная угроза (красный экран), 1 — возможная (жёлтый),
0 — нет. Учитывает разрядку (б о м б а), точки между буквами, латинские
буквы-двойники в кириллице и наоборот, цифры вместо букв, фразы-исключения."""
import re, unicodedata

_LAT2CYR = str.maketrans({
    "a": "а", "e": "е", "o": "о", "p": "р", "c": "с", "y": "у", "x": "х", "k": "к",
    "h": "н", "m": "м", "t": "т", "b": "в", "0": "о", "3": "з", "6": "б",
})
_CYR2LAT = str.maketrans({
    "а": "a", "е": "e", "о": "o", "р": "p", "с": "c", "у": "y", "х": "x", "к": "k",
    "н": "h", "м": "m", "т": "t", "в": "b", "0": "o", "3": "e", "1": "i", "4": "a",
})

_SPACED = re.compile(r"(?<![^\s])((?:[^\W\d_]\s){2,}[^\W\d_])(?![^\s])", re.U)
_DOTTED = re.compile(r"(?<![^\W\d_])((?:[^\W\d_][.\-_*]){2,}[^\W\d_])", re.U)

def _norm(text: str) -> str:
    t = unicodedata.normalize("NFKC", text or "").lower().replace("ё", "е")
    t = _SPACED.sub(lambda m: m.group(1).replace(" ", ""), t)
    t = _DOTTED.sub(lambda m: re.sub(r"[.\-_*]", "", m.group(1)), t)
    return re.sub(r"[ \t]+", " ", t)

def _spaced_tokens(text):
    """Слова, написанные в разрядку («б о м б а в тц»), склеенные вместе с прилипшими короткими
    словами: «бомбав». Проверяются шаблонами без границ слова."""
    t = unicodedata.normalize("NFKC", text or "").lower().replace("ё", "е")
    out = []
    for m in _SPACED.finditer(t):
        out.append(m.group(1).replace(" ", ""))
    for m in _DOTTED.finditer(t):
        out.append(re.sub(r"[.\-_*]", "", m.group(1)))
    return [x for x in out if len(x) >= 4]

def compile_patterns(words):
    out = []
    for w in words or []:
        w = (w or "").strip()
        if not w:
            continue
        try:
            out.append(re.compile(w, re.I | re.U))
        except re.error:
            out.append(re.compile(re.escape(w), re.I | re.U))
    return out

def _plain(words):
    return [(_norm(w) or "").strip() for w in (words or []) if (w or "").strip()]

_LETTER = "0-9a-zа-яёўқғҳ"

class _Phrase:
    """Слово или фраза словаря: ищется с начала слова; пробел в конце записи — слово должно закончиться."""
    def __init__(self, w, word_end=True):
        raw = unicodedata.normalize("NFKC", w or "").lower().replace("ё", "е")
        self.text = re.sub(r"[ \t]+", " ", raw).strip()
        pat = "(?<![%s])" % _LETTER + re.escape(self.text)
        if raw.endswith((" ", "\t")):
            # пробел в конце: слово заканчивается; у исключений допустимы падежные окончания (тир, тире, тиру — не «тираж»)
            pat += ("" if word_end else "(?:а|у|е|ом|ы|ов|ах|ами|ам|ой|ей|я|ю)?") + "(?![%s])" % _LETTER
        self.rx = re.compile(pat, re.I)

    def found(self, text):
        return self.rx.search(text) is not None

def _phrases(words, word_end=True):
    return [x for x in (_Phrase(w, word_end) for w in (words or []) if (w or "").strip()) if x.text]

class Detector:
    def __init__(self, cfg):
        self.hard = compile_patterns(cfg.get("threat_words"))
        self.hard_nb = compile_patterns([(w or "").replace(r"\b", "") for w in cfg.get("threat_words") or []])
        self.exceptions = _phrases(cfg.get("threat_exceptions"), word_end=False)   # «тир» = тир, тире, тиру
        for x in self.exceptions:   # исключение-оборот со словом-угрозой внутри («это бомба!») гасит только своё слово
            x.own = any(p.search(x.text) for p in self.hard_nb)
        self.soft = _phrases(cfg.get("threat_soft_words"))
        self.place = _phrases(cfg.get("threat_place_words"))
        self.time = _phrases(cfg.get("threat_time_words"))
        self.polite = _phrases(cfg.get("threat_polite_words"))

    def _word_at(self, v, s, e):
        ws = max(v.rfind(c, 0, s) for c in " \n\r\t") + 1         # слово целиком: граница — пробел или перевод строки
        ends = [i for i in (v.find(c, e) for c in " \n\r\t") if i != -1]
        we = min(ends) if ends else len(v)
        return v[ws:we].strip(".,;:!?()\"'«»\n\r\t")

    def _excepted(self, v, s, e):
        # исключение рядом с найденным словом (±30 знаков), ищется с начала слова: «тир» не цепляет «квартир».
        # Оборот с самим словом («просто бомба», «aksiya bombasi», «взрывной рост») гасит только ЭТО вхождение
        # (найденное слово внутри оборота): «Это бомба! … В здании бомба» — вторая «бомба» тревогу поднимет.
        hit = v[s:e]
        stem = hit[:4]
        win = v[max(0, s - 30):e + 30]
        for x in self.exceptions:
            if any(s < mm.end() and e > mm.start() for mm in x.rx.finditer(v)):
                return True              # само это слово — часть безобидного оборота («просто бомба», «photo shooting»)
            if x.own or (stem and stem in x.text):
                continue                 # оборот со словом-угрозой в другом месте письма соседние слова не гасит
            if x.found(win):
                return True              # слово-контекст рядом: «стрельба в тире», «лазертаг: стрельба»
        return False

    def analyze(self, text: str):
        """-> (level, words)"""
        base = _norm(text)
        variants = [base, base.translate(_LAT2CYR), base.translate(_CYR2LAT)]
        hard_words = []
        for pat in self.hard:
            for v in variants:
                for m in pat.finditer(v):
                    s, e = m.span()
                    if self._excepted(v, s, e):
                        continue
                    w = self._word_at(v, s, e)
                    if w and w not in hard_words:
                        hard_words.append(w)
                    break
                else:
                    continue
                break
        if not hard_words:
            toks = _spaced_tokens(text)
            if toks:                                   # разрядка: «б о м б а в тц» -> «бомбав» содержит «бомба»
                joined = " ".join(toks)
                for v in (joined, joined.translate(_LAT2CYR), joined.translate(_CYR2LAT)):
                    for pat in self.hard_nb:
                        m = pat.search(v)
                        if m and not self._excepted(v, *m.span()) and not any(m.group(0) in x.text and x.found(base) for x in self.exceptions):
                            hard_words.append(m.group(0))
                            break
                    if hard_words:
                        break
        if hard_words:
            return 2, hard_words
        soft_words = list(dict.fromkeys(w.text for w in self.soft if any(w.found(v) for v in variants)))   # «найдём»/«найдем» — одно слово
        combo = []
        # «место + время» без вежливых слов — жёлтый; вопрос («Будет ли работать парковка завтра в 7?») — не угроза
        if not soft_words and "?" not in base and not any(w.found(base) for w in self.polite):
            pl = [w.text for w in self.place if w.found(base)]
            tm = [w.text for w in self.time if w.found(base)]
            if pl and tm:
                combo = [pl[0], tm[0]]
        if soft_words or combo:
            return 1, (soft_words or combo)[:6]
        return 0, []

def find_threats(text, patterns):
    """Совместимость со старым вызовом: только явные слова."""
    d = Detector.__new__(Detector)
    d.hard, d.hard_nb, d.exceptions, d.soft, d.place, d.time, d.polite = patterns, [], [], [], [], [], []
    return d.analyze(text)[1]
