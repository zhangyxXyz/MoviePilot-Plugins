"""通用种子元信息核验，不落盘、不添加下载器、不持久化临时下载链接。"""
import hashlib
from urllib.parse import urlsplit, urljoin

MAX_BYTES = 8 * 1024 * 1024


def info_hash(data):
    """校验有限大小的 bencode，并对原始 info 字节取 SHA-1，避免重编码改变身份。"""
    if not data or len(data) > MAX_BYTES or data[:1] != b'd':
        raise ValueError('不是有效种子元信息')
    def parse(start, depth=0):
        """严格读取 bencode 边界，拒绝截断、重复字典键及过深结构。"""
        if depth > 80 or start >= len(data): raise ValueError('种子元信息结构无效')
        c=data[start:start+1]
        if c == b'i':
            stop=data.index(b'e', start+1)
            raw=data[start+1:stop]
            import re
            if not re.fullmatch(rb'0|-?[1-9][0-9]*',raw): raise ValueError('整数无效')
            return stop+1, None
        if c in [b'd',b'l']:
            pos=start+1; keys=set(); mapping={}
            while data[pos:pos+1] != b'e':
                if c==b'd':
                    pos,key=parse(pos,depth+1)
                    if not isinstance(key,bytes) or key in keys: raise ValueError('字典键无效')
                    keys.add(key); begin=pos;pos,_=parse(pos,depth+1);mapping[key]=(begin,pos)
                else:pos,_=parse(pos,depth+1)
            return pos+1,mapping if c==b'd' else None
        colon=data.index(b':',start);raw=data[start:colon]
        if not raw.isdigit() or len(raw)>1 and raw.startswith(b'0'): raise ValueError('长度无效')
        stop=colon+1+int(raw)
        if stop>len(data):raise ValueError('元信息截断')
        return stop,data[colon+1:stop]
    stop,mapping=parse(0)
    if stop != len(data) or b'info' not in mapping: raise ValueError('缺少 info 字段')
    begin,end=mapping[b'info']
    if data[begin:begin+1] != b'd':raise ValueError('info 不是字典')
    return hashlib.sha1(data[begin:end]).hexdigest()


def validate_download_url(url, roots, extra_hosts):
    """仅接受已配置站点源或明确允许的分发主机，禁止跨源传递站点认证头。"""
    p=urlsplit(url)
    if p.scheme!='https' or not p.hostname or p.username or p.password or p.fragment:
        raise ValueError('下载地址必须为有效 HTTPS 地址')
    origin=(p.scheme,p.hostname,p.port or 443)
    allowed={(urlsplit(r).scheme,urlsplit(r).hostname,urlsplit(r).port or 443) for r in roots}
    if origin not in allowed and not (p.hostname in extra_hosts and (p.port or 443)==443):
        raise ValueError('种子下载重定向域名未获允许，请在本站配置分发域名')
    return url


def fetch_metadata(gateway, verifier, site_id, url):
    """逐跳校验下载地址及配额，仅本站携带 Cookie，分发域名不携带站点凭据。"""
    from app.sdk.network import RequestUtils
    from app.sdk.config import settings
    profile,site=gateway.policies[site_id],gateway.sites[site_id]
    roots=[site['url']]
    if profile.verification=='mteam':roots.append(gateway.api_base(site_id))
    site_origin=urlsplit(site['url'])
    for hop in range(4):
        if verifier.stopped():raise ValueError('插件已停止')
        validate_download_url(url, roots, profile.torrent_download_hosts)
        verifier.reserve(site_id)
        target=urlsplit(url)
        same_site=(target.scheme,target.hostname,target.port)==(site_origin.scheme,site_origin.hostname,site_origin.port)
        request=RequestUtils(headers={'User-Agent':site.get('ua') or settings.USER_AGENT},
                             cookies=site.get('cookie') if same_site and profile.verification=='nexusphp' else None,
                             proxies=settings.PROXY if site.get('proxy') else None, timeout=20,verify=True)
        response=request.get_res(url,allow_redirects=False,stream=True)
        if response is None:raise ValueError('种子文件请求失败')
        try:
            if response.status_code in [301,302,303,307,308]:
                url=urljoin(url,response.headers.get('Location',''));continue
            if response.status_code!=200:raise ValueError('种子文件未返回成功响应')
            data=bytearray()
            for chunk in response.iter_content(65536):
                if verifier.stopped():raise ValueError('插件已停止')
                data.extend(chunk)
                if len(data)>MAX_BYTES:raise ValueError('种子元信息超过大小上限')
            return bytes(data)
        finally:response.close()
    raise ValueError('种子下载重定向次数超限')


def verify_metadata(verifier, site_id, detail_url, torrent_hash):
    """为一次元信息核验预占下载额度，Hash 不一致或任何失败都不判定失效。"""
    gateway=verifier.gateway
    profile=gateway.policies[site_id]
    if not profile.allow_torrent_verification:return False
    if profile.verification=='mteam':
        import json
        torrent_id=urlsplit(detail_url).path.rstrip('/').rsplit('/',1)[-1]
        payload=json.loads(verifier.fetch(site_id,gateway.api_base(site_id)+'/torrent/genDlToken',
                           method='POST',data={'id':torrent_id},category='download') or '{}')
        if str(payload.get('code'))!='0' or not isinstance(payload.get('data'),str):return False
        url=payload['data']
    elif profile.verification=='nexusphp' and profile.torrent_download_path:
        from urllib.parse import parse_qs
        torrent_id=parse_qs(urlsplit(detail_url).query).get(profile.detail_id_param,[''])[0]
        if not torrent_id.isdigit():return False
        url=urljoin(gateway.sites[site_id]['url'],profile.torrent_download_path.format(id=torrent_id))
        verifier.reserve(site_id,'download',global_request=False)
    else:return False
    digest=info_hash(fetch_metadata(gateway,verifier,site_id,url))
    return digest==torrent_hash.lower()
