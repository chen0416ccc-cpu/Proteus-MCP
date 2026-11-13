// Experimental adapter for the calibrated x64 Qt 5.15 build of Proteus 9.
// Work runs on Qt's own event loop. No mouse, keyboard or screen API is used.
const core=Process.getModuleByName('Qt5Core.dll');
const widgets=Process.getModuleByName('Qt5Widgets.dll');
const gui=Process.getModuleByName('Qt5Gui.dll');
const commandFrame=Process.getModuleByName('AppFrame.dll');
function bind(mod,name,ret,args) { return new NativeFunction(mod.getExportByName(name),ret,args); }
const find=bind(widgets,'?find@QWidget@@SAPEAV1@_K@Z','pointer',['uint64']);
const children=bind(core,'?children@QObject@@QEBAAEBV?$QList@PEAVQObject@@@@XZ','pointer',['pointer']);
const objectName=bind(core,'?objectName@QObject@@QEBA?AVQString@@XZ','pointer',['pointer','pointer']);
const className=bind(core,'?className@QMetaObject@@QEBAPEBDXZ','pointer',['pointer']);
const inherits=bind(core,'?inherits@QObject@@QEBA_NPEBD@Z','bool',['pointer','pointer']);
const actionText=bind(widgets,'?text@QAction@@QEBA?AVQString@@XZ','pointer',['pointer','pointer']);
const actionEnabled=bind(widgets,'?isEnabled@QAction@@QEBA_NXZ','bool',['pointer']);
const actionChecked=bind(widgets,'?isChecked@QAction@@QEBA_NXZ','bool',['pointer']);
const labelText=bind(widgets,'?text@QLabel@@QEBA?AVQString@@XZ','pointer',['pointer','pointer']);
const plainText=bind(widgets,'?toPlainText@QPlainTextEdit@@QEBA?AVQString@@XZ','pointer',['pointer','pointer']);
const plainDocument=bind(widgets,'?document@QPlainTextEdit@@QEBAPEAVQTextDocument@@XZ','pointer',['pointer']);
const richDocument=bind(widgets,'?document@QTextEdit@@QEBAPEAVQTextDocument@@XZ','pointer',['pointer']);
const documentCharacters=bind(gui,'?characterCount@QTextDocument@@QEBAHXZ','int',['pointer']);
const reportChanges=new Map();
Interceptor.attach(gui.getExportByName('?contentsChanged@QTextDocument@@QEAAXXZ'),{
    onEnter(args) {const key=args[0].toString();reportChanges.set(key,(reportChanges.get(key)||0)+1);}
});
const richText=bind(widgets,'?toPlainText@QTextEdit@@QEBA?AVQString@@XZ','pointer',['pointer','pointer']);
const windowTitle=bind(widgets,'?windowTitle@QWidget@@QEBA?AVQString@@XZ','pointer',['pointer','pointer']);
const widgetActions=bind(widgets,'?actions@QWidget@@QEBA?AV?$QList@PEAVQAction@@@@XZ','pointer',['pointer','pointer']);
const allWidgets=bind(widgets,'?allWidgets@QApplication@@SA?AV?$QList@PEAVQWidget@@@@XZ','pointer',['pointer']);
const activeModal=bind(widgets,'?activeModalWidget@QApplication@@SAPEAVQWidget@@XZ','pointer',[]);
const messageText=bind(widgets,'?text@QMessageBox@@QEBA?AVQString@@XZ','pointer',['pointer','pointer']);
const messageInfo=bind(widgets,'?informativeText@QMessageBox@@QEBA?AVQString@@XZ','pointer',['pointer','pointer']);
const messageButtons=bind(widgets,'?standardButtons@QMessageBox@@QEBA?AV?$QFlags@W4StandardButton@QMessageBox@@@@XZ','pointer',['pointer','pointer']);
const messageButton=bind(widgets,'?button@QMessageBox@@QEBAPEAVQAbstractButton@@W4StandardButton@1@@Z','pointer',['pointer','int']);
const buttonClick=bind(widgets,'?click@QAbstractButton@@QEAAXXZ','void',['pointer']);
const treeCount=bind(widgets,'?topLevelItemCount@QTreeWidget@@QEBAHXZ','int',['pointer']);
const treeItem=bind(widgets,'?topLevelItem@QTreeWidget@@QEBAPEAVQTreeWidgetItem@@H@Z','pointer',['pointer','int']);
const treeColumns=bind(widgets,'?columnCount@QTreeWidget@@QEBAHXZ','int',['pointer']);
const treeText=bind(widgets,'?text@QTreeWidgetItem@@QEBA?AVQString@@H@Z','pointer',['pointer','pointer','int']);
const viewModel=bind(widgets,'?model@QAbstractItemView@@QEBAPEAVQAbstractItemModel@@XZ','pointer',['pointer']);
const indexData=bind(core,'?data@QModelIndex@@QEBA?AVQVariant@@H@Z','pointer',['pointer','pointer','int']);
const variantString=bind(core,'?toString@QVariant@@QEBA?AVQString@@XZ','pointer',['pointer','pointer']);
const variantDestroy=bind(core,'??1QVariant@@QEAA@XZ','void',['pointer']);
const visible=bind(widgets,'?isVisible@QWidget@@QEBA_NXZ','bool',['pointer']);
const qStringDestroy=bind(core,'??1QString@@QEAA@XZ','void',['pointer']);
const listDispose=bind(core,'?dispose@QListData@@SAXPEAUData@1@@Z','void',['pointer']);
const allocate=bind(Process.getModuleByName('ucrtbase.dll'),'malloc','pointer',['size_t']);
const eventCtor=bind(core,'??0QEvent@@QEAA@W4Type@0@@Z','pointer',['pointer','int']);
const eventType=bind(core,'?registerEventType@QEvent@@SAHH@Z','int',['int'])(-1);
const postEvent=bind(core,'?postEvent@QCoreApplication@@SAXPEAVQObject@@PEAVQEvent@@H@Z','void',['pointer','pointer','int']);
const singleShot=bind(core,'?singleShot@QTimer@@SAXHPEBVQObject@@PEBD@Z','void',['int','pointer','pointer']);
const triggerSlot=Memory.allocUtf8String('1trigger()');
const pending=new Map();
const typeNames=new Map();
let root=ptr(0);

