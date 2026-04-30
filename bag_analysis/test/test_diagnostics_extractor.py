"""Tests for diagnostic_msgs/DiagnosticArray extractor.

Constructs a DiagnosticArray with statuses at every level and checks
that counts are non-zero and flagged-name lists pick up the right
entries. Also covers the bytes-vs-int level-field quirk that produced
all-zero counts on real bag data.
"""

from diagnostic_msgs.msg import DiagnosticArray, DiagnosticStatus

from bag_analysis.extractors.diagnostics import extract


def _status(name: str, level: int) -> DiagnosticStatus:
    s = DiagnosticStatus()
    s.name = name
    s.level = level
    return s


def test_extract_counts_each_level():
    msg = DiagnosticArray()
    msg.header.frame_id = 'd_frame'
    msg.header.stamp.sec = 1
    msg.header.stamp.nanosec = 0
    msg.status = [
        _status('cpu', DiagnosticStatus.OK),
        _status('mem', DiagnosticStatus.WARN),
        _status('wifi', DiagnosticStatus.WARN),
        _status('dropped', DiagnosticStatus.ERROR),
        _status('stale_topic', DiagnosticStatus.STALE),
    ]
    fields = extract(msg)
    assert fields['n_status'] == 5
    assert fields['n_ok'] == 1
    assert fields['n_warn'] == 2
    assert fields['n_error'] == 1
    assert fields['n_stale'] == 1
    assert fields['warn_names'] == 'mem|wifi'
    assert fields['error_names'] == 'dropped'


def test_extract_handles_bytes_level_field():
    """Level is a `byte` field; rclpy can deliver it as bytes, not int.

    The extractor casts to int up-front so counts work either way; this
    test simulates the bytes path with a duck-typed status object.
    """
    class FakeStatus:
        def __init__(self, name: str, level: bytes) -> None:
            self.name = name
            self.level = level

    class FakeHeader:
        frame_id = ''

        class _Stamp:
            sec = 0
            nanosec = 0

        stamp = _Stamp()

    class FakeArray:
        header = FakeHeader()
        status = [
            FakeStatus('a', b'\x00'),
            FakeStatus('b', b'\x01'),
            FakeStatus('c', b'\x02'),
        ]

    fields = extract(FakeArray())
    assert fields['n_ok'] == 1
    assert fields['n_warn'] == 1
    assert fields['n_error'] == 1


def test_extract_handles_empty_status_list():
    msg = DiagnosticArray()
    msg.status = []
    fields = extract(msg)
    assert fields['n_status'] == 0
    assert fields['n_ok'] == 0
    assert fields['warn_names'] == ''
    assert fields['error_names'] == ''
