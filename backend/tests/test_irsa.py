import threading

import httpx
import pytest
from astropy.table import Table

from processing import irsa

pytestmark = pytest.mark.synthetic

FITS_BYTES = b"SIMPLE  =                    T" + b" " * 60
POLY = "POLYGON ICRS 133.2 -6.8 136.5 -7.9 135.6 -11.2 132.2 -10.1"


def _row(uri, mjd, band="SPHEREx-D3", coll="spherex_dr1", **over):
    r = {"access_url": irsa.IRSA_BASE + uri, "obs_collection": coll, "obs_id": "2025W19_1B_0116_1", "energy_bandpassname": band,
         "s_ra": 134.41, "s_dec": -9.0, "s_region": POLY, "em_min": 1.6301e-06, "em_max": 2.4346e-06, "em_res_power": 40.6,
         "t_min": mjd, "t_max": mjd + 0.001, "t_exptime": 113.6, "s_pixel_scale": 6.15, "obs_release_date": "2026-09-27 23:59:28",
         "target_name": "SPS_1", "access_estsize": 65791}
    r.update(over)
    return r


def _table(*rows):
    return Table(rows=list(rows))


def test_sia_url_numbers_only_and_validated():
    u = irsa.sia_url("spherex_dr1", 133.795129, -7.245147)
    assert u.startswith("https://irsa.ipac.caltech.edu/SIA?COLLECTION=spherex_dr1&POS=circle+133.795129+-7.245147+0.01")
    for bad in [(361, 0), (10, 91), (float("nan"), 0)]:
        with pytest.raises(ValueError):
            irsa.sia_url("spherex_dr1", *bad)
    with pytest.raises(ValueError):
        irsa.sia_url("spherex_dr1&x=1", 10, 10)           # collection name cannot smuggle parameters
    with pytest.raises(ValueError):
        irsa.sia_url("spherex_dr1", "1; DROP", 0)


def test_discover_maps_real_archive_fields_and_converts_units():
    t = _table(_row("ibe/data/spherex/dr1/a.fits", 60800.7))
    (f,) = irsa.discover(133.8, -7.2, collections=("spherex_dr1",), fetch=lambda c, ra, dec: t)
    assert f.uri == "ibe/data/spherex/dr1/a.fits" and f.release == "dr1" and f.bandpass == "SPHEREx-D3"
    assert f.wave_lo_um == pytest.approx(1.6301) and f.wave_hi_um == pytest.approx(2.4346)
    assert f.pixel_arcsec == 6.15 and f.mjd_start == 60800.7 and (f.center_ra, f.center_dec) == (134.41, -9.0)
    assert len(f.footprint) == 4 and f.footprint[0] == (133.2, -6.8)


def test_discover_merges_collections_sorts_and_dedupes():
    tables = {
        "spherex_dr1": _table(_row("ibe/x/late.fits", 61000.0), _row("ibe/x/early.fits", 60800.0)),
        "spherex_qr3": _table(_row("ibe/x/mid.fits", 60900.0, coll="spherex_qr3"), _row("ibe/x/early.fits", 60800.0)),
    }
    files = irsa.discover(10, 10, collections=tuple(tables), fetch=lambda c, ra, dec: tables[c])
    assert [f.uri.rsplit("/", 1)[-1] for f in files] == ["early.fits", "mid.fits", "late.fits"]
    assert files[1].release == "qr3"


def test_empty_coverage_is_an_empty_list():
    empty = Table(names=["access_url"], dtype=[str])
    assert irsa.discover(10, 10, collections=("spherex_dr1",), fetch=lambda c, ra, dec: empty) == []


def test_missing_values_become_none_not_garbage():
    t = _table(_row("ibe/x/a.fits", 60800.0, em_min=float("nan"), s_region="", s_pixel_scale=float("nan")))
    (f,) = irsa.discover(10, 10, collections=("spherex_dr1",), fetch=lambda c, ra, dec: t)
    assert f.wave_lo_um is None and f.footprint == () and f.pixel_arcsec is None


