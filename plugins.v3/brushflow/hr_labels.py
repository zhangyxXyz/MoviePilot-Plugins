"""H&R助手的标签名称配置；不执行识别、复核或标签写入。"""
def protection_tags():
    from app.sdk.plugins import PluginManager
    config = PluginManager().get_plugin_config('HitAndRun') or {}
    return set(filter(None, [config.get('confirmed_tag') or 'H&R',
                             config.get('pending_tag') or 'H&R待核验',
                             *config.get('tag_aliases', [])]))