// Release implicitly shared QList copies with the same atomic decrement as Qt.
const decrementCode=Memory.alloc(Process.pageSize);
Memory.patchCode(decrementCode,10,p=>p.writeByteArray([0xb8,0xff,0xff,0xff,0xff,0xf0,0x0f,0xc1,0x01,0xc3]));
const decrement=new NativeFunction(decrementCode,'int',['pointer']);
const test=Memory.alloc(4); test.writeS32(3);
if (decrement(test)!==3 || test.readS32()!==2) throw new Error('Atomic helper validation failed');

function is(obj,name) {
    if (!typeNames.has(name)) typeNames.set(name,Memory.allocUtf8String(name));
    return inherits(obj,typeNames.get(name));
}
function string(fun,obj,...args) {
    const out=Memory.alloc(8); fun(obj,out,...args);
    const d=out.readPointer(), length=d.add(4).readS32();
    if (length<0 || length>1000000) throw new Error('Invalid QString bounds');
    // QString may contain embedded NULs in translated/rich native messages.
    const units=new Uint16Array(d.add(d.add(16).readS64().toNumber()).readByteArray(Math.min(length,64000)*2));
    let value='';
    for(let i=0;i<units.length;i+=4096)value+=String.fromCharCode(...units.slice(i,i+4096));
    qStringDestroy(out); return value;
}
function listItems(list) {
    const d=list.readPointer(), begin=d.add(8).readS32(), end=d.add(12).readS32();
    if (begin<0 || end<begin || end-begin>20000) throw new Error('Invalid QList bounds');
    const out=[];
    for (let i=begin;i<end;i++) out.push(d.add(16+8*i).readPointer());
    return out;
}
function releaseList(list) {
    const d=list.readPointer();
    if (d.readS32()>0 && decrement(d)===1) listDispose(d);
}
function cls(obj) {
    const meta=new NativeFunction(obj.readPointer().readPointer(),'pointer',['pointer'])(obj);
    if (meta.isNull()) throw new Error('Missing Qt metaobject');
    return className(meta).readUtf8String();
}
function commandIdentity(obj) {
    const table=bind(commandFrame,'?getTable@Action@Appln@@QEBAPEAVCmdInfoTable@2@XZ','pointer',['pointer'])(obj);
    const owner=table.isNull()?ptr(0):bind(commandFrame,'?getOwner@CmdInfoTable@Appln@@QEBAPEAVModule@2@XZ','pointer',['pointer'])(table);
    const name=owner.isNull()?null:bind(commandFrame,'?getModuleCName@Module@Appln@@QEBAPEBDXZ','pointer',['pointer'])(owner).readUtf8String();
    return {command_id:bind(commandFrame,'?getID@Action@Appln@@QEBAIXZ','uint',['pointer'])(obj),owner_module:name};
}
function uniqueAction(actions) {
    const groups=new Map();
    for(const action of actions) {
        const key=action.class==='Appln::Action'?action.owner_module+':'+action.command_id:action.address;
        if(!groups.has(key))groups.set(key,[]);
        groups.get(key).push(action);
    }
    if(groups.size!==1)throw new Error('Expected one native command identity, got '+groups.size);
    const group=[...groups.values()][0];
    if(group.some(a=>a.enabled!==group[0].enabled || a.checked!==group[0].checked))throw new Error('Duplicate command state disagreement');
    return {...group[0],instances:group.length};
}
function modelRows(view) {
    const model=viewModel(view);
    if (model.isNull() || !is(model,'QAbstractItemModel')) return [];
    const parent=Memory.alloc(24);parent.writeS32(-1);parent.add(4).writeS32(-1);
    parent.add(8).writePointer(ptr(0));parent.add(16).writePointer(ptr(0));
    // Calibrated in Qt5Core: hasIndex calls vtable+0x70/+0x78;
    // QModelIndex::child calls +0x58 to obtain the concrete model's index.
    const table=model.readPointer();
    const rowCount=new NativeFunction(table.add(0x70).readPointer(),'int',['pointer','pointer']);
    const columnCount=new NativeFunction(table.add(0x78).readPointer(),'int',['pointer','pointer']);
    const index=new NativeFunction(table.add(0x58).readPointer(),'pointer',['pointer','pointer','int','int','pointer']);
    const rows=rowCount(model,parent), columns=columnCount(model,parent), result=[];
    if (rows<0 || rows>1000000 || columns<0 || columns>1000) throw new Error('Invalid native log model size');
    for (let r=Math.max(0,rows-500);r<rows;r++) {
        const row=[];
        for (let c=0;c<Math.min(columns,12);c++) {
            const item=Memory.alloc(24),value=Memory.alloc(16);
            index(model,item,r,c,parent);indexData(item,value,0);
            row.push(string(variantString,value));variantDestroy(value);
        }
        result.push(row);
    }
    return result;
}
function scan() {
    const app=core.getExportByName('?self@QCoreApplication@@0PEAV1@EA').readPointer();
    const queue=[root,app], seen=new Set(), result={actions:[],texts:[],views:[],objects:0,modal:null};
    const list=Memory.alloc(8); allWidgets(list);
    queue.push(...listItems(list)); releaseList(list);
    while (queue.length && result.objects<10000) {
        const obj=queue.shift(), address=obj.toString();
        if (obj.isNull() || seen.has(address)) continue;
        seen.add(address); result.objects++;
        const type=cls(obj), name=string(objectName,obj);
        queue.push(...listItems(children(obj)));
        if (is(obj,'QAction')) result.actions.push({address,class:type,name,text:string(actionText,obj),enabled:!!actionEnabled(obj),checked:!!actionChecked(obj),
            ...(type==='Appln::Action'?commandIdentity(obj):{})});
        else if (is(obj,'QWidget')) {
            const shown=!!visible(obj);
            if (is(obj,'QAbstractItemView') || /Log|Console|Message/.test(type)) result.views.push({address,class:type,name,visible:shown,itemView:!!is(obj,'QAbstractItemView'),plainText:!!is(obj,'QPlainTextEdit')});
            let value='';
            if (is(obj,'QLabel')) value=string(labelText,obj);
            else if (is(obj,'QPlainTextEdit')) value=string(plainText,obj);
            else if (is(obj,'QTextEdit')) value=string(richText,obj);
            if (value || is(obj,'QPlainTextEdit')) {
                const document=is(obj,'QPlainTextEdit')?plainDocument(obj):is(obj,'QTextEdit')?richDocument(obj):ptr(0);
                const length=document.isNull()?null:documentCharacters(document)-1;
                result.texts.push({address,class:type,name,text:value,visible:shown,
                    ...(!document.isNull()?{document:document.toString(),change_count:reportChanges.get(document.toString())||0,
                        character_count:length,truncated:length>64000}:{})});
            }
            if (is(obj,'QTreeWidget')) {
                const rows=[];
                for (let r=0;r<Math.min(treeCount(obj),500);r++) {
                    const row=[],item=treeItem(obj,r);
                    for (let c=0;c<Math.min(treeColumns(obj),12);c++) row.push(string(treeText,item,c));
                    rows.push(row);
                }
                if (rows.length) result.texts.push({class:type,name,rows,visible:shown});
            }
            if (is(obj,'QAbstractItemView') && name==='logPopupView') {
                const rows=modelRows(obj);
                if (rows.length) result.texts.push({class:type,name,rows,visible:shown});
            }
            if (type==='QMenu' || type==='QMenuBar' || type.endsWith('ToolBar')) {
                widgetActions(obj,list); queue.push(...listItems(list)); releaseList(list);
            }
        }
    }
    result.traversal_truncated=queue.some(o=>!o.isNull() && !seen.has(o.toString()));
    result.modal=modalInfo();
    result.modules=Process.enumerateModules().map(m=>m.name);
    return result;
}

