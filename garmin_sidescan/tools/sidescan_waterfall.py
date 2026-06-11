#!/usr/bin/env python3
"""
Render a Garmin GCV-20 sidescan + water-column waterfall from a bag.

A QA / inspection tool: decodes a recorded ``debug/raw`` stream with the SAME
code the driver uses (``garmin_sidescan.decode``) and produces a two-panel image
over a time window:

  * top    — down-look (water column), depth-corrected;
  * bottom — side-scan (port | starboard), across-track corrected;
  * shared time x-axis.

It is **self-contained** — it depends only on the sidescan ``debug/raw`` topic,
no external nav/sonar (no M3). The metres-per-sample scale is derived from the
device's own per-ping **bottom-range varint** (sub-header offset 14, see
``docs/gcv_protocol.md``): for each range bracket (byte 13), bin size =
median(bottom_range / bottom_index) over the down-look pings, then applied to
both channels (they share the bracket). The decoded bottom range is also drawn
on the water-column panel as a check.

Requires a bag recorded with ``debug_raw:=true``. Run in a sourced workspace
(needs ``garmin_sidescan`` on the path) with numpy + matplotlib available:

    python3 sidescan_waterfall.py BAG [--start S] [--end S] [--out out.png]
"""
import argparse
import sys

from garmin_sidescan.decode import (
    D807, EB07, echo_layer, is_water_column, PingAssembler,
    RANGE_BRACKET_OFFSET, subheader_bottom_range_m)
import numpy as np
from rclpy.serialization import deserialize_message
import rosbag2_py
from std_msgs.msg import UInt8MultiArray

DOWN, PORT, STBD = 2, 0, 1        # GCV-20 channel map


def find_raw_topic(bag):
    """Return the first topic ending in ``debug/raw`` (UInt8MultiArray), or None."""
    r = _reader(bag)
    for t in r.get_all_topics_and_types():
        if t.name.endswith('debug/raw'):
            return t.name
    return None


def _reader(bag):
    r = rosbag2_py.SequentialReader()
    r.open(rosbag2_py.StorageOptions(uri=bag, storage_id='mcap'),
           rosbag2_py.ConverterOptions('', ''))
    return r


def detect_bottom(samples):
    """Leading edge of the strongest sustained return (the bottom), or None."""
    a = np.frombuffer(samples, dtype='<u2').astype(float)
    n = len(a)
    if n < 200:
        return None
    g = int(n * 0.06)
    s = np.convolve(a, np.ones(21) / 21, mode='same')
    s[:g] = 0
    pk = int(np.argmax(s))
    i = pk
    while i > g and s[i] > 0.5 * s[pk]:
        i -= 1
    return i


def read_pings(bag, raw_topic, start, end):
    """Assemble pings in [start, end]; return per-channel lists + range brackets."""
    r = _reader(bag)
    t0 = None
    asm = PingAssembler(echo_layer)
    bracket = None
    bottom_range = None
    chans = {DOWN: [], PORT: [], STBD: []}
    while r.has_next():
        topic, data, ts = r.read_next()
        if topic != raw_topic:
            continue
        if t0 is None:
            t0 = ts
        rel = (ts - t0) / 1e9
        if rel > end + 2:
            break
        b = bytes(deserialize_message(data, UInt8MultiArray).data)
        out = []
        if b[:2] == EB07 and len(b) > 32:
            bracket = b[RANGE_BRACKET_OFFSET]
            if is_water_column(b):
                bottom_range = subheader_bottom_range_m(b)
            out = asm.feed(b, recv_time=rel)
        elif b[:2] == D807:
            out = asm.feed(b, recv_time=rel)
        for ch, samp, st in out:
            if start <= st <= end and ch in chans:
                br = bottom_range if ch == DOWN else None
                chans[ch].append((st, bracket, samp, br))
    return chans


def bin_size_by_bracket(down_pings):
    """median(bottom_range / bottom_index) per range bracket -> metres/sample."""
    acc = {}
    for _st, bracket, samp, br in down_pings:
        if br is None or br <= 0:
            continue
        bi = detect_bottom(samp)
        if bi and bi > 50:
            acc.setdefault(bracket, []).append(br / bi)
    return {k: float(np.median(v)) for k, v in acc.items() if v}


