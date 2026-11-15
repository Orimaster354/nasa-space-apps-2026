"""Usage (from backend/): PYTHONPATH=. python scripts/ship_snapshot.py <running build dir> <ship dir>, then SPX_DATA_DIR=<ship dir> PYTHONPATH=. python scripts/ship_finalize.py <ship dir>.
Snapshot the running build's data directory, slim it, and verify it, WITHOUT touching the running build."""
import os, shutil, sqlite3, sys, time
from pathlib import Path
src, dst = Path(sys.argv[1]), Path(sys.argv[2])
if dst.exists():
    shutil.rmtree(dst)
dst.mkdir(parents=True)
t = time.time()
for sub in ("cutouts", "images"):
    (dst / sub).mkdir()
    n = 0
    for f in (src / sub).iterdir():
        if f.is_file() and not f.name.endswith(".tmp"):
            shutil.copy2(f, dst / sub / f.name); n += 1
    print(sub, n, "files copied")
a = sqlite3.connect(src / "registry.sqlite"); b = sqlite3.connect(dst / "registry.sqlite")
a.backup(b); b.close(); a.close()
print(f"copied in {time.time()-t:.0f}s")
sys.path.insert(0, ".")
from services import bundle
r = bundle.slim_directory(dst / "cutouts")
print(f"slimmed {r['files']} cutouts: {r['bytesBefore']/1e6:.0f} MB -> {r['bytesAfter']/1e6:.0f} MB")
tot = sum(f.stat().st_size for f in dst.rglob("*") if f.is_file())
print(f"ship bundle total {tot/1e6:.0f} MB")
