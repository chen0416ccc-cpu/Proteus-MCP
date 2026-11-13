"""Native command inventory and freshness-checked Proteus reports."""
import json
import re
import time
import uuid
from pathlib import Path

from .bridge import simulation_state
from .live import Live, state_token
from .model import CircuitError, atomic_write, json_bytes


COMMAND_TOOLS = {
    ('ISIS',334858):'native_check',
    ('PDS',66563):'project_open',
    ('PDS',66564):'pcb_read',
    ('ARES',399365):'native_check',
}


def command_inventory(actions, query='', module=None):
    if not isinstance(query,str) or len(query)>128:
        raise CircuitError('Command query must be a bounded string')
    groups = {}
    for action in actions:
        if action.get('class') != 'Appln::Action':
            continue
        owner, number = action.get('owner_module'),action.get('command_id')
        if module and owner != module:
            continue
        if query.casefold() not in action.get('text','').casefold():
            continue
        key = (owner,number)
        if key not in groups:
            groups[key] = {'module':owner,'command_id':number,'labels':[],
                           'enabled':action['enabled'],'checked':action['checked'],
                           'instances':0,'state_consistent':True,'status':'discovered',
                           'arbitrary_execution_exposed':False,'tool':COMMAND_TOOLS.get(key)}
        item = groups[key]
        item['instances'] += 1
        item['state_consistent'] &= (item['enabled'],item['checked']) == (action['enabled'],action['checked'])
        if action['text'] not in item['labels']:
            item['labels'].append(action['text'])
    return sorted(groups.values(),key=lambda a:(a['module'] or '',a['command_id'] or 0))


def erc_report(text, design):
    """Only recognize the calibrated native English report, not console prose."""
    headers = list(re.finditer(r'(?m)^ELECTRICAL RULES CHECK\n=+\n',text))
    if not headers:
        return None
    body = text[headers[-1].end():]
    name = re.search(r'(?m)^Design:\s*(.*?)\s*$',body)
    if not name or name[1] != design:
        return None
    clean = re.search(r'(?m)^No ERC errors found\.\s*$',body)
    errors = re.search(r'(?im)^(\d+) ERC errors?(?:\s+(?:found|detected))?\.?\s*$',body)
    failed = re.search(r'(?m)^ERC errors found\.\s*$',body)
    diagnostics = [{'severity':m[1],'text':m[2]} for m in re.finditer(r'(?m)^(ERROR|WARNING|UNDRIVEN):\s*(.*?)\s*$',body)]
    if not clean and not errors and not failed:
        return None
    if clean and (errors or failed or diagnostics):
        return None
    if errors and (int(errors[1]) != sum(d['severity']=='ERROR' for d in diagnostics) or
                   (int(errors[1])==0 and (failed or diagnostics))):
        return None
    count = 0 if clean else int(errors[1]) if errors else sum(d['severity']=='ERROR' for d in diagnostics)
    if failed and not diagnostics:
        return None
    return {'completed':True,'passed':bool(clean) or bool(errors and count==0),
            'error_count':count,'native_error_count':int(errors[1]) if errors else None,'text':text,
            'diagnostics':diagnostics,'undriven_count':sum(d['severity']=='UNDRIVEN' for d in diagnostics),
            'scope':'Proteus native electrical rules checker with current project rules; not simulation or PCB DRC'}


def fresh_reports(texts, before):
    previous = {t['document']:t for t in before if t.get('document')}
    return [t for t in texts if t.get('document') and
            (t['document'] not in previous or
             t.get('change_count',0)>previous[t['document']].get('change_count',0))]


def drc_report(native):
    """A clean cached/disabled/truncated table must never certify a design."""
    rows = native['rows']
    active = sum(not r['ignored'] for r in rows)
    ignored = sum(r['ignored'] for r in rows)
    complete = (not native['table_truncated'] and not native['native_checker_limit_reached'] and
                len(rows)==native['total'] and active==native['active'] and ignored==native['ignored'])
    fresh = bool(native['refreshed']) and not native['global_disabled']
    return {'completed':fresh and complete,'fresh':fresh,'result_table_complete':complete,
            'passed':not rows if fresh and complete else None,
            'native_passed_excluding_ignored':not active if fresh and complete else None,
            'error_count':native['total'],'active_error_count':native['active'],'ignored_error_count':native['ignored'],
            'violations':rows,'real_time_enabled':native['real_time_enabled'],
            'global_disabled':native['global_disabled'],'native_checker_limit':native['native_checker_limit'],
            'native_checker_limit_reached':native['native_checker_limit_reached'],
            'scope':'Native ARES whole-design physical clearance checker with CURRENT configured rules; not all manufacturing checks',
            'full_pcb_drc':False,'unverified_coverage':['pre_production_checks','connectivity_CRC',
                'manufacturing_and_fabrication_rules','rule_configuration_completeness']}


