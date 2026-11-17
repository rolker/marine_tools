"""
Tests for bag_to_xtf output splitting.

A synthetic moving track (see ``xtf_fakes``) is converted with different
split limits. The parts' ping bytes, concatenated, must equal the ping
bytes of the unsplit file, which also proves that ping numbers and the
speed / seconds-per-ping carry-over run across part boundaries.
"""

import re
import struct

from bag_analysis.cli import bag_to_xtf
import pytest
from xtf_fakes import base_args, fake_messages

_HEADER = 1024
_PING_RECORD = 1024 + 256 + 128  # file header + ping with empty channels


def _run(tmp_path, monkeypatch, messages, *extra, name='out.xtf'):
    monkeypatch.setattr(
        bag_to_xtf, 'iter_messages', lambda *a, **k: iter(messages))
    bag = tmp_path / 'bag'
    bag.mkdir(exist_ok=True)
    out = tmp_path / name
    return bag_to_xtf.main(base_args(bag, out, *extra)), out


def _parts(tmp_path, stem='out', suffix='.xtf'):
    pat = re.compile(rf'{re.escape(stem)}_\d{{8}}T\d{{6}}Z(_\d+)?'
                     rf'{re.escape(suffix)}')
    return sorted(p for p in tmp_path.iterdir() if pat.fullmatch(p.name))


def _ping_bytes(paths):
    return b''.join(p.read_bytes()[_HEADER:] for p in paths)


def _ping_numbers(path):
    data = path.read_bytes()
    offset, numbers = _HEADER, []
    while offset < len(data):
        numbers.append(struct.unpack_from('<I', data, offset + 28)[0])
        offset += struct.unpack_from('<I', data, offset + 10)[0]
    return numbers


def _unsplit(tmp_path, monkeypatch, messages, *extra):
    sub = tmp_path / 'unsplit'
    sub.mkdir(exist_ok=True)
    rc, out = _run(sub, monkeypatch, messages, '--no-split', *extra)
    assert rc == 0
    return out


@pytest.mark.parametrize('layout', ['standard', 'pingmapper'])
def test_size_split_equals_unsplit_pings(tmp_path, monkeypatch, layout):
    messages = fake_messages(30, dt_s=0.3)
    single = _unsplit(tmp_path, monkeypatch, messages, '--layout', layout)
    rc, out = _run(
        tmp_path, monkeypatch, messages, '--split-size', '0.008',
        '--layout', layout)
    assert rc == 0
    parts = _parts(tmp_path)
    assert len(parts) > 2
    assert not out.exists()
    assert not list(tmp_path.glob('*.partial'))
    for part in parts:
        assert part.stat().st_size <= 8000
    assert _ping_bytes(parts) == single.read_bytes()[_HEADER:]
    numbers = [n for p in parts for n in _ping_numbers(p)]
    assert numbers == list(range(30))


def test_carry_over_is_global(tmp_path, monkeypatch):
    # The first ping of a later part carries speed and seconds-per-ping
    # from the last ping of the previous part, so it is not the nominal
    # 0.1 s / 0 m/s a reset would write.
    messages = fake_messages(10, dt_s=0.3, speed_mps=2.0)
    rc, _ = _run(tmp_path, monkeypatch, messages, '--split-size', '0.005')
    assert rc == 0
    second = _parts(tmp_path)[1].read_bytes()
    speed = struct.unpack_from('<f', second, _HEADER + 120)[0]
    spp = struct.unpack_from('<f', second, _HEADER + 256 + 20)[0]
    assert speed == pytest.approx(2.0, rel=1e-3)
    assert spp == pytest.approx(0.3, rel=1e-3)


def test_oversized_ping_gets_its_own_part(tmp_path, monkeypatch, capsys):
    messages = fake_messages(4, samples=lambda i: 40)
    rc, _ = _run(tmp_path, monkeypatch, messages, '--split-size', '0.001')
    assert rc == 0
    captured = capsys.readouterr()
    assert len(_parts(tmp_path)) == 4
    assert 'every ping will get its own part' in captured.err
    assert 'over the size limit' in captured.out


