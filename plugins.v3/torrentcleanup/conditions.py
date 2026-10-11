"""按用户显式勾选的状态、关键词及保护条件进行筛选。"""
import math
from .policy import Decision


def assess_conditions(task, row, observations, evidence, records, now):
    """同组状态或关键词任一命中，不同条件同时满足，观察不跨恢复累计。"""
    t = row.torrent
    reasons = []
    state = str(getattr(row, 'client_state', '')).lower()
    message = getattr(row, 'error_message', '') or ''
    selected = {s.lower() for s in task.states}
    state_ok = not selected or state in selected or 'error' in selected and getattr(row, 'has_error', False)
    keyword_ok = not task.error_keywords or any(k.casefold() in message.casefold() for k in task.error_keywords)
    if not state_ok: reasons.append('未命中所选下载器状态')
    if not keyword_ok: reasons.append('未命中错误信息关键词')
    if t.downloader != task.downloader: reasons.append('不在选定下载器内')
    if not task.all_sites and (not t.sites_complete or not t.site_ids or not t.site_ids.issubset(task.site_ids)):
        reasons.append('站点映射不完整或关联未选站点')
    if task.completion == 'completed' and not t.completed: reasons.append('仅处理已完成下载')
    if task.completion == 'incomplete' and (t.completed or getattr(row, 'progress', None) is None or row.progress >= 1): reasons.append('仅处理未完成下载')
    if task.require_healthy and not t.healthy: reasons.append('本地状态异常或正在校验')
    if set(task.protect_tags).intersection(t.tags): reasons.append('命中保护标签')
    if not set(task.include_tags).issubset(t.tags): reasons.append('缺少必需标签')
    local_hr = False
    if task.hr_local_conditions:
        identified = getattr(row, 'hr_identified', None)
        local_hr = bool(set(task.hr_tags).intersection(t.tags) or identified is True
                        or identified is None and task.error_protect_unknown_hr)
        if local_hr:
            seed, uploaded = t.seed_seconds, getattr(row, 'uploaded', None)
            time_ok = task.hr_seed_hours > 0 and seed is not None and math.isfinite(seed) and seed >= task.hr_seed_hours * 3600
            upload_ok = task.hr_upload_gb > 0 and uploaded is not None and math.isfinite(uploaded) and uploaded >= task.hr_upload_gb * 1024**3
            if not t.completed:
                reasons.append('H&R 分支要求下载完成')
            if not (time_ok or upload_ok):
                reasons.append('H&R 做种时间或上传量尚未达标')
    elif t.hr is True or task.error_protect_unknown_hr and t.hr is None:
        reasons.append('H&R 未满足或无法确认')
    if not local_hr:
        if task.filter_seed and (t.seed_seconds is None or t.seed_seconds < task.min_seed_hours*3600): reasons.append('累计做种时间不足或未知')
        if task.filter_ratio and (t.ratio is None or t.ratio < task.min_ratio): reasons.append('分享率不足或未知')
    speed = getattr(row, 'download_speed', None)
    progress = getattr(row, 'progress', None)
    added = getattr(row, 'added_at', None)
    downloaded = getattr(row, 'downloaded', None)
    idle = not task.require_idle_download or speed == 0
    if not idle: reasons.append('下载仍在进行或速度未知')
    if task.filter_age and (added is None or not 0 < added <= now or now-added < task.error_min_age_hours*3600): reasons.append('添加后保护期未满足或添加时间未知')
    if task.filter_progress and (progress is None or not 0 <= progress*100 <= task.error_max_progress): reasons.append('下载进度超过上限或未知')
    key = f'{task.id}:{t.downloader}:{t.hash}:conditions'
    if task.observe_state:
        if not state_ok or not keyword_ok or not idle:
            observations.pop(key, None)
        elif progress is None or downloaded is None or added is None or not 0 < added <= now:
            observations.pop(key, None)
            reasons.append('缺少进度、已下载量或添加时间，无法连续观察')
        else:
            signature = [state, message, progress, downloaded, added]
            old = observations.get(key)
            record = dict(old) if old and old.get('signature') == signature and now >= old['last'] else {'first': now, 'last': now, 'count': 1, 'signature': signature}
            if now-record['last'] >= task.error_gap_minutes*60:
                record.update(last=now, count=record['count']+1)
            observations[key] = record
            hours = (record['last']-record['first'])/3600
            if hours < task.error_observation_hours or record['count'] < task.error_min_observations:
                reasons.append(f"连续命中 {hours:.2f}/{task.error_observation_hours:g} 小时，确认 {record['count']}/{task.error_min_observations} 次")
    if task.verify_invalid:
        if not t.site_ids: reasons.append('缺少站点身份，无法确认失效')
        for sid in t.site_ids:
            e, record = evidence.get(sid), records.get(sid)
            if (not e or e.downloader != t.downloader or e.hash != t.hash or e.site_id != sid
                    or not e.confirmed_missing or not 0 <= now-e.checked_at <= 300):
                reasons.append(f'站点 {sid} 未确认失效，保留')
            elif not record or record.count < task.min_observations or record.last-record.first < task.observation_hours*3600:
                reasons.append(f'站点 {sid} 尚未完成失效观察期')
    if t.size <= 0 and (task.action == 'deletefile' or task.completion == 'completed'):
        reasons.append('任务体积未知')
    return Decision(not reasons, tuple(reasons) if reasons else ('满足全部勾选条件',), task.action)


def identify_hr(torrent, policies, record=None, event=None):
    """识别属性，不检查 Tracker 健康或站点完成状态；本地门槛单独判断。"""
    record, event = record or {}, event or {}
    signals = dict(record.get('signals') or {})
    if event.get('site_id') and event.get('signal') is not None:
        signals[str(event['site_id'])] = event['signal']
    if any(policies.get(s) and policies[s].hr_mode == 'managed' and policies[s].hr_detection == 'all' for s in torrent.site_ids):
        return True
    if any(signals.get(str(s)) is True for s in torrent.site_ids):
        return True
    if record.get('history_exempt'):
        return False
    if not torrent.sites_complete or not torrent.site_ids:
        return None
    for sid in torrent.site_ids:
        policy = policies.get(sid)
        if not policy or policy.hr_mode == 'unknown':
            return None
        if policy.hr_mode == 'managed' and signals.get(str(sid)) is not False:
            return None
    return False
