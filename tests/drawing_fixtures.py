"""Small original in-memory records, not distributable Proteus templates."""
import copy
import struct

from proteus_mcp.native9 import GRID, PIN_ORDER


def label(text, xy=(0, 0), visible=True):
    return {'text': text, 'xy': list(xy), 'transform': '000000000000',
            'style': struct.pack('<iiiIH', GRID, GRID*4//5, 0, int(visible), 0).hex()}


def templates():
    result = []
    for device, order in PIN_ORDER.items():
        result.append({'type': 'component', 'ref': 'X', 'device': device, 'identity': 1,
                       'origin': [0, 0], 'transform': '00000000', 'pin_count': len(order),
                       'labels': [label('X'), label(device), label(''), label('')],
                       'wire_keys': [None]*len(order)})
    for device in ('$TERGROUND', '$TERPOWER'):
        result.append({'type': 'terminal', 'device': device, 'origin': [0, 0],
                       'transform': '00000000', 'label': label(''), 'wire_key': None})
    result.append({'type': 'wire', 'key': 'template', 'labels': [], 'points': [[0, 0], [GRID, 0]]})
    return result


def model(devices, nets):
    return {'components': {ref: {'device': device, 'properties': {}, 'position': [0, 0],
                                'rotation': 0, 'mirror': 0} for ref, device in devices.items()},
            'nets': copy.deepcopy(nets)}


def chain(count=5):
    return model({f'R{i}': 'RES' for i in range(count)},
                 {f'N{i}': [f'R{i}.2', f'R{i+1}.1'] for i in range(count-1)})