def test_duration_split(tmp_path, monkeypatch):
    # 0.5 s per ping; 0.025 min = 1.5 s -> 3 pings per part.
    messages = fake_messages(9, dt_s=0.5)
    rc, _ = _run(tmp_path, monkeypatch, messages, '--split-duration', '0.025')
    assert rc == 0
    parts = _parts(tmp_path)
    assert [len(_ping_numbers(p)) for p in parts] == [3, 3, 3]


def test_duration_and_size_first_reached_wins(tmp_path, monkeypatch, capsys):
    messages = fake_messages(12, dt_s=0.5)
    rc, _ = _run(
        tmp_path, monkeypatch, messages,
        '--split-duration', '0.025', '--split-size', '0.0025')
    assert rc == 0
    out = capsys.readouterr().out
    assert '(new part: size)' in out
    assert len(_parts(tmp_path)) == 6


def test_gap_split(tmp_path, monkeypatch, capsys):
    messages = fake_messages(10, dt_s=0.3, extra_gaps={4: 90.0})
    rc, _ = _run(tmp_path, monkeypatch, messages)
    assert rc == 0
    parts = _parts(tmp_path)
    assert [len(_ping_numbers(p)) for p in parts] == [4, 6]
    assert '(new part: gap)' in capsys.readouterr().out


def test_gap_just_under_and_over_in_integer_ns(tmp_path, monkeypatch):
    # Previous-ping spacing of exactly 60 s does not split; 60 s + 1 ns does.
    exact = fake_messages(6, dt_s=0.3, extra_gaps={3: 59.7})
    rc, _ = _run(tmp_path, monkeypatch, exact)
    assert rc == 0
    assert _parts(tmp_path) == []
    over = fake_messages(6, dt_s=0.3, extra_gaps={3: 59.7000001})
    sub = tmp_path / 'over'
    sub.mkdir()
    rc, _ = _run(sub, monkeypatch, over)
    assert rc == 0
    assert len(_parts(sub)) == 2


def test_gap_carry_over_matches_unsplit_file(tmp_path, monkeypatch):
    messages = fake_messages(10, dt_s=0.3, extra_gaps={4: 90.0})
    single = _unsplit(tmp_path, monkeypatch, messages)
    rc, _ = _run(tmp_path, monkeypatch, messages)
    assert rc == 0
    assert _ping_bytes(_parts(tmp_path)) == single.read_bytes()[_HEADER:]


def test_gap_off_and_no_split_give_one_file(tmp_path, monkeypatch):
    messages = fake_messages(10, dt_s=0.3, extra_gaps={4: 90.0})
    rc, out = _run(tmp_path, monkeypatch, messages, '--split-gap', '0')
    assert rc == 0 and out.exists() and _parts(tmp_path) == []
    sub = tmp_path / 'ns'
    sub.mkdir()
    rc, out = _run(sub, monkeypatch, messages, '--no-split')
    assert rc == 0 and out.exists() and _parts(sub) == []


def test_one_part_keeps_plain_name_and_leaves_no_partial(
        tmp_path, monkeypatch):
    rc, out = _run(tmp_path, monkeypatch, fake_messages(5))
    assert rc == 0
    assert out.exists()
    assert sorted(p.name for p in tmp_path.iterdir() if p.is_file()) == [
        'out.xtf']


def test_same_second_parts_get_a_counter(tmp_path, monkeypatch):
    # Every ping its own part, five pings inside one UTC second.
    messages = fake_messages(5, dt_s=0.1)
    rc, _ = _run(tmp_path, monkeypatch, messages, '--split-size', '0.001')
    assert rc == 0
    names = [p.name for p in _parts(tmp_path)]
    assert len(names) == 5
    assert len(set(names)) == 5
    assert any(re.search(r'Z_2\.xtf$', n) for n in names)


