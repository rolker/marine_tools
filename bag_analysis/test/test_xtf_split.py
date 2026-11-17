"""
Tests for bag_to_xtf output splitting.

A synthetic moving track (see ``xtf_fakes``) is converted with different
split limits. The parts' ping bytes, concatenated, must equal the ping
bytes of the unsplit file, which also proves that ping numbers and the
speed / seconds-per-ping carry-over run across part boundaries.
"""

import os
from pathlib import Path
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
    ['--split-size', 'inf'], ['--split-size', 'nan'],
    ['--split-size', '1e308'], ['--split-duration', 'inf'],
    ['--split-duration', '1e308'], ['--split-gap', 'inf'],
    ['--split-gap', '1e308'],
    ['--split-duration', '1e-12'],   # rounds to 0 ns
    ['--split-gap', '1e-10'],        # rounds to 0 ns
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
    rc, _ = _run(tmp_path, monkeypatch, messages, '--overwrite')
    assert rc == 0
    err = capsys.readouterr().err
    assert 'WARNING' in err
    assert 'mixes files from two runs' in err
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
        odd, monkeypatch, fake_messages(3), '--overwrite', name='a.b+c.xtf')
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
    rc = bag_to_xtf.main(
        base_args(bag, tmp_path / 'out.xtf', '--overwrite'))
    assert rc == 1
    assert old.exists()
    assert old.name in capsys.readouterr().err


# --- refuse unless --overwrite -------------------------------------------

_SMALL = ('--split-size', '0.008')


def _snapshot(folder):
    return {p.name: p.read_bytes() for p in folder.iterdir() if p.is_file()}


def _forbid_reading(monkeypatch):
    def boom(*a, **k):
        raise AssertionError('the bag was read')
    monkeypatch.setattr(bag_to_xtf, 'iter_messages', boom)


def _first_part_name(tmp_path, monkeypatch, messages):
    ref = tmp_path / 'ref'
    ref.mkdir()
    rc, _ = _run(ref, monkeypatch, messages, *_SMALL)
    assert rc == 0
    return _parts(ref)[0].name


def test_rerun_without_overwrite_refuses_and_changes_nothing(
        tmp_path, monkeypatch, capsys):
    messages = fake_messages(30, dt_s=0.3)
    rc, _ = _run(tmp_path, monkeypatch, messages, *_SMALL)
    assert rc == 0
    before = _snapshot(tmp_path)
    capsys.readouterr()
    _forbid_reading(monkeypatch)
    rc = bag_to_xtf.main(
        base_args(tmp_path / 'bag', tmp_path / 'out.xtf', *_SMALL))
    assert rc == 2
    assert _snapshot(tmp_path) == before
    err = capsys.readouterr().err
    assert 'already exist' in err and '--overwrite' in err
    assert 'Nothing was written' in err
    for name in before:
        assert name in err


def test_refusal_caps_a_long_list(tmp_path, monkeypatch, capsys):
    for i in range(25):
        (tmp_path / f'out_20200101T0000{i:02d}Z.xtf').write_bytes(b'x')
    _forbid_reading(monkeypatch)
    (tmp_path / 'bag').mkdir()
    rc = bag_to_xtf.main(
        base_args(tmp_path / 'bag', tmp_path / 'out.xtf'))
    assert rc == 2
    err = capsys.readouterr().err
    assert '25 file(s)' in err
    assert '... and 15 more (25 files in all)' in err
    assert err.count('out_2020') == 10


def test_rerun_with_overwrite_replaces_and_reports(
        tmp_path, monkeypatch, capsys):
    messages = fake_messages(30, dt_s=0.3)
    rc, _ = _run(tmp_path, monkeypatch, messages, *_SMALL)
    first = _snapshot(tmp_path)
    for path in _parts(tmp_path):
        path.write_bytes(b'stale')
    capsys.readouterr()
    rc, _ = _run(tmp_path, monkeypatch, messages, *_SMALL, '--overwrite')
    assert rc == 0
    assert _snapshot(tmp_path) == first
    captured = capsys.readouterr()
    assert f'replaced {len(first)} file(s)' in captured.out
    for name in first:
        assert name in captured.out
    assert 'WARNING' not in captured.err


