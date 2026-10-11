"""从最终执行记录生成有长度上限的清理通知，不包含站点凭据和文件路径。"""

ACTION_NAMES = {'mark': '标记清理候选', 'pause': '暂停任务', 'delete': '仅删除任务（保留文件）',
                'deletefile': '删除任务和文件'}
STATUS_NAMES = {'confirmed': '已确认', 'unconfirmed': '待确认', 'failed': '失败', 'skipped': '跳过'}


def execution_notification(task, report):
    """展示最终结果与处理明细，体积只描述任务大小，不声称释放磁盘空间。"""
    items = report.get('items', [])
    counts = {key: sum(i.get('status') == key for i in items) for key in STATUS_NAMES}
    confirmed_bytes = sum(i.get('size', 0) or 0 for i in items if i.get('status') == 'confirmed')
    lines = [f'下载器：{task.downloader}', f'动作：{ACTION_NAMES.get(task.action, task.action)}',
             '，'.join(f'{label} {counts[key]} 项' for key, label in STATUS_NAMES.items()),
             f'已确认任务体积：{confirmed_bytes / 1024**3:.2f} GiB']
    if report.get('status') == 'stopped':
        lines.append('本轮执行已中断，以下为已记录结果。')
    if not items:
        lines.append('本轮没有处理任何种子。')
    shown = 0
    for item in items:
        name = ' '.join(str(item.get('name') or '未命名种子').split())[:180]
        reason = '，'.join(item.get('cleanup_reasons') or []) if item.get('status') == 'confirmed' else item.get('reason', '')
        reason = ' '.join(str(reason or '详见执行记录').split())[:220]
        block = f"\n{shown+1}. 【{STATUS_NAMES.get(item.get('status'), '未知')}】{name}\n大小：{(item.get('size', 0) or 0) / 1024**3:.2f} GiB\n原因：{reason}"
        if shown >= 20 or len('\n'.join(lines)) + len(block) > 5500:
            break
        lines.append(block)
        shown += 1
    if shown < len(items):
        lines.append(f'其余 {len(items)-shown} 项请查看插件执行记录。')
    lines.append('完整结果：整理 → 种子清理 → 执行记录。')
    return f'清理任务：{task.name}', '\n'.join(lines)
