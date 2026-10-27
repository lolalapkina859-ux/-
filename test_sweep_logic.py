"""Lightweight regression checks for H1/H4 sweep detection."""
from datetime import datetime, timezone
from scanner import _find_h1_sweep, _find_h4_sweep

now = int(datetime.now(timezone.utc).timestamp())

def bars(n, step, start, base=100.0):
    out=[]
    for i in range(n):
        t=start+i*step
        out.append({"timestamp":t,"open":base,"high":base+1,"low":base-1,"close":base})
    return out

# H1: the latest fully closed candle sweeps prior highs and closes back below.
h1=bars(40,3600,now-40*3600)
h1[-1].update(high=105.0, close=100.0)
assert _find_h1_sweep(h1) is not None

# H4: the latest fully closed candle sweeps prior lows and closes back above.
h4=bars(30,14400,now-30*14400)
h4[-1].update(low=95.0, close=100.0)
res=_find_h4_sweep(h4)
assert res is not None and res["direction"] == "LONG" and res["source_tf"] == "H4"
print("H1/H4 sweep detector checks passed")
