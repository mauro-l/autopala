from __future__ import annotations

import math

import pytest

from app.normalize import (
    make_fingerprint,
    make_loose_key,
    normalize_company,
    normalize_location,
    normalize_text,
    normalize_title,
    parse_is_remote,
)


class TestNormalizeText:
    def test_strips_accents_and_case(self):
        assert normalize_text("Desarrollador Sénior React") == "desarrollador senior react"

    def test_collapses_punctuation_and_whitespace(self):
        assert normalize_text("  Node.js/Express -- MongoDB  ") == "node js express mongodb"

    def test_handles_none_and_empty(self):
        assert normalize_text(None) == ""
        assert normalize_text("") == ""
        assert normalize_text("   ") == ""


class TestNormalizeCompany:
    def test_drops_legal_suffixes(self):
        assert normalize_company("BERNARDO LEW E HIJOS SRL") == "bernardo lew e hijos"
        assert normalize_company("Acme S.A.") == "acme"
        assert normalize_company("Globant Inc.") == "globant"

    def test_keeps_suffix_like_words_that_are_part_of_the_name(self):
        assert normalize_company("SAS Institute") == "sas institute"


class TestNormalizeTitle:
    def test_drops_remote_markers(self):
        assert normalize_title("React Developer (Remote)") == "react developer"
        assert normalize_title("Frontend Dev - Remoto") == "frontend dev"

    def test_keeps_the_actual_role(self):
        assert normalize_title("Desarrollador Senior React + TypeScript") == (
            "desarrollador senior react typescript"
        )


class TestNormalizeLocation:
    def test_returns_empty_for_missing(self):
        assert normalize_location(None) == ""
        assert normalize_location("") == ""

    def test_normalizes_text(self):
        assert normalize_location("Buenos Aires, Argentina") == "buenos aires argentina"


class TestFingerprint:
    def test_is_sha256_hex(self):
        fp = make_fingerprint("Acme", "Dev", "CABA")
        assert len(fp) == 64
        assert all(c in "0123456789abcdef" for c in fp)

    def test_stable_across_case_accents_and_suffixes(self):
        a = make_fingerprint(
            "Globant S.A.", "Desarrollador Sénior React", "Buenos Aires, Argentina"
        )
        b = make_fingerprint("globant", "desarrollador senior react", "buenos aires, argentina")
        assert a == b

    def test_differs_by_company(self):
        a = make_fingerprint("Acme", "React Developer", "CABA")
        b = make_fingerprint("Other", "React Developer", "CABA")
        assert a != b

    def test_empty_location_is_deterministic(self):
        a = make_fingerprint("BairesDev", "React Developer", None)
        b = make_fingerprint("BairesDev", "React Developer", "")
        assert a == b

    def test_location_still_separates_postings(self):
        a = make_fingerprint("Acme", "React Developer", "CABA")
        b = make_fingerprint("Acme", "React Developer", "Cordoba")
        assert a != b


class TestLooseKey:
    def test_ignores_location_but_keeps_identity(self):
        a = make_loose_key("BairesDev", "React Developer")
        b = make_loose_key("bairesdev s.a.", "react developer (remote)")
        assert a == b

    def test_differs_by_title(self):
        assert make_loose_key("Acme", "React Developer") != make_loose_key("Acme", "Backend Dev")


class TestParseIsRemote:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("False", False),
            ("false", False),
            ("True", True),
            (True, True),
            (False, False),
            (None, False),
            (float("nan"), False),
            ("", False),
        ],
    )
    def test_casts_the_stringly_typed_flag(self, raw, expected):
        assert parse_is_remote(raw) is expected

    def test_non_nan_float_is_truthy(self):
        assert parse_is_remote(1.0) is True
        assert math.isnan(float("nan")) is True
