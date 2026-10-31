"""Free-text search in the FAQ: whatever the user writes is matched against question titles, keywords and answers.

No external AI service: words are compared with simple stems («refunded»/«refund», «оплатил»/«оплата»); Chinese and
Japanese have no spaces, so they are compared by pairs of characters. Phrases like «call an operator» / «позови
оператора» / «转人工» mean the user wants a person.
"""
import re

WORD = re.compile(r"[\w']+", re.UNICODE)
CJK = re.compile(r"[㐀-鿿豈-﫿぀-ヿ]")
STOP = {"the", "a", "an", "is", "are", "i", "my", "me", "to", "of", "in", "on", "and", "or", "it", "do", "how", "what",
        "can", "you", "your", "for", "with", "was", "be", "have", "has", "not", "no", "yes", "please", "hi", "hello",
        "я", "мне", "меня", "мой", "моя", "мои", "и", "в", "на", "не", "что", "как", "это", "у", "с", "по", "за",
        "а", "но", "ли", "же", "бы", "вы", "вас", "ваш", "здравствуйте", "привет", "пожалуйста", "можно",
        "el", "la", "los", "las", "de", "que", "en", "un", "una", "mi", "por", "para", "con", "es", "hola",
        "le", "les", "des", "du", "et", "je", "est", "pour", "pas", "bonjour", "der", "die", "das", "und", "ist", "ich",
        "o", "os", "um", "uma", "meu", "minha", "da", "olá",
        "का", "की", "के", "है", "में", "मेरा", "मेरी", "और", "को", "আমি", "আমার", "এবং", "কি", "না",
        "في", "من", "على", "أنا", "هل", "ما", "و", "میں", "کا", "کی", "ہے", "اور", "کیا", "של", "את", "אני", "על", "מה", "זה", "לא"}
PERSON_WORDS = re.compile(
    r"\b(operator\w*|human|agent|live\s+(chat|support|person)|real\s+person|support\s+team|"
    r"оператор\w*|человек\w*|живо\w+|позов\w*|соедин\w*|специалист\w*|"
    r"operador\w*|humano|agente|persona\s+real|op[ée]rat(eur|rice)\w*|humain|conseill\w+|atendente|pessoa\s+real)\b",
    re.IGNORECASE)
PERSON_TEXT = re.compile(r"人工|客服|真人|转接|ऑपरेटर|इंसान|एजेंट|অপারেটর|মানুষ|এজেন্ট|موظف|انسان|إنسان|مشغل|خدمة العملاء|"
                         r"آپریٹر|نمائندہ|کسی انسان|נציג|מוקד|בן אדם|אדם אמיתי")
MIN_SCORE = 1.0


def stem(word: str) -> str:
    return word[:6] if len(word) > 6 else word


def words(text: str) -> set[str]:
    result = set()
    for word in WORD.findall(re.sub(r"<[^>]+>", " ", text or "").lower()):
        if CJK.search(word):
            result |= {word[i:i + 2] for i in range(len(word) - 1)} or {word}
        elif word not in STOP and len(word) > 1:
            result.add(stem(word))
    return result


def wants_person(text: str) -> bool:
    return bool(PERSON_WORDS.search(text or "") or PERSON_TEXT.search(text or ""))


def rank(text: str, items: list[tuple[int, str, str, str]], limit: int = 3) -> list[tuple[int, float]]:
    """Best matches as (id, score) out of (id, title, keywords, body), most relevant first."""
    asked = words(text)
    if not asked:
        return []
    scored = []
    for item_id, title, keywords, body in items:
        score = 2 * len(asked & (words(title) | words(keywords))) + 0.5 * len(asked & words(body))
        score /= max(1.0, len(asked) ** 0.5)
        if score >= MIN_SCORE:
            scored.append((item_id, round(score, 2)))
    return sorted(scored, key=lambda s: -s[1])[:limit]
