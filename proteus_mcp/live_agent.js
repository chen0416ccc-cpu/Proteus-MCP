// Loaded only after the ISIS/AppFrame/LxlCore fingerprint gate.
const liveFrame=Process.getModuleByName('AppFrame.dll');
const liveIsis=Process.getModuleByName('ISIS.dll');
const liveLxl=Process.getModuleByName('LxlCore.dll');
const livePrims=Process.getModuleByName('Prims.dll');
const liveActive=bind(liveFrame,'?getActiveModule@Process@Appln@@SAPEAVModule@2@XZ','pointer',[]);
const livePush=bind(liveFrame,'?pushContext@Module@Appln@@QEAAXXZ','void',['pointer']);
const livePop=bind(liveFrame,'?popContext@Module@Appln@@QEAAXXZ','void',['pointer']);
const liveBegin=new NativeFunction(liveIsis.base.add(0x75860),'void',['pointer']);
const liveFinish=new NativeFunction(liveIsis.base.add(0x77610),'void',['pointer']);
const liveCreate=new NativeFunction(liveIsis.base.add(0x78810),'pointer',['pointer','pointer','pointer','pointer','pointer','pointer']);
const liveConnect=new NativeFunction(liveIsis.base.add(0x75c40),'bool',['pointer','pointer','pointer','pointer','pointer']);
const liveProperty=new NativeFunction(liveIsis.base.add(0x79100),'bool',['pointer','pointer','pointer','pointer','pointer','uint16']);
const liveInvert=bind(liveLxl,'?invert@CTS@@QEAAAEAV1@XZ','pointer',['pointer']);
const liveCompose=bind(liveLxl,'??XCTS@@QEAAAEAV0@AEBV0@@Z','pointer',['pointer','pointer']);
const liveNumber=new NativeFunction(liveIsis.base.add(0x11e5b0),'pointer',['pointer','pointer']);
const liveUtf=value=>Memory.allocUtf8String(value);
function liveLabel(label) {
    const text=bind(livePrims,'?text@LABEL@@QEBAPEBDXZ','pointer',['pointer'])(label);
    const cts=Memory.alloc(12);
    bind(livePrims,'?mapping@LABEL@@QEBA?AVCTS@@XZ','pointer',['pointer','pointer'])(label,cts);
    const integer=name=>bind(livePrims,'?'+name+'@LABEL@@QEBAHXZ','int',['pointer'])(label);
    const just=name=>bind(livePrims,'?'+name+'@LABEL@@QEBAGXZ','uint16',['pointer'])(label);
    const flag=name=>bind(livePrims,'?'+name+'@LABEL@@QEBA_NXZ','bool',['pointer'])(label);
    const font=bind(livePrims,'?font@LABEL@@QEBAPEBDXZ','pointer',['pointer'])(label);
    return {text:text.isNull()?null:text.readUtf8String(),geometry:{
        position_nm:[cts.readS32(),cts.add(4).readS32()],angle_tenths:cts.add(8).readS16(),mirror:cts.add(10).readU16(),
        width:integer('width'),height:integer('height'),hjust:just('hjust'),vjust:just('vjust'),
        font:font.isNull()?null:font.readUtf8String(),hidden:flag('ishidden'),flipped:flag('isflipped')}};
}
function liveContext() {
    const module=liveActive();
    if(module.isNull())throw new Error('No active module');
    const name=bind(liveFrame,'?getModuleCName@Module@Appln@@QEBAPEBDXZ','pointer',['pointer'])(module).readUtf8String();
    if(name!=='ISIS')throw new Error('ACTIVE_MODULE_NOT_SCHEMATIC: '+name);
    const draw=module.add(0x428).readPointer();
    if(draw.isNull()||!draw.readPointer().equals(liveIsis.base.add(0x1bd1c8)))throw new Error('Uncalibrated ISISAUTODRAW');
    // IModule is a secondary base; call its virtual getter, not a base body
    // with the complete QObject/ISISAPP address.
    const moduleInterface=module.add(0x10);
    const document=new NativeFunction(moduleInterface.readPointer().add(8).readPointer(),'pointer',['pointer'])(moduleInterface);
    if(document.isNull())throw new Error('No current native document');
    return {module,draw,design:draw.add(0x40).readPointer(),document};
}
function liveType(obj) {
    const locator=obj.readPointer().sub(8).readPointer();
    if(locator.compare(liveIsis.base)<0||locator.compare(liveIsis.base.add(liveIsis.size))>=0)throw new Error('Object RTTI outside calibrated ISIS');
    return liveIsis.base.add(locator.add(12).readU32()).add(16).readUtf8String();
}
function liveObjects(list) {
    let link=list.readPointer();
    const result=[],seen=new Set();
    while(!link.isNull()) {
        const key=link.toString();
        if(seen.has(key)||result.length>=10000)throw new Error('Native object list bound/loop');
        seen.add(key);result.push(link.sub(0x20));link=link.readPointer();
    }
    return result;
}
function liveGet(obj,key) {
    const adi=obj.add(0x18),getter=new NativeFunction(adi.readPointer().readPointer(),'pointer',['pointer','pointer']);
    const value=getter(adi,liveUtf(key));
    return value.isNull()?null:value.readUtf8String();
}
function liveInspect(keys) {
    const c=liveContext();
    const file=bind(liveFrame,'?nativePathName@Document@Appln@@QEAAPEBDXZ','pointer',['pointer'])(c.document);
    const state={path:file.isNull()?null:file.readUtf8String(),module:c.module.toString(),document:c.document.toString(),design:c.design.toString(),
        sheet_identity:c.design.add(0x1b0).readPointer().toString(),
        components:[],objects:[],wires:[]};
    for(const obj of liveObjects(c.design.add(0x1b0).readPointer())) {
        const type=liveType(obj),address=obj.toString();
        if(!['.?AVCOMPONENT@@','.?AVACTIVECOMPONENT@@'].includes(type)) {
            const record={address,type,origin:[obj.add(0x48).readS32(),obj.add(0x4c).readS32()],
                ref:liveGet(obj,'REF'),value:liveGet(obj,'VALUE'),device:liveGet(obj,'DEVICE')};
            if(type==='.?AVTERMINAL@@') {
                if(!obj.readPointer().equals(liveIsis.base.add(0x1cdd28)))throw new Error('LIVE_TERMINAL_PROFILE');
                const net=new NativeFunction(obj.readPointer().add(0x138).readPointer(),'pointer',['pointer'])(obj);
                record.net=net.isNull()?null:net.readUtf8String();record.label=liveLabel(obj.add(0x6c));
                record.terminal_type=liveGet(obj,'TYPE');record.symbol=liveGet(obj,'SYMBOL');
            }
            state.objects.push(record);
            continue;
        }
        const table=obj.readPointer(),count=new NativeFunction(table.add(0x120).readPointer(),'int',['pointer'])(obj);
        if(count<0||count>1024)throw new Error('Native pin count outside bounds');
        const pins=[],definition=obj.add(0x240).readPointer(),overrides=obj.add(0x27c).readPointer();
        const device=liveGet(obj,'DEVICE');
        const calibratedNumbers={'LED-RED':{A:'1',K:'2'},'CAP-ELEC':{'+':'1','-':'2'}};
        for(let i=0;i<count;i++) {
            const label=new NativeFunction(table.add(0x138).readPointer(),'pointer',['pointer','int'])(obj,i);
            const pinData=definition.add(0x190).readPointer().add(8*i).readPointer();
            const number=liveNumber(pinData,overrides.isNull()?ptr(0):overrides.add(i*0x56));
            const out=Memory.alloc(8);
            new NativeFunction(table.add(0x130).readPointer(),'pointer',['pointer','pointer','int'])(obj,out,i);
            const link=new NativeFunction(table.add(0x128).readPointer(),'pointer',['pointer','int'])(obj,i);
            const name=label.isNull()?null:label.readUtf8String(),raw=number.isNull()?null:number.readUtf8String();
            // These baseline symbols have blank native number fields. Their
            // physical numbering is independently calibrated against DSN/CDB.
            const physical=raw || (calibratedNumbers[device]||{})[name] || name;
            pins.push({name,number:physical,raw_number:raw,
                number_source:raw?'native_instance_override_or_definition':
                    ((calibratedNumbers[device]||{})[name]?'calibrated_baseline_DSNCDB_pinout':'native_pin_name'),
                xy:[out.readS32(),out.add(4).readS32()],wire:link.readPointer().toString()});
        }
        state.components.push({address,type,ref:liveGet(obj,'REF'),device:liveGet(obj,'DEVICE'),value:liveGet(obj,'VALUE'),
            position_nm:[obj.add(0x48).readS32(),obj.add(0x4c).readS32()],rotation:((obj.add(0x50).readS16()/10)%360+360)%360,
            mirror:obj.add(0x52).readU16(),properties:Object.fromEntries(keys.map(k=>[k,liveGet(obj,k)])),pins});
    }
    for(const wire of liveObjects(c.design.add(0x1b8).readPointer())) {
        if(liveType(wire)!=='.?AVWIRE@@')throw new Error('Unknown native wire record');
        const count=wire.add(0xc4).readS32(),points=wire.add(0xb8).readPointer();
        if(count<0||count>50000)throw new Error('Native polyline bound');
        const xy=[];
        if(count===0) {
            for(const off of [0x48,0x68])xy.push([wire.add(off).readS32(),wire.add(off+4).readS32()]);
        } else {
            if(points.isNull())throw new Error('Missing native wire polyline');
            for(let i=0;i<=count;i++)xy.push([points.add(8*i).readS32(),points.add(8*i+4).readS32()]);
        }
        state.wires.push({address:wire.toString(),points:xy,endpoints:[0x48,0x68].map(off=>({
            xy:[wire.add(off).readS32(),wire.add(off+4).readS32()],owner:wire.add(off+8).readPointer().toString(),
            link:wire.add(off+16).readPointer().toString()}))});
        const labels=wire.add(0xb0).readPointer(),labelCount=wire.add(0xc0).readS32();
        if(labelCount<0||labelCount>1000)throw new Error('Native label count bound');
        state.wires[state.wires.length-1].labels=[];
        for(let i=0;i<labelCount;i++) {
            state.wires[state.wires.length-1].labels.push(liveLabel(labels.add(i*0x58)));
        }
    }
    for(const dot of state.objects.filter(o=>o.type==='.?AVDOT@@')) {
        const obj=ptr(dot.address);
        if(!obj.readPointer().equals(liveIsis.base.add(0x1ce608)))throw new Error('LIVE_JUNCTION_PROFILE');
        for(let i=0;i<4;i++) {
            const wire=obj.add(0x5c+16*i).readPointer();
            if(!wire.isNull()&&!state.wires.some(w=>w.address===wire.toString()&&w.endpoints.some(e=>e.owner===dot.address)))
                throw new Error('LIVE_JUNCTION_RECIPROCAL_LINK_MISMATCH');
        }
    }
    return state;
}
function livePart(state,ref) {
    const matches=state.components.filter(p=>p.ref===ref);
    if(matches.length!==1)throw new Error('Missing/ambiguous component '+ref);
    return matches[0];
}
function livePin(part,pin) {
    let matches=part.pins.filter(p=>p.number===pin);
    if(!matches.length)matches=part.pins.filter(p=>p.name===pin);
    if(matches.length!==1)throw new Error('Missing/ambiguous pin '+part.ref+'.'+pin);
    if(part.pins.filter(p=>p.name===matches[0].name).length!==1)throw new Error('Native pin name is ambiguous');
    return matches[0].name;
}
function liveCts(position,rotation,mirror) {
    const cts=Memory.alloc(12);cts.writeS32(position[0]);cts.add(4).writeS32(position[1]);
    cts.add(8).writeS16(rotation*10);cts.add(10).writeU16(mirror);return cts;
}
function liveRefreshAttachedGeometry(part) {
    // COMPONENT::map updates WIRE endpoints, not the cached polyline ends.
    // Match WIRE::read at 0x150df2: keep bends, copy the authoritative ends.
    const wires=[...new Set(part.pins.map(p=>p.wire).filter(p=>p!=='0x0'))];
    for(const address of wires) {
        const wire=ptr(address);
        if(!wire.readPointer().equals(liveIsis.base.add(0x1d4008)))throw new Error('LIVE_WIRE_GEOMETRY_PROFILE');
        const count=wire.add(0xc4).readS32(),points=wire.add(0xb8).readPointer();
        if(count<0||count>50000 || (count>0 && points.isNull()))throw new Error('LIVE_WIRE_GEOMETRY_BOUND');
        if(count>0) {
            points.writeByteArray(wire.add(0x48).readByteArray(8));
            points.add(8*count).writeByteArray(wire.add(0x68).readByteArray(8));
        }
        // WIRE::getbox recomputes its native bounds from all bends and labels.
        new NativeFunction(wire.readPointer().add(0x38).readPointer(),'pointer',['pointer','pointer','int'])(wire,wire.add(0x30),0);
    }
}
function liveDelete(c,before,operations,keys) {
    if(operations.some(op=>!['delete','delete_wire','delete_junction'].includes(op.op)))throw new Error('LIVE_DELETE_SEPARATE_BATCH_REQUIRED');
    const selection=c.module.add(0x140).readPointer().add(0x90);
    if(!selection.readPointer().equals(liveFrame.base.add(0xc9288)) || selection.add(0x60).readS32()!==0)
        throw new Error('LIVE_DELETE_EXISTING_SELECTION');
    const all=[...before.components,...before.objects,...before.wires];
    if(all.some(o=>ptr(o.address).add(0x40).readU32()&0x800))throw new Error('LIVE_DELETE_EXISTING_TAGS');
    const componentOps=operations.filter(op=>op.op==='delete'),parts=componentOps.map(op=>livePart(before,op.ref));
    const componentOwners=new Set(before.components.map(p=>p.address));
    const targets=new Map();
    for(let i=0;i<parts.length;i++) {
        const part=parts[i],attached=part.pins.filter(p=>p.wire!=='0x0');
        if(attached.length && !componentOps[i].delete_connected_wires)throw new Error('LIVE_DELETE_CONNECTED');
        for(const pin of attached) {
            const wire=before.wires.find(w=>w.address===pin.wire);
            if(!wire || wire.endpoints.some(e=>!componentOwners.has(e.owner)))throw new Error('LIVE_DELETE_JUNCTION_NOT_CALIBRATED');
        }
        if(!ptr(part.address).readPointer().add(0x48).readPointer().equals(liveIsis.base.add(0xfffd0)))
            throw new Error('LIVE_DELETE_UNCALIBRATED_COMPONENT_TAG');
        targets.set(part.address,part);
    }
    for(const op of operations) {
        if(op.op==='delete_wire') {
            const wire=before.wires.find(w=>w.address===op.wire_address);
            if(!wire)throw new Error('LIVE_DELETE_UNKNOWN_WIRE');
            targets.set(wire.address,wire);
        } else if(op.op==='delete_junction') {
            const dot=before.objects.find(o=>o.address===op.junction_address&&o.type==='.?AVDOT@@');
            if(!dot||!ptr(dot.address).readPointer().equals(liveIsis.base.add(0x1ce608)))throw new Error('LIVE_DELETE_UNKNOWN_JUNCTION');
            const attached=before.wires.filter(w=>w.endpoints.some(e=>e.owner===dot.address));
            if(JSON.stringify(attached.map(w=>w.address).sort())!==JSON.stringify([...op.connected_wire_addresses].sort()))
                throw new Error('LIVE_DELETE_JUNCTION_WIRE_CONSENT_MISMATCH');
            for(const wire of attached)targets.set(wire.address,wire);
            targets.set(dot.address,dot);
        }
    }
    for(const item of targets.values()) {
        const obj=ptr(item.address);
        if(before.wires.includes(item)) {
            if(!obj.readPointer().equals(liveIsis.base.add(0x1d4008))||
               !obj.readPointer().add(0x48).readPointer().equals(liveIsis.base.add(0x1511f0)))
                throw new Error('LIVE_DELETE_WIRE_PROFILE');
            if(item.endpoints.some(e=>e.owner!=='0x0'&&!componentOwners.has(e.owner)&&
               !before.objects.some(o=>o.address===e.owner&&['.?AVDOT@@','.?AVTERMINAL@@'].includes(o.type))))
                throw new Error('LIVE_DELETE_UNKNOWN_ENDPOINT_OWNER');
        } else if(!obj.readPointer().add(0x48).readPointer().equals(liveIsis.base.add(0xfffd0))) {
            throw new Error('LIVE_DELETE_OBJECT_TAG_PROFILE');
        }
    }
    const update=bind(liveFrame,'?updateUiStates@EditorModule@Appln@@UEAAXXZ','void',['pointer']);
    livePush(c.module);
    let failure=null,completed=[];
    try {
        for(const item of targets.values()) {
            const obj=ptr(item.address);
            new NativeFunction(obj.readPointer().add(0x48).readPointer(),'void',['pointer'])(obj);
            bind(liveFrame,'?addObject@Selection@Appln@@QEAAXPEAVBaseObject@2@@Z','void',['pointer','pointer'])(selection,obj);
        }
        update(c.module);
        const action=uniqueAction(scan().actions.filter(a=>a.class==='Appln::Action' && a.owner_module==='ISIS' && a.command_id===4099));
        if(!action.enabled)throw new Error('LIVE_DELETE_ACTION_DISABLED');
        // The registered delete handler owns its undo transaction. Do not nest
        // it in ISISAUTODRAW begin/finish or queue it past the revision guard.
        bind(widgets,'?activate@QAction@@QEAAXW4ActionEvent@1@@Z','void',['pointer','int'])(ptr(action.address),0);
        completed=operations.map((op,i)=>i);
    } catch(error) {failure={index:null,error:String(error)};}
    finally {
        const now=liveInspect(keys);
        for(const item of [...now.components,...now.objects,...now.wires]) {
            const obj=ptr(item.address);
            if(obj.add(0x40).readU32()&0x800)
                new NativeFunction(obj.readPointer().add(0x50).readPointer(),'void',['pointer'])(obj);
        }
        bind(liveFrame,'?reset@Selection@Appln@@QEAAXXZ','void',['pointer'])(selection);
        update(c.module);livePop(c.module);
    }
    return {before,after:liveInspect(keys),completed,failure,atomic:false};
}
rpc.exports.liveread=(hwnd,keys)=>onUi(hwnd,()=>{
    if(!activeModal().isNull())throw new Error('Modal dialog blocks live read');
    return liveInspect(keys);
});
rpc.exports.livehistory=(hwnd,keys,expected,labels)=>onUi(hwnd,()=>{
    if(!activeModal().isNull())throw new Error('Modal dialog blocks live history');
    // The idle timer can leave redo disabled just after a synchronous undo.
    // Refresh QAction states from the native undo stack before inspecting them.
    bind(liveFrame,'?updateUiStates@EditorModule@Appln@@UEAAXXZ','void',['pointer'])(liveContext().module);
    const state=scan(),stop=state.actions.filter(a=>['Stop','停止'].includes(a.text));
    if(stop.length!==1 || stop[0].enabled)throw new Error('LIVE_EDIT_REQUIRES_STOPPED_SIMULATION');
    if(JSON.stringify(liveInspect(keys))!==expected)throw new Error('LIVE_REVISION_CONFLICT_AT_NATIVE_COMMIT');
    const candidates=state.actions.filter(a=>a.class==='Appln::Action' && a.owner_module==='ISIS' && labels.includes(a.text));
    const action=uniqueAction(candidates);
    if(!action.enabled)return {dispatched:false,reason:'action_disabled',action};
    // Undo/redo cannot open a file chooser. Keep the guard and mutation in the
    // same UI event so a queued user edit cannot become the undo target.
    bind(widgets,'?activate@QAction@@QEAAXW4ActionEvent@1@@Z','void',['pointer','int'])(ptr(action.address),0);
    return {dispatched:true,action,desktop_input:false};
});
rpc.exports.nativeerc=(hwnd,keys,expected)=>onUi(hwnd,()=>{
    if(!activeModal().isNull())throw new Error('Modal dialog blocks native ERC');
    const state=scan(),stop=state.actions.filter(a=>['Stop','\u505c\u6b62'].includes(a.text));
    if(stop.length!==1 || stop[0].enabled)throw new Error('NATIVE_CHECK_REQUIRES_STOPPED_SIMULATION');
    if(JSON.stringify(liveInspect(keys))!==expected)throw new Error('LIVE_REVISION_CONFLICT_AT_NATIVE_CHECK');
    const action=uniqueAction(state.actions.filter(a=>a.class==='Appln::Action' && a.owner_module==='ISIS' && a.command_id===334858));
    if(!action.enabled)return {dispatched:false,reason:'action_disabled',action};
    singleShot(0,ptr(action.address),triggerSlot);
    return {dispatched:true,action,before_reports:state.texts.filter(t=>t.document),desktop_input:false};
});
rpc.exports.liveedit=(hwnd,keys,expected,operations)=>onUi(hwnd,()=>{
    if(!activeModal().isNull())throw new Error('Modal dialog blocks live edit');
    const stop=scan().actions.filter(a=>['Stop','\u505c\u6b62'].includes(a.text));
    if(stop.length!==1||stop[0].enabled)throw new Error('LIVE_EDIT_REQUIRES_STOPPED_SIMULATION');
    const before=liveInspect(keys);
    if(JSON.stringify(before)!==expected)throw new Error('LIVE_REVISION_CONFLICT: native structure changed');
    const c=liveContext(),completed=[];
    if(operations.length && ['delete','delete_wire','delete_junction'].includes(operations[0].op))return liveDelete(c,before,operations,keys);
    let failure=null;
    liveBegin(c.draw);
    try {
        for(let i=0;i<operations.length;i++) {
            const op=operations[i];
            try {
                if(op.op==='add') {
                    const box=Memory.alloc(16);
                    liveCreate(c.draw,box,liveUtf(op.ref),op.value===null?ptr(0):liveUtf(op.value),liveUtf(op.device),liveCts(op.position_nm,op.rotation,op.mirror));
                    const made=livePart(liveInspect(keys),op.ref);
                    if(op.expected_pins && JSON.stringify(made.pins.map(p=>[p.name,p.number]).sort())!==
                       JSON.stringify(op.expected_pins.map(p=>[p.name,p.number]).sort()))throw new Error('NATIVE_LIBRARY_OR_UNIT_PINOUT_MISMATCH');
                } else if(op.op==='transform') {
                    const part=livePart(liveInspect(keys),op.ref),obj=ptr(part.address),delta=Memory.alloc(12);
                    delta.writeByteArray(obj.add(0x48).readByteArray(12));liveInvert(delta);
                    liveCompose(delta,liveCts(op.position_nm,op.rotation,op.mirror));
                    livePush(c.module);
                    try {
                        new NativeFunction(obj.readPointer().add(0xa0).readPointer(),'void',['pointer','pointer','bool'])(obj,delta,0);
                        liveRefreshAttachedGeometry(livePart(liveInspect(keys),op.ref));
                    }
                    finally {livePop(c.module);}
                } else if(op.op==='set') {
                    livePush(c.module);
                    try {for(const [key,value] of Object.entries(op.properties))if(!liveProperty(c.draw,liveUtf(op.ref),ptr(0),liveUtf(key),liveUtf(value),0))throw new Error('Native property rejected '+key);}
                    finally {livePop(c.module);}
                } else if(op.op==='connect') {
                    const now=liveInspect(keys),a=livePart(now,op.from_ref),b=livePart(now,op.to_ref);
                    if(!liveConnect(c.draw,liveUtf(op.from_ref),liveUtf(livePin(a,op.from_pin)),liveUtf(op.to_ref),liveUtf(livePin(b,op.to_pin))))throw new Error('Native connection rejected');
                } else throw new Error('Unregistered live operation');
                completed.push(i);
            } catch(error){failure={index:i,error:String(error)};break;}
        }
    } finally {liveFinish(c.draw);}
    return {before,after:liveInspect(keys),completed,failure,atomic:false};
});
