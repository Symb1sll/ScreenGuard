from typing import Dict

RISK_LABELS = {
    0: '正常合规内容',
    1: '违法违规内容',
}
SUBTYPES = ['正常内容', '涉黄/低俗内容', '涉赌内容', '涉毒/违禁品', '暴力/恐怖内容', '隐私泄露', '综合违规']

def decision(prob_violation: float, subtype: str = '综合违规', high: float = 0.72, review: float = 0.55) -> Dict:
    if prob_violation >= high:
        return {'label': '违法违规内容', 'subtype': subtype, 'risk_score': float(prob_violation), 'action': '自动告警'}
    if prob_violation >= review:
        return {'label': '疑似违规内容', 'subtype': subtype, 'risk_score': float(prob_violation), 'action': '人工复核'}
    return {'label': '正常合规内容', 'subtype': '正常内容', 'risk_score': float(prob_violation), 'action': '正常归档'}