def _waterfall(pings, bin_size, axis_m, n_rows, fold=False):
    """Resample each ping onto a 0..axis_m grid; per-ping normalize; -> 2D image."""
    grid = np.linspace(0, axis_m, n_rows)
    fallback = np.median(list(bin_size.values())) if bin_size else 0.007
    cols = []
    times = []
    for st, bracket, samp, _br in pings:
        a = np.frombuffer(samp, dtype='<u2').astype(float)
        bs = bin_size.get(bracket, fallback)
        col = np.interp(grid / bs, np.arange(len(a)), a, right=np.nan)
        v = col[np.isfinite(col)]
        if len(v) > 10:
            lo, hi = np.percentile(v, 35), np.percentile(v, 99.5)
            col = np.clip((col - lo) / (hi - lo + 1e-9), 0, 1)
        cols.append(np.nan_to_num(col))
        times.append(st)
    return np.array(cols).T, np.array(times)


def render(chans, bin_size, out, max_depth, across):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt

    fig, (a1, a2) = plt.subplots(2, 1, figsize=(15, 10), sharex=True)
    # top: down-look depth-corrected + decoded bottom-range line
    dt, dtimes = _waterfall(chans[DOWN], bin_size, max_depth, 560)
    a1.imshow(dt, aspect='auto', cmap='magma', interpolation='bilinear',
              extent=[dtimes[0], dtimes[-1], max_depth, 0])
    br = [(st, b) for st, _, _, b in chans[DOWN] if b is not None]
    if br:
        a1.plot([x[0] for x in br], [x[1] for x in br], '.', color='cyan', ms=2,
                label='decoded bottom range (varint)')
        a1.legend(loc='lower left', fontsize=8)
    a1.set_ylabel('depth (m)')
    a1.set_ylim(max_depth, 0)
    a1.set_title('GCV sidescan waterfall — down-look (top) + side-scan (bottom), '
                 'bin size self-derived from the bottom-range varint')
    # bottom: side-scan port|starboard
    n = min(len(chans[PORT]), len(chans[STBD]))
    NR = 420
    grid = np.linspace(0, across, NR)
    fallback = np.median(list(bin_size.values())) if bin_size else 0.007
    img = np.full((2 * NR, n), 0.0)
    stimes = []
    for i in range(n):
        col = []
        for side, flip in ((PORT, True), (STBD, False)):
            st, bracket, samp, _ = chans[side][i]
            a = np.frombuffer(samp, dtype='<u2').astype(float)
            bs = bin_size.get(bracket, fallback)
            c = np.interp(grid / bs, np.arange(len(a)), a, right=np.nan)
            v = c[np.isfinite(c)]
            if len(v) > 10:
                lo, hi = np.percentile(v, 30), np.percentile(v, 99.3)
                c = np.clip((c - lo) / (hi - lo + 1e-9), 0, 1)
            col.append(np.nan_to_num(c)[::-1] if flip else np.nan_to_num(c))
        img[:, i] = np.concatenate(col)
        stimes.append(chans[PORT][i][0])
    a2.imshow(img, aspect='auto', cmap='copper', interpolation='bilinear',
              extent=[stimes[0], stimes[-1], across, -across])
    a2.axhline(0, color='cyan', lw=0.5, alpha=0.5)
    a2.set_ylabel('across-track (m)\nPORT <- 0 -> STBD')
    a2.set_xlabel('time in bag (s)')
    a1.set_xlim(dtimes[0], dtimes[-1])
    plt.tight_layout()
    plt.savefig(out, dpi=100)
    print(f'saved {out}')


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[1])
    ap.add_argument('bag', help='path to an mcap bag recorded with debug_raw:=true')
    ap.add_argument('--start', type=float, default=0.0, help='window start (s into bag)')
    ap.add_argument('--end', type=float, default=1e9, help='window end (s into bag)')
    ap.add_argument('--out', default='sidescan_waterfall.png', help='output PNG')
    ap.add_argument('--max-depth', type=float, default=20.0, help='water-column depth axis (m)')
    ap.add_argument('--across', type=float, default=20.0,
                    help='side-scan across-track half-width (m)')
    args = ap.parse_args(argv)

    raw_topic = find_raw_topic(args.bag)
    if raw_topic is None:
        sys.exit('error: no */debug/raw topic in the bag — record with debug_raw:=true')
    chans = read_pings(args.bag, raw_topic, args.start, args.end)
    if not chans[DOWN]:
        sys.exit('error: no down-look pings decoded in the window')
    bs = bin_size_by_bracket(chans[DOWN])
    print('bin size per range bracket (mm/sample): '
          + ', '.join(f'0x{k:02x}={v*1000:.2f}' for k, v in sorted(bs.items())))
    render(chans, bs, args.out, args.max_depth, args.across)


if __name__ == '__main__':
    main()
