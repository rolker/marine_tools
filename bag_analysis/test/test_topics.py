"""Tests for bag_analysis.topics.topic() and SYSTEM_TOPICS."""

from bag_analysis.topics import SYSTEM_TOPICS, topic


def test_topic_prefixes_robot_scoped_name():
    assert topic('mavros/state', 'bizzy') == '/bizzy/mavros/state'


def test_topic_handles_leading_slash():
    assert topic('/odom', 'bizzy') == '/bizzy/odom'


def test_topic_passes_system_topics_unchanged():
    assert topic('/diagnostics', 'bizzy') == '/diagnostics'
    assert topic('/tf', 'izzy') == '/tf'
    assert topic('/rosout', 'whatever') == '/rosout'


def test_topic_supports_alternate_namespace():
    assert topic('odom', 'izzy') == '/izzy/odom'


def test_system_topics_use_canonical_leading_slash():
    """All SYSTEM_TOPICS entries must start with '/'."""
    for t in SYSTEM_TOPICS:
        assert t.startswith('/'), f'{t!r} missing leading slash'
