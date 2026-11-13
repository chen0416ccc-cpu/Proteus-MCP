// Calibrated ARES Electra interchange, dispatched on the application's thread.
const pcbFrame=Process.getModuleByName('AppFrame.dll');
const pcbAres=Process.getModuleByName('ARES.dll');
const pcbPush=bind(pcbFrame,'?pushContext@Module@Appln@@QEAAXXZ','void',['pointer']);
const pcbPop=bind(pcbFrame,'?popContext@Module@Appln@@QEAAXXZ','void',['pointer']);
function pcbContext() {
    if(activeModal().isNull()===false)throw new Error('PCB_MODAL_DIALOG');
    const state=scan(),stop=state.actions.filter(a=>['Stop','停止'].includes(a.text));
    if(stop.length!==1 || stop[0].enabled)throw new Error('PCB_REQUIRES_STOPPED_SIMULATION');
    const q=Memory.alloc(8),text=Memory.allocUtf8String('ARES');
    q.writePointer(bind(core,'?fromAscii_helper@QString@@CAPEAU?$QTypedArrayData@G@@PEBDH@Z','pointer',['pointer','int'])(text,4));
    let intf;
    try {intf=bind(pcbFrame,'?getModuleInterface@Module@Appln@@SAPEAVIModule@2@AEBVQString@@I@Z','pointer',['pointer','uint'])(q,0x29);}
    finally {qStringDestroy(q);}
    if(intf.isNull())throw new Error('No ARES auto draw interface');
    const locator=intf.readPointer().sub(8).readPointer();
    if(locator.compare(pcbAres.base)<0 || locator.compare(pcbAres.base.add(pcbAres.size))>=0)throw new Error('ARES RTTI outside profile');
    const draw=intf.sub(locator.add(4).readU32());
    if(!draw.readPointer().equals(pcbAres.base.add(0x3523f8)))throw new Error('Uncalibrated ARES interface');
    const module=draw.add(0x38).readPointer(),mi=module.add(0x10);
    const document=new NativeFunction(mi.readPointer().add(8).readPointer(),'pointer',['pointer'])(mi);
    if(document.isNull())throw new Error('No ARES document');
    const file=bind(pcbFrame,'?nativePathName@Document@Appln@@QEAAPEBDXZ','pointer',['pointer'])(document);
    return {draw,module,design:draw.add(0x40).readPointer(),document,path:file.isNull()?null:file.readUtf8String()};
}
function pcbConfig(c) {
    let config=c.module.add(0xe48).readPointer();
    if(config.isNull()) {
        const memory=new NativeFunction(pcbAres.base.add(0x30a4fc),'pointer',['size_t'])(0x1930);
        if(memory.isNull())throw new Error('ARES configuration allocation failed');
        config=new NativeFunction(pcbAres.base.add(0x124fd0),'pointer',['pointer','pointer','pointer'])(memory,c.module,c.module.add(0x130).readPointer());
        c.module.add(0xe48).writePointer(config);
    }
    return config;
}
function pcbExport(c,path) {
    const config=pcbConfig(c);
    const encoded=Memory.allocUtf8String(path);
    let length=0;while(encoded.add(length).readU8()!==0 && length<=240)length++;
    if(length>240)throw new Error('PCB exchange path exceeds 240 UTF8 bytes');
    const options=config.add(0x18),old=options.readByteArray(260);
    try {
        options.writeByteArray(encoded.readByteArray(length+1));
        // Second argument is the export-options STRUCT (path first), not char*.
        return new NativeFunction(pcbAres.base.add(0x126f30),'bool',['pointer','pointer','pointer','pointer'])(c.module,options,config.add(0x448),config.add(0x474));
    }finally {options.writeByteArray(old);}
}
function pcbCString(pointer,limit) {
    let length=0;while(length<limit && pointer.add(length).readU8()!==0)length++;
    if(length===limit)throw new Error('PCB_METADATA_STRING_BOUND');
    return pointer.readUtf8String(length);
}
function pcbStackMetadata(c) {
    // Export builds a stable PS index -> native PAD_STYLE/span table. Read
    // drill and mask data omitted by EDF; never infer a hole from copper size.
    const index=pcbConfig(c).add(0x448),count=index.add(0x10).readS32(),data=index.add(0x14).readPointer();
    if(count<0 || count>10000 || (count && data.isNull()))throw new Error('PCB_PADSTACK_INDEX_BOUND');
    const result={},types=['CPAD','SPAD','DPAD','ECPAD','CSMPAD','RSMPAD','PSMPAD','STACK','CVIA','SVIA'];
    for(let i=0;i<count;i++) {
        const item=data.add(i*8).readPointer(),name=pcbCString(item,128);
        const key=name.startsWith('"$VIA')?name.slice(1,-1):'PS'+i;
        const style=item.add(0x80).readPointer(),kind=style.add(0x48).readS32(),table=style.readPointer();
        const meta={native_style_name:pcbCString(style.add(8),64),
            native_type:types[kind]||'unresolved',span_code:item.add(0x88).readU64().toString(16),calibrated:false};
        if((table.equals(pcbAres.base.add(0x35fbb0)) && [0,8].includes(kind)) ||
           (table.equals(pcbAres.base.add(0x35fd08)) && [1,9].includes(kind))) {
            // ARES dimensions are 10 nm integers, unlike ISIS nm integers.
            Object.assign(meta,{calibrated:true,width_nm:10*style.add(0x50).readS32(),height_nm:10*style.add(0x54).readS32(),
                drill_nm:10*style.add(0x60).readS32(),drill_mark_nm:10*style.add(0x5c).readS32(),
                guard_nm:10*style.add(0x64).readS32(),secondary_dimension_nm:10*style.add(0x58).readS32(),
                flags:style.add(0x6c).readU32(),aux_flags:style.add(0x68).readU32()});
        }
        if(Object.prototype.hasOwnProperty.call(result,key))throw new Error('PCB_PADSTACK_INDEX_DUPLICATE');
        result[key]=meta;
    }
    return result;
}
function pcbGuard(c,path,expectedExport,expectedStacks) {
    if(!pcbExport(c,path))throw new Error('PCB_GUARD_EXPORT_FAILED');
    const actual=File.readAllText(path).replace(/\r\n/g,'\n'),expected=expectedExport.replace(/\r\n/g,'\n');
    if(actual!==expected) {
        let at=0;while(at<Math.min(actual.length,expected.length)&&actual[at]===expected[at])at++;
        throw new Error('PCB_REVISION_CONFLICT_AT_NATIVE_COMMIT: '+JSON.stringify({at,actual:actual.slice(at,at+120),expected:expected.slice(at,at+120)}));
    }
    if(expectedStacks!==undefined && expectedStacks!==null && JSON.stringify(pcbStackMetadata(c))!==expectedStacks)
        throw new Error('PCB_PADSTACK_REVISION_CONFLICT_AT_NATIVE_COMMIT');
}
function pcbDrcRows(viewer) {
    const table=viewer.add(0x48).readPointer();
    if(!is(table,'QTableWidget') || !table.readPointer().equals(pcbAres.base.add(0x33fc88)))throw new Error('PCB_DRC_TABLE_PROFILE');
    const total=bind(widgets,'?rowCount@QTableWidget@@QEBAHXZ','int',['pointer'])(table);
    const columns=bind(widgets,'?columnCount@QTableWidget@@QEBAHXZ','int',['pointer'])(table);
    if(total<0 || total>1000000 || columns!==5)throw new Error('PCB_DRC_TABLE_BOUND');
    const getItem=bind(widgets,'?item@QTableWidget@@QEBAPEAVQTableWidgetItem@@HH@Z','pointer',['pointer','int','int']);
    const itemText=bind(widgets,'?text@QTableWidgetItem@@QEBA?AVQString@@XZ','pointer',['pointer','pointer']);
    const type=bind(core,'?userType@QVariant@@QEBAHXZ','int',['pointer']);
    const constData=bind(core,'?constData@QVariant@@QEBAPEBXXZ','pointer',['pointer']);
    const rows=[];
    for(let r=0;r<Math.min(total,5000);r++) {
        const values=[];
        for(let col=0;col<5;col++) {
            const item=getItem(table,r,col);
            if(item.isNull())throw new Error('PCB_DRC_MISSING_CELL');
            values.push(string(itemText,item));
        }
        const item=getItem(table,r,0),value=Memory.alloc(16);
        new NativeFunction(item.readPointer().add(0x10).readPointer(),'pointer',['pointer','pointer','int'])(item,value,0x100);
        try {
            if(type(value)!==31)throw new Error('PCB_DRC_FLAG_NOT_VOID_POINTER');
            const flag=constData(value).readPointer();
            if(flag.isNull() || !flag.readPointer().equals(pcbAres.base.add(0x3a1fa8)))throw new Error('PCB_DRC_FLAG_PROFILE');
            rows.push({rule:values[0],violation_type:values[1],layers:values[2],
                specified_clearance:values[3],actual_clearance:values[4],ignored:!!flag.add(0xb0).readU8()});
        }finally{variantDestroy(value);}
    }
    const active=new NativeFunction(pcbAres.base.add(0x28890),'int',['pointer'])(viewer);
    return {rows,total,active,ignored:total-active,table_truncated:rows.length!==total,
        native_checker_limit:5000,native_checker_limit_reached:total>=5000};
}
rpc.exports.pcbdrc=(hwnd,expectedPath,guardPath,expectedExport,expectedStacks)=>onUi(hwnd,()=>{
    const c=pcbContext();
    if(c.path!==expectedPath)throw new Error('PCB_DOCUMENT_CONFLICT');
    const viewer=c.module.add(0x548).readPointer();
    if(viewer.isNull() || cls(viewer)!=='DrcViewer' || !viewer.readPointer().equals(pcbAres.base.add(0x340628)))
        throw new Error('PCB_DRC_VIEWER_PROFILE');
    pcbPush(c.module);
    try {
        pcbGuard(c,guardPath,expectedExport,expectedStacks);
        const designBounds=Array.from({length:4},(_,i)=>c.design.add(0x24+i*4).readS32());
        const moduleBounds=Array.from({length:4},(_,i)=>c.module.add(0x574+i*4).readS32());
        // Equal full-design boxes take the checker's whole-design branch.
        // A mismatched viewport/update region is not a global check.
        if(!designBounds.every((n,i)=>n===moduleBounds[i]))throw new Error('PCB_DRC_FULL_DESIGN_BOUNDARY_NOT_CALIBRATED');
        const disabled=!!pcbAres.base.add(0x463184).readU8();
        if(!disabled)new NativeFunction(pcbAres.base.add(0x1136d0),'void',
            ['pointer','pointer','pointer','bool'])(c.module,c.design.add(0x24),c.design.add(0x24),0);
        return {...pcbDrcRows(viewer),refreshed:!disabled,global_disabled:disabled,
            real_time_enabled:!!c.design.add(0x1cc).readU8(),whole_design_bounds:designBounds,
            document:c.document.toString(),module:c.module.toString(),path:c.path,desktop_input:false};
    }finally{pcbPop(c.module);}
});
rpc.exports.pcbexport=(hwnd,path,expectedPath)=>onUi(hwnd,()=>{
    const c=pcbContext();
    if(c.path!==expectedPath)throw new Error('PCB_DOCUMENT_CONFLICT');
    pcbPush(c.module);
    try {
        const ok=pcbExport(c,path);
        return {ok,path:c.path,module:c.module.toString(),document:c.document.toString(),design:c.design.toString(),
            padstacks:ok?pcbStackMetadata(c):null};
    }finally {pcbPop(c.module);}
});
rpc.exports.pcbimport=(hwnd,path,expectedPath,guardPath,expectedExport,expectedStacks)=>onUi(hwnd,()=>{
    const c=pcbContext();
    if(c.path!==expectedPath)throw new Error('PCB_DOCUMENT_CONFLICT');
    const encoded=Memory.allocUtf8String(path);
    let length=0;while(encoded.add(length).readU8()!==0 && length<=240)length++;
    if(length>240)throw new Error('PCB exchange path exceeds 240 UTF8 bytes');
    const config=pcbConfig(c),old=config.add(0x11c).readByteArray(260);
    pcbPush(c.module);
    try {
        // Re-export and compare inside this same event callback so planning
        // cannot race an interactive edit before the actual native mutation.
        pcbGuard(c,guardPath,expectedExport,expectedStacks);
        config.add(0x11c).writeByteArray(encoded.readByteArray(length+1));
        new NativeFunction(c.module.readPointer().add(0x2c0).readPointer(),'void',['pointer','int'])(c.module,2);
        const ok=new NativeFunction(pcbAres.base.add(0x128760),'bool',['pointer','pointer'])(c.module,config.add(0x18));
        new NativeFunction(c.module.readPointer().add(0x298).readPointer(),'void',['pointer','int'])(c.module,1);
        return {ok,document:c.document.toString(),module:c.module.toString(),
            desktop_input:false};
    }finally {
        config.add(0x11c).writeByteArray(old);pcbPop(c.module);
    }
});
