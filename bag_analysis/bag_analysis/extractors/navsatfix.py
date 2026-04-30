"""sensor_msgs/NavSatFix extractor."""

from typing import Any

from ._common import header_fields


def extract(msg) -> dict[str, Any]:
    """Flatten NavSatFix; covariance summarized to its diagonal."""
    cov = list(msg.position_covariance)
    return {
        **header_fields(msg.header),
        'status': msg.status.status,
        'service': msg.status.service,
        'latitude': msg.latitude,
        'longitude': msg.longitude,
        'altitude': msg.altitude,
        'position_covariance_type': msg.position_covariance_type,
        'cov_xx': cov[0] if len(cov) >= 9 else float('nan'),
        'cov_yy': cov[4] if len(cov) >= 9 else float('nan'),
        'cov_zz': cov[8] if len(cov) >= 9 else float('nan'),
    }
