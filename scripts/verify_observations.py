"""Opt-in native acceptance for active instances and joint observations."""
import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))

from proteus_mcp.bridge import shutdown_bridge
from proteus_mcp.capabilities import source_fingerprint
from proteus_mcp.model import atomic_write,digest,json_bytes
from proteus_mcp.native_backend import read_project
from proteus_mcp.server import TOOLS,check_schema
from proteus_mcp.service import Service
from scripts.verify_native import Client


SOURCE = r'''#include <REG51.H>
sbit KEY0=P3^2;
sbit KEY1=P3^3;
sbit SOUND=P3^7;
sbit COUNT_EVENT=P3^5;
volatile unsigned char ticks;
volatile unsigned int alarm;
volatile unsigned int count;
unsigned char code mask[8]={0xfe,0xfd,0xfb,0xf7,0xef,0xdf,0xbf,0x7f};
unsigned char code seg[10]={0x3f,0x06,0x5b,0x4f,0x66,0x6d,0x7d,0x07,0x7f,0x6f};
void timer0(void) interrupt 1 {
    static unsigned char scan;
    TH0=0xfc; TL0=0x18; ++ticks;
    if(alarm && --alarm==0) SOUND=0;
    if(KEY0) {IE0=0;EX0=1;}
    if(KEY1) {IE1=0;EX1=1;}
    P2=0xff;
    P0=seg[scan==3?count%10:0];
    P2=0xf0|(0x0f^(1<<scan));
    scan=(scan+1)&3;
}
void ext0(void) interrupt 0 {EX0=0; ++count; COUNT_EVENT=!COUNT_EVENT; alarm=900; SOUND=1;}
void ext1(void) interrupt 2 {EX1=0; ++count; COUNT_EVENT=!COUNT_EVENT; alarm=900; SOUND=1;}
void main(void) {
    unsigned char led=0,start;
    EA=0; P0=0;P1=0xff;P2=0xff;P3=0xff;SOUND=0;COUNT_EVENT=0;
    TMOD=1;TH0=0xfc;TL0=0x18;IT0=0;IT1=0;IE0=0;IE1=0;
    EX0=1;EX1=1;ET0=1;PT0=1;TR0=1;EA=1;
    while(1) {P1=mask[led];start=ticks;while((unsigned char)(ticks-start)<200) {}led=(led+1)&7;}
}
'''


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--run-native',action='store_true')
    parser.add_argument('--workspace',type=Path,required=True)
    parser.add_argument('--fixture',type=Path,help='Optional local saved project; rebuild BZ1 only into a new copy')
    parser.add_argument('--installed-server',action='store_true',help='Exercise tools via isolated installed stdio server')
    args=parser.parse_args()
    if not args.run_native:
        parser.error('Explicit --run-native is required; this starts an owned Proteus process')
    service=Service(args.workspace)
    report={'source_before':source_fingerprint(),'checks':{},'calls':{},'status':'unconfirmed',
            'installed_stdio_server':args.installed_server,'desktop_input':False,'screen_captured':False,
            'harness_sha256':digest(Path(__file__).read_bytes())}
    schemas={t['name']:t['inputSchema'] for t in TOOLS}
    session=None;client=None
    output=args.workspace/'observations-acceptance.json'
    def call(name,arguments):
        check_schema(arguments,schemas[name])
        if client is None:
            value=service.call(name,arguments)
        else:
            response=client.request('tools/call',{'name':name,'arguments':arguments})
            if response.get('isError'):
                raise RuntimeError(name+': '+json.dumps(response,ensure_ascii=True)[:2000])
            value=json.loads(response['content'][0]['text'])
        report['calls'].setdefault(name,[]).append(value)
        atomic_write(output,json_bytes(report))
        print(json.dumps({'tool':name,'status':value.get('status'),'session':value.get('session')},ensure_ascii=True),flush=True)
        return value
    def check(name,value):
        report['checks'][name]=bool(value)
        if not value:
            raise AssertionError(name)
    try:
        if args.installed_server:
            args.workspace.mkdir(parents=True,exist_ok=True)
            probe=subprocess.run([sys.executable,'-I','-X','utf8','-c',
                'import json; from proteus_mcp.capabilities import source_fingerprint; print(json.dumps(source_fingerprint()))'],
                cwd=args.workspace,capture_output=True,text=True,encoding='utf-8',check=True,timeout=30)
            server_source=json.loads(probe.stdout)
            expected={k:v for k,v in report['source_before']['files'].items() if k.startswith('proteus_mcp/')}
            check('installed_package_matches_checkout',server_source['files']==expected)
            client=Client(args.workspace,installed=True)
            client.request('initialize',{'protocolVersion':'2025-06-18','capabilities':{},
                                        'clientInfo':{'name':'observations-acceptance','version':'1'}})
            client.notify('notifications/initialized')
        else:
            server_source=report['source_before']
        doctor=call('proteus_doctor',{})
        report['server_source_before']=doctor['adapter_source_sha256']
        check('server_matches_harness_source',report['server_source_before']==server_source['sha256'])
        check('known_native_build',doctor['native_build']['capabilities']['simulation']['available'])
        name='observations_'+time.strftime('%Y%m%d_%H%M%S')
        example=call('example_external_interrupt',{'project':name,'mode':'level'})
        previous=digest((service.store.folder(name)/'main.c').read_bytes())
        call('source_write',{'project':name,'source':SOURCE,'expected_sha256':previous})
        built=call('firmware_build',{'project':name})
        check('fixture_compiled',built['status']=='compiled')
        if args.fixture:
            from proteus_mcp.native9 import Builder,GRID,rewrite_project
            from proteus_mcp.native_backend import native_read,set_properties
            from proteus_mcp.devices import definition
            fixture_sha256=digest(args.fixture.read_bytes())
            before=native_read(args.fixture)
            generated=args.workspace/(name+'_fixture.pdsprj')
            def rebuild(objects):
                source=next(p for p in objects if p.get('ref')=='BZ1')
                spec=definition('BUZZER',str(service.library.root/'ACTIVE.LIB'))
                provenance={k:spec[k] for k in ('library_sha256','definition_sha256','library_path')}
                part=Builder([p for p in objects if p.get('device')!='BUZZER']).component(
                    'BZ1','BUZZER',[v/GRID for v in source['origin']],identity=source['identity'],
                    native_definition=provenance)
                source.update(kind=part['kind'],pin_header=part['pin_header'])
                source['labels'][2]['text']=part['labels'][2]['text']
                for obj in objects:
                    if obj.get('ref')=='U1':
                        set_properties(obj,{'program':built['hex_path']})
                return objects
            rewrite_project(args.fixture,generated,rebuild,database_mode='mirror')
            after=native_read(generated)
            check('fixture_connectivity_preserved',before['stored_connectivity']==after['stored_connectivity'])
            check('fixture_geometry_preserved',before['wire_geometry']==after['wire_geometry'])
            check('fixture_source_unchanged',digest(args.fixture.read_bytes())==fixture_sha256)
            exported={'path':str(generated)}
            report['fixture_mode']='New copy; production Builder reconstructs BZ1 metadata; original wires unchanged'
        else:
            exported=call('native_export',{'project':name,'routing':example['routing'],
                'placements':example['placements'],'sheet_size':example['sheet_size'],'wire_plan':example['wire_plan']})
        _,parsed=read_project(exported['path'])
        buzzer=next(p for p in parsed['components'] if p['ref']=='BZ1')
        check('exported_buzzer_active',buzzer['kind']==8)
        check('exported_buzzer_subcircuit',buzzer['labels'][2]['text']=='BUZZER')
        opened=call('project_open',{'path':exported['path'],'wait_seconds':12})
        session=opened.get('session')
        check('owned_process_started',opened['status'] in ('project_window_observed','process_alive'))
        loaded=call('live_read',{'session':session})
        check('owned_project_opened',Path(loaded['state']['path']).resolve()==Path(exported['path']).resolve())
        events=[{'ref':'SW1','value':1,'at_ms':300},{'ref':'SW1','value':0,'at_ms':500},
                {'ref':'SW2','value':1,'at_ms':1400},{'ref':'SW2','value':0,'at_ms':1600},
                {'ref':'SW1','value':1,'at_ms':2800},{'ref':'SW1','value':0,'at_ms':3000}]
        nets=['INT0','INT1','COUNT_EVENT','BUZZER_DRIVE']+['LED'+str(i)+'_K' for i in range(1,9)]
        cold=call('simulation_cold_start',{'session':session,'events':events,'nets':nets,
            'refs':['D1','BZ1'],'audio':True,'timeout_ms':18000,'max_start_ms':500,'tail_ms':1400})
        check('cold_start_completed',cold['status']=='sampled')
        check('startup_first_half_second',cold['startup_window_observed'])
        check('all_cold_inputs_dispatched',len(cold['dispatched'])==len(events))
        check('cold_audio_output',cold['audio']['status']=='output_detected')
        check('cold_cleanup_stopped',cold['cleanup']['stopped'])
        check('cold_no_file_mutation',cold['project_file_unchanged'])
        check('cold_no_firmware_mutation',cold['firmware_files_unchanged'])
        check('cold_count_changed',len({t['levels'][2] for t in cold['transitions']})==2)
        check('cold_alarm_changed',len({t['levels'][3] for t in cold['transitions']})==2)
        check('run_for_joint_diagnosis',call('simulation_control',{'session':session,'action':'run'})['status']=='verified')
        diagnosis=call('simulation_diagnose',{'session':session,'refs':['D1','BZ1','U1']})
        check('joint_diagnosis_sampled',diagnosis['status']=='sampled')
        led=next(p for p in diagnosis['components'] if p['ref']=='D1')
        cathode=next(p for p in led['pins'] if p['number']=='2')
        check('joint_d1_to_p1_pin',set(cathode['pins'])=={'D1.2','U1.1'})
        check('joint_native_state_present',led['native_state'] is not None)
        check('joint_firmware_hash_present',bool(diagnosis['provenance']['firmware'][0]['sha256']))
        audio=call('simulation_audio',{'session':session,'nets':['BUZZER_DRIVE'],'wall_ms':1000})
        check('standalone_audio_no_errors',not audio['errors'] and audio['identity_error'] is None and audio['capture_error'] is None)
        check('no_recording_or_volume_change',audio['recording'] is False and audio['volume_changed'] is False)
        check('no_optical_or_listening_claim',diagnosis['optical_verified'] is False and audio['audibility_verified'] is False)
        check('stop',call('simulation_control',{'session':session,'action':'stop'})['status']=='verified')
        closed=call('project_close',{'session':session})
        check('close',closed['status']=='closed');session=None
        report['status']='passed'
    except Exception as error:
        report.update(status='failed',error=str(error))
    finally:
        if session:
            try:
                call('simulation_control',{'session':session,'action':'stop'})
                report['cleanup']=call('project_close',{'session':session})
            except Exception as error:
                report['cleanup_error']=str(error)
        report['source_after']=source_fingerprint()
        report['checks']['source_unchanged_during_acceptance']=report['source_before']==report['source_after']
        try:
            after=call('proteus_doctor',{})
            report['server_source_after']=after['adapter_source_sha256']
            report['checks']['server_source_unchanged']=report['server_source_before']==report['server_source_after']
        except Exception as error:
            report['checks']['server_source_unchanged']=False
            report['server_postcheck_error']=str(error)
        if client is not None:
            code,stderr=client.close()
            report.update(server_exit_code=code,server_stderr=stderr)
            report['checks']['server_clean_exit']=code==0 and not stderr
        if not all(report['checks'].values()) or report.get('cleanup_error'):
            report['status']='failed'
        atomic_write(output,json_bytes(report))
        shutdown_bridge()
    print(json.dumps({'status':report['status'],'checks':report['checks'],'error':report.get('error'),
                      'report':str(output)},ensure_ascii=True,indent=2))
    return int(report['status']!='passed')


if __name__=='__main__':
    sys.exit(main())
