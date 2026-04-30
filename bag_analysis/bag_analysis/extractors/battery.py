"""sensor_msgs/BatteryState extractor."""

from typing import Any

from ._common import header_fields


def extract(msg) -> dict[str, Any]:
    """Flatten BatteryState; per-cell arrays summarized as min/max."""
    cells = list(msg.cell_voltage) if msg.cell_voltage is not None else []
    return {
        **header_fields(msg.header),
        'voltage': msg.voltage,
        'temperature': msg.temperature,
        'current': msg.current,
        'charge': msg.charge,
        'capacity': msg.capacity,
        'design_capacity': msg.design_capacity,
        'percentage': msg.percentage,
        'power_supply_status': msg.power_supply_status,
        'power_supply_health': msg.power_supply_health,
        'power_supply_technology': msg.power_supply_technology,
        'present': msg.present,
        'n_cells': len(cells),
        'cell_voltage_min': min(cells) if cells else float('nan'),
        'cell_voltage_max': max(cells) if cells else float('nan'),
        'location': msg.location,
        'serial_number': msg.serial_number,
    }
