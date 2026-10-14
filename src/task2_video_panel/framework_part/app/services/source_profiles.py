"""Per-source overrides; saved and running configurations are independently visible."""
import json
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator
from app.core import database as db
from app.services import settings
from screen_guard_core import LABELS


class AutoPolicy(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    enabled: bool = False
    local_connection: str = 'local-qwen'
    cloud_connection: str = ''
    allow_cloud: bool = False
    daily_limit: int = Field(40, ge=1, le=1000)
    min_interval_seconds: int = Field(30, ge=1, le=3600)
    dedupe_seconds: int = Field(600, ge=0, le=86400)
    ttl_seconds: int = Field(600, ge=30, le=3600)
    include_audits: bool = False

    @model_validator(mode='after')
    def validate_connections(self):
        for value in (self.local_connection, self.cloud_connection):
            if value and not re.fullmatch(r'[a-z0-9][a-z0-9_-]{0,63}', value):
                raise ValueError('模型连接 ID 无效')
        if self.enabled and not self.local_connection:
            raise ValueError('请选择本地图片模型连接')
        if self.cloud_connection and not self.allow_cloud:
            raise ValueError('选择云端连接时需允许该视频源发送疑似画面')
        return self


class SourceProfile(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    sample_seconds: float | None = Field(None, ge=.1, le=60)
    model_id: str | None = Field(None, pattern=r'^[a-zA-Z0-9_-]{1,80}$')
    policies: dict[str, settings.LabelPolicy] | None = None
    auto_alerts: bool | None = None
    schedule_enabled: bool = False
    schedule_start: str = '00:00'
    schedule_end: str = '00:00'
    schedule_days: list[int] = Field(default_factory=lambda: list(range(7)), max_length=7)
    ai: AutoPolicy = Field(default_factory=AutoPolicy)

    @model_validator(mode='after')
    def validate_profile(self):
        if self.policies is not None and set(self.policies) != set(LABELS):
            raise ValueError('每路策略需包含全部五类标签')
        for value in (self.schedule_start, self.schedule_end):
            if not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d', value):
                raise ValueError('运行时段需为 HH:MM')
        if len(set(self.schedule_days)) != len(self.schedule_days) or any(d < 0 or d > 6 for d in self.schedule_days):
            raise ValueError('星期取值为 0（周一）至 6（周日）')
        if self.schedule_enabled and not self.schedule_days:
            raise ValueError('至少选择一个运行日')
        return self


def get(source_id):
    row = db.query_one('SELECT * FROM source_profiles WHERE source_id=?', (source_id,))
    return {'version': row['version'] if row else 0,
            'profile': SourceProfile.model_validate_json(row['body']).model_dump() if row else SourceProfile().model_dump()}


def effective(source_id):
    global_config = settings.current()
    profile = get(source_id)
    config = dict(global_config['settings'])
    for key in ('sample_seconds', 'model_id', 'policies', 'auto_alerts'):
        if profile['profile'][key] is not None:
            config[key] = profile['profile'][key]
    return {'settings_version': global_config['version'], 'profile_version': profile['version'],
            'settings': config, 'profile': profile['profile']}


def within_schedule(profile, timezone_name='Asia/Shanghai', at=None):
    if not profile['schedule_enabled']:
        return True
    tz = timezone(timedelta(hours=8)) if timezone_name == 'Asia/Shanghai' else timezone.utc
    now = datetime.fromtimestamp(at if at is not None else time.time(), tz)
    value = now.strftime('%H:%M')
    start, end = profile['schedule_start'], profile['schedule_end']
    day = now.weekday()
    if start > end and value < end:
        day = (day - 1) % 7  # Overnight belongs to the day it started.
    in_time = start == end or start <= value < end if start <= end else value >= start or value < end
    return day in profile['schedule_days'] and in_time


def save(source_id, body, expected_version, actor):
    parsed = SourceProfile.model_validate(body).model_dump()
    current = effective(source_id)
    merged = dict(current['settings'])
    global_cfg = settings.current()['settings']
    for key in ('sample_seconds', 'model_id', 'policies', 'auto_alerts'):
        merged[key] = parsed[key] if parsed[key] is not None else global_cfg[key]
    from app.services.models import validate_model
    validate_model(merged['model_id'])
    if any(merged[k] != current['settings'][k] for k in ('model_id', 'policies')):
        from app.services.settings import require_comparison
        require_comparison(merged)
    policy = parsed['ai']
    if policy['enabled']:
        from app.services import ai_connections as ai
        for field, provider in (('local_connection', 'local'), ('cloud_connection', 'cloud')):
            if not policy[field]:
                continue
            cfg = json.loads(ai.get_row(policy[field])['body'])
            if not cfg['enabled'] or not cfg['vision'] or not cfg['model']:
                raise ValueError('自动审核连接必须启用图片能力并填写模型 ID')
            if (cfg['provider'] == 'local') != (provider == 'local'):
                raise ValueError('本地和云端连接类型不匹配')
    with db.transaction() as c:
        if not c.execute('SELECT id FROM video_sources WHERE id=? AND archived=0', (source_id,)).fetchone():
            raise KeyError('视频源不存在或已归档')
        old = c.execute('SELECT version FROM source_profiles WHERE source_id=?', (source_id,)).fetchone()
        if (old['version'] if old else 0) != expected_version:
            raise RuntimeError('每路配置已变化，请重新加载')
        version = expected_version + 1
        values = (source_id, version, json.dumps(parsed, ensure_ascii=False), time.time(), actor)
        c.execute('INSERT INTO source_profiles VALUES(?,?,?,?,?) ON CONFLICT(source_id) DO UPDATE SET version=excluded.version,body=excluded.body,updated_at=excluded.updated_at,actor=excluded.actor', values)
        c.execute('INSERT INTO source_profile_history(source_id,version,body,updated_at,actor) VALUES(?,?,?,?,?)', values)
    return {**get(source_id), 'effective': '采集策略下次启动生效；自动审核与云端许可立即生效，旧配置排队任务取消'}
