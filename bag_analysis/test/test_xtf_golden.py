"""
Characterization test for bag_to_xtf.

The golden file was produced by the converter before the ping loop was
split into a generator and a part sink. With the default split limits a
small run is one part, and its output (and the report counts) must stay
byte-identical.
"""

from pathlib import Path

from bag_analysis.cli import bag_to_xtf
from xtf_fakes import base_args, fake_messages

_GOLDEN = Path(__file__).parent / 'data' / 'xtf_golden_single.xtf'


def _run(tmp_path, monkeypatch, capsys, *extra):
    messages = fake_messages(12, drop_starboard={3})
    monkeypatch.setattr(
        bag_to_xtf, 'iter_messages', lambda *a, **k: iter(messages))
    bag = tmp_path / 'bag'
    bag.mkdir()
    out = tmp_path / 'out.xtf'
    rc = bag_to_xtf.main(base_args(bag, out, *extra))
    report = capsys.readouterr().out.replace(str(out), 'OUT')
    return rc, out, report


def test_single_file_output_is_byte_identical(
        tmp_path, monkeypatch, capsys):
    rc, out, report = _run(tmp_path, monkeypatch, capsys)
    assert rc == 0
    assert out.read_bytes() == _GOLDEN.read_bytes()
    assert report.startswith('wrote 11 XTF pings -> OUT')
    assert 'paired=11' in report and 'unpaired=1' in report


def test_max_pings_counts(tmp_path, monkeypatch, capsys):
    rc, out, report = _run(
        tmp_path, monkeypatch, capsys, '--max-pings', '4')
    assert rc == 0
    assert 'wrote 4 XTF pings' in report
    assert 'paired=4' in report
    assert 'unpaired=1' in report
