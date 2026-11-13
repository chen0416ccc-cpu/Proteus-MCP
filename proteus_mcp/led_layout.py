"""Authored eight-channel teaching layout; no named-net signal shortcuts."""
from .geometry import orientation
from .native9 import GRID, PIN_ORDER, pin_xy

SHEET_SIZE = [100, 80]
PLACEMENTS = {'U1': [0, 2], 'X1': [-28, 28], 'C1': [-36, 22], 'C2': [-20, 22],
              'C3': [8, 28], 'R9': [18, 24], 'C4': [28, 15]}
ROTATIONS = {'C1': 270, 'C2': 270, 'R9': 270, 'C4': 270}
for index in range(8):
    PLACEMENTS.update({f'D{index+1}': [-24, 12-4*index], f'R{index+1}': [-34, 12-4*index]})
    ROTATIONS.update({f'D{index+1}': 90, f'R{index+1}': 180})


def matches(model):
    from .native_backend import expected_led_topology
    devices, nets = expected_led_topology()
    return ({ref: part['device'] for ref, part in model['components'].items()} == devices and
            {name: sorted(pins) for name, pins in model['nets'].items()} ==
            {name: sorted(pins) for name, pins in nets.items()} and
            all(part.get('rotation', 0) == ROTATIONS.get(ref, 0) and part.get('mirror', 0) == 0
                for ref, part in model['components'].items()))


def plan(model):
    coordinates = {}
    for ref, pos in PLACEMENTS.items():
        part = {'device': model['components'][ref]['device'], 'origin': [v*GRID for v in pos],
                'transform': orientation(ROTATIONS.get(ref, 0), 0)}
        for number in PIN_ORDER[part['device']]:
            xy = pin_xy(part, number)
            if xy is not None:
                coordinates[ref+'.'+number] = [v/GRID for v in xy]
    result = {'junctions': {}, 'terminals': {}, 'wires': [], 'labels': {}, 'component_labels': {}}

    def junction(name, at):
        key = '@'+name
        result['junctions'][key] = coordinates[key] = list(at)
        return key

    def wire(net, a, b, *middle):
        points = [coordinates[a], *map(list, middle), coordinates[b]]
        clean = []
        for point in points:
            if not clean or point != clean[-1]:
                clean.append(point)
        result['wires'].append({'net': net, 'ends': [a, b], 'points': clean})

    def power(pin, net, at):
        key = '@'+pin.replace('.', '_')
        coordinates[key] = list(at)
        result['terminals'][key] = {'net': net, 'at': list(at)}
        wire(net, pin, key)

    # Monotonic lanes preserve P1 order without crossings in the LED bank.
    for index in range(8):
        row, lane = 12-4*index, -10-index
        wire(f'LED{index+1}_A', f'R{index+1}.1', f'D{index+1}.1')
        wire(f'LED{index+1}_K', f'D{index+1}.2', f'U1.{index+1}',
             [lane, row], [lane, -17-index])
        power(f'R{index+1}.2', 'VCC', [-40, row])
        result['component_labels'][f'D{index+1}'] = {
            'ref': {'at': [-25, row+2], 'height': 1.2, 'width': .65}, 'value': {'visible': False}}
        result['component_labels'][f'R{index+1}'] = {
            'ref': {'at': [-36, row+2], 'height': 1.2, 'width': .65},
            'value': {'at': [-32, row+2], 'height': 1, 'width': .65}}
        result['labels'][f'LED{index+1}_A'] = {'visible': False}
        result['labels'][f'LED{index+1}_K'] = {'visible': False}

    a = junction('xtal1', [-32, 28])
    b = junction('xtal2', [-24, 24])
    r = junction('reset', [18, 28])
    wire('XTAL1', 'X1.1', a)
    wire('XTAL1', 'C1.1', a, [-36, 28])
    wire('XTAL1', 'U1.19', a, [-6, 1], [-6, 30], [-32, 30])
    wire('XTAL2', 'X1.2', b, [-24, 28])
    wire('XTAL2', 'C2.1', b)
    wire('XTAL2', 'U1.18', b, [-7, -2], [-7, 26], [-24, 26])
    wire('RESET', 'C3.2', r)
    wire('RESET', 'R9.1', r)
    wire('RESET', 'U1.9', r, [-5, -6], [-5, 32], [18, 32])
    # Keep ground captions clear of the first LED row's reference labels.
    power('C1.2', 'GND', [-36, 19])
    power('C2.2', 'GND', [-20, 19])
    power('C3.1', 'VCC', [2, 28])
    power('R9.2', 'GND', [18, 18])
    power('C4.1', 'VCC', [28, 20])
    power('C4.2', 'GND', [28, 10])
    power('U1.31', 'VCC', [-6, -13])
    for net in ('XTAL1', 'XTAL2', 'RESET'):
        result['labels'][net] = {'visible': False}
    result['component_labels']['C3'] = {
        'ref': {'at': [6, 31], 'height': 1.2, 'width': .65},
        'value': {'at': [6, 29.5], 'height': 1, 'width': .65}}
    return result
