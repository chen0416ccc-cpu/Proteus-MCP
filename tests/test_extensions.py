import copy
import struct
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from proteus_mcp.devices import parse_device
from proteus_mcp.geometry import orientation, part_transform, transform_xy
from proteus_mcp.live import coordinate, deletion_projection, disk_matches, prepare_operations, property_values, state_token
from proteus_mcp.model import CircuitError


def device_blob(symbol='$PINDEFAULT', hidden=False):
    pin = bytes([0, 0 if hidden else 2, 0, 0])+struct.pack('<iihH', 100, 200, 900, 0)
    pin += symbol.encode()+b'\0OUT\0' + b'3\0'
    props = b'{*DEVICE}\nPREFIX=U\n{*COMPONENT}\n{VALUE=TEST}\n\0'
    offset = 20+len(pin)
    return struct.pack('<IIHHHHI', offset+4+len(props), offset, 1, 0, 1, 1, 4)+pin+struct.pack('<I', len(props))+props


class GeometryExtensionTests(unittest.TestCase):
    def test_quadrants(self):
        self.assertEqual([[2,3],[-3,2],[-2,-3],[3,-2]], [transform_xy([2,3],a) for a in (0,900,1800,2700)])

    def test_mirror_precedes_rotation(self):
        self.assertEqual([-3,-2], transform_xy([2,3],900,1))
        self.assertEqual([3,2], transform_xy([2,3],900,2))

    def test_native_connected_resistor_calibration(self):
        part = {'origin':[7620000,2540000], 'transform':orientation(180,1)}
        self.assertEqual([7112000,2540000], part_transform(part,[-508000,0]))

    def test_orientation_rejects_boolean_and_unexposed_angles(self):
        for rotation, mirror in ((True,0),(45,0),(0,4),(0,True)):
            with self.assertRaises(CircuitError):
                orientation(rotation,mirror)

    def test_known_library_pin_transform(self):
        spec = parse_device(device_blob(),'TEST')
        self.assertEqual([100,-507800], spec['pins'][0]['xy'])
        self.assertEqual('3',spec['pins'][0]['number'])

    def test_short_and_hidden_pin(self):
        self.assertEqual([100,-253800],parse_device(device_blob('$PINSHORT'),'X')['pins'][0]['xy'])
        self.assertIsNone(parse_device(device_blob(hidden=True),'X')['pins'][0]['xy'])

    def test_unknown_pin_glyph_is_not_guessed(self):
        with self.assertRaisesRegex(CircuitError,'Unsupported pin symbol'):
            parse_device(device_blob('$PINUNRESEARCHED'),'X')
        result = parse_device(device_blob('$PINUNRESEARCHED'),'X',allow_unresolved_pins=True)
        self.assertIsNone(result['pins'][0]['xy'])
        self.assertIsNone(result['pins'][0]['direction'])
        self.assertEqual('requires_native_instance',result['pins'][0]['geometry_source'])
        self.assertEqual(['$PINUNRESEARCHED'],result['unresolved_pin_glyphs'])

    def test_corrupt_size(self):
        with self.assertRaises(CircuitError):
            parse_device(device_blob()+b'bad','X')