def test_plain_single_file_rerun_refuses_then_overwrites(
        tmp_path, monkeypatch, capsys):
    messages = fake_messages(5)
    rc, out = _run(tmp_path, monkeypatch, messages)
    assert rc == 0
    before = out.read_bytes()
    out.write_bytes(b'precious')
    rc, _ = _run(tmp_path, monkeypatch, messages)
    assert rc == 2
    assert out.read_bytes() == b'precious'
    capsys.readouterr()
    rc, _ = _run(tmp_path, monkeypatch, messages, '--overwrite')
    assert rc == 0
    assert out.read_bytes() == before
    assert 'replaced 1 file(s)' in capsys.readouterr().out


def test_max_pings_test_run_over_full_export_refuses(
        tmp_path, monkeypatch):
    messages = fake_messages(30, dt_s=0.3)
    rc, _ = _run(tmp_path, monkeypatch, messages, *_SMALL)
    before = _snapshot(tmp_path)
    rc, _ = _run(tmp_path, monkeypatch, messages, '--max-pings', '3')
    assert rc == 2
    assert _snapshot(tmp_path) == before


def test_mixed_case_old_run_had_more_parts_warns(tmp_path, monkeypatch, capsys):
    messages = fake_messages(30, dt_s=0.3)
    _run(tmp_path, monkeypatch, messages, '--split-size', '0.004')
    old_parts = _parts(tmp_path)
    assert len(old_parts) > 3
    capsys.readouterr()
    _run(tmp_path, monkeypatch, messages, '--split-size', '0.008',
         '--overwrite')
    err = capsys.readouterr().err
    assert 'mixes files from two runs' in err
    assert 'nothing was deleted' in err
    leftovers = [p for p in old_parts if f'  {p.name}\n' in err]
    assert leftovers
    assert all(p.exists() for p in old_parts)


def test_existing_partial_leftover_refuses_then_is_replaced(
        tmp_path, monkeypatch, capsys):
    messages = fake_messages(30, dt_s=0.3)
    name = _first_part_name(tmp_path, monkeypatch, messages)
    same = tmp_path / (name + '.partial')
    other = tmp_path / 'out_20200101T000000Z.xtf.partial'
    same.write_bytes(b'precious')
    other.write_bytes(b'other')
    rc, _ = _run(tmp_path, monkeypatch, messages, *_SMALL)
    assert rc == 2
    assert same.read_bytes() == b'precious'
    assert not _parts(tmp_path)
    capsys.readouterr()
    rc, _ = _run(tmp_path, monkeypatch, messages, *_SMALL, '--overwrite')
    assert rc == 0
    captured = capsys.readouterr()
    assert not same.exists()
    assert same.name in captured.out         # listed as replaced
    assert other.read_bytes() == b'other'    # not regenerated: kept
    assert other.name in captured.err


def test_file_appearing_as_partial_mid_run_is_not_truncated(
        tmp_path, monkeypatch, capsys):
    messages = fake_messages(30, dt_s=0.3)
    name = _first_part_name(tmp_path, monkeypatch, messages)
    folder = tmp_path / 'run'
    folder.mkdir()
    foreign = folder / (name + '.partial')

    def gen(*a, **k):
        foreign.write_bytes(b'foreign')
        yield from messages
    monkeypatch.setattr(bag_to_xtf, 'iter_messages', gen)
    bag = tmp_path / 'bag'
    bag.mkdir()
    rc = bag_to_xtf.main(base_args(bag, folder / 'out.xtf', *_SMALL))
    assert rc == 1
    assert foreign.read_bytes() == b'foreign'
    assert [p.name for p in folder.iterdir()] == [foreign.name]


