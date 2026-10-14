"""Additive, numbered migrations. Existing media and audit history are preserved."""
LATEST = 5


def apply(connection):
    version = connection.execute('PRAGMA user_version').fetchone()[0]
    if version > LATEST:
        raise RuntimeError('数据库由更新版本创建，请使用匹配的程序版本')
    if version < 5:
        additions = {
            'video_sources': {
                'profile_version': 'INTEGER NOT NULL DEFAULT 0',
                'running_config': "TEXT NOT NULL DEFAULT '{}'",
                'last_inference_at': 'REAL', 'inference_ms': 'REAL',
                'queue_lag_seconds': 'REAL', 'dropped_frames': 'INTEGER NOT NULL DEFAULT 0',
            },
            'events': {'source_profile_version': 'INTEGER NOT NULL DEFAULT 0'},
        }
        for table, columns in additions.items():
            existing = {r[1] for r in connection.execute(f'PRAGMA table_info({table})')}
            for name, declaration in columns.items():
                if name not in existing:
                    connection.execute(f'ALTER TABLE {table} ADD COLUMN {name} {declaration}')
        statements = [
            "CREATE TABLE IF NOT EXISTS source_profiles(source_id INTEGER PRIMARY KEY REFERENCES video_sources(id),version INTEGER NOT NULL,body TEXT NOT NULL,updated_at REAL NOT NULL,actor TEXT NOT NULL)",
            "CREATE TABLE IF NOT EXISTS source_profile_history(id INTEGER PRIMARY KEY,source_id INTEGER NOT NULL,version INTEGER NOT NULL,body TEXT NOT NULL,updated_at REAL NOT NULL,actor TEXT NOT NULL,UNIQUE(source_id,version))",
            "CREATE TABLE IF NOT EXISTS auto_jobs(id TEXT PRIMARY KEY,event_id INTEGER NOT NULL UNIQUE REFERENCES events(id),source_id INTEGER NOT NULL,evidence_id INTEGER,sha256 TEXT NOT NULL,content_version INTEGER NOT NULL,profile_version INTEGER NOT NULL,settings_version INTEGER NOT NULL,policy TEXT NOT NULL,status TEXT NOT NULL,stage TEXT NOT NULL DEFAULT 'local',local_request TEXT,cloud_request TEXT,created_at REAL NOT NULL,expires_at REAL NOT NULL,finished_at REAL,reason TEXT NOT NULL DEFAULT '',outcome TEXT NOT NULL DEFAULT 'unknown',related_job TEXT)",
            'CREATE INDEX IF NOT EXISTS auto_queue ON auto_jobs(status,created_at)',
            'CREATE INDEX IF NOT EXISTS auto_dedupe ON auto_jobs(source_id,sha256,created_at)',
            "CREATE TABLE IF NOT EXISTS case_state(event_id INTEGER PRIMARY KEY REFERENCES events(id),state TEXT NOT NULL,version INTEGER NOT NULL,owner TEXT NOT NULL DEFAULT '',note TEXT NOT NULL DEFAULT '',updated_at REAL NOT NULL,actor TEXT NOT NULL)",
            'CREATE TABLE IF NOT EXISTS case_history(id INTEGER PRIMARY KEY,event_id INTEGER NOT NULL,state TEXT NOT NULL,version INTEGER NOT NULL,owner TEXT NOT NULL,note TEXT NOT NULL,created_at REAL NOT NULL,actor TEXT NOT NULL)',
            'CREATE INDEX IF NOT EXISTS case_queue ON case_state(state,updated_at)',
            'CREATE INDEX IF NOT EXISTS evidence_hash ON evidence(sha256)',
            'CREATE INDEX IF NOT EXISTS evidence_path ON evidence(path)',
        ]
        for sql in statements:
            connection.execute(sql)
        connection.execute('PRAGMA user_version=5')