def test_part_report_lists_time_span(tmp_path, monkeypatch, capsys):
    messages = fake_messages(10, dt_s=0.3, extra_gaps={4: 90.0})
    _run(tmp_path, monkeypatch, messages)
    out = capsys.readouterr().out
    assert 'in 2 parts' in out
    assert out.count('  part ') == 2
    assert re.search(r'part 1: out_\d{8}T\d{6}Z\.xtf  pings=4  '
                     r'\d{4}-\d\d-\d\dT[\d:]+Z to \d{4}-\d\d-\d\dT[\d:]+Z',
                     out)


def test_max_pings_counts_across_parts(tmp_path, monkeypatch):
    messages = fake_messages(30, dt_s=0.3)
    rc, _ = _run(
        tmp_path, monkeypatch, messages, '--split-size', '0.008',
        '--max-pings', '17')
    assert rc == 0
    numbers = [n for p in _parts(tmp_path) for n in _ping_numbers(p)]
    assert numbers == list(range(17))


def test_time_window_with_split(tmp_path, monkeypatch):
    messages = fake_messages(20, dt_s=1.0)
    start = messages[2][2] / 1e9 + 4.5
    end = start + 8.0
    rc, _ = _run(
        tmp_path, monkeypatch, messages, '--split-duration', '0.05',
        '--start-time', str(start), '--end-time', str(end))
    assert rc == 0
    numbers = [n for p in _parts(tmp_path) for n in _ping_numbers(p)]
    assert numbers == list(range(len(numbers))) and len(numbers) == 8
    assert len(_parts(tmp_path)) == 3


def test_pyxtf_reads_every_part(tmp_path, monkeypatch):
    pyxtf = pytest.importorskip('pyxtf')
    messages = fake_messages(30, dt_s=0.3)
    rc, _ = _run(tmp_path, monkeypatch, messages, '--split-size', '0.008')
    assert rc == 0
    total = 0
    parts = _parts(tmp_path)
    assert len(parts) > 2
    for part in parts:
        _fh, packets = pyxtf.xtf_read(str(part))
        total += len(packets.get(pyxtf.XTFHeaderType.sonar, []))
    assert total == 30


def test_zero_pings_exit_1_no_files(tmp_path, monkeypatch, capsys):
    rc, _ = _run(tmp_path, monkeypatch, [])
    assert rc == 1
    assert [p for p in tmp_path.iterdir() if p.is_file()] == []
    assert 'no pings written' in capsys.readouterr().err


def _failing(messages, after):
    def gen(*a, **k):
        for i, item in enumerate(messages):
            if i == after:
                raise OSError('disk full')
            yield item
    return gen


def test_failure_in_part_two_keeps_finished_parts(
        tmp_path, monkeypatch, capsys):
    messages = fake_messages(30, dt_s=0.3)
    monkeypatch.setattr(
        bag_to_xtf, 'iter_messages', _failing(messages, 70))
    bag = tmp_path / 'bag'
    bag.mkdir()
    out = tmp_path / 'out.xtf'
    rc = bag_to_xtf.main(
        base_args(bag, out, '--split-size', '0.008'))
    assert rc == 1
    assert not out.exists()
    assert not list(tmp_path.glob('*.partial'))
    parts = _parts(tmp_path)
    assert len(parts) >= 1
    captured = capsys.readouterr()
    assert 'finished parts kept' in captured.out
    assert parts[0].name in captured.out


def test_failure_in_part_one_leaves_nothing(tmp_path, monkeypatch):
    messages = fake_messages(30, dt_s=0.3)
    monkeypatch.setattr(bag_to_xtf, 'iter_messages', _failing(messages, 20))
    bag = tmp_path / 'bag'
    bag.mkdir()
    rc = bag_to_xtf.main(base_args(bag, tmp_path / 'out.xtf'))
    assert rc == 1
    assert [p for p in tmp_path.iterdir() if p.is_file()] == []


