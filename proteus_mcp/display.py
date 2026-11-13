"""Decode multiplexed seven-segment net traces, never pixels or optical output."""
import json
import uuid
from pathlib import Path

from .model import CircuitError,atomic_write,digest,json_bytes
from .simulation import digital_level,net_names


SEGMENTS={0x3f:'0',0x06:'1',0x5b:'2',0x4f:'3',0x66:'4',0x6d:'5',0x7d:'6',0x07:'7',0x7f:'8',0x6f:'9'}


def decode_samples(samples,segment_nets,digit_nets,segment_active=1,digit_active=0,max_frame_ms=500):
    net_names(segment_nets);net_names(digit_nets)
    if len(segment_nets) not in (7,8) or not 1<=len(digit_nets)<=8 or set(segment_nets)&set(digit_nets):
        raise CircuitError('Display needs 7/8 distinct segment nets A..G[,DP] and 1..8 distinct select nets')
    if type(segment_active) is not int or segment_active not in (0,1) or type(digit_active) is not int or digit_active not in (0,1) or type(max_frame_ms) is not int or not 1<=max_frame_ms<=2000:
        raise CircuitError('Display polarities must be binary and max_frame_ms=1..2000')
    pending={};frames=[];invalid=0;ambiguous=0;blank=0;unstable=0;expired=0
    readings=[{} for _ in digit_nets]
    previous_time=-1
    for index,sample in enumerate(samples):
        now=sample['simulation_time_after']
        if now<previous_time:raise CircuitError('DISPLAY_TRACE_TIME_REWOUND')
        previous_time=now
        if not sample['stable_snapshot'] or not sample['native_clock_unchanged']:
            unstable+=1;continue
        values={n['name']:digital_level(n['state']) if n['found'] else None for n in sample['nets']}
        if any(values.get(n) is None for n in segment_nets+digit_nets):
            invalid+=1;continue
        selected=[i for i,n in enumerate(digit_nets) if values[n]==digit_active]
        if not selected:blank+=1;continue
        if len(selected)!=1:ambiguous+=1;pending={};continue
        pattern=sum((values[n]==segment_active)<<i for i,n in enumerate(segment_nets[:7]))
        digit=SEGMENTS.get(pattern)
        if digit is None:invalid+=1;continue
        position=selected[0]
        readings[position][digit]=readings[position].get(digit,0)+1
        if pending and (now-min(p['time'] for p in pending.values()))*1000>max_frame_ms:
            expired+=1;pending={}
        pending[position]={'digit':digit,'time':now,'sample_index':index}
        if len(pending)==len(digit_nets):
            text=''.join(pending[i]['digit'] for i in range(len(digit_nets)))
            frame={'text':text,'simulation_time_start':min(p['time'] for p in pending.values()),
                   'simulation_time_end':now,'sample_index':index}
            if frames and frames[-1]['text']==text:
                frames[-1]['confirmations']+=1;frames[-1]['simulation_time_end']=now
            else:frames.append(frame | {'confirmations':1})
            pending={}
    return {'frames':frames,'repeated_frames':[f for f in frames if f['confirmations']>=2],
        'per_digit_histograms':readings,'invalid_samples':invalid,'ambiguous_select_samples':ambiguous,
        'blank_samples':blank,'unstable_or_clock_crossing_samples':unstable,'expired_partial_frames':expired,
        'decoded_frames':sum(f['confirmations'] for f in frames),
        'scope':'temporal composite of sampled segment/select nets, not a simultaneous or optical display reading; polling can miss transitions'}


def decode_display(root,trace_path,expected_sha256,segment_nets,digit_nets,segment_active=1,digit_active=0,max_frame_ms=500):
    path=Path(trace_path).resolve(strict=True)
    if not path.is_relative_to(Path(root).resolve()) or path.suffix!='.json' or path.stat().st_size>128*1024*1024:
        raise CircuitError('Display decoder requires a bounded workspace JSON trace')
    raw=path.read_bytes()
    if digest(raw)!=expected_sha256:raise CircuitError('DISPLAY_TRACE_REVISION_CONFLICT')
    data=json.loads(raw)
    if not isinstance(data.get('samples'),list) or not 1<=len(data['samples'])<=20000:
        raise CircuitError('Display decoder requires 1..20000 raw samples')
    if not set(segment_nets+digit_nets)<=set(data.get('native_nets',[])):
        raise CircuitError('Display nets not included in the source trace')
    result={'status':'decoded','source_path':str(path),'source_sha256':expected_sha256,
        'segment_nets':segment_nets,'digit_nets':digit_nets,
        **decode_samples(data['samples'],segment_nets,digit_nets,segment_active,digit_active,max_frame_ms),
        'simulation_verified':False,'optical_verified':False}
    target=path.with_name('display-decode-'+uuid.uuid4().hex[:12]+'.json')
    atomic_write(target,json_bytes(result))
    return result | {'evidence_path':str(target)}
