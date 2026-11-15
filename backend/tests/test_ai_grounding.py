"""The numeric check and wording rules that stop the LLM from inventing measurements or claiming discoveries."""
import json

import pytest

from ai import grounding

pytestmark = pytest.mark.synthetic

TEXTS = {
    "F1": "Measurement (aperture_flux): 36.93 with uncertainty 4.1 mJy; the value is 9.0 times its uncertainty; measured at 1.842 micrometres; aperture radius 12 arcsec.",
    "F2": "Observation 2025W19_1B_0116_1: band SPHEREx-D3; observed 2025-05-14T09:00:00Z; image quality good.",
}
NUMS = {"F1": [36.9371, 4.1, 9.0, 1.842, 12.0], "F2": []}


def raw(answer, cited=("F1",), hyp=()):
    return json.dumps({"answer": answer, "citedFactIds": list(cited), "hypotheses": list(hyp)})


def check(answer, cited=("F1",), hyp=()):
    return grounding.validate(raw(answer, cited, hyp), TEXTS, NUMS)


def test_numbers_copied_or_rounded_from_the_cited_fact_are_accepted():
    assert check("The flux is 36.9 mJy, give or take 4.1, measured at 1.84 micrometres in a 12 arcsec aperture.").ok
    assert check("Roughly 37 mJy.").ok
    assert check("It is 9 times its uncertainty.").ok
    assert check("A drop of -36.9 would be the same size.").ok                     # sign is ignored


def test_an_invented_number_is_rejected_and_named():
    v = check("The flux is 38.5 mJy.")
    assert not v.ok and "38.5" in v.problems[0] and "not supported" in v.problems[0]
    assert not check("The flux is 36.8 mJy.").ok                                    # a wrong rounding is also invented


def test_numbers_only_count_if_they_come_from_a_fact_the_model_cited():
    v = check("The star was observed in 2025.", cited=("F1",))                      # the year is in F2, which was not cited
    assert not v.ok
    assert check("The star was observed in 2025.", cited=("F2",)).ok
    assert not check("It is 36.9 mJy.", cited=()).ok                                # digits with no citation at all


def test_fact_ids_and_band_names_are_not_mistaken_for_numbers():
    assert check("Band SPHEREx-D3 looks fine (F2, meas_7be114).", cited=("F2",)).ok


def test_hypotheses_are_number_checked_too():
    assert not check("It is 36.9 mJy.", hyp=("A flare of 70 mJy could explain it.",)).ok
    assert check("It is 36.9 mJy.", hyp=("The source may lie at a different wavelength.",)).ok


def test_discovery_wording_is_rejected_unless_negated():
    assert not check("This confirms a new planet.").ok
    assert not check("A discovery!").ok
    assert not check("This proves the star moved.").ok
    assert check("This does not confirm a real change.").ok
    assert check("There is no discovery here, only a measurement.").ok


def test_unknown_citations_and_bad_shapes_are_rejected():
    assert not check("Fine.", cited=("F9",)).ok
    assert not grounding.validate("not json", TEXTS, NUMS).ok
    assert not grounding.validate(json.dumps({"answer": ""}), TEXTS, NUMS).ok
    assert not grounding.validate(json.dumps({"answer": "x", "citedFactIds": "F1"}), TEXTS, NUMS).ok
    assert not grounding.validate(json.dumps([1, 2]), TEXTS, NUMS).ok
    assert not check("x" * 1600).ok


def test_markdown_fenced_json_is_accepted():
    fenced = "```json\n" + raw("Measured at 1.84 micrometres.") + "\n```"
    assert grounding.validate(fenced, TEXTS, NUMS).ok


def test_exponent_and_thousands_formats():
    texts = {"F1": "Pixel solid angle 9.03e-10 sr and 1,234 pixels."}
    assert grounding.validate(raw("Solid angle 9.03e-10 sr over 1,234 pixels."), texts, {"F1": []}).ok
    assert not grounding.validate(raw("Solid angle 9.5e-10 sr."), texts, {"F1": []}).ok