class NativeChecks:
    def __init__(self, bridge, library):
        self.bridge,self.runtime = bridge,bridge.runtime
        self.live = Live(bridge,library)
        from .pcb import PCB
        self.pcb = PCB(bridge,library)

    def commands(self, session, query='', module=None):
        with self.bridge.attach(session) as (api,hwnd):
            state = api.snapshot(hwnd)
        commands = command_inventory(state['actions'],query,module)
        return {'status':'read','session':session,'commands':commands,'count':len(commands),
                'inventory_complete':not state.get('traversal_truncated',True),
                'inventory_scope':'QObjects reachable in the CURRENT owned loaded project, not all plugins or future contexts',
                'modal':state['modal'],'desktop_input':False,
                'note':'Discovery is not verification or permission to execute arbitrary commands.'}

    def check(self, session, expected_token, kind='erc'):
        if kind == 'preproduction':
            from .preproduction import check
            return check(self,session,expected_token)
        if kind == 'drc':
            return self.drc(session,expected_token)
        if kind != 'erc':
            raise CircuitError('NATIVE_CHECK_KIND_NOT_VERIFIED: choose erc, drc or preproduction')
        folder = self.runtime.session_path(session).parent
        with self.live.lock(session),self.bridge.attach(session,live=True) as (api,hwnd):
            state = api.snapshot(hwnd)
            if state['modal'] or simulation_state(state) != 'stopped':
                raise CircuitError('NATIVE_CHECK_REQUIRES_STOPPED_SIMULATION_WITHOUT_MODAL')
            keys = self.live.keys(session)
            before = api.liveread(hwnd,keys)
            self.live.check_document(session,before)
            if state_token(before) != expected_token:
                raise CircuitError('LIVE_REVISION_CONFLICT: refresh live_read token')
            evidence = folder/('native-erc-'+uuid.uuid4().hex[:8]+'.json')
            result = {'status':'unconfirmed','session':session,'kind':kind,'desktop_input':False,
                      'source':'Proteus native ERC command 334858','completed':False,
                      'fresh':False,'simulation_verified':False,'full_pcb_drc':False}
            atomic_write(evidence,json_bytes(result | {'before':before}))
            response = api.nativeerc(hwnd,keys,json.dumps(before,ensure_ascii=False,separators=(',',':')))
            deadline = time.monotonic()+10
            report = None
            while response.get('dispatched'):
                time.sleep(.15)
                state = api.snapshot(hwnd)
                for item in fresh_reports(state['texts'],response['before_reports']):
                    if item.get('truncated'):
                        continue
                    candidate = erc_report(item['text'].replace('\r\n','\n'),Path(before['path']).name)
                    if candidate:
                        report = candidate | {'native_document':item['document'],'change_count':item['change_count']}
                if report or state['modal'] or time.monotonic()>=deadline:
                    break
            after = api.liveread(hwnd,keys) if not state['modal'] else None
            preserved = after == before
            result.update(command=response,report=report,completed=bool(report),fresh=bool(report),
                          state_preserved=preserved,modal=state['modal'],token=state_token(after) if after else None)
            if report and preserved and not state['modal']:
                # A completed check containing errors is a successful invocation,
                # not a successful design. Keep the two outcomes separate.
                result.update(status='verified',passed=report['passed'],error_count=report['error_count'])
            atomic_write(evidence,json_bytes(result | {'before':before,'after':after,'texts':state['texts']}))
        return result | {'evidence_path':str(evidence)}

    def drc(self, session, expected_token):
        folder = self.runtime.session_path(session).parent
        with self.live.lock(session),self.bridge.attach(session,pcb=True) as (api,hwnd):
            before = self.pcb.read_attached(api,hwnd,session)
            if before['token']!=expected_token:
                raise CircuitError('PCB_REVISION_CONFLICT: refresh pcb_read token')
            evidence = folder/('native-drc-'+uuid.uuid4().hex[:8]+'.json')
            result = {'status':'unconfirmed','session':session,'kind':'drc','desktop_input':False,
                      'source':'ARES native whole-design clearance checker and DrcViewer flags',
                      'completed':False,'fresh':False,'simulation_verified':False,'full_pcb_drc':False}
            atomic_write(evidence,json_bytes(result | {'before':before}))
            guard = Path(before['evidence_path'])
            response = api.pcbdrc(hwnd,self.runtime.load(session)['path'],str(guard),guard.read_text(encoding='utf-8'),
                                 json.dumps(before['native']['padstacks'],ensure_ascii=False,separators=(',',':')))
            state = api.snapshot(hwnd)
            after = self.pcb.read_attached(api,hwnd,session) if not state['modal'] else None
            preserved = (after is not None and before['token']==after['token'] and
                         before['native']['document']==response['document']==after['native']['document'])
            report = drc_report(response)
            result.update(command=response,report=report,completed=report['completed'],fresh=report['fresh'],
                          state_preserved=preserved,modal=state['modal'],token=after['token'] if after else None)
            if report['completed'] and preserved and not state['modal']:
                result.update(status='verified',passed=report['passed'],error_count=report['error_count'])
            atomic_write(evidence,json_bytes(result | {'before':before,'after':after}))
        return result | {'evidence_path':str(evidence)}
