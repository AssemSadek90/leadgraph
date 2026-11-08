import pytest

from leadgraph.people import email_type, first_last, linkedin_match, name_tokens, valid_email_syntax


@pytest.mark.parametrize("email,person,expected", [
    ("info@x.de", None, "generic"),
    ("m.mueller@x.de", None, "personal"),
    ("weg@x.de", None, "role"),
    ("kontakt@x.de", None, "generic"),
    ("office@x.de", "Max Müller", "generic"),
    ("verwaltung@x.de", None, "role"),
    ("mietverwaltung@x.de", None, "role"),
    ("max.mustermann@x.de", None, "personal"),
    ("mmustermann@x.de", "Max Mustermann", "personal"),
    ("mueller@x.de", "Dr. Max Müller", "personal"),
    ("buchhaltung@x.de", "Max Müller", "role"),
    ("erika.beispiel@x.de", "Erika Beispiel", "personal"),
])
def test_email_type_table(email, person, expected):
    assert email_type(email, person) == expected


def test_names_and_syntax():
    assert name_tokens("Dr. Max-Peter Müller") == ["max", "peter", "mueller"]
    assert first_last("Dipl.-Ing. Erika Beispiel") == ("erika", "beispiel")
    assert first_last("Müller") is None
    assert valid_email_syntax("m.mueller@x.de") and not valid_email_syntax("m.mueller@x")


def test_linkedin_match():
    url = "https://de.linkedin.com/in/max-mustermann-123"
    assert linkedin_match(url, "Max Mustermann – Geschäftsführer – Muster HV | LinkedIn",
                          "Max Mustermann")
    assert not linkedin_match(url, "Max Schmidt – Muster HV | LinkedIn", "Max Mustermann")
    assert not linkedin_match("https://www.linkedin.com/company/muster", "Max Mustermann",
                              "Max Mustermann")
    assert linkedin_match("https://www.linkedin.com/in/jmueller", "Jörg Müller - Berlin",
                          "Jörg Müller")
