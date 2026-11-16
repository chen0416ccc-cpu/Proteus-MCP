// Calibrated VSM animation-event interface; no input emulation or net mutation.
function simContext() {
    if(!activeModal().isNull())throw new Error('SIMULATION_MODAL_BLOCKED');
    const c=liveContext(),mod=Process.getModuleByName('VSMDEBUG.DLL');
    const owner=mod.base.add(0x13c6c0).readPointer();
    if(owner.isNull())throw new Error('SIMULATION_NOT_ACTIVE');
    const shared=owner.add(0xb0),table=shared.readPointer();
    if(!table.equals(mod.base.add(0x61e50)) ||
       !table.add(0x108).readPointer().equals(mod.base.add(0xbb00)) ||
       !table.add(0xb8).readPointer().equals(mod.base.add(0x7de0)))throw new Error('SIMULATION_INTERFACE_PROFILE');
    const engine=shared.add(0x728).readPointer();
    const spice=Process.getModuleByName('PROSPICE.dll');
    if(engine.isNull() || !shared.add(0xf68).readPointer().equals(spice.getExportByName('rts_event')))
        throw new Error('SIMULATION_EVENT_CALLBACK_PROFILE');
    const file=bind(liveFrame,'?nativePathName@Document@Appln@@QEAAPEBDXZ','pointer',['pointer'])(c.document);
    const identity={path:file.isNull()?null:file.readUtf8String(),document:c.document.toString(),
        design:c.design.toString(),sheet:c.design.add(0x1b0).readPointer().toString(),
        simulator:shared.toString(),engine:engine.toString()};
    const time=()=>new NativeFunction(table.add(0xb8).readPointer(),'double',['pointer'])(shared);
    const t=time();
    if(!Number.isFinite(t)||t<0)throw new Error('INVALID_SIMULATOR_TIME');
    return {identity,shared,time,t};
}
function simMatch(c,expected) {
    if(JSON.stringify(c.identity)!==JSON.stringify(expected))throw new Error('SIMULATION_CONTEXT_CHANGED');
}
function simInventory() {
    const c=simContext(),state=liveInspect(['EID','STATE','PRIMITIVE','MODDLL']),inputs=[];
    for(const part of state.components) {
        if(!['BUTTON','LOGICSTATE'].includes(part.device))continue;
        if(part.type!=='.?AVACTIVECOMPONENT@@')throw new Error('SIMULATION_INPUT_TYPE_PROFILE');
        const primitive=part.device==='BUTTON'?'PASSIVE,RTSWITCH':'DIGITAL,RTDSTATE';
        if(part.properties.PRIMITIVE!==primitive || part.properties.MODDLL)
            throw new Error('SIMULATION_INPUT_MODEL_PROFILE');
        const p=ptr(part.address),eid=part.properties.EID;
        const id=new NativeFunction(p.readPointer().add(0x188).readPointer(),'uint',['pointer'])(p);
        if(!eid || !/^[0-9a-f]+$/i.test(eid) || parseInt(eid,16)!==id || id===0)
            throw new Error('SIMULATION_INPUT_EID_MISMATCH');
        inputs.push({ref:part.ref,device:part.device,address:part.address,eid:id,
            configured_state:part.properties.STATE,values:[0,1],
            meaning:part.device==='BUTTON'?{'0':'released','1':'pressed'}:{'0':'low','1':'high'}});
    }
    if(new Set(inputs.map(p=>p.eid)).size!==inputs.length)throw new Error('SIMULATION_DUPLICATE_EID');
    return {identity:c.identity,simulation_time:c.t,inputs};
}
function simResolve(names) {
    const mod=Process.getModuleByName('DSIM.dll');
    const link=bind(mod,'?dsim_linknet@@YA_NPEADPEAPEAW4STATE@@@Z','bool',['pointer','pointer']);
    return names.map(name=>{
        const out=Memory.alloc(8);out.writePointer(ptr(0));
        const ok=link(Memory.allocUtf8String(name),out),address=out.readPointer();
        return {name,found:!!ok&&!address.isNull(),address};
    });
}
function simVector(c,signals) {
    const before=c.time(),collect=()=>signals.map(s=>s.found?s.address.readU32():null);
    let values=collect(),stable=false,passes=1;
    for(;passes<10;passes++) {
        const next=collect();
        if(next.every((v,i)=>v===values[i])) {values=next;stable=true;break;}
        values=next;
    }
    const after=c.time();
    return {simulation_time_before:before,simulation_time_after:after,
        native_clock_unchanged:before===after,stable_snapshot:stable,read_passes:passes+1,
        wall_time_ms:Date.now(),nets:signals.map((s,i)=>({name:s.name,found:s.found,state:values[i]}))};
}
rpc.exports.siminputs=hwnd=>onUi(hwnd,simInventory);
rpc.exports.simdiagnose=(hwnd,names)=>onUi(hwnd,()=>{
    const c=simContext(),before=c.time();
    const state=liveInspect(['EID','STATE','MODFILE','MODDLL','PRIMITIVE','PROGRAM','CLOCK']);
    const vector=simVector(c,simResolve(names));
    return {identity:c.identity,state,vector,simulation_time_before:before,simulation_time_after:c.time()};
});
rpc.exports.simactuate=(hwnd,expected,target,value)=>onUi(hwnd,()=>{
    const actions=scan().actions;
    const stop=uniqueAction(actions.filter(a=>['Stop','停止'].includes(a.text)));
    const pause=uniqueAction(actions.filter(a=>['Pause','暂停'].includes(a.text)));
    if(!stop.enabled || pause.checked)throw new Error('SIMULATION_REQUIRES_RUNNING');
    const inventory=simInventory(),c=simContext();simMatch(c,expected);
    const part=inventory.inputs.find(p=>p.ref===target.ref);
    if(!part || part.address!==target.address || part.eid!==target.eid || part.device!==target.device)
        throw new Error('SIMULATION_INPUT_IDENTITY_CHANGED');
    if(value!==0&&value!==1)throw new Error('SIMULATION_INPUT_VALUE_NOT_CALIBRATED');
    const before=c.time();
    new NativeFunction(c.shared.readPointer().add(0x108).readPointer(),'void',['pointer','uint','int'])(c.shared,part.eid,value);
    return {ref:part.ref,device:part.device,eid:part.eid,value,dispatched:true,
        simulation_time_before:before,simulation_time_after:c.time(),persistent_state_changed:false};
});
rpc.exports.simtrace=(hwnd,expected,names,wallMs,intervalMs,maxSamples)=>{
    return new Promise((resolve,reject)=>{
        const samples=[],started=Date.now(),deadline=started+wallMs;let signals=null,lastTime=-1;
        const step=()=>onUi(hwnd,()=>{
            const c=simContext();simMatch(c,expected);
            if(c.t<lastTime)throw new Error('SIMULATION_TIME_REWOUND');
            lastTime=c.t;
            if(signals===null)signals=simResolve(names);
            const value=simVector(c,signals);samples.push(value);
        }).then(()=>{
            if(Date.now()>=deadline||samples.length>=maxSamples)resolve({samples,error:null,
                sample_limit_reached:samples.length>=maxSamples,wall_ms_elapsed:Date.now()-started});
            else setTimeout(step,intervalMs);
        }).catch(error=>resolve({samples,error:String(error),wall_ms_elapsed:Date.now()-started}));
        step();
    });
};

