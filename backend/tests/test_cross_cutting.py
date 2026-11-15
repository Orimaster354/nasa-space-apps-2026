"""Cross-cutting guards (checklist X-01 to X-04). They fail if a future change breaks one of the project's standing rules."""
import ast
import pathlib
import re
import subprocess

import pytest

pytestmark = pytest.mark.synthetic

BACKEND = pathlib.Path(__file__).resolve().parent.parent
REPO = BACKEND.parent


def _arith(path):
    src = path.read_text(encoding="utf-8")
    out = []
    for n in ast.walk(ast.parse(src)):
        if isinstance(n, ast.BinOp) and isinstance(n.op, (ast.Mult, ast.Div, ast.Sub, ast.Pow, ast.FloorDiv)):
            if isinstance(n.left, ast.Constant) and isinstance(n.left.value, str):
                continue
            out.append(ast.get_source_segment(src, n))
    return out


def test_x01_the_ai_package_does_no_arithmetic_on_scientific_results():
    """The only arithmetic allowed in ai/ is the numeric tolerance check (grounding.py), time and quota bookkeeping (limits.py, llm.py), and one
    band-index lookup (facts.py). Anything else would mean the AI layer computes results instead of quoting them."""
    allowed_files = {"grounding.py", "limits.py", "llm.py"}
    for f in sorted((BACKEND / "ai").glob("*.py")):
        hits = _arith(f)
        if f.name in allowed_files:
            continue
        assert hits in ([], ["band - 1"]), f"{f.name} does arithmetic: {hits}"


def test_x01_the_frontend_does_only_display_arithmetic_on_wavelengths():
    """The frontend may pick the nearest band or lay bands out; it must not compute fluxes, uncertainties or signal-to-noise itself."""
    root = REPO / "frontend" / "js"
    if not root.is_dir():
        pytest.skip("no frontend in this checkout")
    bad = []
    for f in root.rglob("*.js"):
        if "mock" in f.parts or f.name in ("scene.js", "placement.js"):
            continue
        for i, line in enumerate(f.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            code = line.split("//")[0]
            if re.search(r"\b(snr|signalToNoise|fluxUncertainty|uncertainty)\w*\s*[-+*/]\s*[\w(]|[-+*/]\s*(snr|signalToNoise|fluxUncertainty)\b", code):
                bad.append(f"{f.name}:{i}: {line.strip()[:90]}")
    assert not bad, bad


def test_x02_ai_text_enters_the_api_only_through_the_chat_endpoint():
    """AI-written text is produced by ai.companion and must only be reachable via POST /chat/messages (and the stored assistant messages of an
    investigation, which carry role 'assistant'). Raw data and derived results never embed it."""
    users = []
    for f in BACKEND.rglob("*.py"):
        if any(p in f.parts for p in ("tests", ".venv", "scripts", "__pycache__")):
            continue
        text = f.read_text(encoding="utf-8", errors="replace")
        if re.search(r"^\s*(from|import)\s+ai\.companion|from\s+ai\s+import\s+companion", text, re.M):
            users.append(f.relative_to(BACKEND).as_posix())
    assert sorted(users) == ["app/main.py"], users                          # the app wires it once; routes reach it via app.state


def test_x03_no_secret_is_tracked_and_the_env_file_is_ignored():
    def git(*args):
        return subprocess.run(["git", *args], cwd=REPO, capture_output=True, text=True)
    if git("rev-parse", "--is-inside-work-tree").stdout.strip() != "true":
        pytest.skip("not a git checkout")
    tracked = git("ls-files").stdout.split()
    assert not [p for p in tracked if re.search(r"(^|/)\.env($|\.(?!example))", p)], "an env file is tracked"
    hits = git("grep", "-I", "-n", "-E", r"AIza[0-9A-Za-z_-]{30}|sk-[A-Za-z0-9]{32}|AKIA[0-9A-Z]{16}|-----BEGIN (RSA|PRIVATE|OPENSSH)", "--", ".", ":!backend/tests").stdout
    assert hits == "", "a key-like string is committed:\n" + hits
    ignored = git("check-ignore", "backend/.env")
    assert ignored.returncode == 0, "backend/.env is not git-ignored"
    example = (BACKEND / ".env.example").read_text(encoding="utf-8")
    for line in example.splitlines():
        if line.startswith("SPX_LLM_API_KEY"):
            assert line.split("=", 1)[1].strip() == "", "the example file must not hold a key"


def test_x04_results_carry_their_provenance_fields(tmp_path):
    """Every measurement states a method, units and the basis of its uncertainty; every proper motion says whether it is catalogue or derived."""
    from api import schemas
    for model, required in ((schemas.Measurement, {"method", "unit"}), (schemas.ProperMotion, {"provenanceKind", "source"})):
        fields = set(model.model_fields)
        assert required <= fields, (model.__name__, required - fields)
    assert "uncertaintyBasis" in schemas.Measurement.model_fields
