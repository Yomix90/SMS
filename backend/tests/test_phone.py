import pytest

from app.services.phone import ERR_EMPTY, ERR_FORMAT, ERR_LANDLINE, format_display, normalize_phone


@pytest.mark.parametrize("raw", [
    "0612345678", "06 12 34 56 78", "06.12.34.56.78", "06-12-34-56-78", "(06) 12 34 56 78",
    "+212612345678", "+212 6 12 34 56 78", "00212612345678", "212612345678", "+212 0612345678",
    "612345678", 612345678, 612345678.0, "٠٦١٢٣٤٥٦٧٨", " 0612345678 ",
])
def test_moroccan_mobile_formats(raw):
    assert normalize_phone(raw).e164 == "+212612345678"


def test_07_prefix():
    assert normalize_phone("0700112233").e164 == "+212700112233"
    assert normalize_phone("700112233").e164 == "+212700112233"


@pytest.mark.parametrize("raw", ["0522123456", "+212522123456", "522123456", "0808123456"])
def test_landline_refused(raw):
    res = normalize_phone(raw)
    assert not res.valid and res.error == ERR_LANDLINE


@pytest.mark.parametrize("raw,error", [("", ERR_EMPTY), (None, ERR_EMPTY), ("abc", ERR_FORMAT),
                                       ("06123", ERR_FORMAT)])
def test_invalid(raw, error):
    res = normalize_phone(raw)
    assert not res.valid and res.error == error


def test_too_short_moroccan():
    assert not normalize_phone("061234567").valid  # 9 chiffres avec 0 initial


def test_international_numbers():
    assert normalize_phone("+33612345678").e164 == "+33612345678"
    assert normalize_phone("0033612345678").e164 == "+33612345678"
    assert not normalize_phone("+33123").valid


def test_format_display():
    assert format_display("+212612345678") == "06 12 34 56 78"