def test_partial_failure_returns_the_collections_that_answered():
    def fetch(c, ra, dec):
        if c == "spherex_qr3":
            raise irsa.IrsaError("502")
        return _table(_row("ibe/x/a.fits", 60800.0))

    assert len(irsa.discover(10, 10, collections=("spherex_dr1", "spherex_qr3"), fetch=fetch)) == 1


def test_all_collections_failing_raises_a_retryable_error():
    def fetch(c, ra, dec):
        raise irsa.IrsaError("archive down")

    with pytest.raises(irsa.IrsaError) as e:
        irsa.discover(10, 10, collections=("spherex_dr1", "spherex_qr3"), fetch=fetch)
    assert e.value.retryable


def test_unexpected_exceptions_are_wrapped():
    def fetch(c, ra, dec):
        raise RuntimeError("boom")

    with pytest.raises(irsa.IrsaError):
        irsa.discover(10, 10, collections=("spherex_dr1",), fetch=fetch)


def test_parse_polygon_edge_cases():
    assert irsa.parse_polygon(None) == () and irsa.parse_polygon("POLYGON ICRS 1 2 3") == () and irsa.parse_polygon("CIRCLE 1 2 3") == ()
    assert irsa.parse_polygon("POLYGON ICRS 1 2 3 4 5 6") == ((1.0, 2.0), (3.0, 4.0), (5.0, 6.0))


def test_cutout_url_matches_documented_pattern():
    u = irsa.cutout_url("ibe/data/spherex/dr1/x.fits", 133.795, -7.245, 0.05)
    assert u == "https://irsa.ipac.caltech.edu/ibe/data/spherex/dr1/x.fits?center=133.795,-7.245d&size=0.05"
    with pytest.raises(ValueError):
        irsa.cutout_url("x", 1, 1, 5)


def _cache(tmp_path, handler, **kw):
    return irsa.CutoutCache(tmp_path, client=httpx.Client(transport=httpx.MockTransport(handler)), backoff_s=0, **kw)


def test_second_request_is_served_from_disk(tmp_path):
    calls = []

    def handler(req):
        calls.append(1)
        return httpx.Response(200, content=FITS_BYTES)

    c = _cache(tmp_path, handler)
    p1 = c.get("https://irsa.example/x.fits?center=1,2d&size=0.05")
    p2 = c.get("https://irsa.example/x.fits?center=1,2d&size=0.05")
    assert p1 == p2 and p1.read_bytes() == FITS_BYTES and len(calls) == 1


def test_retries_on_5xx_then_succeeds(tmp_path):
    seq = iter([503, 502, 200])

    def handler(req):
        s = next(seq)
        return httpx.Response(s, content=FITS_BYTES if s == 200 else b"")

    assert _cache(tmp_path, handler).get("https://irsa.example/y.fits").exists()


def test_gives_up_after_retries_and_reports_retryable(tmp_path):
    c = _cache(tmp_path, lambda req: httpx.Response(503), retries=2)
    with pytest.raises(irsa.IrsaError) as e:
        c.get("https://irsa.example/z.fits")
    assert e.value.retryable


def test_404_is_not_retried(tmp_path):
    n = []

    def handler(req):
        n.append(1)
        return httpx.Response(404)

    with pytest.raises(irsa.IrsaError) as e:
        _cache(tmp_path, handler).get("https://irsa.example/missing.fits")
    assert not e.value.retryable and len(n) == 1


def test_non_fits_body_is_rejected_and_not_cached(tmp_path):
    c = _cache(tmp_path, lambda req: httpx.Response(200, content=b"<html>error</html>"), retries=0)
    with pytest.raises(irsa.IrsaError):
        c.get("https://irsa.example/w.fits")
    assert not c.is_cached("https://irsa.example/w.fits")


