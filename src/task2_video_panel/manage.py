"""Project-local operations. Nothing is installed or registered with the OS."""
import argparse,getpass,os,sys
from pathlib import Path
PROJECT=Path(__file__).resolve().parent
def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--data-dir',required=True,help='absolute runtime data directory')
    sub=parser.add_subparsers(dest='command',required=True)
    create=sub.add_parser('create-admin');create.add_argument('username',nargs='?')
    reset=sub.add_parser('reset-password');reset.add_argument('username')
    sub.add_parser('backup')
    restore=sub.add_parser('restore');restore.add_argument('archive');restore.add_argument('destination')
    serve=sub.add_parser('serve');serve.add_argument('--host',default='127.0.0.1');serve.add_argument('--port',type=int,default=8765)
    serve.add_argument('--accounts',action='store_true')
    args=parser.parse_args()
    os.environ.update(SCREEN_GUARD_BASE_DIR=str(PROJECT),SCREEN_GUARD_DATA_DIR=str(Path(args.data_dir).resolve()),PYTHONDONTWRITEBYTECODE='1')
    sys.dont_write_bytecode=True;sys.path[:0]=[str(PROJECT),str(PROJECT/'framework_part')]
    if args.command=='restore':
        from app.services import backups
        print(__import__('json').dumps(backups.restore(args.archive,args.destination),ensure_ascii=False,indent=2))
        return
    if args.command=='serve':
        if args.accounts:os.environ['SCREEN_GUARD_AUTH_MODE']='accounts'
        if args.host not in ('127.0.0.1','localhost','::1'):parser.error('通过本机 HTTPS 反向代理开放远程访问；应用保持回环监听')
        from app.services import accounts
        from app.core import database as db
        db.init_db();accounts.init()
        if args.accounts and not db.query_one("SELECT username FROM users WHERE enabled=1 AND role='admin'"):parser.error('请先 create-admin 创建管理员')
        import uvicorn
        uvicorn.run('app.main:app',host=args.host,port=args.port,proxy_headers=True,forwarded_allow_ips='127.0.0.1,::1',access_log=False)
    else:
        from app.core import database as db
        from app.services import accounts,operations,settings,ai_connections
        db.init_db();accounts.init();operations.init();settings.current();ai_connections.init()
        if args.command in ('create-admin','reset-password'):
            if not args.username:args.username=input('Admin username: ').strip()
            password=getpass.getpass('Password (12+ characters): ')
            if password!=getpass.getpass('Repeat password: '):parser.error('两次密码不同')
            if args.command=='create-admin':accounts.create_user(args.username,password,'admin')
            else:accounts.change_password(args.username,password)
            accounts.audit('server-console',args.command,args.username);print('完成')
        else:
            from app.services import backups,runtime_lock
            with runtime_lock.locked():result=backups.create()
            print(__import__('json').dumps(result,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
