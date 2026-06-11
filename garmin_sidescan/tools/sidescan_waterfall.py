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
no external nav/sonar (no M3). The metres-per-sample scale comes straight from
each channel's sub-header: ``bin_size = display_range / n_bins`` where the
display range is that channel's per-ping ``v2`` varint (see
``docs/gcv_protocol.md``). The down-look's v2 is the water-column depth range;
the side-scan's v2 is the across-track slant range (~2x larger), so each channel
is scaled by its OWN v2. No bottom detection, no hard-coded ladder. The down-look
``v1`` (bottom range) is drawn on the water-column panel as a check.

Requires a bag recorded with ``debug_raw:=true``. Run in a sourced workspace
(needs ``garmin_sidescan`` on the path) with numpy + matplotlib available:

    python3 sidescan_waterfall.py BAG [--start S] [--end S] [--out out.png]
"""
import argparse
import sys

from garmin_sidescan.decode import (
    D807, EB07, echo_layer, parse_subheader, PingAssembler)
import numpy as np
from rclpy.serialization import deserialize_message
import rosbag2_py
from std_msgs.msg import UInt8MultiArray

DOWN, PORT, STBD = 2, 0, 1        # GCV-20 channel map


def _reader(bag):
    r = rosbag2_py.SequentialReader()
    r.open(rosbag2_py.StorageOptions(uri=bag, storage_id='mcap'),
           rosbag2_py.ConverterOptions('', ''))
    return r


def find_raw_topic(bag):
    """Return the first topic ending in ``debug/raw``, or None."""
    for t in _reader(bag).get_all_topics_and_types():
        if t.name.endswith('debug/raw'):
            return t.name
    return None


def read_pings(bag, raw_topic, start, end):
    """
    Assemble pings in ``[start, end]``.

    Returns ``{channel: [(t, sub, samples)]}`` where ``sub`` is the parsed
    sub-header for that channel (None if it failed to parse).
    """
    r = _reader(bag)
    t0 = None
    asm = PingAssembler(echo_layer)
    subs = {}                         # channel -> latest parsed Subheader
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
            s = parse_subheader(b)
            if s is not None:
                subs[s.channel] = s
            out = asm.feed(b, recv_time=rel)
        elif b[:2] == D807:
            out = asm.feed(b, recv_time=rel)
        for ch, samp, st in out:
            if start <= st <= end and ch in chans:
                chans[ch].append((st, subs.get(ch), samp))
    return chans


def _bin_size(sub, n_samples, fallback):
    """metres/sample from this channel's own display range (v2)."""
    if sub and sub.display_range_m and n_samples:
        return sub.display_range_m / n_samples
    return fallback


def _normalize(col):
    v = col[np.isfinite(col)]
    if len(v) > 10:
        lo, hi = np.percentile(v, 35), np.percentile(v, 99.5)
        col = np.clip((col - lo) / (hi - lo + 1e-9), 0, 1)
    return np.nan_to_num(col)


def _median_bin(pings):
    bs = [s.display_range_m / (len(samp) // 2)
          for _t, s, samp in pings if s and s.display_range_m and len(samp)]
    return float(np.median(bs)) if bs else 0.011


def render(chans, out, max_depth, across):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt

    fig, (a1, a2) = plt.subplots(2, 1, figsize=(15, 10), sharex=True)

    # top: down-look depth-corrected (per-ping bin = water-column v2 / n_bins)
    fb_down = _median_bin(chans[DOWN])
    grid = np.linspace(0, max_depth, 560)
    dcols, dt, brl = [], [], []
    for st, sub, samp in chans[DOWN]:
        a = np.frombuffer(samp, dtype='<u2').astype(float)
        bs = _bin_size(sub, len(a), fb_down)
        dcols.append(_normalize(np.interp(grid / bs, np.arange(len(a)), a, right=np.nan)))
        dt.append(st)
        if sub and sub.bottom_range_m:
            brl.append((st, sub.bottom_range_m))
    a1.imshow(np.array(dcols).T, aspect='auto', cmap='magma', interpolation='bilinear',
              extent=[dt[0], dt[-1], max_depth, 0])
    if brl:
        a1.plot([x[0] for x in brl], [x[1] for x in brl], '.', color='cyan', ms=2,
                label='bottom range (sub-header v1)')
        a1.legend(loc='lower left', fontsize=8)
    a1.set_ylabel('depth (m)')
    a1.set_ylim(max_depth, 0)
    a1.set_title('GCV sidescan waterfall — down-look (top) + side-scan (bottom); '
                 'each channel scaled by its own display-range varint (v2)')

    # bottom: side-scan port|starboard (each side scaled by its own v2)
    fb_side = _median_bin(chans[PORT] + chans[STBD])
    n = min(len(chans[PORT]), len(chans[STBD]))
    NR = 420
    grid = np.linspace(0, across, NR)
    img = np.zeros((2 * NR, n))
    stimes = []
    for i in range(n):
        parts = []
        for side, flip in ((PORT, True), (STBD, False)):
            st, sub, samp = chans[side][i]
            a = np.frombuffer(samp, dtype='<u2').astype(float)
            bs = _bin_size(sub, len(a), fb_side)
            c = _normalize(np.interp(grid / bs, np.arange(len(a)), a, right=np.nan))
            parts.append(c[::-1] if flip else c)
        img[:, i] = np.concatenate(parts)
        stimes.append(chans[PORT][i][0])
    a2.imshow(img, aspect='auto', cmap='copper', interpolation='bilinear',
              extent=[stimes[0], stimes[-1], across, -across])
    a2.axhline(0, color='cyan', lw=0.5, alpha=0.5)
    a2.set_ylabel('across-track (m)\nPORT <- 0 -> STBD')
    a2.set_xlabel('time in bag (s)')
    a1.set_xlim(dt[0], dt[-1])
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
    ap.add_argument('--across', type=float, default=50.0,
                    help='side-scan across-track half-width (m)')
    args = ap.parse_args(argv)

    raw_topic = find_raw_topic(args.bag)
    if raw_topic is None:
        sys.exit('error: no */debug/raw topic in the bag — record with debug_raw:=true')
    chans = read_pings(args.bag, raw_topic, args.start, args.end)
    if not chans[DOWN]:
        sys.exit('error: no down-look pings decoded in the window')
    print(f'down-look bin ~{_median_bin(chans[DOWN]) * 1000:.2f} mm/sample, '
          f'side-scan bin ~{_median_bin(chans[PORT] + chans[STBD]) * 1000:.2f} mm/sample')
    render(chans, args.out, args.max_depth, args.across)


if __name__ == '__main__':
    main()
