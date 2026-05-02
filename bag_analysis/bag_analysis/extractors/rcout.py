"""mavros_msgs/RCOut extractor."""

from typing import Any

from ._common import header_fields


def extract(msg) -> dict[str, Any]:
    """Flatten RCOut: channels[] -> ch_0..ch_N PWM values + count."""
    fields: dict[str, Any] = dict(header_fields(msg.header))
    for i, value in enumerate(msg.channels):
        fields[f'ch_{i}'] = int(value)
    fields['n_channels'] = len(msg.channels)
    return fields
