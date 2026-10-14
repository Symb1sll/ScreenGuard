"""Build the canonical layout, validate it, and optionally synchronize a managed v4."""
from __future__ import annotations
import argparse,ast,hashlib,json,os,socket,subprocess,sys,time
from pathlib import Path
from uuid import uuid4
import xml.etree.ElementTree as ET

ROOT=Path(__file__).resolve().parents[1]
TARGET=ROOT.parent/'v4'
RECORDS=ROOT/'artifacts/releases'
MANIFEST=ROOT/'config/last_v4.json'
TEMPLATES=ROOT/'docs/templates'
TEXT={'.py','.md','.ps1','.bat','.txt','.json','.yaml','.yml','.html','.css','.js','.example','.csv'}

def digest(data):return hashlib.sha256(data).hexdigest()

def inside(path,directory):
    if not path.resolve().is_relative_to(directory.resolve()) or path.resolve()==directory.resolve():
        raise RuntimeError('Path escaped the intended directory: '+str(path))

def usage(root,portable):
    # Public instructions use repository-relative paths on every installation.
    return (TEMPLATES/'使用说明.template.md').read_text(encoding='utf-8')

def build_files():
    files={};sources={}
    def add(source,destination=None):
        inside(source,ROOT)
        if source.is_symlink():raise RuntimeError('Source symlink is not supported: '+str(source))
        data=source.read_bytes();name=source.relative_to(ROOT).as_posix()
        sources[name]=digest(data);files[destination or name]=data
    # Only engineering trees, never archives, runtime, generated results or credentials.
    for tree in ('src','tests','scripts','deploy','docs'):
        for source in sorted((ROOT/tree).rglob('*')):
            if not source.is_file() or any(p in {'__pycache__','.pytest_cache'} for p in source.parts):continue
            if source.suffix.lower() in {'.pyc','.log','.db','.sqlite','.sqlite3','.pem','.key','.p12','.pfx','.gguf','.pt','.pth','.onnx'}:continue
            if source.name=='.env' or (source.name.startswith('.env.') and source.name!='.env.example'):continue
            add(source)
    add(ROOT/'config/runtime_config.example.json')
    for name in ('README.md','使用说明.md','requirements-dev.txt','.gitignore','export_v4.py'):
        add(ROOT/name)
    for source in sorted(ROOT.glob('*.bat')):add(source)
    add(TEMPLATES/'AGENTS.v4.md','AGENTS.md')
    add(ROOT/'windows_preview/README.md')
    files['docs/使用说明.md']=usage(TARGET,True).encode('utf-8')
    for name,data in files.items():
        if name.endswith('.py'):ast.parse(data.decode('utf-8-sig'),filename=name)
    return files,sources

def target_preflight(files):
    if TARGET.is_symlink() or TARGET.resolve()!=ROOT.parent.resolve()/'v4':
        raise RuntimeError('v4 must be a real sibling directory')
    previous=json.loads(MANIFEST.read_text(encoding='utf-8')) if MANIFEST.exists() else {'target':str(TARGET),'files':{}}
    if previous['target']!=str(TARGET):raise RuntimeError('Unexpected prior destination')
    # Check obsolete files as well: locally modified files are never removed.
    for name in set(files)|set(previous['files']):
        path=TARGET/name;inside(path,TARGET)
        expected=previous['files'].get(name)
        if path.exists() and (not path.is_file() or expected is None or digest(path.read_bytes())!=expected):
            raise RuntimeError('v4 contains a conflicting or locally modified file: '+str(path))
        if expected is not None and not path.exists():raise RuntimeError('Previously delivered file is missing: '+str(path))
    import psutil
    prefix=str(TARGET).lower()+os.sep
    for process in psutil.process_iter(['pid','name','cmdline']):
        if (process.info['name'] or '').lower() in {'python.exe','pythonw.exe','llama-server.exe'}:
            if any(prefix in arg.lower() for arg in process.info['cmdline'] or []):
                raise RuntimeError('Stop the v4 service before exporting; PID '+str(process.pid))
    return previous

def write_files(destination,files):
    for name,data in files.items():
        path=destination/name;inside(path,destination)
        path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(data)

def synchronize(files,run):
    previous=target_preflight(files)
    old={name:(TARGET/name).read_bytes() for name in previous['files']}
    write_files(run/'previous_delivery',old)
    touched=[]
    try:
        for name,data in files.items():
            path=TARGET/name;inside(path,TARGET)
            path.parent.mkdir(parents=True,exist_ok=True)
            temp=path.with_name(path.name+'.'+uuid4().hex+'.new');inside(temp,TARGET)
            try:temp.write_bytes(data);temp.replace(path)
            finally:
                if temp.exists():temp.unlink()
            touched.append(name)
        for name in set(old)-set(files):
            path=TARGET/name;inside(path,TARGET)
            if digest(path.read_bytes())!=previous['files'][name]:raise RuntimeError('File changed during synchronization: '+name)
            path.unlink();touched.append(name)
        for name,data in files.items():
            if digest((TARGET/name).read_bytes())!=digest(data):raise RuntimeError('Post-copy verification failed: '+name)
    except BaseException:
        for name in reversed(touched):
            path=TARGET/name;inside(path,TARGET)
            if name in old:path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(old[name])
            elif path.exists() and digest(path.read_bytes())==digest(files[name]):path.unlink()
        raise