def test_file_appearing_as_final_name_mid_run_is_not_replaced(
        tmp_path, monkeypatch, capsys):
    messages = fake_messages(30, dt_s=0.3)
    name = _first_part_name(tmp_path, monkeypatch, messages)
    folder = tmp_path / 'run'
    folder.mkdir()
    foreign = folder / name

    def gen(*a, **k):
        for i, item in enumerate(messages):
            if i == 5:
                foreign.write_bytes(b'foreign')
            yield item
    monkeypatch.setattr(bag_to_xtf, 'iter_messages', gen)
    (tmp_path / 'bag').mkdir(exist_ok=True)
    rc = bag_to_xtf.main(
        base_args(tmp_path / 'bag', folder / 'out.xtf', *_SMALL))
    assert rc == 1
    assert foreign.read_bytes() == b'foreign'
    captured = capsys.readouterr()
    assert 'was NOT replaced' in captured.err
    # The finished part is complete data: kept under its .partial name,
    # named in the report, not deleted.
    kept = list(folder.glob('*.partial'))
    assert len(kept) == 1
    assert kept[0].name == name + '.partial'
    assert kept[0].stat().st_size > _HEADER
    assert 'left under their .partial name' in captured.out
    assert kept[0].name in captured.out


def test_plain_name_appearing_before_the_final_rename_is_not_replaced(
        tmp_path, monkeypatch, capsys):
    messages = fake_messages(5)
    plain = tmp_path / 'out.xtf'

    def gen(*a, **k):
        yield from messages
        plain.write_bytes(b'foreign')
    monkeypatch.setattr(bag_to_xtf, 'iter_messages', gen)
    (tmp_path / 'bag').mkdir()
    rc = bag_to_xtf.main(base_args(tmp_path / 'bag', plain))
    assert rc == 1
    assert plain.read_bytes() == b'foreign'
    # The finished part keeps its time name and is reported.
    kept = _parts(tmp_path)
    assert len(kept) == 1
    assert kept[0].name in capsys.readouterr().out


# --- up-front checks ------------------------------------------------------

def test_output_that_is_a_directory_is_rejected_up_front(
        tmp_path, monkeypatch, capsys):
    _forbid_reading(monkeypatch)
    (tmp_path / 'bag').mkdir()
    target = tmp_path / 'out.xtf'
    target.mkdir()
    assert bag_to_xtf.main(base_args(tmp_path / 'bag', target)) == 2
    assert 'is a directory' in capsys.readouterr().err


@pytest.mark.skipif(os.geteuid() == 0, reason='root ignores permissions')
def test_unwritable_folder_is_rejected_up_front(
        tmp_path, monkeypatch, capsys):
    _forbid_reading(monkeypatch)
    (tmp_path / 'bag').mkdir()
    folder = tmp_path / 'ro'
    folder.mkdir()
    folder.chmod(0o500)
    try:
        rc = bag_to_xtf.main(
            base_args(tmp_path / 'bag', folder / 'out.xtf'))
    finally:
        folder.chmod(0o700)
    assert rc == 2
    assert 'not writable' in capsys.readouterr().err


# --- failure handling -----------------------------------------------------

def test_zero_pings_still_warns_about_old_files(
        tmp_path, monkeypatch, capsys):
    old = tmp_path / 'out.xtf'
    old.write_bytes(b'x')
    rc, _ = _run(tmp_path, monkeypatch, [], '--overwrite')
    assert rc == 1
    err = capsys.readouterr().err
    assert 'no pings written' in err
    assert 'WARNING' in err and old.name in err
    assert old.read_bytes() == b'x'


