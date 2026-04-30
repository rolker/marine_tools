"""
Topic-name helpers.

The Tier-1 plot inventory mixes robot-scoped topics (everything under
`/<namespace>/...`) with system topics that ROS publishes outside any
namespace (`/diagnostics`, `/tf`, `/rosout`, etc.). Plot modules use
`topic(name, namespace)` to build the full topic name; the helper looks
up the bare leaf in `SYSTEM_TOPICS` and returns it unchanged for system
topics, otherwise prefixes it with `/<namespace>/`.

Keeping the allowlist in one place means plot modules don't make
per-topic decisions, and a future namespace flip (e.g. `izzy`) is a
single CLI argument away.
"""

from __future__ import annotations

# Topic names that are NOT prefixed with the robot namespace. Stored as
# the canonical leading-slash form so equality checks are unambiguous.
SYSTEM_TOPICS: frozenset[str] = frozenset({
    '/diagnostics',
    '/marine/platforms',
    '/rosout',
    '/tf',
    '/tf_static',
})


def topic(name: str, namespace: str = 'bizzy') -> str:
    """
    Build a full topic name from a bare name and a robot namespace.

    `name` may be passed with or without a leading slash. System topics
    in `SYSTEM_TOPICS` are returned unchanged; everything else is
    prefixed with `/<namespace>/`.

    Examples
    --------
    >>> topic('mavros/state', 'bizzy')
    '/bizzy/mavros/state'
    >>> topic('/diagnostics', 'bizzy')
    '/diagnostics'
    >>> topic('odom', 'izzy')
    '/izzy/odom'
    """
    canonical = name if name.startswith('/') else '/' + name
    if canonical in SYSTEM_TOPICS:
        return canonical
    bare = canonical.lstrip('/')
    return f'/{namespace}/{bare}'
