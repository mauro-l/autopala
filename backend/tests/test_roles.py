"""Tests for the technology-role gate.

The titles below are taken verbatim from ``public.jobs`` so the gate is pinned
against the noise that actually reached the board, not against invented cases.
"""

from app.roles import is_technology_role


def test_math_tutor_is_not_a_technology_role():
    title = (
        "Tutor/a online de Matemáticas, Física o Química para estudiantes de "
        "España"
    )
    assert is_technology_role(title) is False


def test_known_non_technology_professions_are_dropped():
    titles = [
        "Cold Caller – Part-time",
        "Bilingual Administrative Assistant",
        "Bookkeeping & Administrative Support Virtual Assistant",
        "Dental Virtual Assistant",
        "Government RFP and Bid Coordinator — Commercial Printing",
        "Assistant Travel Coordinator (Remote – LATAM) | Full-Time & Part-Time",
        "Sales Development Representative - B2B",
    ]
    for title in titles:
        assert is_technology_role(title) is False, title


def test_unusual_technology_titles_survive():
    titles = [
        "Creative Technologist",
        "Product Designer, Safeguards",
        "UI Engineer (Analytics) - Remote Work | REF#310318",
        "Automation QA Engineer (QVAC) (100% remote)",
        "Web Designer",
        "Data Engineer Trainee",
        "Desarrollador Frontend (UX/UI)",
        "Full Stack Developer",
    ]
    for title in titles:
        assert is_technology_role(title) is True, title


def test_a_technology_noun_vetoes_the_non_technology_list():
    # "Sales" and "assistant" are noise nouns, but the role noun wins.
    assert is_technology_role("Sales Engineer") is True
    assert is_technology_role("Engineering Assistant") is True


def test_unknown_titles_fail_open():
    # An unrecognized profession is kept: dropping it would lose a rare but
    # legitimate role, which is the cost this gate refuses to pay.
    assert is_technology_role("Research Associate, Biology") is True
    assert is_technology_role("Operador de Mesa de Ayuda") is True


def test_empty_title_is_not_judged_here():
    # Emptiness is dropped upstream with its own counter, not by this gate.
    assert is_technology_role("") is True
    assert is_technology_role(None) is True
