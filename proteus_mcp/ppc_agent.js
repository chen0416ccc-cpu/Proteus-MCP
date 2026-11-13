// Native pre-production suite, kept attached until its modal run is finished.
let ppcRun=null;
const ppcChecked=bind(widgets,'?isChecked@QAbstractButton@@QEBA_NXZ','bool',['pointer']);
const ppcSetChecked=bind(widgets,'?setChecked@QAbstractButton@@QEAAX_N@Z','void',['pointer','bool']);
const ppcButtonText=bind(widgets,'?text@QAbstractButton@@QEBA?AVQString@@XZ','pointer',['pointer','pointer']);
function ppcDialog() {
    if(!ppcRun)throw new Error('PPC_RUN_NOT_STARTED');
    const dialog=activeModal();
    if(dialog.isNull() || cls(dialog)!=='PPCDialog' || !dialog.readPointer().equals(pcbAres.base.add(0x35ce30)))
        throw new Error('PPC_DIALOG_PROFILE');
    const owner=dialog.add(0x158).readPointer();
    if(owner.isNull() || !owner.readPointer().equals(pcbAres.base.add(0x35aad8)) ||
       !owner.add(0x28).readPointer().equals(ppcRun.context.module))throw new Error('PPC_DOCUMENT_OWNER_CONFLICT');
    if(ppcRun.dialog && !ppcRun.dialog.equals(dialog))throw new Error('PPC_DIALOG_IDENTITY_CHANGED');
    const intf=ppcRun.context.module.add(0x10);
    const document=new NativeFunction(intf.readPointer().add(8).readPointer(),'pointer',['pointer'])(intf);
    if(!document.equals(ppcRun.context.document))throw new Error('PPC_DOCUMENT_CONFLICT');
    return dialog;
}
function ppcControls(dialog) {
    const queue=[dialog],seen=new Set(),checks=[],logs=[],labels=[];
    while(queue.length) {
        const obj=queue.shift(),key=obj.toString();
        if(obj.isNull() || seen.has(key))continue;
        if(seen.size>=1000)throw new Error('PPC_CONTROL_BOUND');
        seen.add(key);queue.push(...listItems(children(obj)));
        if(is(obj,'QCheckBox'))checks.push({address:key,label:string(ppcButtonText,obj),checked:!!ppcChecked(obj)});
        if(is(obj,'QTextBrowser') && string(objectName,obj)==='ppcDisplay') {
            const doc=bind(widgets,'?document@QTextEdit@@QEBAPEAVQTextDocument@@XZ','pointer',['pointer'])(obj);
            const length=bind(gui,'?characterCount@QTextDocument@@QEBAHXZ','int',['pointer'])(doc)-1;
            logs.push({text:string(richText,obj),character_count:length,truncated:length>64000,
                document:doc.toString(),change_count:reportChanges.get(doc.toString())||0});
        }
        if(is(obj,'QLabel') && string(objectName,obj)==='resultSummaryLabel')labels.push(string(labelText,obj));
    }
    if(checks.length!==23 || new Set(checks.map(c=>c.label)).size!==23 || logs.length!==1 || labels.length!==1)
        throw new Error('PPC_CONTROL_PROFILE');
    return {checks,log:logs[0],summary:labels[0]};
}
rpc.exports.ppcbegin=(hwnd,expectedPath,guardPath,expectedExport,expectedStacks)=>onUi(hwnd,()=>{
    if(ppcRun)throw new Error('PPC_RUN_ALREADY_STARTED');
    const c=pcbContext();
    if(c.path!==expectedPath)throw new Error('PCB_DOCUMENT_CONFLICT');
    pcbPush(c.module);
    try {pcbGuard(c,guardPath,expectedExport,expectedStacks);}finally {pcbPop(c.module);}
    const action=uniqueAction(scan().actions.filter(a=>a.class==='Appln::Action' && a.owner_module==='ARES' && a.command_id===399365));
    if(!action.enabled)throw new Error('PPC_ACTION_DISABLED');
    ppcRun={context:c,guardPath,expectedExport,expectedStacks,dialog:null,original:null,started:false};
    singleShot(0,ptr(action.address),triggerSlot);
    return {dispatched:true,action,document:c.document.toString(),desktop_input:false};
});
rpc.exports.ppcstart=hwnd=>onUi(hwnd,()=>{
    const dialog=ppcDialog();
    if(ppcRun.started)throw new Error('PPC_RUN_ALREADY_STARTED');
    ppcRun.dialog=dialog;
    const c=ppcRun.context;
    pcbPush(c.module);
    try {pcbGuard(c,ppcRun.guardPath,ppcRun.expectedExport,ppcRun.expectedStacks);}finally {pcbPop(c.module);}
    ppcRun.original=ppcControls(dialog);
    // Enable all 23 checks, but restore the user's original choices on exit.
    for(const check of ppcRun.original.checks)ppcSetChecked(ptr(check.address),1);
    const selected=ppcControls(dialog);
    if(selected.checks.some(c=>!c.checked))throw new Error('PPC_CHECK_SELECTION_FAILED');
    ppcRun.started=true;
    singleShot(0,dialog,Memory.allocUtf8String('1startChecks()'));
    return {started:true,original:ppcRun.original,selected,desktop_input:false};
});
rpc.exports.ppcread=hwnd=>onUi(hwnd,()=>{
    const dialog=ppcDialog(),state=ppcControls(dialog);
    return {...state,dialog:dialog.toString(),started:ppcRun.started,desktop_input:false};
});
rpc.exports.ppcfinish=hwnd=>onUi(hwnd,()=>{
    const dialog=ppcDialog(),before=ppcControls(dialog);
    if(ppcRun.original)for(const check of ppcRun.original.checks)ppcSetChecked(ptr(check.address),Number(check.checked));
    const after=ppcControls(dialog);
    const restored=!ppcRun.original || JSON.stringify(after.checks)===JSON.stringify(ppcRun.original.checks);
    // The vendor close callback safely handles an in-progress cancellation.
    singleShot(0,dialog,Memory.allocUtf8String('1closeButtonCallback()'));
    return {close_queued:true,configuration_restored:restored,before,after,desktop_input:false};
});
