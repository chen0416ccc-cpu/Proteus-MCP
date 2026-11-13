"""Fresh, all-checks native PPC results; not a fabrication certification."""
import json
import re
import time
import uuid
from pathlib import Path

from .model import CircuitError, atomic_write, json_bytes
from .pcb import exchange_payload


def parse_report(state, expected_path):
    text = state['log']['text'].replace('\r\n','\n')
    if state['log'].get('truncated'):
        return None
    starts = list(re.finditer(r'(?m)^(?:Pre-production check start\.|开始预生产检查。)\s*$',text))
    if not starts:
        return None
    body = text[starts[-1].end():]
    path = re.search(r'(?m)^(?:File:|文件[：:])[ \t]*(.+?)[ \t]*$',body)
    if not path or Path(path[1]).resolve()!=Path(expected_path).resolve():
        return None
    terminal = re.search(r'(?m)^(?:Pre-production check end:|预生产检查结束[：:])\s*\n',body)
    if not terminal:
        return None
    ending = body[terminal.end():].strip()
    english = r'(\d+) errors, (\d+) warnings, (\d+) failed, (\d+) skipped, (\d+) passed\.'
    chinese = r'(\d+) 个错误，(\d+) 个警告，(\d+) 个失败，(\d+) 个跳过，(\d+) 个通过。'
    counts = re.fullmatch(english,ending) or re.fullmatch(chinese,ending)
    if not counts:
        return None
    errors,warnings,failed,skipped,passed = map(int,counts.groups())
    labels = state['checks']
    all_selected = len(labels)==23 and len({c['label'] for c in labels})==23 and all(c['checked'] is True for c in labels)
    complete = all_selected and sum((failed,skipped,passed))==len(labels)
    missing = re.findall(r'(?m)^(?:Missing\s+[Cc]onnection\s*:|缺少的\s*连接[：:])\s*(.+?)\s*$',body)
    complete = complete and not (missing and not failed)
    return {'completed':complete,'all_native_checks_selected':all_selected,
            'native_suite_check_count':len(labels),'error_count':errors,'warning_count':warnings,
            'failed_check_count':failed,'skipped_check_count':skipped,'passed_check_count':passed,
            'passed':not any((errors,warnings,failed,skipped)) if complete else None,
            'native_passed_excluding_warnings_and_skips':not any((errors,failed)) if complete else None,
            'selected_checks':[c['label'] for c in labels],'missing_connections':missing,
            'text':text,'summary':state.get('summary'),
            'scope':'All 23 checks in the native Proteus 9 PPC suite, with current board rules and project settings',
            'full_pcb_drc':False,'fabrication_certified':False,
            'unverified_coverage':['rule_configuration_completeness','fabricator_specific_requirements']}


def check(service, session, expected_token):
    pcb, live, bridge = service.pcb, service.live, service.bridge
    runtime = bridge.runtime
    if not Path(runtime.load(session)['path']).resolve().is_relative_to(runtime.root.parent.resolve()):
        raise CircuitError('PPC_REQUIRES_WORKSPACE_COPY')
    evidence = runtime.session_path(session).parent/('native-ppc-'+uuid.uuid4().hex[:8]+'.json')
    result = {'status':'unconfirmed','session':session,'kind':'preproduction','desktop_input':False,
              'completed':False,'fresh':False,'full_pcb_drc':False,'simulation_verified':False}
    with live.lock(session), bridge.attach(session,pcb=True) as (api,hwnd):
        before = pcb.read_attached(api,hwnd,session)
        if before['token']!=expected_token:
            raise CircuitError('PCB_REVISION_CONFLICT: refresh pcb_read token')
        guard = Path(before['evidence_path'])
        atomic_write(evidence,json_bytes(result | {'before':before}))
        opened = False
        finish, report, latest = None, None, None
        try:
            result['command'] = api.ppcbegin(hwnd,runtime.load(session)['path'],str(guard),
                guard.read_text(encoding='utf-8'),json.dumps(before['native']['padstacks'],ensure_ascii=False,separators=(',',':')))
            opened = True
            deadline = time.monotonic()+10
            while True:
                state = api.modalread(hwnd)
                if state:
                    if state['class']!='PPCDialog':
                        raise CircuitError('PPC_UNKNOWN_MODAL: '+json.dumps(state,ensure_ascii=True))
                    break
                if time.monotonic()>deadline:
                    raise CircuitError('PPC_OPEN_TIMEOUT')
                time.sleep(.1)
            start = result['start'] = api.ppcstart(hwnd)
            atomic_write(evidence,json_bytes(result | {'before':before}))
            initial = start['original']['log']
            deadline = time.monotonic()+60
            while True:
                time.sleep(.15)
                latest = api.ppcread(hwnd)
                log = latest['log']
                fresh = log['document']==initial['document'] and log['change_count']>initial['change_count']
                report = parse_report(latest,runtime.load(session)['path']) if fresh else None
                if report or time.monotonic()>deadline:
                    break
            result.update(report=report,completed=bool(report and report['completed']),fresh=bool(report),last_native_state=latest)
        except Exception as error:
            result['error'] = str(error)
            atomic_write(evidence,json_bytes(result | {'before':before}))
        finally:
            if opened:
                state = api.modalread(hwnd)
                if state and state['class']=='PPCDialog':
                    try:
                        finish = result['finish'] = api.ppcfinish(hwnd)
                    except Exception as error:
                        result['finish_error'] = str(error)
                        # Only our exact native PPC dialog may be cancelled.
                        # This fallback never treats options as restored.
                        result['cancel'] = api.cancelpreproduction(hwnd)
                    deadline = time.monotonic()+15
                    while api.modalread(hwnd) and time.monotonic()<deadline:
                        time.sleep(.1)
            result['modal'] = api.modalread(hwnd)
            result['configuration_restored'] = bool(finish and finish['configuration_restored'])
            after = pcb.read_attached(api,hwnd,session) if not result['modal'] else None
            result['native_exchange_preserved'] = bool(after and
                exchange_payload(guard.read_text(encoding='utf-8')) ==
                exchange_payload(Path(after['evidence_path']).read_text(encoding='utf-8')))
            result['exchange_comparison'] = 'all_exchange_records_except_export_file_basename'
            result['state_preserved'] = after is not None and before['token']==after['token'] and before['native']['document']==after['native']['document'] and result['native_exchange_preserved']
            result['token'] = after['token'] if after else None
            if report and report['completed'] and result['state_preserved'] and result['configuration_restored'] and not result['modal'] and not result.get('error'):
                result.update(status='verified',passed=report['passed'],error_count=report['error_count'],
                              failed_check_count=report['failed_check_count'])
            atomic_write(evidence,json_bytes(result | {'before':before,'after':after}))
    return result | {'evidence_path':str(evidence)}
