"""P0-37: a bundle built while the archive was reachable lets the demo targets work with the archive UNREACHABLE.

Stage 1 builds a bundle with a stand-in archive (a mock HTTP transport serving full-size synthetic cutouts). Stage 2 reopens the same
data directory in a brand-new Services whose archive discovery raises and whose HTTP client fails every request, then exercises the
things a judge would do."""
import math
import shutil

import httpx
import numpy as np
import pytest
from astropy.io import fits

from processing import irsa
from processing.fits_io import read_cutout
from services import bundle
from services.core import NotFound, Services
from store.registry import Registry
from tests.conftest import CENTER, make_cutout_fits
from tests.test_api import STAR, URIS, _file, _no_nulls

pytestmark = pytest.mark.synthetic

OMEGA = (6.2 / 206264.806247) ** 2


def _full_cutout(path, uri_spec) -> bytes:
    """A synthetic cutout padded with a big PSF-table-like extension, so slimming has something real to drop."""
    make_cutout_fits(path, *STAR, rotation_deg=uri_spec["rotation"], mjd=uri_spec["mjd"], detector=uri_spec["detector"], flag_value=(1 << 21))
    with fits.open(path, mode="append") as h:
        cols = [fits.Column(name="PSF", format="20000E", array=np.random.default_rng(1).normal(size=(40, 20000)).astype("float32"))]
        h.append(fits.BinTableHDU.from_columns(cols, name="EPSF"))
    return path.read_bytes()


class Archive:
    """Stand-in IRSA: serves a full synthetic cutout for each known file URI, can be switched off."""

    def __init__(self, tmp_path):
        self.up = True
        self.bodies = {uri: _full_cutout(tmp_path / f"full{i}.fits", spec) for i, (uri, spec) in enumerate(URIS.items())}
        self.requests = 0

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests += 1
        if not self.up:
            raise httpx.ConnectError("archive unreachable")
        for uri, body in self.bodies.items():
            if uri in str(request.url):
                return httpx.Response(200, content=body)
        return httpx.Response(404)


def _services(data_dir, archive, *, discover, resolver, retries=0):
    cache = irsa.CutoutCache(data_dir / "cutouts", client=httpx.Client(transport=httpx.MockTransport(archive.handler)), retries=retries, backoff_s=0)
    return Services(Registry(data_dir / "registry.sqlite"), cache, data_dir / "images", discover=discover, resolver=resolver)


@pytest.fixture
def built(tmp_path):
    """Stage 1: build the bundle while the archive is up. Returns (data_dir, archive, report, slim_report, a_full_cutout_size)."""
    data = tmp_path / "bundle"
    archive = Archive(tmp_path)
    files = [_file(u, s) for u, s in URIS.items()]
    svc = _services(data, archive, discover=lambda ra, dec: files, resolver=lambda name: CENTER)
    report = bundle.warm_target(svc, "Synthetic Test Field", band="SPHEREx-D3", frames=50, workers=2, pause_s=0, log=lambda *a: None)
    full_sizes = [p.stat().st_size for p in (data / "cutouts").glob("*.fits")]
    slim = bundle.slim_directory(data / "cutouts")
    return data, archive, report, slim, max(full_sizes)


def _offline(data_dir, archive):
    """Stage 2: same data directory, brand-new process-like Services, archive discovery raises and every download fails."""
    archive.up = False

    def no_discovery(ra, dec):
        raise irsa.IrsaError("The SPHEREx archive could not be reached.")

    def no_resolver(name):
        raise NotFound("name resolver is offline")

    return _services(data_dir, archive, discover=no_discovery, resolver=no_resolver)


def test_building_warms_every_frame_and_slimming_drops_the_unused_extensions(built):
    data, archive, report, slim, full_size = built
    assert report["frames"] == 2 and report["ready"] == 2 and report["missing"] == []          # the two D3 frames; the D6 frame is not in the band
    assert slim["files"] == 2 and slim["bytesAfter"] < 0.1 * slim["bytesBefore"]
    for f in (data / "cutouts").glob("*.fits"):
        names = [h.name for h in fits.open(f)]
        assert names == ["PRIMARY", "IMAGE", "FLAGS", "VARIANCE", "ZODI", "WCS-WAVE"]


def test_a_slim_cutout_reads_exactly_like_the_full_one(tmp_path):
    spec = next(iter(URIS.values()))
    full = tmp_path / "full.fits"
    _full_cutout(full, spec)
    slim = tmp_path / "slim.fits"
    before, after = bundle.slim_fits(full, slim)
    assert after < 0.1 * before
    a, b = read_cutout(full), read_cutout(slim)
    for name in ("image", "flags", "variance", "zodi", "wavelength_um", "bandwidth_um"):
        np.testing.assert_array_equal(getattr(a, name), getattr(b, name), err_msg=name)
    assert a.mjd_obs == b.mjd_obs and a.detector == b.detector and a.unit == b.unit and a.wcs.to_header() == b.wcs.to_header()


