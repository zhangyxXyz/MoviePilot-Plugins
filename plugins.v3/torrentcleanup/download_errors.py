"""未完成下载的错误采样策略，与站点删种核验相互独立。"""

from .policy import Decision


def assess_download_error(task, row, observations, now):
    """仅处理持续采样命中的未完成异常，恢复、进度变化或重新添加会重置计时。"""
    torrent = row.torrent
    key = f'{task.id}:{torrent.downloader}:{torrent.hash}:download_error'
    reasons = []
    state = getattr(row, 'client_state', '')
    progress = getattr(row, 'progress', None)
    speed = getattr(row, 'download_speed', None)
    added = getattr(row, 'added_at', None)
    downloaded = getattr(row, 'downloaded', None)
    matching = state in task.error_states and progress is not None and 0 <= progress < 1 and not torrent.completed and speed == 0
    if not matching:
        observations.pop(key, None)
        return Decision(False, ('未命中未完成下载错误状态，或下载仍在进行',), task.action)
    if progress * 100 > task.error_max_progress:
        reasons.append('下载进度超过清理上限')
    if added is None or not 0 < added <= now:
        reasons.append('缺少可信添加时间')
    elif now - added < task.error_min_age_hours * 3600:
        reasons.append('任务添加时间尚未达到保护期')
    if downloaded is None:
        reasons.append('缺少已下载字节数，无法确认期间没有恢复')
    signature = [state, progress, downloaded, added]
    previous = observations.get(key)
    if not previous or previous.get('signature') != signature or now < previous.get('last', now):
        record = {'first': now, 'last': now, 'count': 1, 'signature': signature}
    else:
        record = dict(previous)
        if now - record['last'] >= task.error_gap_minutes * 60:
            record.update(last=now, count=record['count'] + 1)
    observations[key] = record
    hours = max(0, record['last'] - record['first']) / 3600
    if record['count'] < task.error_min_observations or hours < task.error_observation_hours:
        reasons.append(f"错误观察 {hours:.2f}/{task.error_observation_hours:g} 小时，确认 {record['count']}/{task.error_min_observations} 次")
    if torrent.downloader != task.downloader:
        reasons.append('不在选定下载器内')
    if not task.all_sites:
        if not torrent.sites_complete or not torrent.site_ids:
            reasons.append('Tracker 与站点映射不完整')
        elif not torrent.site_ids.issubset(task.site_ids):
            reasons.append('任务还关联未选中的站点')
    if set(task.protect_tags).intersection(torrent.tags):
        reasons.append('命中保护标签')
    if not set(task.include_tags).issubset(torrent.tags):
        reasons.append('缺少必需标签')
    if torrent.hr is True or task.error_protect_unknown_hr and torrent.hr is None:
        reasons.append('H&R 未满足或无法确认')
    if task.action == 'deletefile' and torrent.size <= 0:
        reasons.append('任务体积未知，禁止删除文件')
    return Decision(not reasons, tuple(reasons) if reasons else (f'持续命中 {state}，错误观察及保护条件满足',), task.action)
