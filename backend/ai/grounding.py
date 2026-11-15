"""Check an LLM answer against the facts it was given. Deterministic code; the LLM is never trusted to police itself.

Rules (each failure is a named problem the retry prompt can quote back to the model):
  * output must be a JSON object with a non-empty `answer`, a `citedFactIds` list of known IDs, optional `hypotheses` list;
  * every number written as digits in `answer` and `hypotheses` must match a number in a CITED fact (rounding to the digits
    shown is accepted; sign is ignored);
  * no discovery or confirmation language (unless negated, e.g. "does not confirm");
  * a fact ID or opaque result ID in the text is not treated as a number.
Known limit: numbers written as WORDS ("twelve") are not caught. The system prompt tells the model to write quantities as
digits so the check can see them.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

_NUM = re.compile(r"(?<![\w.])[-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?")
_THOUSANDS = re.compile(r"(?<=\d),(?=\d{3}\b)")
_FORBIDDEN = re.compile(r"\b(confirm(?:ed|s|ing)?|discover(?:y|ies|ed|s)?|prov(?:es?|en|ing)|definitely|new (?:planet|object|star|brown dwarf|moon))\b", re.I)
_NEGATION = re.compile(r"(?:\bnot|n't|\bno|\bcannot|\bnever|\bwithout|\bnor)\s+(?:\w+\s+){0,2}$", re.I)
MAX_ANSWER_CHARS = 1500


def extract_numbers(text: str) -> list[str]:
    return _NUM.findall(_THOUSANDS.sub("", text))


def _decimals(token: str) -> int | None:
    if "e" in token.lower():
        return None
    return len(token.split(".")[1]) if "." in token else 0


def number_supported(token: str, allowed: list[float]) -> bool:
    x = abs(float(token))
    d = _decimals(token)
    for y in allowed:
        y = abs(y)
        tol = (0.5 * 10 ** (-d) if d is not None else 0.01 * max(y, 1e-300)) + 1e-9 * y
        if abs(x - y) <= tol:
            return True
    return False


def allowed_numbers(fact_texts: list[str], explicit: list[list[float]]) -> list[float]:
    out: list[float] = [v for nums in explicit for v in nums]
    for t in fact_texts:
        out.extend(float(n) for n in extract_numbers(t))
    return out


def forbidden_phrases(text: str) -> list[str]:
    bad = []
    for m in _FORBIDDEN.finditer(text):
        if not _NEGATION.search(text[max(0, m.start() - 30):m.start()]):
            bad.append(m.group(0))
    return bad


@dataclass
class Parsed:
    answer: str
    cited: list[str]
    hypotheses: list[str] = field(default_factory=list)


@dataclass
class Verdict:
    ok: bool
    problems: list[str]
    parsed: Parsed | None = None


def parse(raw: str) -> tuple[Parsed | None, str | None]:
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.I)
    try:
        obj = json.loads(text)
    except (ValueError, TypeError):
        return None, "The reply was not valid JSON."
    if not isinstance(obj, dict) or not isinstance(obj.get("answer"), str) or not obj["answer"].strip():
        return None, "The JSON must be an object with a non-empty string `answer`."
    cited = obj.get("citedFactIds", [])
    hyp = obj.get("hypotheses", [])
    if not isinstance(cited, list) or not all(isinstance(c, str) for c in cited):
        return None, "`citedFactIds` must be a list of strings."
    if not isinstance(hyp, list) or not all(isinstance(h, str) for h in hyp):
        return None, "`hypotheses` must be a list of strings."
    return Parsed(obj["answer"].strip(), [c.strip() for c in cited], [h.strip() for h in hyp if h.strip()][:4]), None


def validate(raw: str, fact_texts: dict[str, str], fact_numbers: dict[str, list[float]]) -> Verdict:
    parsed, err = parse(raw)
    if parsed is None:
        return Verdict(False, [err or "Unreadable reply."])
    problems: list[str] = []
    unknown = [c for c in parsed.cited if c not in fact_texts]
    if unknown:
        problems.append(f"citedFactIds contains IDs that do not exist: {', '.join(unknown)}.")
    if len(parsed.answer) > MAX_ANSWER_CHARS:
        problems.append(f"The answer is longer than {MAX_ANSWER_CHARS} characters.")
    cited = [c for c in parsed.cited if c in fact_texts]
    allowed = allowed_numbers([fact_texts[c] for c in cited], [fact_numbers.get(c, []) for c in cited])
    body = " ".join([parsed.answer, *parsed.hypotheses])
    unsupported = []
    for tok in extract_numbers(body):
        if not number_supported(tok, allowed) and tok not in unsupported:
            unsupported.append(tok)
    if unsupported:
        where = "cite the fact that contains them" if not cited else "they are not in the facts you cited"
        problems.append(f"These numbers are not supported by the cited facts: {', '.join(unsupported)} ({where}). Use only numbers copied from the facts.")
    bad = forbidden_phrases(body)
    if bad:
        problems.append(f"Do not use discovery or confirmation wording ({', '.join(sorted(set(b.lower() for b in bad)))}); describe evidence and hypotheses instead.")
    return Verdict(not problems, problems, parsed)