rpc.exports.simcold=(hwnd,names,refs,events,wallMs,intervalMs,maxStartMs,tailMs)=>{
    const started=Date.now(),samples=[],delivered=[];
    let identity=null,signals=null,parts=null,targets=null,lastTime=-1,lastDelivery=null;
    return new Promise(resolve=>{
        const finish=error=>resolve({samples,dispatched:delivered,identity,error,
            wall_ms_elapsed:Date.now()-started,first_sample_time:samples[0]?.simulation_time_before??null});
        const step=()=>onUi(hwnd,()=>{
            if(!activeModal().isNull())throw new Error('SIMULATION_MODAL_BLOCKED');
            const mod=Process.findModuleByName('VSMDEBUG.DLL');
            if(!mod||mod.base.add(0x13c6c0).readPointer().isNull())return false;
            const c=simContext();
            if(identity===null){
                identity=c.identity;
                const state=liveInspect(['EID','STATE']);
                parts=refs.map(ref=>livePart(state,ref));
                targets=Object.fromEntries(simInventory().inputs.map(p=>[p.ref,p]));
                if(events.some(e=>!targets[e.ref]))throw new Error('SIMULATION_INPUT_NOT_CALIBRATED');
                signals=simResolve(names);
            }else simMatch(c,identity);
            if(c.t<lastTime)throw new Error('SIMULATION_TIME_REWOUND');
            lastTime=c.t;
            const row=simVector(c,signals);
            row.components=parts.map(p=>({ref:p.ref,device:p.device,
                eid:p.properties.EID,state:liveGet(ptr(p.address),'STATE')}));
            row.components_time_after=c.time();
            row.native_clock_unchanged=row.native_clock_unchanged&&row.simulation_time_before===row.components_time_after;
            row.simulation_time_after=row.components_time_after;
            samples.push(row);
            if(samples.length===1&&row.simulation_time_before*1000>maxStartMs)
                throw new Error('COLD_START_WINDOW_MISSED');
            if(samples.length>=20000)throw new Error('SIMULATION_TRACE_SAMPLE_LIMIT');
            const event=events[delivered.length];
            if(event){
                const nominal=event.at_ms/1000;
                const spacing=delivered.length?(event.at_ms-events[delivered.length-1].at_ms)/1000:0;
                const due=lastDelivery===null?nominal:Math.max(nominal,lastDelivery+spacing);
                if(c.time()>=due){
                    const target=targets[event.ref],now=c.time();
                    new NativeFunction(c.shared.readPointer().add(0x108).readPointer(),'void',
                        ['pointer','uint','int'])(c.shared,target.eid,event.value);
                    lastDelivery=c.time();
                    delivered.push({...event,eid:target.eid,index:delivered.length,dispatched:true,
                        scheduled_simulation_time:nominal,effective_due_time:due,
                        simulation_time_before:now,simulation_time_after:lastDelivery,
                        lateness_ms:1000*(now-nominal),persistent_state_changed:false});
                }
            }
            return delivered.length===events.length&&c.time()>=lastDelivery+tailMs/1000;
        }).then(done=>{
            if(done)finish(null);
            else if(Date.now()-started>=wallMs)finish('COLD_START_TIMEOUT');
            else setTimeout(step,intervalMs);
        }).catch(error=>finish(String(error)));
        onUi(hwnd,()=>{
            const state=scan();
            if(state.modal)throw new Error('SIMULATION_MODAL_BLOCKED');
            const run=uniqueAction(state.actions.filter(a=>['Run','运行'].includes(a.text)));
            const stop=uniqueAction(state.actions.filter(a=>['Stop','停止'].includes(a.text)));
            if(stop.enabled||!run.enabled)throw new Error('COLD_START_REQUIRES_STOPPED_SESSION');
            singleShot(0,ptr(run.address),triggerSlot);
        }).then(()=>setTimeout(step,1)).catch(error=>finish(String(error)));
    });
};
