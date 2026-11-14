"""Throwaway: how long do real cutout downloads take, one at a time and concurrently? Uses a temp cache."""
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor

from processing import irsa

ra, dec = 133.795129, -7.245147
files = irsa.discover(ra, dec)
print("files:", len(files), flush=True)
d3 = [f for f in files if f.bandpass == "SPHEREx-D3"]
urls = [irsa.cutout_url(f.uri, ra, dec, 0.075) for f in d3[:8]]
cache = irsa.CutoutCache(tempfile.mkdtemp(), max_concurrent=4)

t = time.time()
for u in urls[:2]:
    t0 = time.time()
    p = cache.get(u)
    print(f"sequential cutout: {time.time() - t0:.1f}s, {p.stat().st_size} bytes", flush=True)
print(f"2 sequential total {time.time() - t:.1f}s", flush=True)

t = time.time()
with ThreadPoolExecutor(6) as pool:
    list(pool.map(cache.get, urls[2:8]))
print(f"6 concurrent (4 slots) total {time.time() - t:.1f}s", flush=True)
t = time.time()
[cache.get(u) for u in urls]
print(f"8 cached reads {time.time() - t:.3f}s", flush=True)
