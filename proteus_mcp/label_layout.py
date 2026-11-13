"""Bounded LABEL/TSTYLE overrides for the calibrated Proteus 9 file format."""

import math
import struct

from .model import CircuitError
from .native9 import GRID


def text_style(label):
    try:
        raw = bytes.fromhex(label['style'])
        height, width, spacing, flags = struct.unpack_from('<iiiI', raw)
        angle, mirror, justification = struct.unpack('<hHH', bytes.fromhex(label['transform']))
    except (KeyError, ValueError, struct.error) as error:
        raise CircuitError('LABEL_STYLE_NOT_CALIBRATED') from error
    if len(raw) < 18 or height <= 0 or width <= 0:
        raise CircuitError('LABEL_STYLE_NOT_CALIBRATED')
    return {'height': height, 'width': width, 'spacing': spacing, 'flags': flags,
            'rotation': angle / 10, 'mirror': mirror, 'justification': justification,
            'visible': bool(flags & 1)}


def apply_label(label, spec):
    """Keep text/net identity intact; override layout, not inherited style names."""
    from .orderly import grid_point

    if isinstance(spec, list):
        spec = {'at': spec}
    if not isinstance(spec, dict) or set(spec) - {'at', 'height', 'width', 'rotation', 'visible'}:
        raise CircuitError('LABEL_LAYOUT_SCHEMA: at, height, width, rotation, visible only')
    style = text_style(label)
    raw = bytearray.fromhex(label['style'])
    flags = style['flags']
    for key, offset, inherited in (('height', 0, 1 << 17), ('width', 4, 1 << 18)):
        if key not in spec:
            continue
        value = spec[key]
        if type(value) not in (int, float) or not math.isfinite(value) or not .4 <= value <= 4:
            raise CircuitError('LABEL_LAYOUT_SIZE: 0.4..4 grid units required')
        struct.pack_into('<i', raw, offset, round(value * GRID))
        flags &= ~inherited
    if 'visible' in spec:
        if type(spec['visible']) is not bool:
            raise CircuitError('LABEL_LAYOUT_VISIBLE: boolean required')
        # TSTYLE setters clear the corresponding named-style inheritance bit.
        flags = (flags & ~((1 << 24) | 1)) | int(spec['visible'])
    struct.pack_into('<I', raw, 12, flags)
    transform = label['transform']
    if 'rotation' in spec:
        if type(spec['rotation']) is not int or spec['rotation'] not in (0, 90, 180, 270):
            raise CircuitError('LABEL_LAYOUT_ROTATION: 0/90/180/270 required')
        transform = (struct.pack('<h', spec['rotation'] * 10) + bytes.fromhex(transform)[2:]).hex()
    position = grid_point(spec['at']) if 'at' in spec else label['xy']
    label.update(style=raw.hex(), transform=transform, xy=list(position))


def apply_component_labels(parts, specifications):
    if not isinstance(specifications, dict):
        raise CircuitError('COMPONENT_LABELS_SCHEMA: ref -> {ref, value} required')
    for ref, fields in specifications.items():
        if ref not in parts or not isinstance(fields, dict) or set(fields) - {'ref', 'value'}:
            raise CircuitError('COMPONENT_LABELS_SCHEMA: unknown component or label field: ' + str(ref))
        for key, spec in fields.items():
            apply_label(parts[ref]['labels'][0 if key == 'ref' else 1], spec)


def _label_snapshot(label):
    style = text_style(label)
    return {'text': label['text'], 'position_nm': list(label['xy']),
            **{key: style[key] for key in ('height', 'width', 'rotation', 'mirror', 'justification', 'visible')}}


def _native_label_snapshot(label):
    geometry = label['geometry']
    return {'text': label['text'], 'position_nm': geometry['position_nm'],
            'height': geometry['height'], 'width': geometry['width'],
            'rotation': geometry['angle_tenths'] / 10, 'mirror': geometry['mirror'],
            'justification': geometry['hjust'] | geometry['vjust'],
            'visible': not geometry['hidden']}


def _terminal_key(terminal):
    return (*terminal['origin_nm'], terminal['net'], terminal['label']['text'])


def layout_snapshot(objects):
    """Semantic text layout, excluding serializer offsets and inheritance flags."""
    return {'components': {obj['ref']: [_label_snapshot(label) for label in obj['labels'][:2]]
                           for obj in objects if obj['type'] == 'component'},
            'wires': sorted((_label_snapshot(label) for obj in objects if obj['type'] == 'wire'
                             for label in obj['labels']), key=lambda label: label['text']),
            'terminals': sorted(({'origin_nm': list(obj['origin']), 'net': obj['label']['text'],
                                  'label': _label_snapshot(obj['label'])}
                                 for obj in objects if obj['type'] == 'terminal'), key=_terminal_key)}


def wire_label_mismatches(state, objects):
    """Compare native LABEL getters with the file's explicit dimensions/visibility."""
    expected = layout_snapshot(objects)['wires']
    actual = []
    for wire in state['wires']:
        for label in wire.get('labels', []):
            actual.append(_native_label_snapshot(label))
    actual.sort(key=lambda label: label['text'])
    return [] if actual == expected else [{'expected': expected, 'native': actual}]


def terminal_label_mismatches(state, objects):
    """Match repeated supply names by terminal origin, preserving multiplicity."""
    expected = layout_snapshot(objects)['terminals']
    actual = sorted(({'origin_nm': list(obj['origin']), 'net': obj['net'],
                      'label': _native_label_snapshot(obj['label'])}
                     for obj in state['objects'] if obj['type'] == '.?AVTERMINAL@@'), key=_terminal_key)
    return [] if actual == expected else [{'expected': expected, 'native': actual}]