def test_non_os_error_is_reraised_after_cleanup(tmp_path, monkeypatch):
    def gen(*a, **k):
        yield from fake_messages(3)
        raise RuntimeError('boom')
    monkeypatch.setattr(bag_to_xtf, 'iter_messages', gen)
    bag = tmp_path / 'bag'
    bag.mkdir()
    with pytest.raises(RuntimeError):
        bag_to_xtf.main(base_args(bag, tmp_path / 'out.xtf'))
    assert [p for p in tmp_path.iterdir() if p.is_file()] == []


@pytest.mark.parametrize('extra', [
    ['--split-size', '-1'], ['--split-duration', '-5'],
    ['--split-gap', '-1'],
    ['--no-split', '--split-size', '100'],
    ['--no-split', '--split-gap', '0'],
    ['--no-split', '--split-duration', '5'],
    ['--max-pings', '0'],
])
def test_option_validation(tmp_path, extra):
    with pytest.raises(SystemExit) as exc:
        bag_to_xtf.main(base_args(tmp_path, tmp_path / 'o.xtf', *extra))
    assert exc.value.code == 2


def test_size_over_2gb_warns(tmp_path, monkeypatch, capsys):
    rc, _ = _run(
        tmp_path, monkeypatch, fake_messages(3), '--split-size', '3000')
    assert rc == 0
    assert 'over 2 GB' in capsys.readouterr().err


def test_old_files_are_listed_and_never_deleted(
        tmp_path, monkeypatch, capsys):
    old = [
        tmp_path / 'out_20200101T000000Z.xtf',
        tmp_path / 'out_20200101T000000Z_2.xtf',
        tmp_path / 'out_20200101T000000Z.xtf.partial',
        tmp_path / 'out.xtf.partial',
    ]
    unrelated = [
        tmp_path / 'other_20200101T000000Z.xtf', tmp_path / 'out_notes.xtf',
        tmp_path / 'out_20200101T000000Z.xtf.bak']
    for path in old + unrelated:
        path.write_bytes(b'x')
    # A multi-part run does not replace the plain-name file of an earlier
    # run, so it is listed as well.
    plain = tmp_path / 'out.xtf'
    plain.write_bytes(b'x')
    messages = fake_messages(10, dt_s=0.3, extra_gaps={4: 90.0})
    rc, _ = _run(tmp_path, monkeypatch, messages)
    assert rc == 0
    err = capsys.readouterr().err
    assert 'WARNING' in err
    for path in old + [plain]:
        assert f'  {path.name}\n' in err
        assert path.exists()
    for path in unrelated:
        assert f'  {path.name}\n' not in err
        assert path.exists()


def test_old_file_scan_escapes_the_stem(tmp_path, monkeypatch, capsys):
    odd = tmp_path / 'a.b+c'
    odd.mkdir()
    lookalike = odd / 'aXb+c_20200101T000000Z.xtf'
    real = odd / 'a.b+c_20200101T000000Z.xtf'
    lookalike.write_bytes(b'x')
    real.write_bytes(b'x')
    rc, _ = _run(
        odd, monkeypatch, fake_messages(3), name='a.b+c.xtf')
    assert rc == 0
    err = capsys.readouterr().err
    assert f'  {real.name}\n' in err
    assert f'  {lookalike.name}\n' not in err


def test_old_files_warned_and_kept_on_failure(tmp_path, monkeypatch, capsys):
    old = tmp_path / 'out_20200101T000000Z.xtf'
    old.write_bytes(b'x')
    monkeypatch.setattr(
        bag_to_xtf, 'iter_messages', _failing(fake_messages(10), 20))
    bag = tmp_path / 'bag'
    bag.mkdir()
    rc = bag_to_xtf.main(base_args(bag, tmp_path / 'out.xtf'))
    assert rc == 1
    assert old.exists()
    assert old.name in capsys.readouterr().err
