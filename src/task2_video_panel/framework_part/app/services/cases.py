"""Response/disposition history is separate from review and notification acknowledgement."""
import time
from app.core import database as db


def reopen(connection, event_id, actor):
    """A new risk verdict must not inherit an already resolved response."""
    row = connection.execute('SELECT * FROM case_state WHERE event_id=?', (event_id,)).fetchone()
    if row and row['state'] != 'pending':
        stamp = time.time()
        note = '风险结论或类别已变化，请重新确认处置'
        connection.execute("UPDATE case_state SET state='pending',version=version+1,note=?,updated_at=?,actor=? WHERE event_id=?", (note, stamp, actor, event_id))
        connection.execute("INSERT INTO case_history(event_id,state,version,owner,note,created_at,actor) VALUES(?,'pending',?,?,?,?,?)", (event_id, row['version']+1, row['owner'], note, stamp, actor))


def get(event_id):
    row = db.query_one('SELECT * FROM case_state WHERE event_id=?', (event_id,))
    event = db.query_one('SELECT review_state FROM events WHERE id=?', (event_id,))
    if not event:
        raise KeyError('事件不存在')
    return {'current': row or {'event_id': event_id, 'state': 'pending', 'version': 0, 'owner': '', 'note': '', 'updated_at': None},
            'applicable': event['review_state'] == 'confirmed',
            'history': db.query('SELECT * FROM case_history WHERE event_id=? ORDER BY id DESC LIMIT 100', (event_id,))}


def save(event_id, state, version, owner, note, actor, review_version):
    if state not in ('pending', 'in_progress', 'resolved'):
        raise ValueError('处置状态无效')
    if not note.strip():
        raise ValueError('请填写处置说明')
    with db.transaction() as c:
        event = c.execute('SELECT review_state,review_version FROM events WHERE id=?', (event_id,)).fetchone()
        if not event:
            raise KeyError('事件不存在')
        if event['review_state'] != 'confirmed':
            raise ValueError('人工确认风险后才能登记处置；历史记录仍保留')
        if event['review_version'] != review_version:
            raise RuntimeError('审核结论已变化，请重载事件')
        previous = c.execute('SELECT * FROM case_state WHERE event_id=?', (event_id,)).fetchone()
        if (previous['version'] if previous else 0) != version:
            raise RuntimeError('处置记录已变化，请重载事件')
        stamp = time.time()
        c.execute('INSERT INTO case_state VALUES(?,?,?,?,?,?,?) ON CONFLICT(event_id) DO UPDATE SET state=excluded.state,version=excluded.version,owner=excluded.owner,note=excluded.note,updated_at=excluded.updated_at,actor=excluded.actor',
                  (event_id, state, version+1, owner.strip(), note.strip(), stamp, actor))
        c.execute('INSERT INTO case_history(event_id,state,version,owner,note,created_at,actor) VALUES(?,?,?,?,?,?,?)',
                  (event_id, state, version+1, owner.strip(), note.strip(), stamp, actor))
    return get(event_id)