def smoke(stage,run):
    import httpx
    with socket.socket() as probe:probe.bind(('127.0.0.1',0));port=probe.getsockname()[1]
    url=f'http://127.0.0.1:{port}'
    env={k:v for k,v in os.environ.items() if not k.startswith('SCREEN_GUARD_')}
    env.update(PYTHONDONTWRITEBYTECODE='1',TEMP=str(run),TMP=str(run),TMPDIR=str(run),SCREEN_GUARD_AUTH_MODE='local')
    with (run/'startup.log').open('w',encoding='utf-8') as log,httpx.Client(base_url=url,timeout=2,trust_env=False) as client:
        process=subprocess.Popen([sys.executable,'-X','utf8','-B',str(stage/'scripts/serve_windows.py'),'--port',str(port)],cwd=stage,env=env,stdout=log,stderr=log)
        try:
            deadline=time.monotonic()+30
            while time.monotonic()<deadline:
                if process.poll() is not None:raise RuntimeError('Preview startup failed; inspect '+str(run/'startup.log'))
                try:
                    result=client.get('/api/bootstrap')
                    if result.status_code==200:break
                except httpx.HTTPError:pass
                time.sleep(.2)
            else:raise RuntimeError('Preview startup timed out')
            client.headers['X-SG-CSRF']=result.json()['csrf']
            for route in ('/','/api/health','/api/preview','/api/settings','/api/ai/connections','/api/operations/status','/api/runtime/status','/api/automatic-review','/api/dispositions','/static/runtime-ui.js'):
                client.get(route).raise_for_status()
            assert client.get('/api/sources').json()==[]
            assert client.get('/api/preview').json()['pid']==process.pid
            assert any(c['id']=='local-qwen' for c in client.get('/api/ai/connections').json())
            client.post('/api/shutdown').raise_for_status();process.wait(timeout=20)
            if process.returncode:raise RuntimeError('Preview did not exit cleanly')
        finally:
            if process.poll() is None:process.terminate();process.wait(timeout=10)

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    mode=parser.add_mutually_exclusive_group()
    mode.add_argument('--check',action='store_true',help='read-only verification of last v4 delivery')
    mode.add_argument('--stage-only',action='store_true',help='validate locally without reading or changing v4')
    args=parser.parse_args()
    if not args.stage_only and ROOT.name!='v3':parser.error('Publish/check from the v3 development directory only')
    if args.check:
        record=json.loads(MANIFEST.read_text(encoding='utf-8'));changed=[]
        if record['target']!=str(TARGET):raise RuntimeError('Unexpected prior destination')
        for name,expected in record['files'].items():
            path=TARGET/name;inside(path,TARGET)
            if not path.is_file() or digest(path.read_bytes())!=expected:changed.append(name)
        print(json.dumps({'target':str(TARGET),'checked':len(record['files']),'changed':changed},ensure_ascii=False,indent=2));return bool(changed)
    (ROOT/'docs/使用说明.md').write_text(usage(ROOT,False),encoding='utf-8')
    files,sources=build_files()
    if not args.stage_only:target_preflight(files)
    # Keep nested validation/backup paths below the Windows legacy path limit.
    run=RECORDS/uuid4().hex[:12];stage=run/'project';stage.mkdir(parents=True)
    write_files(stage,files)
    print(f'Prepared {len(files)} engineering files. Validation directory: {run}',flush=True)
    subprocess.run([sys.executable,'-X','utf8','-B',str(stage/'tests/run_tests.py')],cwd=stage,check=True)
    smoke(stage,run)
    for name,expected in sources.items():
        if digest((ROOT/name).read_bytes())!=expected:raise RuntimeError('Source changed during verification: '+name)
    for name,data in files.items():
        if digest((stage/name).read_bytes())!=digest(data):raise RuntimeError('Engineering file changed during verification: '+name)
    if not args.stage_only:synchronize(files,run)
    totals={'tests':0,'failures':0,'errors':0}
    for report in (stage/'artifacts/test_runs').glob('*/*.xml'):
        for suite in ET.parse(report).getroot().iter('testsuite'):
            for name in totals:totals[name]+=int(suite.attrib.get(name,0))
    record={'target':str(stage if args.stage_only else TARGET),'stage_only':args.stage_only,'source':str(ROOT),
            'validation':str(run),'tests':totals,'startup_smoke':True,'files':{n:digest(d) for n,d in files.items()},
            'source_files':sources,'bytes':sum(map(len,files.values()))}
    if not args.stage_only:MANIFEST.write_text(json.dumps(record,ensure_ascii=False,indent=2),encoding='utf-8')
    (run/'delivery.json').write_text(json.dumps(record,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'target':record['target'],'stage_only':args.stage_only,'files':len(files),'tests':totals,'startup_smoke':True},ensure_ascii=False),flush=True)
    return 0

if __name__=='__main__':raise SystemExit(main())
