import json,time,re
from app.core import database as db
from screen_guard_core import validate_points
DEFAULT={'id':'screen-1','name':'全画面（尚未标定）','points':[[0,0],[1,0],[1,1],[0,1]]}
def get_regions(source_id):
    row=db.query_one('SELECT * FROM roi_history WHERE source_id=? ORDER BY id DESC LIMIT 1',(source_id,))
    return {'version':row['id'],'regions':json.loads(row['body'])} if row else {'version':0,'regions':[dict(DEFAULT)]}
def save_regions(source_id,regions,expected_version):
    if not 1<=len(regions)<=4:raise ValueError('每路需配置 1–4 个屏幕区域')
    ids=set();clean=[]
    for r in regions:
        name=str(r.get('name','')).strip();key=str(r.get('id',''))
        if not re.fullmatch('[a-zA-Z0-9_-]{1,40}',key) or key in ids:raise ValueError('屏幕编号无效或重复')
        if not 1<=len(name)<=60:raise ValueError('屏幕名称需为 1–60 字')
        points=validate_points(r['points']).tolist();ids.add(key)
        clean.append({'id':key,'name':name,'points':points})
    with db.transaction() as c:
        row=c.execute('SELECT id FROM roi_history WHERE source_id=? ORDER BY id DESC LIMIT 1',(source_id,)).fetchone()
        if (row['id'] if row else 0)!=expected_version:raise RuntimeError('屏幕区域已被修改，请重新加载')
        version=c.execute('INSERT INTO roi_history(source_id,body,created_at) VALUES(?,?,?)',(source_id,json.dumps(clean,ensure_ascii=False),time.time())).lastrowid
    return {'version':version,'regions':clean,'effective':'任务下次启动时生效'}