function modalInfo(modal=activeModal()) {
    if(modal.isNull())return null;
    const info={class:cls(modal),title:string(windowTitle,modal)};
    if(is(modal,'QMessageBox')) {
        const flags=Memory.alloc(4);messageButtons(modal,flags);
        Object.assign(info,{text:string(messageText,modal),
            informative_text:string(messageInfo,modal),standard_buttons:flags.readU32()});
    }
    return info;
}
function knownDrcNotice(modal) {
    const info=modalInfo(modal);
    const expected='One or more design rule (DRC) errors have been detected.\n\nTo see a list of the errors, click left on the DRC status box at the bottom of the screen, near to the status bar.';
    if(!info || info.class!=='Appln::MessageBox' || !['PCB Layout','PCB 布局'].includes(info.title) ||
       info.text.replace(/\r\n/g,'\n').replace(/\n$/,'')!==expected || info.informative_text || info.standard_buttons!==0x400)return null;
    const button=messageButton(modal,0x400);
    if(button.isNull())throw new Error('DRC_NOTICE_HAS_NO_OK_BUTTON');
    return {info,button};
}
function acknowledgeDrcNotice() {
    const notice=knownDrcNotice(activeModal());
    if(!notice)return null;
    const {info,button}=notice;
    buttonClick(button);
    return {acknowledged:true,kind:'native_drc_errors_detected',observed:info,desktop_input:false};
}

