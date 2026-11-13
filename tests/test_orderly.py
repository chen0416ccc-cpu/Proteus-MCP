import copy
import tempfile
import unittest
from pathlib import Path

from proteus_mcp.model import CircuitError, Store
from proteus_mcp.native9 import Builder, GRID, schedule_wires, pin_xy
from proteus_mcp.native_backend import CALIBRATION, read_project, native_export
from proteus_mcp.orderly import grid_point, dense


class OrderedWiresTests(unittest.TestCase):
    def test_serializer_reverses_all_vertices_not_only_the_endpoints(self):
        _,parsed=read_project(CALIBRATION)
        b=Builder(parsed['objects'])
        b.component('R1','RES',[0,0]);b.component('R2','RES',[15,10])
        a,c=b.pin('R1.2'),b.pin('R2.1')
        path=[c,[10*GRID,10*GRID],[10*GRID,0],a]
        b.wire('test',['R2.1','R1.2'],path)
        wire=next(o for o in schedule_wires(b.objects) if o['type']=='wire')
        self.assertEqual(path[::-1],wire['points'])
        self.assertEqual(a,wire['points'][0]);self.assertEqual(c,wire['points'][-1])

    def test_junction_wire_direction_follows_node_serialization_order(self):
        _,parsed=read_project(CALIBRATION)
        for junction_first in (False,True):
            with self.subTest(junction_first=junction_first):
                b=Builder(parsed['objects'])
                b.component('R1','RES',[0,0])
                end=[12*GRID,8*GRID]
                junction={'type':'junction','origin':end,'wire_keys':['branch',None,None,None]}
                b.objects.insert(0 if junction_first else len(b.objects),junction)
                path=[end,[8*GRID,8*GRID],[8*GRID,0],b.pin('R1.2')]
                b.wire('branch',['R1.2'],path)
                wire=next(o for o in schedule_wires(b.objects) if o['type']=='wire')
                expected=path if junction_first else path[::-1]
                self.assertEqual(expected,wire['points'])
                self.assertEqual(expected,next(o for o in schedule_wires(b.objects) if o['type']=='wire')['points'])

    def test_terminal_wire_direction_follows_node_serialization_order(self):
        _,parsed=read_project(CALIBRATION)
        for net in ('VCC','GND'):
            for terminal_first in (False,True):
                with self.subTest(net=net,terminal_first=terminal_first):
                    b=Builder(parsed['objects'])
                    b.component('R1','RES',[0,0])
                    b.rail('supply','R1.2',net,[10,6])
                    terminal=next(o for o in b.objects if o['type']=='terminal')
                    if terminal_first:
                        b.objects.remove(terminal);b.objects.insert(0,terminal)
                    wire=next(o for o in schedule_wires(b.objects) if o['type']=='wire')
                    pin=b.pin('R1.2')
                    self.assertEqual(pin,wire['points'][-1 if terminal_first else 0])
                    self.assertEqual([10*GRID,6*GRID],wire['points'][0 if terminal_first else -1])

    def test_plan_coordinates_and_diagonals_fail_closed(self):
        self.assertEqual([GRID//2,-3*GRID],grid_point([.5,-3]))
        for p in ([.25,0],['0',0],[0,501]):
            with self.assertRaises(CircuitError):grid_point(p)
        with self.assertRaises(CircuitError):dense([[0,0],[GRID,GRID]])

    def test_wrong_plan_does_not_change_model_or_publish_project(self):
        with tempfile.TemporaryDirectory() as temp:
            store=Store(temp);store.create('plan')
            model=store.edit('plan',0,[{'op':'add','device':'RES','ref':'R1','position':[0,0]},
                {'op':'add','device':'RES','ref':'R2','position':[150,0]},
                {'op':'connect','name':'A','pins':['R1.2','R2.1']},
                {'op':'connect','name':'VCC','pins':['R1.1','R2.2']}])
            plan={'terminals':{'@v1':{'net':'VCC','at':[-4,0]},'@v2':{'net':'VCC','at':[20,0]}},
                'wires':[{'net':'A','ends':['R1.2','R2.1'],'points':[[3,0],[13,0]]},
                    {'net':'VCC','ends':['R1.1','@v1'],'points':[[-2,0],[-4,0]]},
                    {'net':'VCC','ends':['R2.2','@v2'],'points':[[18,0],[20,0]]}]}
            result=native_export(store,'plan',layout='model',routing='schematic',wire_plan=plan)
            self.assertEqual('explicit_ordered_channel_plan',result['routing']['algorithm'])
            bad=copy.deepcopy(plan);bad['wires'][0]['points'][0]=[4,0]
            with self.assertRaisesRegex(CircuitError,'ENDPOINT_GEOMETRY'):
                native_export(store,'plan',layout='model',routing='schematic',wire_plan=bad)
            self.assertEqual(model,store.read('plan'))

    def test_example_channel_plan_has_no_led_or_display_internal_crossings(self):
        from collections import defaultdict
        from proteus_mcp.interrupt_layout import plan, PLACEMENTS, ROTATIONS
        from proteus_mcp.schematic import audit_crossings
        groups={'LED':defaultdict(list),'DISPLAY':defaultdict(list)}
        for w in plan(PLACEMENTS,ROTATIONS)['wires']:
            net=w['net']
            group='LED' if net.startswith('LED') else 'DISPLAY' if net.startswith(('SEG_','DIG_')) or net.endswith('_OUT') else None
            if group:groups[group][net].append(dense([grid_point(p) for p in w['points']]))
        self.assertEqual(0,audit_crossings(groups['LED']))
        self.assertEqual(0,audit_crossings(groups['DISPLAY']))


if __name__=='__main__':unittest.main()
