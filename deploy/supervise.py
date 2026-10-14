"""Run on Windows in a dedicated terminal. Ctrl+C stops supervisor and child."""
import argparse,os,signal,subprocess,sys,time,socket
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
PROJECT=ROOT/'src/task2_video_panel'
def main():
    parser=argparse.ArgumentParser();parser.add_argument('--port',type=int,default=8765);parser.add_argument('--accounts',action='store_true')
    parser.add_argument('--data-dir',default=str(ROOT/'windows_preview/runtime/data'));args=parser.parse_args()
    data=Path(args.data_dir).resolve()
    if not data.is_relative_to(ROOT.resolve()):parser.error('数据目录必须位于本项目目录内')
    with socket.socket() as probe:
        try:probe.bind(('127.0.0.1',args.port))
        except OSError:parser.error('该端口已有服务；请先停止本机演示，或使用 --port 选择其它端口')
    data.mkdir(parents=True,exist_ok=True);runtime=data/'process-temp';runtime.mkdir(exist_ok=True)
    env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1',TEMP=str(runtime),TMP=str(runtime),TMPDIR=str(runtime))
    cmd=[sys.executable,'-X','utf8','-B',str(PROJECT/'manage.py'),'--data-dir',str(data),'serve','--port',str(args.port)]+(['--accounts'] if args.accounts else [])
    failures=0
    while True:
        started=time.monotonic();process=subprocess.Popen(cmd,cwd=PROJECT,env=env)
        try:code=process.wait()
        except KeyboardInterrupt:
            try:process.wait(timeout=20)
            except subprocess.TimeoutExpired:process.terminate();process.wait(timeout=5)
            break
        if code==0:break
        failures=failures+1 if time.monotonic()-started<60 else 1
        if failures>=5:raise SystemExit('连续 5 次启动失败；停止重试，请检查配置')
        delay=min(30,2**failures);print(f'服务退出 ({code})，{delay} 秒后重启',flush=True);time.sleep(delay)
if __name__=='__main__':main()
