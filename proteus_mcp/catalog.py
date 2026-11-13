"""Logical catalog: MCU DIP numbers; passive IDs need native-library mapping."""

from copy import deepcopy


def mcu_pins():
    pins = {str(i + 1): f"P1.{i}" for i in range(8)}
    pins.update({str(21 + i): f"P2.{i}" for i in range(8)})
    pins.update({str(39 - i): f"P0.{i}" for i in range(8)})
    pins.update({str(10 + i): f"P3.{i}" for i in range(8)})
    pins.update({"9": "RST", "18": "XTAL2", "19": "XTAL1", "20": "GND",
                 "29": "PSEN", "30": "ALE", "31": "EA", "40": "VCC"})
    return pins


CATALOG = {
    "AT89C51": {"pins": mcu_pins(), "properties": {"clock_hz": 12000000, "program": ""}},
    "80C51": {"pins": mcu_pins(), "properties": {"clock_hz": 12000000, "program": ""}},
    "LED-RED": {"pins": {"1": "A", "2": "K"}, "properties": {"value": "RED"}},
    "RES": {"pins": {"1": "1", "2": "2"}, "properties": {"value": "330"}},
    "CAP": {"pins": {"1": "1", "2": "2"}, "properties": {"value": "22p"}},
    "CAP-ELEC": {"pins": {"1": "+", "2": "-"}, "properties": {"value": "10u"}},
    "CRYSTAL": {"pins": {"1": "1", "2": "2"}, "properties": {"value": "12MHz"}},
}


def properties_from_definition(spec):
    properties = {key.lower():value for key,value in spec['defaults'].items()}
    properties.setdefault('value',spec['device'])
    if 'clock' in properties:
        properties['clock_hz'] = 12000000
    if 'program' in properties:
        properties['program'] = ''
    return properties


def catalog(device=None, library_path=None):
    if device is not None:
        if device not in CATALOG or library_path is not None:
            from .devices import definition
            spec = definition(device, library_path)
            if device in CATALOG:
                raise ValueError('Explicit library override for a calibrated baseline device is not supported')
            properties = properties_from_definition(spec | {'device':device})
            return {'pins':{p['number']:p['name'] for p in spec['pins']},
                    'properties':properties, 'library':spec['library'],
                    'native_definition':{key:spec[key] for key in ('library_path','library_sha256','definition_sha256')},
                    'source':spec['source']}
        return deepcopy(CATALOG[device])
    return deepcopy(CATALOG)


def pin_number(device, pin, library_path=None):
    pins = catalog(device, library_path)["pins"]
    if pin in pins:
        return pin
    matches = [number for number, name in pins.items() if name == pin]
    if len(matches) != 1:
        raise ValueError(f"Unknown or ambiguous pin {device}.{pin}")
    return matches[0]
