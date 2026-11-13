"""Narrow native notice handling while a synchronous vendor call is blocked."""
import json
import os
import subprocess
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from .model import CircuitError,atomic_write


@contextmanager
def drc_notice_watcher(runtime,session):
    """Never send input, touch settings, or dismiss an unknown dialog."""
    processes = runtime.status(session)['processes']
    if os.name!='nt' or len(processes)!=1:
        raise CircuitError('NOTICE_WATCHER_REQUIRES_ONE_TRACKED_WINDOWS_PROCESS')
    process = processes[0]
    if not Path(runtime.load(session)['path']).resolve().is_relative_to(runtime.root.parent.resolve()):
        raise CircuitError('NOTICE_WATCHER_REQUIRES_OWN_WORKSPACE_COPY')
    base = runtime.session_path(session).parent/('notice-'+uuid.uuid4().hex[:8])
    ready,stop,report,log = (base.with_suffix(s) for s in ('.ready','.stop','.json','.log'))
    receipt = {'evidence_path':str(report),'notices':[],'errors':[],
               'desktop_input':False,'transport':'UIAutomation_metadata_and_WM_CLOSE'}
    with log.open('wb') as output:
        helper = subprocess.Popen(['powershell.exe','-NoProfile','-NonInteractive','-File',
            str(Path(__file__).with_name('notice_watcher.ps1')),'-OwnerPid',str(process['ProcessId']),
            '-Created',process['created'],'-ReadyPath',str(ready),'-StopPath',str(stop),'-ReportPath',str(report)],
            stdin=subprocess.DEVNULL,stdout=output,stderr=output,creationflags=0x08000000)
        try:
            deadline = time.monotonic()+8
            while not ready.is_file() and helper.poll() is None and time.monotonic()<deadline:
                time.sleep(.05)
            if not ready.is_file():
                raise CircuitError('NATIVE_NOTICE_WATCHER_NOT_READY: '+str(log))
            yield receipt
        finally:
            atomic_write(stop,b'stop')
            try:
                helper.wait(timeout=4)
            except subprocess.TimeoutExpired:
                # Only the helper is ours to terminate, never the vendor app.
                helper.terminate();helper.wait(timeout=3)
                receipt['errors'].append('NOTICE_HELPER_DID_NOT_EXIT_ON_STOP')
            if report.is_file():
                errors = receipt['errors'][:]
                receipt.update(json.loads(report.read_text(encoding='utf-8')))
                receipt['errors'] = errors+receipt['errors']
            else:
                receipt['errors'].append('NOTICE_HELPER_MISSING_REPORT')
