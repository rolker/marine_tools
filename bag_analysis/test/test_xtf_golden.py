"""
Characterization test for bag_to_xtf.

The golden file was produced by the converter before the ping loop was
split into a generator and a part sink. With the default split limits a
small run is one part, and its output (and the report counts) must stay
byte-identical.

It was regenerated once, for #100, when the writer began to fill the ping
header's ComputerClock fields (offsets 236 to 239) with the stamp as
recorded. Checked at the time against the previous file: the only bytes that
differ are those four bytes of each ping header, and with an offset of 0
they equal the ping time (bytes 18 to 21). Poses are unchanged because the
fake sends /tf with the same stamp as each ping, so a stamped lookup finds
the exact sample, as the old code did.
"""

from pathlib import Path
import struct

from bag_analysis.cli import bag_to_xtf
from xtf_fakes import base_args, fake_messages, only_xtf

_GOLDEN = Path(__file__).parent / 'data' / 'xtf_golden_single.xtf'


def _run(tmp_path, monkeypatch, capsys, *extra):
    messages = fake_messages(12, drop_starboard={3})
    monkeypatch.setattr(
        bag_to_xtf, 'iter_messages', lambda *a, **k: iter(messages))
    bag = tmp_path / 'bag'
    bag.mkdir()
    folder = tmp_path / 'out'
    rc = bag_to_xtf.main(base_args(bag, folder, *extra))
    out = only_xtf(folder)
    report = capsys.readouterr().out.replace(str(out), 'OUT')
    return rc, out, report


def test_single_file_output_is_byte_identical(
        tmp_path, monkeypatch, capsys):
    rc, out, report = _run(tmp_path, monkeypatch, capsys)
    assert rc == 0
    assert out.read_bytes() == _GOLDEN.read_bytes()
    assert report.startswith('wrote 11 XTF pings -> OUT')
    assert 'paired=11' in report and 'unpaired=1' in report


def test_computer_clock_equals_ping_time_without_an_offset():
    data = _GOLDEN.read_bytes()
    offset, pings = 1024, 0
    while offset < len(data):
        assert data[offset + 236:offset + 240] == data[offset + 18:offset + 22]
        offset += struct.unpack_from('<I', data, offset + 10)[0]
        pings += 1
    assert pings == 11


def test_max_pings_counts(tmp_path, monkeypatch, capsys):
    rc, out, report = _run(
        tmp_path, monkeypatch, capsys, '--max-pings', '4')
    assert rc == 0
    assert 'wrote 4 XTF pings' in report
    assert 'paired=4' in report
    assert 'unpaired=1' in report
