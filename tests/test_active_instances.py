import copy
import struct
import unittest
from unittest.mock import patch

from proteus_mcp.devices import parse_device
from proteus_mcp.model import CircuitError
from proteus_mcp.native9 import Builder
from tests.drawing_fixtures import templates
from test_extensions import device_blob


class ActiveInstanceTests(unittest.TestCase):
    def spec(self):
        return {'device':'BUZZER', 'elements':1, 'units':1, 'kind':0,
                'animation_frames':[], 'active_model':'SPEAKER,0,DLL',
                'definition_sha256':'0c59ac064d2f2058e3763aefb57fa72feb61391bad381c458045ec3fb5827434',
                'pins':[{'number':'1'}, {'number':'2'}], 'defaults':{'VALUE':'BUZZER'},
                'properties':'{MODFILE=BUZZER}\n'}

    def test_device_active_metadata_is_not_discarded_without_frames(self):
        blob=device_blob()
        old=b'{*DEVICE}\nPREFIX=U\n'
        new=b'{*DEVICE}\nPREFIX=U\nACTIVE=SPEAKER,0,DLL\n'
        blob=bytearray(blob.replace(old,new))
        prop_at=struct.unpack_from('<I',blob,4)[0]
        struct.pack_into('<I',blob,0,len(blob))
        struct.pack_into('<I',blob,prop_at,len(blob)-prop_at-4)
        spec=parse_device(bytes(blob),'TEST')
        self.assertEqual('SPEAKER,0,DLL',spec['active_model'])
        self.assertEqual([],spec['animation_frames'])

    def test_buzzer_instance_is_active_and_has_its_own_hidden_name(self):
        source=templates()
        resistor=next(p for p in source if p.get('device')=='RES')
        resistor['labels'][2]['text']='RES'
        original=copy.deepcopy(source)
        with patch('proteus_mcp.devices.selected_definition',return_value=self.spec()):
            part=Builder(source).component('BZ1','BUZZER',[0,0])
        self.assertEqual(8,part['kind'])
        self.assertEqual('00'*9,part['pin_header'])
        self.assertEqual('BUZZER',part['labels'][2]['text'])
        self.assertEqual(source,original)

    def test_unknown_active_build_fails_closed(self):
        for field,value in [('definition_sha256','unknown'),('active_model','UNKNOWN,0,DLL')]:
            spec=self.spec();spec[field]=value
            with patch('proteus_mcp.devices.selected_definition',return_value=spec):
                with self.assertRaisesRegex(CircuitError,'ACTIVE_INSTANCE_NOT_CALIBRATED'):
                    Builder(templates()).component('BZ1','BUZZER',[0,0])

    def test_nonactive_derived_device_does_not_keep_resistor_subcircuit(self):
        spec=self.spec();spec.update(device='TEST',active_model=None)
        with patch('proteus_mcp.devices.selected_definition',return_value=spec):
            part=Builder(templates()).component('U2','TEST',[0,0])
        self.assertEqual(0,part['kind'])
        self.assertEqual('TEST',part['labels'][2]['text'])