def test_cleanup_failures_do_not_hide_the_original_error(
        tmp_path, monkeypatch, capsys):
    messages = fake_messages(30, dt_s=0.3)
    monkeypatch.setattr(
        bag_to_xtf, 'iter_messages', _failing(messages, 70))
    real_unlink = Path.unlink

    def unlink(self, *a, **k):
        if self.name.endswith('.partial'):
            raise PermissionError('cannot remove')
        return real_unlink(self, *a, **k)

    def warn(*a, **k):
        raise OSError('listing failed')
    monkeypatch.setattr(Path, 'unlink', unlink)
    monkeypatch.setattr(bag_to_xtf, '_warn_old_files', warn)
    bag = tmp_path / 'bag'
    bag.mkdir()
    rc = bag_to_xtf.main(base_args(bag, tmp_path / 'out.xtf', *_SMALL))
    monkeypatch.undo()
    assert rc == 1
    captured = capsys.readouterr()
    assert 'disk full' in captured.err
    assert 'could not remove' in captured.err
    assert 'listing failed' in captured.err
    assert 'finished parts kept' in captured.out


def test_failure_report_counts_pings_in_the_discarded_part(
        tmp_path, monkeypatch, capsys):
    messages = fake_messages(30, dt_s=0.3)
    monkeypatch.setattr(bag_to_xtf, 'iter_messages', _failing(messages, 70))
    (tmp_path / 'bag').mkdir()
    bag_to_xtf.main(
        base_args(tmp_path / 'bag', tmp_path / 'out.xtf', *_SMALL))
    out = capsys.readouterr().out
    kept = sum(int(n) for n in re.findall(r'pings=(\d+)', out))
    discarded = int(re.search(r': (\d+) pings were in it', out).group(1))
    # 4 messages per ping; the failure at message 70 follows 17 pings.
    assert kept + discarded == 17
    assert discarded >= 1


def test_interrupt_after_the_move_still_reports_the_part(
        tmp_path, monkeypatch, capsys):
    real_place = bag_to_xtf._PartSink._place
    calls = []

    def place(self, src, dst):
        real_place(self, src, dst)
        if not calls:
            calls.append(1)
            raise KeyboardInterrupt
    monkeypatch.setattr(bag_to_xtf._PartSink, '_place', place)
    monkeypatch.setattr(
        bag_to_xtf, 'iter_messages',
        lambda *a, **k: iter(fake_messages(30, dt_s=0.3)))
    (tmp_path / 'bag').mkdir()
    with pytest.raises(KeyboardInterrupt):
        bag_to_xtf.main(
            base_args(tmp_path / 'bag', tmp_path / 'out.xtf', *_SMALL))
    captured = capsys.readouterr()
    first = _parts(tmp_path)[0]
    assert f'part 1: {first.name}' in captured.out
    assert 'WARNING' not in captured.err      # not called an old file


def test_parts_are_synced_before_they_are_renamed(
        tmp_path, monkeypatch):
    synced = []
    real = os.fsync
    monkeypatch.setattr(os, 'fsync', lambda fd: (synced.append(fd), real(fd)))
    rc, _ = _run(tmp_path, monkeypatch, fake_messages(30, dt_s=0.3), *_SMALL)
    assert rc == 0
    assert len(synced) >= len(_parts(tmp_path))


# --- --overwrite never writes through a link -------------------------------

def test_overwrite_unlinks_a_symlinked_partial_not_its_target(
        tmp_path, monkeypatch, capsys):
    messages = fake_messages(30, dt_s=0.3)
    name = _first_part_name(tmp_path, monkeypatch, messages)
    target = tmp_path / 'precious.bin'
    target.write_bytes(b'precious')
    link = tmp_path / (name + '.partial')
    link.symlink_to(target)
    rc, _ = _run(tmp_path, monkeypatch, messages, *_SMALL, '--overwrite')
    assert rc == 0
    assert target.read_bytes() == b'precious'
    assert not link.is_symlink() and not link.exists()
    assert link.name in capsys.readouterr().out      # listed as replaced


