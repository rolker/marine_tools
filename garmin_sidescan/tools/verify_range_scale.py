#!/usr/bin/env python3
"""
Verify the per-ping range scale + nadir depth against a recorded bag.

Decodes a ``debug/raw`` stream with the SAME code the driver uses
(``PingAssembler`` + per-run sub-header) and plots, on a shared time axis:

  * per channel, the **sub-header v2 display range** -- the metric scale the
    driver now publishes via ``sample_rate`` (issue #35); v1 tag-length
    transitions (the ex-"bracket", where device gain steps) are marked;
  * the **v1 bottom range** the driver now publishes as ``nadir_depth``
    (issue #16);
  * what the bag's recorded ``sonar_image_*`` messages actually carried
    (``sound_speed * bins / (2 * sample_rate)``, NaN when unavailable) and the
    ``state`` topic's commanded-range mirror -- the before/after comparison.

Interactive by default (zoom/pan with the matplotlib toolbar; try the Cod Rock
auto-range transitions); ``--out FILE`` renders headless to a PNG instead.

Requires a bag recorded with ``debug_raw:=true``. Run in a sourced workspace
(needs ``garmin_sidescan`` on the path) with numpy + matplotlib available:

    python3 verify_range_scale.py BAG [--start S] [--end S] [--out out.png]
"""
import argparse
import sys

from garmin_sidescan.decode import (
    D807, dark_layer, EB07, echo_layer, generation_from_layers, PingAssembler)
import numpy as np
from rclpy.serialization import deserialize_message
import rosbag2_py
from std_msgs.msg import UInt8MultiArray

CHAN_NAMES = {0: 'port', 1: 'stbd', 2: 'down'}     # GCV-20 channel map


def _reader(bag):
    r = rosbag2_py.SequentialReader()
    r.open(rosbag2_py.StorageOptions(uri=bag, storage_id='mcap'),
           rosbag2_py.ConverterOptions(input_serialization_format='cdr',
                                       output_serialization_format='cdr'))
    return r


def read_bag(bag, start, end):
    """
    Collect everything the figure needs, in one pass.

    Returns ``(decoded, published, commanded)``:
    ``decoded[ch] = [(t, v1, v2, v1_tag)]`` from the raw stream (driver-path
    decode); ``published[side] = [(t, implied_range)]`` from the recorded
    ``sonar_image_*`` messages; ``commanded = [(t, range_m)]`` from ``state``.
    """
    from marine_acoustic_msgs.msg import RawSonarImage
    from marine_radar_control_msgs.msg import RadarControlSet

    raw_topic = None
    img_topics = {}
    state_topic = None
    r = _reader(bag)
    for t in r.get_all_topics_and_types():
        if t.name.endswith('debug/raw'):
            raw_topic = t.name
        elif (t.type == 'marine_acoustic_msgs/msg/RawSonarImage'
              and 'sonar_image_' in t.name):
            # name-filtered to the driver's own publishers: the same bag may
            # carry other RawSonarImage sources (e.g. the M3 multibeam)
            img_topics[t.name] = t.name.rsplit('_', 1)[-1]
        elif t.type == 'marine_radar_control_msgs/msg/RadarControlSet':
            state_topic = t.name
    if raw_topic is None:
        sys.exit('error: no */debug/raw topic in the bag — record with debug_raw:=true')

    asm = None                    # built once the generation vote resolves
    gen_votes = []
    decoded = {}
    published = {}
    commanded = []
    t0 = None
    while r.has_next():
        topic, data, ts = r.read_next()
        # Anchor the window to the first raw datagram (same convention as
        # sidescan_waterfall / replay_debug_raw, so windows are portable);
        # messages before it are skipped.
        if t0 is None:
            if topic != raw_topic:
                continue
            t0 = ts
        rel = (ts - t0) / 1e9
        if rel > end + 2:
            break
        if topic == raw_topic:
            b = bytes(deserialize_message(data, UInt8MultiArray).data)
            if asm is None:
                # Pick the per-packet echo extractor from the stream itself
                # (GCV-10 dark layer vs GCV-20 first layer), voted like the
                # driver so one odd packet can't misclassify the bag.
                g = generation_from_layers(b)
                if g is not None:
                    gen_votes.append(g)
                if len(gen_votes) < 5:
                    continue
                gen = max(set(gen_votes), key=gen_votes.count)
                asm = PingAssembler(dark_layer if gen == 'gcv10' else echo_layer)
            if (b[:2] == EB07 and len(b) > 32) or b[:2] == D807:
                for line in asm.feed(b, recv_time=rel):
                    if line.subheader and start <= line.stamp <= end:
                        s = line.subheader
                        decoded.setdefault(line.channel, []).append(
                            (line.stamp, s.bottom_range_m, s.display_range_m,
                             s.v1_tag))
        elif topic in img_topics and start <= rel <= end:
            m = deserialize_message(data, RawSonarImage)
            sv, rate = m.ping_info.sound_speed, m.sample_rate
            rng = (sv * m.samples_per_beam / (2.0 * rate)
                   if rate > 0 and sv > 0 else float('nan'))
            published.setdefault(img_topics[topic], []).append((rel, rng))
        elif topic == state_topic and start <= rel <= end:
            m = deserialize_message(data, RadarControlSet)
            for item in m.items:
                if item.name == 'range':
                    try:
                        commanded.append((rel, float(item.value)))
                    except ValueError:
                        pass
    for line in (asm.flush() if asm else []):   # emit the final accumulated run
        if line.subheader and t0 is not None and start <= line.stamp <= end:
            s = line.subheader
            decoded.setdefault(line.channel, []).append(
                (line.stamp, s.bottom_range_m, s.display_range_m, s.v1_tag))
    return decoded, published, commanded


