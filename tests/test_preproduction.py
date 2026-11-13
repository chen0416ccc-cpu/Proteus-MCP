import copy
import unittest

from proteus_mcp.preproduction import parse_report
from proteus_mcp.model import CircuitError
from proteus_mcp.pcb import exchange_payload


class PreproductionReportTests(unittest.TestCase):
    def test_exchange_comparison_ignores_only_export_basename(self):
        text='(PCB one (unit mm) (wiring (wire (path TOP 0.3 1 2 3 4) (net "N"))) (unknown (a 1)))'
        same=text.replace('PCB one', 'PCB two')
        self.assertEqual(exchange_payload(text),exchange_payload(same))
        for changed in (same.replace('TOP','BOT'),same.replace('(a 1)','(a 2)'),
                        same.replace('0.3','0.4'),same.replace('(net "N")','(net "M")'),
                        same.replace('(a 1)','(a 1) (a 1)')):
            self.assertNotEqual(exchange_payload(text),exchange_payload(changed))
        for invalid in ('(session two (unit mm))','(PCB (unit mm))','(PCB x (unit mm)'):
            with self.assertRaises(CircuitError):
                exchange_payload(invalid)

    def state(self,failed=0,warning=0,skipped=0,language='en'):
        path='D:/test/board.pdsprj'
        passed=23-failed-skipped
        if language=='en':
            diagnostic='Missing connection: U1:1, R1:1\n' if failed else ''
            text=f'Pre-production check start.\nFile: {path}\nTEST: Connectivity\n{diagnostic}Pre-production check end:\n0 errors, {warning} warnings, {failed} failed, {skipped} skipped, {passed} passed.\n'
        else:
            diagnostic='缺少的 连接：U1:1, R1:1\n' if failed else ''
            text=f'开始预生产检查。\n文件：{path}\n测试：连接性\n{diagnostic}预生产检查结束：\n0 个错误，{warning} 个警告，{failed} 个失败，{skipped} 个跳过，{passed} 个通过。\n'
        return {'log':{'text':text,'truncated':False},'checks':[{'label':str(i),'checked':True} for i in range(23)],'summary':''}

    def test_complete_english_and_chinese_native_reports(self):
        for language in ('en','zh'):
            report=parse_report(self.state(language=language),'D:/test/board.pdsprj')
            self.assertTrue(report['completed'])
            self.assertTrue(report['passed'])
            self.assertFalse(report['full_pcb_drc'])
            self.assertEqual([],report['missing_connections'])
            report=parse_report(self.state(failed=1,language=language),'D:/test/board.pdsprj')
            self.assertEqual(['U1:1, R1:1'],report['missing_connections'])

    def test_failures_warnings_and_skips_are_never_a_clean_design(self):
        for kwargs in ({'failed':1},{'warning':1},{'skipped':1}):
            report=parse_report(self.state(**kwargs),'D:/test/board.pdsprj')
            self.assertTrue(report['completed'])
            self.assertFalse(report['passed'])

    def test_wrong_project_incomplete_and_truncated_reports_rejected(self):
        state=self.state()
        self.assertIsNone(parse_report(state,'D:/test/other.pdsprj'))
        for change in ({'truncated':True},{'text':'Pre-production check end:\n0 errors, 0 warnings, 0 failed, 0 skipped, 23 passed.'}):
            other=copy.deepcopy(state);other['log'].update(change)
            self.assertIsNone(parse_report(other,'D:/test/board.pdsprj'))

    def test_partial_selection_duplicates_and_count_mismatch_fail_closed(self):
        for mode in ('disabled','duplicate','missing','counts','contradiction'):
            state=self.state()
            if mode=='disabled':state['checks'][0]['checked']=False
            if mode=='duplicate':state['checks'][0]['label']=state['checks'][1]['label']
            if mode=='missing':state['checks'].pop()
            if mode=='counts':state['log']['text']=state['log']['text'].replace('23 passed','22 passed')
            if mode=='contradiction':state['log']['text']=state['log']['text'].replace('TEST: Connectivity','Missing connection: U1:1, R1:1')
            result=parse_report(state,'D:/test/board.pdsprj')
            self.assertFalse(result['completed'])
            self.assertIsNone(result['passed'])


if __name__=='__main__':
    unittest.main()