class LiveExtensionTests(unittest.TestCase):
    def setUp(self):
        self.state = {'components':[{'address':'p1','ref':'R1','device':'RES','value':'10k',
            'position_nm':[0,0],'rotation':0,'mirror':0,'properties':{},
            'pins':[{'number':'1','name':'1','wire':'w1'},{'number':'2','name':'2','wire':'0x0'}]}],
            'wires':[{'address':'w1','endpoints':[{'owner':'p1'},{'owner':'node1'}]}]}

    def test_transform_defaults_preserve_other_fields(self):
        self.state['components'][0]['pins'][0]['wire'] = '0x0'
        self.state['wires'] = []
        operations = prepare_operations(self.state,[{'op':'transform','ref':'R1','rotation':90}],None)
        self.assertEqual([0,0],operations[0]['position_nm'])
        self.assertEqual(0,operations[0]['mirror'])
        self.assertEqual(0,self.state['components'][0]['rotation'])

    def test_invalid_later_operation_is_preflighted(self):
        with self.assertRaisesRegex(CircuitError,'not found'):
            prepare_operations(self.state,[{'op':'set','ref':'R1','properties':{'VALUE':'2k2'}},
                                          {'op':'transform','ref':'missing','rotation':90}],None)

    def test_unknown_operations_and_fields(self):
        for op in ({'op':'remove_everything','ref':'R1'}, {'op':'set','ref':'R1','properties':{'VALUE':'2k2'},'guess':True}):
            with self.assertRaises(CircuitError):
                prepare_operations(self.state,[op],None)

    def test_delete_requires_explicit_connections_and_calibrated_endpoints(self):
        with self.assertRaisesRegex(CircuitError,'JUNCTION'):
            prepare_operations(self.state,[{'op':'delete','ref':'R1','delete_connected_wires':True}],None)
        self.state['wires'][0]['endpoints'][1]['owner']='p1'
        with self.assertRaisesRegex(CircuitError,'LIVE_DELETE_CONNECTED'):
            prepare_operations(self.state,[{'op':'delete','ref':'R1'}],None)
        operations=prepare_operations(self.state,[{'op':'delete','ref':'R1','delete_connected_wires':True}],None)
        projected=deletion_projection(self.state,operations)
        self.assertFalse(projected['components'])
        self.assertFalse(projected['wires'])
        self.assertEqual(1,len(self.state['components']))

    def test_delete_is_separate_and_references_unique(self):
        for operations in ([{'op':'delete','ref':'R1'},{'op':'transform','ref':'R1','rotation':90}],
                           [{'op':'delete','ref':'R1'},{'op':'delete','ref':'R1'}],
                           [{'op':'delete','ref':'R1','delete_connected_wires':'yes'}]):
            with self.assertRaises(CircuitError):
                prepare_operations(self.state,operations,None)

    def test_identity_properties_are_not_exposed(self):
        for key in ('REF','DEVICE','CHILD','PINOUT','UNIT'):
            with self.assertRaises(CircuitError):
                property_values({key:'X'})

    def test_property_strings_and_duplicates(self):
        self.assertEqual({'VALUE':'2k2'},property_values({'value':'2k2'}))
        for properties in ({'VALUE':'a\0b'},{'VALUE':42},{'value':'a','VALUE':'b'}):
            with self.assertRaises(CircuitError):
                property_values(properties)

    def test_coordinate_bounds(self):
        for value in ([True,0],[0,500000001],[1],None):
            with self.assertRaises(CircuitError):
                coordinate(value)

    def test_pin_number_and_self_connection_validation(self):
        with self.assertRaisesRegex(CircuitError,'itself'):
            prepare_operations(self.state,[{'op':'connect','from_ref':'R1','from_pin':'1','to_ref':'R1','to_pin':'1'}],None)
        with self.assertRaisesRegex(CircuitError,'pin'):
            prepare_operations(self.state,[{'op':'connect','from_ref':'R1','from_pin':'3','to_ref':'R1','to_pin':'1'}],None)

    def test_tokens_include_wire_geometry_and_property_changes(self):
        before = state_token(self.state)
        self.state['wires'][0]['points'] = [[0,0],[10,0]]
        self.assertNotEqual(before,state_token(self.state))
        before = state_token(self.state)
        self.state['components'][0]['value'] = 'new'
        self.assertNotEqual(before,state_token(self.state))

    def readback(self):
        return {'components':[{'ref':'R1','device':'RES','value':'10k','origin_grid':[0,0],
                               'rotation':0,'mirror':0,'native_properties':''}],
                'stored_connectivity':{'net':['R1.1']}}

    def test_disk_projection_checks_transform_and_pins(self):
        readback = self.readback()
        self.assertTrue(disk_matches(self.state,readback))
        readback['components'][0]['rotation'] = 90
        self.assertFalse(disk_matches(self.state,readback))

    def test_disk_projection_rejects_missing_connection(self):
        readback = self.readback(); readback['stored_connectivity'] = {}
        self.assertFalse(disk_matches(self.state,readback))

    def test_disk_projection_checks_edited_native_properties(self):
        self.state['components'][0]['properties'] = {'PROGRAM':'test.hex'}
        self.assertFalse(disk_matches(self.state,self.readback()))

    def test_disk_projection_rejects_merging_separate_live_networks(self):
        self.state['components'][0]['pins'][1]['wire'] = 'w2'
        self.state['wires'].append({'address':'w2','endpoints':[{'owner':'p1'},{'owner':'node2'}]})
        readback = self.readback()
        readback['stored_connectivity'] = {'merged':['R1.1','R1.2']}
        self.assertFalse(disk_matches(self.state,readback))
        self.state['wires'][0]['labels'] = [{'text':'SHARED'}]
        self.state['wires'][1]['labels'] = [{'text':'SHARED'}]
        self.assertTrue(disk_matches(self.state,readback))

    def test_add_checks_library_and_persists_definition_provenance(self):
        class Library:
            def search(self,*args):
                return {'results':[{'device':'NE555','library_path':'fixture.lib'}]}
        spec = {'units':1,'elements':1,'pins':[],'blob':b'fixture','unresolved_pin_glyphs':[]}
        with patch('proteus_mcp.devices.definition',return_value=spec) as resolve:
            result = prepare_operations(self.state,[{'op':'add','ref':'U1','device':'NE555','position_nm':[0,0]}],Library())
        self.assertTrue(resolve.call_args.kwargs['allow_unresolved_pins'])
        self.assertEqual('fixture.lib',result[0]['source_library'])
        self.assertEqual(64,len(result[0]['source_definition_sha256']))

    def test_multi_unit_is_explicitly_rejected(self):
        class Library:
            def search(self,*args):
                return {'results':[{'device':'NE555','library_path':'fixture.lib'}]}
        with patch('proteus_mcp.devices.definition',return_value={'units':2,'elements':4}):
            with self.assertRaisesRegex(CircuitError,'MULTI_UNIT'):
                prepare_operations(self.state,[{'op':'add','ref':'U1','device':'NE555','position_nm':[0,0]}],Library())


if __name__ == '__main__':
    unittest.main()
