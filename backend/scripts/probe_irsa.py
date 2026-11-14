"""Throwaway IRSA probe (checklist P0-01..P0-04). Not part of the app.

Usage: python scripts/probe_irsa.py [ra_deg dec_deg]
Default target: WISE 0855 (from the challenge text).
"""
import sys
from astroquery.ipac.irsa import Irsa

ra = float(sys.argv[1]) if len(sys.argv) > 1 else (8 + 55 / 60 + 10.831 / 3600) * 15
dec = float(sys.argv[2]) if len(sys.argv) > 2 else -(7 + 14 / 60 + 42.53 / 3600)
print(f"target ra={ra:.5f} dec={dec:.5f}")

print("\n== P0-02 schema")
cols = Irsa.query_tap(
    "SELECT table_name, column_name, datatype, unit, description FROM TAP_SCHEMA.columns "
    "WHERE table_name IN ('spherex.plane','spherex.artifact')"
).to_table()
for r in cols:
    print(f"{r['table_name']:16} {r['column_name']:28} {str(r['datatype']):10} {str(r['unit'] or ''):8} {str(r['description'])[:70]}")

print("\n== P0-01 query")
q = (
    "SELECT a.uri, p.time_bounds_lower, p.energy_bandpassname, p.energy_bounds_lower, p.energy_bounds_upper "
    "FROM spherex.artifact a JOIN spherex.plane p ON a.planeid = p.planeid "
    f"WHERE 1 = CONTAINS(POINT('ICRS', {ra}, {dec}), p.poly) ORDER BY p.time_bounds_lower"
)
try:
    t = Irsa.query_tap(q).to_table()
except Exception as e:  # column names may differ; report and fall back
    print("full query failed:", repr(e)[:300])
    q = q.replace(", p.energy_bounds_lower, p.energy_bounds_upper", "")
    t = Irsa.query_tap(q).to_table()
print("rows:", len(t))
t.pprint(max_lines=40, max_width=200)
t.write("data/_probe_rows.csv", overwrite=True, format="csv")
