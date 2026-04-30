"""Tests for udp_bridge_interfaces extractors.

Construct BridgeInfo and TopicStatisticsArray with the schema actually
shipped in udp_bridge_interfaces (TopicStatistics with a DataRates send
sub-struct, BridgeInfo with one Remote containing one RemoteConnection)
and assert the extractor emits the correct totals.
"""

import math

from udp_bridge_interfaces.msg import (
    BridgeInfo,
    DataRates,
    Remote,
    RemoteConnection,
    TopicStatistics,
    TopicStatisticsArray,
)

from bag_analysis.extractors.udp_bridge import (
    extract_bridge_info,
    extract_topic_statistics_array,
)


def _data_rates(success: float, failed: float, dropped: float) -> DataRates:
    r = DataRates()
    r.success_bytes_per_second = success
    r.failed_bytes_per_second = failed
    r.dropped_bytes_per_second = dropped
    return r


def test_topic_statistics_array_aggregates_send_data_rates():
    msg = TopicStatisticsArray()
    s1 = TopicStatistics()
    s1.message_bytes_per_second = 100.0
    s1.messages_per_second = 5.0
    s1.send = _data_rates(success=80.0, failed=10.0, dropped=2.0)
    s2 = TopicStatistics()
    s2.message_bytes_per_second = 50.0
    s2.messages_per_second = 1.0
    s2.send = _data_rates(success=40.0, failed=5.0, dropped=0.0)
    msg.topics = [s1, s2]

    fields = extract_topic_statistics_array(msg)
    assert fields['n_topics'] == 2
    assert math.isclose(fields['message_bytes_per_second'], 150.0)
    assert math.isclose(fields['messages_per_second'], 6.0)
    assert math.isclose(fields['send_success_bytes_per_second'], 120.0)
    assert math.isclose(fields['send_failed_bytes_per_second'], 15.0)
    assert math.isclose(fields['send_dropped_bytes_per_second'], 2.0)


def test_topic_statistics_array_handles_empty_list():
    msg = TopicStatisticsArray()
    msg.topics = []
    fields = extract_topic_statistics_array(msg)
    assert fields == {'n_topics': 0}


def test_bridge_info_aggregates_nested_connection_rates():
    msg = BridgeInfo()
    msg.name = 'bizzy_bridge'
    msg.local_port = 4444
    msg.maximum_packet_size = 1400

    conn = RemoteConnection()
    conn.connection_id = 'starlink'
    conn.received_bytes_per_second = 200.0
    conn.duplicate_bytes_per_second = 5.0
    conn.message = _data_rates(success=300.0, failed=20.0, dropped=8.0)
    conn.overhead = _data_rates(success=10.0, failed=1.0, dropped=0.0)
    conn.resend = _data_rates(success=15.0, failed=2.0, dropped=0.0)

    remote = Remote()
    remote.name = 'operator'
    remote.connections = [conn]

    msg.remotes = [remote]

    fields = extract_bridge_info(msg)
    assert fields['name'] == 'bizzy_bridge'
    assert fields['local_port'] == 4444
    assert fields['n_remotes'] == 1
    assert fields['n_connections'] == 1
    assert math.isclose(fields['received_bytes_per_second'], 200.0)
    assert math.isclose(fields['duplicate_bytes_per_second'], 5.0)
    # wire_out = (300+20) + (10+1) + (15+2) = 348 — dropped excluded.
    assert math.isclose(fields['wire_out_bytes_per_second'], 348.0)


def test_bridge_info_handles_no_remotes():
    msg = BridgeInfo()
    msg.name = 'idle_bridge'
    msg.remotes = []

    fields = extract_bridge_info(msg)
    assert fields['name'] == 'idle_bridge'
    assert fields['n_remotes'] == 0
    assert fields['n_connections'] == 0
    assert math.isclose(fields['received_bytes_per_second'], 0.0)
    assert math.isclose(fields['wire_out_bytes_per_second'], 0.0)
