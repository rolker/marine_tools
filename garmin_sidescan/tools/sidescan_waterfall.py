#!/usr/bin/env python3
"""
Render a Garmin GCV-20 sidescan + water-column waterfall from a bag.

A QA / inspection tool: decodes a recorded ``debug/raw`` stream with the SAME
code the driver uses (``garmin_sidescan.decode``) and produces a two-panel image
over a time window:

  * top    — down-look (water column), depth-corrected;
  * bottom — side-scan (port | starboard), slant-range scaled (sample index ->
    metres via the sub-header v2 range; NO ground-range / slant correction);
  * shared time x-axis.

Two input sources (``--source``, default auto): the sidescan ``debug/raw``
topic decoded with the driver's own decoder, or the driver's published
``sonar_image_*`` messages (per-ping scale recovered from ``sample_rate``,
issue #35; bottom line from ``~/nadir_depth``). Either way it is
**self-contained** — no external nav/sonar (no M3). The metres-per-sample
scale comes from each channel's own per-ping display range (sub-header ``v2``,
see ``docs/gcv_protocol.md``): the down-look's is the water-column depth
range, the side-scan's its slant-range swath extent (~2x larger), so each
channel is scaled by its OWN range. No bottom detection, no hard-coded
ladder. The down-look bottom range (``v1`` / ``nadir_depth``) is drawn on the
water-column panel as a check.

Sample values are shown **as decoded** (raw ``uint16``) on a single global
linear brightness scale per panel — no per-ping/contrast manipulation, so the
device's real brightness (e.g. the range-bracket gain step) is visible. The only
transform is spatial: resampling sample-index onto a metric axis. ``vmax``
defaults to a global high percentile (robust to the near-field spike); override
with ``--vmax``/``--vmin`` for the full ``uint16`` range.

Run in a sourced workspace (needs ``garmin_sidescan`` on the path) with
numpy + matplotlib available:

    python3 sidescan_waterfall.py BAG [--start S] [--end S] [--out out.png]
"""
import argparse
import collections
import sys

from garmin_sidescan.decode import (
    D807, dark_layer, EB07, echo_layer, generation_from_layers, PingAssembler)
import numpy as np
from rclpy.serialization import deserialize_message
import rosbag2_py
from std_msgs.msg import UInt8MultiArray

DOWN, PORT, STBD = 2, 0, 1        # GCV-20 channel map

# Per-ping scale shim for the messages source: the renderer needs only these
# two Subheader attributes, recovered from each RawSonarImage
# (range = sound_speed * bins / (2 * sample_rate)) and from ~/nadir_depth.
MsgScale = collections.namedtuple('MsgScale', 'display_range_m bottom_range_m')