def test_concurrent_requests_share_one_download(tmp_path):
    n = []
    gate = threading.Event()

    def handler(req):
        n.append(1)
        gate.wait(0.2)
        return httpx.Response(200, content=FITS_BYTES)

    c = _cache(tmp_path, handler)
    threads = [threading.Thread(target=c.get, args=("https://irsa.example/c.fits",)) for _ in range(6)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert len(n) == 1


VOTABLE = """<?xml version="1.0"?>
<VOTABLE version="1.3" xmlns="http://www.ivoa.net/xml/VOTable/v1.3"><RESOURCE type="results"><TABLE>
<FIELD ID="col_0" name="access_url" datatype="char" arraysize="*"/>
<FIELD ID="col_1" name="t_min" datatype="double"/>
<DATA><TABLEDATA><TR><TD>https://irsa.ipac.caltech.edu/ibe/data/spherex/dr1/a.fits</TD><TD>60800.7</TD></TR></TABLEDATA></DATA>
</TABLE></RESOURCE></VOTABLE>"""


def test_default_fetch_uses_column_names_not_generic_ids(monkeypatch):
    # Regression: the real archive's VOTable fields have generic IDs (col_0, ...); we must read them by name.
    monkeypatch.setattr(irsa.httpx, "get", lambda url, **kw: httpx.Response(200, content=VOTABLE.encode()))
    t = irsa._default_fetch("spherex_dr1", 10.0, 10.0)
    assert "access_url" in t.colnames and float(t["t_min"][0]) == 60800.7


def test_default_fetch_maps_http_failures(monkeypatch):
    monkeypatch.setattr(irsa.time, "sleep", lambda s: None)
    monkeypatch.setattr(irsa.httpx, "get", lambda url, **kw: httpx.Response(502))
    with pytest.raises(irsa.IrsaError) as e:
        irsa._default_fetch("spherex_dr1", 10.0, 10.0)
    assert e.value.retryable
    monkeypatch.setattr(irsa.httpx, "get", lambda url, **kw: httpx.Response(400))
    with pytest.raises(irsa.IrsaError) as e:
        irsa._default_fetch("spherex_dr1", 10.0, 10.0)
    assert not e.value.retryable


def test_some_collections_failing_and_nothing_found_is_an_error_not_no_coverage():
    """Regression (seen live on 2026-11-15): IRSA answered 502 for two collections, the others returned nothing, and the app said
    'no observations cover this position'. With part of the archive silent we cannot claim that."""
    def fetch(c, ra, dec):
        if c in ("spherex_dr1", "spherex_qr3"):
            raise irsa.IrsaError("502")
        return Table(names=["access_url"], dtype=["U10"])

    with pytest.raises(irsa.IrsaError) as e:
        irsa.discover(10, 10, collections=("spherex_dr1", "spherex_qr3", "spherex_dr1_deep"), fetch=fetch)
    assert e.value.retryable and "cannot be confirmed" in str(e.value)


def test_partial_results_name_the_collections_that_did_not_answer():
    def fetch(c, ra, dec):
        if c == "spherex_qr3":
            raise irsa.IrsaError("502")
        return _table(_row("ibe/x/a.fits", 60800.0))

    files = irsa.discover(10, 10, collections=("spherex_dr1", "spherex_qr3"), fetch=fetch)
    assert len(files) == 1 and files.incomplete == ["spherex_qr3"]
    complete = irsa.discover(10, 10, collections=("spherex_dr1",), fetch=lambda c, ra, dec: _table(_row("ibe/x/a.fits", 60800.0)))
    assert complete.incomplete == []


def test_default_fetch_retries_a_transient_502_then_succeeds(monkeypatch):
    sleeps, calls = [], []
    monkeypatch.setattr(irsa.time, "sleep", lambda s: sleeps.append(s))

    def fake_get(url, **kw):
        calls.append(url)
        return httpx.Response(502) if len(calls) < 3 else httpx.Response(200, content=VOTABLE.encode())

    monkeypatch.setattr(irsa.httpx, "get", fake_get)
    t = irsa._default_fetch("spherex_dr1", 10.0, 10.0)
    assert len(calls) == 3 and sleeps == [irsa.SIA_BACKOFF_S, 2 * irsa.SIA_BACKOFF_S] and float(t["t_min"][0]) == 60800.7


def test_default_fetch_does_not_retry_a_client_error(monkeypatch):
    calls = []
    monkeypatch.setattr(irsa.time, "sleep", lambda s: None)
    monkeypatch.setattr(irsa.httpx, "get", lambda url, **kw: (calls.append(1), httpx.Response(400))[1])
    with pytest.raises(irsa.IrsaError):
        irsa._default_fetch("spherex_dr1", 10.0, 10.0)
    assert len(calls) == 1
