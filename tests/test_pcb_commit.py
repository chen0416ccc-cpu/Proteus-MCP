import copy
import json
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from proteus_mcp.model import CircuitError
from proteus_mcp.pcb import PCB, board_token
from proteus_mcp.pcb_push import wire_id


@contextmanager
def notice_watch(*args):
    yield {'notices':[],'errors':[]}


class ReplacementCommitTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        guard = self.root/'before.edf'; guard.write_text('(PCB before (unit mm))',encoding='utf-8')
        self.old = {'net':'OLD','layer':'TOP','width_nm':300000,'points_nm':[[0,0],[1000000,0]]}
        self.other = {**self.old,'net':'OTHER','layer':'BOT'}
        self.moved = {**self.old,'points_nm':[[0,0],[0,1000000],[1000000,1000000],[1000000,0]]}
        self.new = {**self.old,'net':'NEW','points_nm':[[0,2000000],[1000000,2000000]]}
        board = {'wires':[self.old,self.other],'vias':[],'pads':[],'placements':[],'via_styles':{}}
        self.before = {'board':board,'token':board_token(board),'evidence_path':str(guard),
                       'native':{'document':'doc','padstacks':{}}}
        after_board = {**board,'wires':[self.moved,self.new,self.other]}
        self.after = {**self.before,'board':after_board,'token':board_token(after_board)}
        self.runtime = SimpleNamespace(root=self.root,load=lambda _: {'path':str(self.root/'test.pdsprj')},
                                       session_path=lambda _:self.root/'session.json')
        self.pcb = PCB(SimpleNamespace(runtime=self.runtime),None)
        self.api = SimpleNamespace(pcbimport=lambda *args:{'ok':True},acknowledgedrcnotice=lambda *args:None,
                                   pcbdrc=lambda *args:self.native_check)
        self.native_check = {'rows':[],'total':0,'active':0,'ignored':0,'table_truncated':False,
                             'native_checker_limit_reached':False,'refreshed':True,'global_disabled':False,
                             'real_time_enabled':True,'native_checker_limit':5000}
        self.plan = {'wires':[self.moved,self.new],'vias':[],'remove_wires':[self.old],'push_shove':True}
        self.seed_receipt()

    def seed_receipt(self):
        (self.root/'pcb-state.json').write_text(json.dumps({'status':'applied','token':self.before['token'],
            'managed_wire_ids':[wire_id(self.old),wire_id(self.other)]}),encoding='utf-8')

    def commit(self):
        with patch('proteus_mcp.notices.drc_notice_watcher',notice_watch),patch.object(self.pcb,'read_attached',return_value=self.after):
            return self.pcb.import_plan(self.api,'1','s',self.before,self.plan,self.plan,require_native_clearance=True)

    def test_exact_replacement_preserves_other_copper_and_updates_provenance(self):
        result = self.commit()
        self.assertEqual('applied',result['status'])
        self.assertTrue(result['replacement_delta_verified'])
        self.assertTrue(result['board_preserved'])
        self.assertTrue(result['push_shove'])
        self.assertTrue(result['native_clearance_verified'])
        self.assertEqual({wire_id(w) for w in (self.moved,self.new,self.other)},set(result['managed_wire_ids']))
        text = Path(result['intent_exchange_path']).read_text(encoding='utf-8')
        self.assertEqual(3,text.count('(wire '))

    def test_disabled_or_failing_native_check_is_unconfirmed_not_rolled_back(self):
        for update in ({'global_disabled':True,'refreshed':False},
                       {'rows':[{'ignored':False}], 'total':1,'active':1},
                       {'native_checker_limit_reached':True}):
            with self.subTest(update=update):
                original = copy.deepcopy(self.native_check)
                self.native_check.update(update)
                self.seed_receipt()
                result = self.commit()
                self.assertEqual('unconfirmed',result['status'])
                self.assertTrue(result['may_have_applied'])
                self.assertTrue(result['native_copper_verified'])
                self.assertFalse(result['push_shove'])
                self.assertFalse(result['native_clearance_verified'])
                self.native_check = original

    def test_missing_nontarget_trace_and_duplicate_replacement_cannot_pass(self):
        for wires in ([self.moved,self.new],[self.moved,self.new,self.other,self.moved]):
            self.after['board']['wires'] = wires
            self.after['token'] = board_token(self.after['board'])
            self.seed_receipt()
            result = self.commit()
            self.assertEqual('unconfirmed',result['status'])
            self.assertFalse(result['native_copper_verified'])
            self.assertFalse(result['push_shove'])

    def test_remove_intent_must_exist_before_import(self):
        self.plan['remove_wires'].append(self.old)
        with self.assertRaisesRegex(CircuitError,'REPLACEMENT_NOT_PRESENT'):
            self.commit()


if __name__ == '__main__':
    unittest.main()
