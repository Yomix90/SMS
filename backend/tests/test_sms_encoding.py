from app.services.sms_encoding import GSM7, UCS2, analyze, to_gsm_friendly


def test_gsm_single_and_multipart():
    assert analyze("a" * 160).segments == 1
    info = analyze("a" * 161)
    assert info.encoding == GSM7 and info.segments == 2 and info.per_segment == 153
    assert analyze("a" * 306).segments == 2
    assert analyze("a" * 307).segments == 3


def test_gsm_accents_supported():
    info = analyze("Bonjour éèùà ç")
    assert info.encoding == UCS2  # ç minuscule n'est pas dans GSM-7 (seul Ç l'est)
    assert analyze("Bonjour éèùà").encoding == GSM7


def test_extended_chars_count_double():
    info = analyze("€" * 80)
    assert info.encoding == GSM7 and info.units == 160 and info.segments == 1
    assert analyze("€" * 81).segments == 2


def test_extended_char_not_split():
    # 152 caractères simples + « € » (2 unités) : ne tient pas dans 153 -> passe au segment suivant
    text = "a" * 152 + "€" + "a" * 10
    info = analyze(text)
    assert info.segments == 2


def test_arabic_is_ucs2():
    assert analyze("مرحبا" * 14).segments == 1  # 70 caractères
    info = analyze("م" * 71)
    assert info.encoding == UCS2 and info.segments == 2 and info.per_segment == 67
    assert analyze("م" * 134).segments == 2
    assert analyze("م" * 135).segments == 3


def test_emoji_counts_two_units():
    info = analyze("😀" * 35)
    assert info.units == 70 and info.segments == 1


def test_empty():
    assert analyze("").segments == 0


def test_gsm_friendly_replacements():
    assert to_gsm_friendly("L’été « chaud » – ça") == "L'été \" chaud \" - ca"
    assert analyze(to_gsm_friendly("L’été")).encoding == GSM7