const notify=widgets.getExportByName('?notify@QApplication@@UEAA_NPEAVQObject@@PEAVQEvent@@@Z');
Interceptor.attach(notify,{
    onEnter(args) {
        const key=args[2].toString(), task=pending.get(key);
        if (!task) return;
        pending.delete(key); clearTimeout(task.timer);
        try { task.resolve(task.fn()); } catch(e) { task.reject(e); }
    }
});
function onUi(hwnd,fn) {
    root=find(uint64(hwnd));
    if (root.isNull()) throw new Error('Tracked HWND has no Qt widget');
    return new Promise((resolve,reject)=>{
        const event=allocate(64); eventCtor(event,eventType);
        const key=event.toString();
        const timer=setTimeout(()=>{pending.delete(key);reject(new Error('Qt event loop timeout'));},6000);
        pending.set(key,{fn,resolve,reject,timer});
        postEvent(root,event,0);
    });
}

rpc.exports={
    snapshot(hwnd) { return onUi(hwnd,scan); },
    modalread(hwnd) { return onUi(hwnd,modalInfo); },
    acknowledgedrcnotice(hwnd) {return onUi(hwnd,acknowledgeDrcNotice);},
    cancelcloseforsave(hwnd,expected) {
        return onUi(hwnd,()=>{
            const current=modalInfo();
            if(!current || JSON.stringify(current)!==JSON.stringify(expected))throw new Error('MODAL_CHANGED');
            if(current.class!=='Appln::MessageBox' || !['Schematic Capture','原理图绘制','PCB Layout','PCB 布局'].includes(current.title) ||
               !['是否保存对当前工程的更改？','Do you wish to save changes to the current project?'].includes(current.text) ||
               current.informative_text || current.standard_buttons!==0x414000)throw new Error('NOT_A_PROJECT_SAVE_ON_CLOSE_PROMPT');
            const button=messageButton(activeModal(),0x400000);
            if(button.isNull())throw new Error('NO_STANDARD_CANCEL_BUTTON');
            buttonClick(button);
            return {cancelled:true,kind:'cancel_close_before_explicit_save',observed:current,desktop_input:false};
        });
    },
    cancelpreproduction(hwnd) {
        return onUi(hwnd,()=>{
            const current=modalInfo();
            if(!current || current.class!=='PPCDialog' || !['Pre-production Check','预生产检查'].includes(current.title))throw new Error('NOT_A_PREPRODUCTION_DIALOG');
            singleShot(0,activeModal(),Memory.allocUtf8String('1reject()'));
            return {cancelled:true,observed:current,desktop_input:false};
        });
    },
    confirmupgrade(hwnd,expected) {
        return onUi(hwnd,()=>{
            const current=modalInfo();
            if(!current || JSON.stringify(current)!==JSON.stringify(expected))throw new Error('MODAL_CHANGED');
            const body=(current.text+'\n'+current.informative_text).replace(/\0/g,'');
            if(current.class!=='Appln::MessageBox' || current.title!=='Proteus 9 Professional' ||
               current.standard_buttons!==0x400400 || !body.includes('9.0') ||
               !body.includes('[Proteus ') || !body.includes('.pdsprj'))throw new Error('NOT_A_FORMAT_UPGRADE_CONFIRMATION');
            const button=messageButton(activeModal(),0x400);
            if(button.isNull())throw new Error('NO_STANDARD_OK_BUTTON');
            buttonClick(button);
            return {confirmed:true,kind:'project_format_upgrade',observed:current,desktop_input:false};
        });
    },
    command(hwnd,labels,requiredClass,ownerModule) {
        return onUi(hwnd,()=>{
            const state=scan();
            if (state.modal) throw new Error('Proteus is showing a modal dialog: '+state.modal.title);
            const matches=state.actions.filter(a=>labels.includes(a.text) && (!requiredClass || a.class===requiredClass) && (!ownerModule || a.owner_module===ownerModule));
            const action=uniqueAction(matches);
            if (!action.enabled) return {dispatched:false,reason:'action_disabled',action};
            // Queue the native slot, not a JS callback around a synchronous modal call.
            singleShot(0,ptr(action.address),triggerSlot);
            return {dispatched:true,queued:true,action,transport:'Qt QTimer queued QAction::trigger slot'};
        });
    },
    nets(names) {
        const mod=Process.findModuleByName('DSIM.dll');
        if (!mod) throw new Error('Digital simulator is not loaded');
        const link=bind(mod,'?dsim_linknet@@YA_NPEADPEAPEAW4STATE@@@Z','bool',['pointer','pointer']);
        const signals=names.map(name=>{
            const out=Memory.alloc(8);out.writePointer(ptr(0));
            const ok=link(Memory.allocUtf8String(name),out);
            return {name,found:!!ok,address:ok?out.readPointer():ptr(0)};
        });
        // Resolve every signal first. Native link calls can yield while a port
        // changes; never interleave those calls with the vector's state reads.
        const collect=()=>signals.map(signal=>signal.found?signal.address.readU32():null);
        let values=collect(),stable=false,passes=1;
        for (;passes<10;passes++) {
            const next=collect();
            if (next.every((value,index)=>value===values[index])) {stable=true;values=next;break;}
            values=next;
        }
        return signals.map((signal,index)=>({name:signal.name,found:signal.found,state:values[index],stable_snapshot:stable,read_passes:passes+1}));
    }
};
