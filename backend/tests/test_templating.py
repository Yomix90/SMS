from datetime import date, datetime
from decimal import Decimal

from app.services.templating import extract_variables, format_value, normalize_key, render


def test_normalize_key():
    assert normalize_key("Date échéance") == "date_echeance"
    assert normalize_key("  Prénom ") == "prenom"
    assert normalize_key("N'importe quelle colonne") == "n_importe_quelle_colonne"
    assert normalize_key("first_name") == "prenom"  # alias
    assert normalize_key("المبلغ") == "montant"


def test_render_basic():
    res = render("Bonjour {prenom} {nom}, montant : {montant} MAD", {"prenom": "Salma", "nom": "Alaoui",
                                                                      "montant": 1250})
    assert res.text == "Bonjour Salma Alaoui, montant : 1 250 MAD"
    assert res.missing == []


def test_render_with_column_names():
    res = render("Réf {Référence} due le {date_echeance}", {"Référence": "FAC-1", "Date échéance": date(2026, 11, 5)})
    assert res.text == "Réf FAC-1 due le 05/11/2026"


def test_any_column_variable():
    res = render("Agence : {n'importe_quelle_colonne}", {"N'importe quelle colonne": "Casa"})
    assert res.text == "Agence : Casa"


def test_missing_variable_reported_and_removed():
    res = render("Bonjour {prenom} {nom}", {"prenom": "Omar"})
    assert res.text == "Bonjour Omar"
    assert res.missing == ["nom"]
    assert render("Bonjour {nom}", {}, keep_missing=True).text == "Bonjour {nom}"


def test_escaped_braces():
    assert render("Code {{promo}} pour {prenom}", {"prenom": "Ali"}).text == "Code {promo} pour Ali"


def test_arabic_template():
    res = render("مرحبا {prenom}، المبلغ {montant} درهم", {"prenom": "فاطمة", "montant": Decimal("99.5")})
    assert res.text == "مرحبا فاطمة، المبلغ 99,50 درهم"


def test_format_value():
    assert format_value(1250.0) == "1 250"
    assert format_value(1250.5) == "1 250,50"
    assert format_value(Decimal("12345.678")) == "12 345,68"
    assert format_value(datetime(2026, 1, 2, 0, 0)) == "02/01/2026"
    assert format_value(datetime(2026, 1, 2, 9, 5)) == "02/01/2026 09:05"
    assert format_value("2026-11-05") == "05/11/2026"
    assert format_value("2026-11-05T00:00:00") == "05/11/2026"
    assert format_value(None) == ""


def test_extract_variables():
    assert extract_variables("{Nom} {prenom} {nom} {{x}}") == ["nom", "prenom"]


def test_amount_text_from_csv():
    assert render("{montant} MAD", {"Montant": "1250.5"}).text == "1 250,50 MAD"
    assert render("{montant}", {"montant": "1 250,50"}).text == "1 250,50"
    assert render("{montant}", {"montant": "1.250,50"}).text == "1 250,50"
    assert render("{reference}", {"reference": "0612345678"}).text == "0612345678"  # pas un montant
    assert render("{montant}", {"montant": "N/A"}).text == "N/A"