def test_overwrite_unlinks_a_hard_linked_partial_not_the_other_link(
        tmp_path, monkeypatch, capsys):
    messages = fake_messages(30, dt_s=0.3)
    name = _first_part_name(tmp_path, monkeypatch, messages)
    other = tmp_path / 'other_name.bin'
    other.write_bytes(b'precious')
    linked = tmp_path / (name + '.partial')
    os.link(other, linked)
    rc, _ = _run(tmp_path, monkeypatch, messages, *_SMALL, '--overwrite')
    assert rc == 0
    assert other.read_bytes() == b'precious'
    assert other.stat().st_nlink == 1
    assert linked.name in capsys.readouterr().out


def test_overwrite_final_move_replaces_a_symlink_not_its_target(
        tmp_path, monkeypatch):
    messages = fake_messages(30, dt_s=0.3)
    name = _first_part_name(tmp_path, monkeypatch, messages)
    target = tmp_path / 'precious.bin'
    target.write_bytes(b'precious')
    link = tmp_path / name
    link.symlink_to(target)
    rc, _ = _run(tmp_path, monkeypatch, messages, *_SMALL, '--overwrite')
    assert rc == 0
    assert target.read_bytes() == b'precious'
    assert not link.is_symlink()
    assert link.stat().st_size > _HEADER


# --- no-replace move and its fallbacks --------------------------------------

def _sink(tmp_path):
    return bag_to_xtf._PartSink(tmp_path / 'out.xtf', 'x', 'standard')


def _no_links(monkeypatch):
    def link(*a, **k):
        raise PermissionError(1, 'links not supported')
    monkeypatch.setattr(os, 'link', link)


def test_place_by_hard_link_refuses_an_existing_name(tmp_path):
    src, dst = tmp_path / 's', tmp_path / 'd'
    src.write_bytes(b'new')
    dst.write_bytes(b'foreign')
    with pytest.raises(FileExistsError):
        _sink(tmp_path)._place(src, dst)
    assert dst.read_bytes() == b'foreign' and src.exists()


def test_place_without_links_uses_atomic_noreplace_rename(
        tmp_path, monkeypatch):
    _no_links(monkeypatch)
    src, dst = tmp_path / 's', tmp_path / 'd'
    src.write_bytes(b'new')
    calls = []

    def renameat2(fd1, a, fd2, b, flags):
        calls.append(flags)
        os.rename(a, b)
        return 0
    monkeypatch.setattr(bag_to_xtf, '_get_renameat2', lambda: renameat2)

    def no_check(*a, **k):
        raise AssertionError('check-then-rename must not be used')
    monkeypatch.setattr(bag_to_xtf.os, 'replace', no_check)
    _sink(tmp_path)._place(src, dst)
    assert calls == [1]
    assert dst.read_bytes() == b'new' and not src.exists()


def test_place_without_links_noreplace_reports_existing_name(
        tmp_path, monkeypatch):
    # Real renameat2 where the kernel has it; otherwise the check does it.
    _no_links(monkeypatch)
    src, dst = tmp_path / 's', tmp_path / 'd'
    src.write_bytes(b'new')
    dst.write_bytes(b'foreign')
    with pytest.raises(FileExistsError):
        _sink(tmp_path)._place(src, dst)
    assert dst.read_bytes() == b'foreign' and src.exists()


def test_place_noreplace_rejected_by_the_filesystem_falls_back(
        tmp_path, monkeypatch):
    import ctypes
    _no_links(monkeypatch)
    src, dst = tmp_path / 's', tmp_path / 'd'
    src.write_bytes(b'new')

    def renameat2(*a):
        ctypes.set_errno(22)    # EINVAL: filesystem lacks the flag
        return -1
    monkeypatch.setattr(bag_to_xtf, '_get_renameat2', lambda: renameat2)
    _sink(tmp_path)._place(src, dst)
    assert dst.read_bytes() == b'new' and not src.exists()