def test_slimming_refuses_a_file_without_an_image(tmp_path):
    bad = tmp_path / "bad.fits"
    fits.HDUList([fits.PrimaryHDU(), fits.ImageHDU(np.zeros((3, 3)), name="OTHER")]).writeto(bad)
    with pytest.raises(ValueError, match="no IMAGE"):
        bundle.slim_fits(bad, tmp_path / "out.fits")


def test_offline_resolve_listing_and_images(built):
    data, archive, *_ = built
    svc = _offline(data, archive)
    oid = _object_ids(data)[0]
    page = svc.list_observations(oid, bandpass="SPHEREx-D3")
    assert page["total"] == 2 and "coverageNote" not in page                        # a recent saved listing is served as if nothing were wrong
    _no_nulls({k: v for k, v in page.items() if k != "items"})
    svc.snapshot_max_age_s = 0                                                       # an OLD saved listing with the archive down: served, and the user is told
    svc._disc_cache.clear()
    assert "last saved listing" in svc.list_observations(oid, bandpass="SPHEREx-D3")["coverageNote"]
    svc.snapshot_max_age_s = 3 * 86400.0
    before = archive.requests
    for item in page["items"]:
        img = svc.get_image(item["observationId"], wait_s=5)
        assert img["availability"] == {"status": "available"} and img["width"] > 0
        assert (data / "images" / img["imageUrl"].rsplit("/", 1)[1]).exists()
    assert archive.requests == before                                                # the pre-rendered images needed no download at all


def _object_ids(data):
    import sqlite3
    with sqlite3.connect(data / "registry.sqlite") as c:
        return [r[0] for r in c.execute("select id from objects")]


def test_offline_other_stretches_come_from_the_slim_cutouts(built):
    data, archive, *_ = built
    svc = _offline(data, archive)
    item = svc.list_observations(_object_ids(data)[0], bandpass="SPHEREx-D3")["items"][0]
    img = svc.get_image(item["observationId"], stretch="linear", low_pct=5, high_pct=95, wait_s=10)       # never pre-rendered
    assert img["availability"] == {"status": "available"} and img["stretch"]["mode"] == "linear"


def test_offline_photometry_matches_the_online_value(built):
    data, archive, *_ = built
    (data.parent / "second").mkdir(exist_ok=True)
    online_archive = Archive(data.parent / "second")                                  # a second, live stand-in to get the reference value
    files = [_file(u, s) for u, s in URIS.items()]
    live = _services(data.parent / "live", online_archive, discover=lambda ra, dec: files, resolver=lambda n: CENTER)
    oid = live.resolve(q="Synthetic Test Field")["objectId"]
    live_item = live.list_observations(oid, bandpass="SPHEREx-D3")["items"][0]
    region = {"center": {"raDeg": STAR[0], "decDeg": STAR[1]}, "radiusArcsec": 20.0}
    reference = live.create_measurement("aperture_flux", [live_item["observationId"]], region)

    svc = _offline(data, archive)
    item = svc.list_observations(_object_ids(data)[0], bandpass="SPHEREx-D3")["items"][0]
    got = svc.create_measurement("aperture_flux", [item["observationId"]], region)
    assert got["value"] == pytest.approx(reference["value"], rel=1e-9) and got["uncertainty"] == pytest.approx(reference["uncertainty"], rel=1e-9)
    assert got["wavelengthUm"] == pytest.approx(reference["wavelengthUm"])


def test_offline_comparison_and_difference_work_for_bundled_frames(built):
    data, archive, *_ = built
    svc = _offline(data, archive)
    items = svc.list_observations(_object_ids(data)[0], bandpass="SPHEREx-D3")["items"]
    cmp_ = svc.create_comparison(items[0]["observationId"], items[1]["observationId"], "difference")
    assert cmp_["availability"] == {"status": "available"} and cmp_["compatibility"]["verdict"] != "not_comparable"
    assert cmp_["differenceImage"]["availability"] == {"status": "available"} and cmp_["baseImage"]["availability"] == {"status": "available"}


def test_a_frame_outside_the_bundle_fails_cleanly_and_retryably(built):
    data, archive, *_ = built
    svc = _offline(data, archive)
    oid = _object_ids(data)[0]
    # the D6 frame was never warmed; its record exists only because the saved listing contains it
    page = svc.list_observations(oid)
    d6 = next(i for i in page["items"] if i["bandpass"] == "SPHEREx-D6")
    img = svc.get_image(d6["observationId"], wait_s=5)
    assert img["availability"]["status"] == "failed" and img["availability"]["retryAfterSeconds"] == 30 and "imageUrl" not in img


def test_the_bundle_does_not_depend_on_the_machine_it_was_built_on(built, tmp_path):
    data, archive, *_ = built
    moved = tmp_path / "elsewhere"
    shutil.copytree(data, moved)                                                      # what the Dockerfile COPY does
    svc = _offline(moved, archive)
    item = svc.list_observations(_object_ids(moved)[0], bandpass="SPHEREx-D3")["items"][0]
    assert svc.get_image(item["observationId"], wait_s=5)["availability"] == {"status": "available"}
