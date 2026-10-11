"""将 TVDB 原生详情转换成 MoviePilot V3 媒体对象，保持来源身份不变。"""
from urllib.parse import urljoin

from app.sdk.media import MediaInfo
from app.schemas.types import MediaSource, MediaType


def image_url(value):
    """将相对海报路径补全为 TVDB 图片地址，保留已有绝对地址。"""
    return urljoin("https://artworks.thetvdb.com/", value) if value else None


def to_media(info, mtype, language="zh"):
    """投影原生媒体身份和详情；TVDB 热度不作为十分制评分。"""
    translations = info.get("translations") or {}
    if not isinstance(translations, dict):
        translations = {}
    preferred = {"zh": "zho", "zh-cn": "zho", "zh-tw": "zht", "en": "eng", "ja": "jpn"}.get(
        str(language).lower(), language
    )

    def translated(field, fallback):
        entries = translations.get(field + "Translations") or []
        for lang in (preferred, "eng"):
            for item in entries:
                if isinstance(item, dict) and item.get("language") == lang and item.get(field):
                    return item[field]
        return fallback

    is_tv = mtype == MediaType.TV
    title = translated("name", info.get("name"))
    names = [info.get("name"), title]
    names += [item.get("name") for item in (translations.get("nameTranslations") or []) if isinstance(item, dict)]
    names += [item.get("name") if isinstance(item, dict) else item for item in info.get("aliases") or []]
    release = info.get("firstAired") if is_tv else (info.get("first_release") or {}).get("date")
    media = MediaInfo(
        media_source=MediaSource.TVDB, media_id=str(info["id"]), type=mtype,
        title=title, original_title=info.get("name"), original_name=info.get("name"),
        year=str(info.get("year") or (release or "")[:4]) or None,
        overview=translated("overview", info.get("overview")),
        poster_path=image_url(info.get("image")),
        original_language=info.get("originalLanguage"),
        origin_country=[info["originalCountry"]] if info.get("originalCountry") else [],
        names=list(dict.fromkeys(name for name in names if isinstance(name, str) and name)),
        release_date=release, first_air_date=release if is_tv else None,
        last_air_date=info.get("lastAired") if is_tv else None,
        runtime=info.get("averageRuntime") if is_tv else info.get("runtime"),
        status=(info.get("status") or {}).get("name"),
        genres=info.get("genres") or [],
        popularity=info.get("score"),
        homepage=f"https://thetvdb.com/{'series' if is_tv else 'movies'}/{info.get('slug') or info['id']}",
        tvdb_slug=info.get("slug") if is_tv else None,
    )
    # TVDB score 是热度，不是十分制评分；不伪造 vote_average。
    for artwork in info.get("artworks") or []:
        if artwork.get("type") in (3, 15) and artwork.get("image"):
            media.backdrop_path = image_url(artwork["image"])
            break
    for person in info.get("characters") or []:
        if not person.get("personName"):
            continue
        entry = {"name": person["personName"], "character": person.get("name"),
                 "profile_path": image_url(person.get("personImgURL") or person.get("image"))}
        # 不把 TVDB 人物 ID 当作 TMDB 人物 ID 传给宿主人物详情。
        if person.get("peopleType") == "Actor":
            media.actors.append(entry)
        elif person.get("peopleType") == "Director":
            media.directors.append(entry)
    for remote in info.get("remoteIds") or []:
        if str(remote.get("sourceName", "")).lower() == "imdb":
            media.imdb_id = remote.get("id")
    if is_tv:
        order = info.get("defaultSeasonType") or 1
        seasons = {}
        for season in info.get("seasons") or []:
            if (season.get("type") or {}).get("id") != order:
                continue
            number = season.get("number")
            if not isinstance(number, int) or number < 0:
                continue
            seasons[number] = {
                "season_number": number,
                "name": "特别篇" if number == 0 else f"第 {number} 季",
                "poster_path": image_url(season.get("image")) or media.poster_path,
                "overview": season.get("overview"),
                "air_date": season.get("firstAired"),
                "episode_count": season.get("episodeCount"),
            }
        media.season_info = [seasons[number] for number in sorted(seasons)]
        media.number_of_seasons = sum(number > 0 for number in seasons)
        network = info.get("originalNetwork") or info.get("latestNetwork")
        media.networks = [network] if network else []
    return media


def language_code(language):
    """将宿主语言设置转换为 TVDB 的三位语言代码。"""
    value = str(language or "zh").lower().replace("_", "-")
    return {"zh": "zho", "zh-cn": "zho", "zh-hans": "zho", "zh-tw": "zht",
            "zh-hant": "zht", "en": "eng", "ja": "jpn", "ko": "kor"}.get(value, value)


def season_translation(info, field, language):
    """季信息按首选语言、英语、原文字段逐级回退。"""
    translations = info.get("translations") or {}
    entries = translations.get(field + "Translations") or []
    for lang in (language_code(language), "eng"):
        for item in entries:
            if isinstance(item, dict) and item.get("language") == lang and item.get(field):
                return item[field]
    return info.get(field)


def episode_rows(info):
    """只采纳当前季的有效集，保留 TVDB 原生集号并去重排序。"""
    rows = {}
    for episode in info.get("episodes") or []:
        number = episode.get("number")
        if (episode.get("seasonNumber") != info.get("number")
                or not isinstance(number, int) or number < 0):
            continue
        rows.setdefault(number, episode)
    return [rows[number] for number in sorted(rows)]


def to_season(info, language="zh"):
    """从 TVDB 季扩展响应生成可直接显示的季摘要。"""
    episodes = episode_rows(info)
    dates = sorted(ep["aired"] for ep in episodes if ep.get("aired"))
    number = info["number"]
    return {
        "id": info["id"], "season_number": number,
        "name": "特别篇" if number == 0 else f"第 {number} 季",
        "overview": season_translation(info, "overview", language),
        "air_date": dates[0] if dates else None,
        "poster_path": image_url(info.get("image")),
        "episode_count": len(episodes),
    }


def to_episode(info, translation=None):
    """翻译缺失时逐字段保留 TVDB 原文，不伪造剧情或评分。"""
    translation = translation or {}
    return {
        "id": info.get("id"), "show_id": info.get("seriesId"),
        "season_number": info.get("seasonNumber"), "episode_number": info.get("number"),
        "name": translation.get("name") or info.get("name") or f"第 {info['number']} 集",
        "overview": translation.get("overview") or info.get("overview"),
        "air_date": info.get("aired"), "runtime": info.get("runtime"),
        "still_path": image_url(info.get("image")),
    }