def test_place_with_no_renameat2_checks_then_renames(tmp_path, monkeypatch):
    _no_links(monkeypatch)
    monkeypatch.setattr(bag_to_xtf, '_get_renameat2', lambda: None)
    src, dst = tmp_path / 's', tmp_path / 'd'
    src.write_bytes(b'new')
    _sink(tmp_path)._place(src, dst)
    assert dst.read_bytes() == b'new' and not src.exists()
    src.write_bytes(b'newer')
    with pytest.raises(FileExistsError):
        _sink(tmp_path)._place(src, dst)
    assert dst.read_bytes() == b'new'


def test_place_noreplace_real_failure_is_not_swallowed(
        tmp_path, monkeypatch):
    import ctypes
    _no_links(monkeypatch)
    src, dst = tmp_path / 's', tmp_path / 'd'
    src.write_bytes(b'new')

    def renameat2(*a):
        ctypes.set_errno(5)     # EIO
        return -1
    monkeypatch.setattr(bag_to_xtf, '_get_renameat2', lambda: renameat2)
    with pytest.raises(OSError) as info:
        _sink(tmp_path)._place(src, dst)
    assert info.value.errno == 5 and src.exists()


def test_full_run_on_a_filesystem_without_links(tmp_path, monkeypatch):
    _no_links(monkeypatch)
    rc, _ = _run(tmp_path, monkeypatch, fake_messages(30, dt_s=0.3), *_SMALL)
    assert rc == 0
    assert len(_parts(tmp_path)) > 1
    assert not list(tmp_path.glob('*.partial'))


# --- file fsync tolerance ---------------------------------------------------

@pytest.mark.parametrize('code', [22, 38, 95])   # EINVAL, ENOSYS, ENOTSUP
def test_unsupported_file_fsync_is_tolerated(tmp_path, monkeypatch, code):
    real = os.fsync
    refused = []

    def fsync(fd):
        if os.path.isfile(f'/proc/self/fd/{fd}'):
            refused.append(fd)
            raise OSError(code, 'not supported')
        return real(fd)    # directories
    monkeypatch.setattr(os, 'fsync', fsync)
    rc, _ = _run(tmp_path, monkeypatch, fake_messages(30, dt_s=0.3), *_SMALL)
    assert rc == 0
    assert refused
    assert _parts(tmp_path)


def test_eio_on_file_fsync_stays_fatal(tmp_path, monkeypatch, capsys):
    def fsync(fd):
        raise OSError(5, 'Input/output error')
    monkeypatch.setattr(os, 'fsync', fsync)
    rc, _ = _run(tmp_path, monkeypatch, fake_messages(30, dt_s=0.3), *_SMALL)
    assert rc == 1
    assert 'Input/output error' in capsys.readouterr().err
    assert not _parts(tmp_path)


# --- success path reporting is guarded -------------------------------------

def test_old_file_check_failure_after_success_is_not_a_traceback(
        tmp_path, monkeypatch, capsys):
    def warn(*a, **k):
        raise OSError('listing failed')
    monkeypatch.setattr(bag_to_xtf, '_warn_old_files', warn)
    rc, _ = _run(tmp_path, monkeypatch, fake_messages(30, dt_s=0.3), *_SMALL)
    assert rc == 0
    assert _parts(tmp_path)
    err = capsys.readouterr().err
    assert 'listing failed' in err
    assert 'check for files of earlier runs could not be done' in err


def test_summary_failure_after_success_still_names_the_parts(
        tmp_path, monkeypatch, capsys):
    def report(*a, **k):
        raise OSError('stdout gone')
    monkeypatch.setattr(bag_to_xtf, '_report', report)
    rc, _ = _run(tmp_path, monkeypatch, fake_messages(30, dt_s=0.3), *_SMALL)
    assert rc == 0
    captured = capsys.readouterr()
    assert 'stdout gone' in captured.err
    assert _parts(tmp_path)[0].name in captured.out