def _reader(bag):
    r = rosbag2_py.SequentialReader()
    r.open(rosbag2_py.StorageOptions(uri=bag, storage_id='mcap'),
           rosbag2_py.ConverterOptions(input_serialization_format='cdr',
                                       output_serialization_format='cdr'))
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

    Returns ``{channel: [(t, sub, samples)]}`` where ``sub`` is the run's own
    parsed sub-header, attached by the assembler (None if no packet of the
    run parsed) -- the same per-ping sub-header the driver scales from.
    """
    r = _reader(bag)
    t0 = None
    asm = None                    # built once the generation vote resolves
    gen_votes = []
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
        if asm is None:
            # Pick the echo extractor from the stream itself (GCV-10 dark
            # layer vs GCV-20 first layer), voted like the driver.
            g = generation_from_layers(b)
            if g is not None:
                gen_votes.append(g)
            if len(gen_votes) < 5:
                continue
            gen = max(set(gen_votes), key=gen_votes.count)
            asm = PingAssembler(dark_layer if gen == 'gcv10' else echo_layer)
        out = []
        if (b[:2] == EB07 and len(b) > 32) or b[:2] == D807:
            out = asm.feed(b, recv_time=rel)
        for ch, samp, st, sub in out:
            if start <= st <= end and ch in chans:
                chans[ch].append((st, sub, samp))
    for ch, samp, st, sub in (asm.flush() if asm else []):
        if start <= st <= end and ch in chans:
            chans[ch].append((st, sub, samp))
    return chans


def read_pings_messages(bag, start, end):
    """
    Build the same ``{channel: [(t, sub, samples)]}`` from ``sonar_image_*``.

    The decoded-message source (#35): per-ping scale recovered from
    ``sample_rate`` (``range = sound_speed * bins / (2 * rate)``), the bottom
    line from ``~/nadir_depth`` when recorded.  Bags recorded before the
    driver published a scale (``sample_rate`` = 0) cannot be rendered from
    messages -- use the ``debug/raw`` source for those.
    """
    from marine_acoustic_msgs.msg import RawSonarImage
    from sensor_msgs.msg import Range

    side_to_ch = {'port': PORT, 'starboard': STBD, 'down': DOWN}
    img_topics = {}
    nadir_topic = None
    r = _reader(bag)
    for t in r.get_all_topics_and_types():
        for side, ch in side_to_ch.items():
            if t.name.endswith(f'sonar_image_{side}'):
                img_topics[t.name] = ch
        if t.name.endswith('nadir_depth'):
            nadir_topic = t.name
    if not img_topics:
        sys.exit('error: no sonar_image_* topics in the bag')

    chans = {DOWN: [], PORT: [], STBD: []}
    nadir = []                       # (t, bottom_range_m) from ~/nadir_depth
    t0 = None
    unscaled = 0
    while r.has_next():
        topic, data, ts = r.read_next()
        if topic not in img_topics and topic != nadir_topic:
            continue
        if t0 is None:
            t0 = ts
        rel = (ts - t0) / 1e9
        if rel > end:
            break
        if rel < start:
            continue
        if topic == nadir_topic:
            m = deserialize_message(data, Range)
            nadir.append((rel, m.range))
            continue
        m = deserialize_message(data, RawSonarImage)
        sv, rate = m.ping_info.sound_speed, m.sample_rate
        if rate > 0 and sv > 0 and m.samples_per_beam > 0:
            rng = sv * m.samples_per_beam / (2.0 * rate)
        else:
            rng = None
            unscaled += 1
        samples = bytes(m.image.data)
        if m.image.dtype == 0:           # DTYPE_UINT8 (GCV-10) -> uint16 LE
            samples = np.frombuffer(samples, dtype=np.uint8).astype(
                '<u2').tobytes()
        chans[img_topics[topic]].append((rel, MsgScale(rng, None), samples))
    total = sum(len(v) for v in chans.values())
    if total and unscaled == total:
        sys.exit('error: every recorded ping has sample_rate = 0 (bag predates '
                 'the per-ping scale, issue #35) — render from debug/raw instead')
    if unscaled:
        print(f'warning: {unscaled}/{total} pings carry no scale '
              f'(sample_rate = 0); they fall back to the median bin size')
    # attach the published nadir line to the down-look pings (nearest-in-time)
    if nadir and chans[DOWN]:
        times = np.array([t for t, _r in nadir])
        vals = np.array([r for _t, r in nadir])
        chans[DOWN] = [
            (t, MsgScale(sub.display_range_m,
                         float(vals[np.argmin(np.abs(times - t))])), samp)
            for t, sub, samp in chans[DOWN]]
    return chans


def _bin_size(sub, n_samples, fallback):
    """metres/sample from this channel's own display range (v2)."""
    if sub and sub.display_range_m and n_samples:
        return sub.display_range_m / n_samples
    return fallback


def _median_bin(pings):
    bs = [s.display_range_m / (len(samp) // 2)
          for _t, s, samp in pings if s and s.display_range_m and len(samp)]
    return float(np.median(bs)) if bs else 0.011


def _column(samp, sub, grid, fallback):
    """Resample a ping onto the metric grid -- RAW values, NaN beyond range."""
    a = np.frombuffer(samp, dtype='<u2').astype(float)
    bs = _bin_size(sub, len(a), fallback)
    return np.interp(grid / bs, np.arange(len(a)), a, right=np.nan)


def _show_raw(ax, img, extent, cmap, vmin, vmax):
    """Draw raw sample values on one global linear scale; NaN -> black."""
    import matplotlib.pyplot as plt
    finite = img[np.isfinite(img)]
    vlo = 0.0 if vmin is None else vmin
    vhi = vmax if vmax is not None else (float(np.percentile(finite, 99.5))
                                         if len(finite) else 1.0)
    cm = plt.get_cmap(cmap).copy()
    cm.set_bad('black')
    ax.imshow(img, aspect='auto', cmap=cm, interpolation='bilinear',
              extent=extent, vmin=vlo, vmax=vhi)


def render(chans, out, max_depth, across, vmin, vmax):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt

    _fig, (a1, a2) = plt.subplots(2, 1, figsize=(15, 10), sharex=True)

    # top: down-look depth-corrected (raw values, one global scale)
    fb_down = _median_bin(chans[DOWN])
    grid = np.linspace(0, max_depth, 560)
    dcols, dt, brl = [], [], []
    for st, sub, samp in chans[DOWN]:
        dcols.append(_column(samp, sub, grid, fb_down))
        dt.append(st)
        if sub and sub.bottom_range_m:
            brl.append((st, sub.bottom_range_m))
    _show_raw(a1, np.array(dcols).T, [dt[0], dt[-1], max_depth, 0], 'magma', vmin, vmax)
    if brl:
        a1.plot([x[0] for x in brl], [x[1] for x in brl], '.', color='cyan', ms=2,
                label='bottom range (sub-header v1)')
        a1.legend(loc='lower left', fontsize=8)
    a1.set_ylabel('depth (m)')
    a1.set_ylim(max_depth, 0)
    a1.set_title('GCV sidescan waterfall — down-look (top) + side-scan (bottom); '
                 'raw samples on a single global brightness scale')

    # bottom: side-scan port|starboard (each side scaled by its own v2)
    fb_side = _median_bin(chans[PORT] + chans[STBD])
    n = min(len(chans[PORT]), len(chans[STBD]))
    if len(chans[PORT]) != len(chans[STBD]):
        print(f'warning: port ({len(chans[PORT])}) and starboard '
              f'({len(chans[STBD])}) ping counts differ — index pairing may drift')
    a2.set_ylabel('slant range (m)\nPORT <- 0 -> STBD')
    a2.set_xlabel('time (s, from first imagery packet)')
    a1.set_xlim(dt[0], dt[-1])
    if n == 0:                                  # no side-scan in this window
        a2.text(0.5, 0.5, 'no side-scan pings in window', ha='center',
                va='center', transform=a2.transAxes)
        plt.tight_layout()
        plt.savefig(out, dpi=100)
        print(f'saved {out}')
        return
    NR = 420
    grid = np.linspace(0, across, NR)
    img = np.full((2 * NR, n), np.nan)
    stimes = []
    for i in range(n):
        cp = _column(chans[PORT][i][2], chans[PORT][i][1], grid, fb_side)
        cs = _column(chans[STBD][i][2], chans[STBD][i][1], grid, fb_side)
        img[:, i] = np.concatenate([cp[::-1], cs])
        stimes.append(chans[PORT][i][0])
    _show_raw(a2, img, [stimes[0], stimes[-1], across, -across], 'copper', vmin, vmax)
    a2.axhline(0, color='cyan', lw=0.5, alpha=0.5)
    plt.tight_layout()
    plt.savefig(out, dpi=100)
    print(f'saved {out}')


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[1])
    ap.add_argument('bag', help='path to an mcap bag (debug/raw and/or sonar_image_* topics)')
    ap.add_argument('--start', type=float, default=0.0,
                    help='window start (s, relative to the first imagery packet)')
    ap.add_argument('--end', type=float, default=1e9,
                    help='window end (s, relative to the first imagery packet)')
    ap.add_argument('--out', default='sidescan_waterfall.png', help='output PNG')
    ap.add_argument('--max-depth', type=float, default=20.0, help='water-column depth axis (m)')
    ap.add_argument('--across', type=float, default=50.0,
                    help='side-scan slant-range half-width (m)')
    ap.add_argument('--vmin', type=float, default=None,
                    help='raw-value black point (default 0)')
    ap.add_argument('--vmax', type=float, default=None,
                    help='raw-value white point (default a global 99.5%% percentile; '
                         'use 65535 for full uint16 range)')
    ap.add_argument('--source', choices=('auto', 'raw', 'messages'), default='auto',
                    help='input: raw = decode debug/raw with the same decoder '
                         'as the driver; messages = render the published '
                         'sonar_image_* (scale from sample_rate, bottom line '
                         'from nadir_depth); auto prefers raw when present')
    args = ap.parse_args(argv)

    raw_topic = find_raw_topic(args.bag)
    source = args.source
    if source == 'auto':
        source = 'raw' if raw_topic else 'messages'
        print(f'source: {source} (auto)')
    if source == 'raw':
        if raw_topic is None:
            sys.exit('error: no */debug/raw topic in the bag — record with '
                     'debug_raw:=true, or use --source messages')
        chans = read_pings(args.bag, raw_topic, args.start, args.end)
    else:
        chans = read_pings_messages(args.bag, args.start, args.end)
    if not chans[DOWN]:
        sys.exit('error: no down-look pings decoded in the window')
    print(f'down-look bin ~{_median_bin(chans[DOWN]) * 1000:.2f} mm/sample, '
          f'side-scan bin ~{_median_bin(chans[PORT] + chans[STBD]) * 1000:.2f} mm/sample')
    render(chans, args.out, args.max_depth, args.across, args.vmin, args.vmax)


if __name__ == '__main__':
    main()