def render(decoded, published, commanded, out):
    """Two stacked panels: per-channel range scale (top), nadir depth (bottom)."""
    import matplotlib
    if out:
        matplotlib.use('Agg')
    import matplotlib.pyplot as plt

    fig, (a1, a2) = plt.subplots(2, 1, figsize=(15, 9), sharex=True)

    # top: the metric scale, per channel
    for ch in sorted(decoded):
        d = np.array([(t, v2) for t, _v1, v2, _br in decoded[ch]])
        name = CHAN_NAMES.get(ch, f'ch{ch}')
        a1.plot(d[:, 0], d[:, 1], '.', ms=2,
                label=f'{name} sub-header v2 (driver publishes this)')
        brackets = np.array([(t, br) for t, _v1, _v2, br in decoded[ch]])
        steps = np.nonzero(np.diff(brackets[:, 1]))[0]
        for i in steps:
            a1.axvline(brackets[i + 1, 0], color='gray', lw=0.6, alpha=0.5)
    for side in sorted(published):
        p = np.array(published[side])
        a1.plot(p[:, 0], p[:, 1], '+', ms=3, alpha=0.6,
                label=f'{side} as recorded in bag (sample_rate)')
    if commanded:
        c = np.array(commanded)
        a1.plot(c[:, 0], c[:, 1], drawstyle='steps-post', color='red', lw=1,
                label='commanded-range mirror (state)')
    a1.set_ylabel('range (m)')
    a1.legend(loc='upper right', fontsize=8)
    a1.set_title('per-channel display range: driver-path decode vs recorded '
                 'messages (gray = v1 tag-length transitions; gain steps)')

    # bottom: the nadir depth source (down-look v1; shared across channels)
    for ch in sorted(decoded):
        if CHAN_NAMES.get(ch) != 'down':
            continue
        d = np.array([(t, v1) for t, v1, _v2, _br in decoded[ch]])
        a2.plot(d[:, 0], d[:, 1], '.', ms=2, color='tab:green',
                label='down-look v1 -> nadir_depth')
    a2.invert_yaxis()
    a2.set_ylabel('bottom range (m)')
    a2.set_xlabel('time (s, from first raw datagram)')
    a2.legend(loc='lower left', fontsize=8)

    fig.tight_layout()
    if out:
        fig.savefig(out, dpi=100)
        print(f'saved {out}')
    else:
        plt.show()


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[1])
    ap.add_argument('bag', help='path to an mcap bag recorded with debug_raw:=true')
    ap.add_argument('--start', type=float, default=0.0,
                    help='window start (s, relative to the first raw datagram)')
    ap.add_argument('--end', type=float, default=1e9, help='window end (s)')
    ap.add_argument('--out', default=None,
                    help='write a PNG instead of opening the interactive window')
    args = ap.parse_args(argv)

    decoded, published, commanded = read_bag(args.bag, args.start, args.end)
    if not decoded:
        sys.exit('error: no pings with a parseable sub-header in the window')
    for ch in sorted(decoded):
        v2 = [v for _t, _v1, v, _br in decoded[ch]]
        print(f'ch{ch} ({CHAN_NAMES.get(ch, "?")}): {len(v2)} pings, '
              f'v2 {min(v2):.1f}-{max(v2):.1f} m')
    render(decoded, published, commanded, args.out)


if __name__ == '__main__':
    main()
