import asyncio
from threading import RLock
from concurrent.futures import ThreadPoolExecutor

from typing import Any, List, Dict, Tuple, Optional

from cachetools import cached, TTLCache

from app import schemas
from app.sdk.config import settings
from app.sdk.events import eventmanager, Event
from app.sdk.logging import logger
from app.plugins import _PluginBase
from app.schemas import DiscoverSourceEventData
from app.schemas.types import ChainEventType, MediaSource, MediaType
from app.sdk.network import RequestUtils

from .details import image_url, to_media, language_code, episode_rows, to_season, to_episode


class TvdbDiscover(_PluginBase):
    # 插件名称
    plugin_name = "TheTVDB探索"
    # 插件描述
    plugin_desc = "让探索支持TheTVDB的数据浏览。"
    # 插件图标
    plugin_icon = "TheTVDB_A.png"
    # 插件版本
    plugin_version = "2.3.0"
    # 插件作者
    plugin_author = "seiun, jxxghp"
    # 作者主页
    author_url = "https://github.com/zhangyxXyz/MoviePilot-Plugins"
    # 插件配置项ID前缀
    plugin_config_prefix = "tvdbdiscover_"
    # 加载顺序
    plugin_order = 99
    # 可使用的用户级别
    auth_level = 1

    # 私有属性
    _base_api = "https://api4.thetvdb.com/v4"
    _enabled = False
    _proxy = False
    _api_key = None

    def init_plugin(self, config: dict = None):
        self.__get_token.cache_clear()
        self._api_get.cache_clear()
        self._enabled = False
        self._proxy = False
        self._api_key = None
        if config:
            self._enabled = config.get("enabled")
            self._proxy = config.get("proxy")
            self._api_key = config.get("api_key")

    def get_state(self) -> bool:
        return self._enabled

    @staticmethod
    def get_command() -> List[Dict[str, Any]]:
        pass

    def get_api(self) -> List[Dict[str, Any]]:
        """
        获取插件API
        [{
            "path": "/xx",
            "endpoint": self.xxx,
            "methods": ["GET", "POST"],
            "summary": "API说明"
        }]
        """
        return [{
            "path": "/tvdb_discover",
            "endpoint": self.tvdb_discover,
            "methods": ["GET"],
            "summary": "TheTVDB探索数据源",
            "description": "获取TheTVDB探索数据",
            "response_model": schemas.Response[List[schemas.MediaInfo]],
        }, {
            "path": "/tvdb_season/{series_id}/{season_number}",
            "endpoint": self.tvdb_season,
            "methods": ["GET"],
            "auth": "bear",
            "summary": "TheTVDB原生季和单集剧情",
            "response_model": schemas.Response[schemas.MediaSeason],
        }]

    def get_form(self) -> Tuple[List[dict], Dict[str, Any]]:
        """
        拼装插件配置页面，需要返回两块数据：1、页面配置；2、数据结构
        """
        return [
            {
                'component': 'VForm',
                'content': [
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12,
                                    'md': 4
                                },
                                'content': [
                                    {
                                        'component': 'VSwitch',
                                        'props': {
                                            'model': 'enabled',
                                            'label': '启用插件',
                                        }
                                    }
                                ]
                            },
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12,
                                    'md': 4
                                },
                                'content': [
                                    {
                                        'component': 'VSwitch',
                                        'props': {
                                            'model': 'proxy',
                                            'label': '使用代理服务器',
                                        }
                                    }
                                ]
                            }
                        ]
                    },
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12
                                },
                                'content': [
                                    {
                                        'component': 'VTextField',
                                        'props': {
                                            'model': 'api_key',
                                            'label': 'API Key'
                                        }
                                    }
                                ]
                            }
                        ]
                    }
                ]
            }
        ], {
            "enabled": False,
            "proxy": False,
            "api_key": "ed2aa66b-7899-4677-92a7-67bc9ce3d93a"
        }

    def get_page(self) -> List[dict]:
        pass

    @cached(cache=TTLCache(maxsize=1, ttl=24 * 3600), lock=RLock())
    def __get_token(self) -> str:
        res = RequestUtils(headers={"Accept": "application/json", "Content-Type": "application/json"}).post_res(
            f"{self._base_api}/login", json={"apikey": self._api_key},
            proxies=settings.PROXY if self._proxy else None,
        )
        if res is None or not res.ok:
            raise RuntimeError("TheTVDB 登录失败，请检查插件 API Key 和代理设置")
        token = (res.json().get("data") or {}).get("token")
        if not token:
            raise RuntimeError("TheTVDB 登录响应缺少 token")
        return token

    @cached(cache=TTLCache(maxsize=128, ttl=1800), lock=RLock())
    def _api_get(self, path: str, **params):
        for attempt in range(2):
            res = RequestUtils(headers={
                "Accept": "application/json",
                "Authorization": f"Bearer {self.__get_token()}",
            }).get_res(
                f"{self._base_api}/{path}", params=params,
                proxies=settings.PROXY if self._proxy else None,
            )
            if res is None:
                raise ConnectionError("无法连接 TheTVDB，请检查插件代理和网络")
            if res.status_code == 401 and attempt == 0:
                self.__get_token.cache_clear()
                continue
            if not res.ok:
                raise RuntimeError(f"TheTVDB {path} 请求失败（HTTP {res.status_code}）")
            payload = res.json()
            if payload.get("status") != "success" or payload.get("data") is None:
                raise RuntimeError(f"TheTVDB {path} 未返回有效数据")
            return payload["data"]

    def __request(self, mtype: str, **kwargs):
        return self._api_get(f"{mtype}/filter", **kwargs)

    def get_module(self) -> Dict[str, Any]:
        if not self._enabled or not self._api_key:
            return {}
        return {
            "recognize_media": self.recognize_media,
            "async_recognize_media": self.async_recognize_media,
        }

    def recognize_media(self, meta=None, mtype=None, media_source=None,
                        media_id=None, **kwargs):
        """仅处理显式 TVDB 原生身份，不干预 TMDB 等其它识别源。"""
        if not self._enabled or media_source != MediaSource.TVDB:
            return None
        identity = str(media_id or "").strip()
        if not identity.isdigit() or int(identity) <= 0:
            return None
        mtype = mtype or getattr(meta, "type", None)
        if mtype not in (MediaType.TV, MediaType.MOVIE):
            return None
        resource = "series" if mtype == MediaType.TV else "movies"
        try:
            info = self._api_get(f"{resource}/{identity}/extended", meta="translations")
            if not isinstance(info, dict) or str(info.get("id")) != identity or not info.get("name"):
                raise ValueError("详情缺少名称或返回的 TVDB ID 不匹配")
            media = to_media(info, mtype, getattr(settings, "TMDB_LOCALE", "zh"))
            if mtype == MediaType.TV:
                self._fill_seasons(media, info)
            return media
        except Exception as err:
            logger.error(f"TheTVDB {resource}/{identity} 详情识别失败：{err}")
            return None

    async def async_recognize_media(self, *args, **kwargs):
        # 阻塞 HTTP 调用离开主事件循环，避免浏览详情拖慢其他 API。
        return await asyncio.to_thread(self.recognize_media, *args, **kwargs)

    @staticmethod
    def _native_seasons(info):
        order = info.get("defaultSeasonType") or 1
        seasons = {}
        for season in info.get("seasons") or []:
            number = season.get("number")
            if ((season.get("type") or {}).get("id") == order
                    and isinstance(number, int) and number >= 0 and season.get("id")):
                seasons.setdefault(number, season)
        return [seasons[number] for number in sorted(seasons)]

    def _season_info(self, series_id, season):
        info = self._api_get(f"seasons/{season['id']}/extended", meta="translations")
        if (str(info.get("seriesId")) != str(series_id)
                or info.get("number") != season.get("number")
                or str(info.get("id")) != str(season["id"])):
            raise ValueError("TVDB 季身份不匹配")
        return info

    def _fill_seasons(self, media, info):
        """补齐默认编排的季摘要及真实集号，单集剧情在展开时另行加载。"""
        seasons = self._native_seasons(info)
        if not seasons:
            return
        def load(season):
            try:
                return self._season_info(media.media_id, season)
            except Exception as err:
                logger.warning(f"TheTVDB {media.media_id} 第 {season['number']} 季摘要加载失败：{err}")
                return None
        # 限制并发；不会为同一剧集同时发送数十个 TVDB 请求。
        with ThreadPoolExecutor(max_workers=4) as executor:
            details = list(executor.map(load, seasons))
        summaries = {item["season_number"]: item for item in media.season_info}
        for detail in details:
            if detail is None:
                continue
            number = detail["number"]
            summaries[number] = to_season(detail, getattr(settings, "TMDB_LOCALE", "zh"))
            media.seasons[number] = [episode["number"] for episode in episode_rows(detail)]
            year = (summaries[number].get("air_date") or "")[:4]
            if year:
                media.season_years[number] = year
        media.season_info = [summaries[number] for number in sorted(summaries)]
        if all(detail is not None for detail in details):
            media.number_of_episodes = sum(len(episodes) for number, episodes in media.seasons.items() if number > 0)

    def _episode_with_translation(self, episode, language):
        translated = None
        available = set(episode.get("nameTranslations") or []) | set(episode.get("overviewTranslations") or [])
        if language != "eng" and language in available and episode.get("id"):
            try:
                translated = self._api_get(f"episodes/{episode['id']}/translations/{language}")
            except Exception as err:
                logger.warning(f"TheTVDB 单集 {episode['id']} 翻译不可用，保留原文：{err}")
        return to_episode(episode, translated)

    def _load_season(self, series_id: int, season_number: int):
        if not self._enabled or series_id <= 0 or season_number < 0:
            raise ValueError("无效的剧集或季号，或插件未启用")
        series = self._api_get(f"series/{series_id}/extended", meta="translations")
        if str(series.get("id")) != str(series_id):
            raise ValueError("TVDB 剧集身份不匹配")
        season = next((item for item in self._native_seasons(series) if item["number"] == season_number), None)
        if season is None:
            raise ValueError("TVDB 默认编排中未找到该季")
        detail = self._season_info(series_id, season)
        language = language_code(getattr(settings, "TMDB_LOCALE", "zh"))
        result = to_season(detail, language)
        with ThreadPoolExecutor(max_workers=4) as executor:
            result["episodes"] = list(executor.map(
                lambda episode: self._episode_with_translation(episode, language), episode_rows(detail)
            ))
        return schemas.MediaSeason.model_validate(result)

    async def tvdb_season(self, series_id: int, season_number: int) -> schemas.Response[schemas.MediaSeason]:
        """按需读取 TVDB 默认编排中的一季，提供逐集标题、剧情和原生图片地址。"""
        try:
            season = await asyncio.to_thread(self._load_season, series_id, season_number)
            return schemas.Response(success=True, data=season)
        except Exception as err:
            logger.error(f"TheTVDB {series_id} 第 {season_number} 季加载失败：{err}")
            return schemas.Response(success=False, message=str(err))

    def tvdb_discover(self, apikey: str, mtype: str = "series",
                      company: int = None, contentRating: int = None, country: str = "usa",
                      genre: int = None, lang: str = "eng", sort: str = "score", sortType: str = "desc",
                      status: int = None, year: int = None,
                      page: int = 1, count: int = 30) -> schemas.Response[List[schemas.MediaInfo]]:
        """
        获取显式统一响应格式的 TheTVDB 探索数据。
        """

        def __movie_to_media(movie_info: dict) -> schemas.MediaInfo:
            """
            电影数据转换为MediaInfo
            {
              "id": 353554,
              "name": "I Am: Celine Dion",
              "slug": "i-am-celine-dion",
              "image": "/banners/v4/movie/353554/posters/6656173b5167f.jpg",
              "nameTranslations": null,
              "overviewTranslations": null,
              "aliases": null,
              "score": 22669,
              "runtime": 102,
              "status": {
                "id": 5,
                "name": "Released",
                "recordType": "movie",
                "keepUpdated": true
              },
              "lastUpdated": "2024-08-10 10:37:05",
              "year": "2024"
            }
            """
            return schemas.MediaInfo(
                type="电影",
                title=movie_info.get("name"),
                year=movie_info.get("year"),
                title_year=f"{movie_info.get('name')} ({movie_info.get('year')})",
                media_source=MediaSource.TVDB,
                media_id=str(movie_info.get("id")),
                poster_path=image_url(movie_info.get("image")),
                popularity=movie_info.get("score"),
                runtime=movie_info.get("runtime"),
                overview=movie_info.get("overview")
            )

        def __series_to_media(series_info: dict) -> schemas.MediaInfo:
            """
            电视剧数据转换为MediaInfo
            {
              "id": 79399,
              "name": "Who Wants to Be a Superhero?",
              "slug": "who-wants-to-be-a-superhero",
              "image": "https://artworks.thetvdb.com/banners/posters/79399-1.jpg",
              "nameTranslations": null,
              "overviewTranslations": null,
              "aliases": null,
              "firstAired": "2006-07-27",
              "lastAired": "2007-09-06",
              "nextAired": "",
              "score": 190,
              "status": {
                "id": 2,
                "name": "Ended",
                "recordType": "series",
                "keepUpdated": false
              },
              "originalCountry": "usa",
              "originalLanguage": "eng",
              "defaultSeasonType": 1,
              "isOrderRandomized": false,
              "lastUpdated": "2022-01-16 03:32:39",
              "averageRuntime": 45,
              "episodes": null,
              "overview": "",
              "year": "2006"
            }
            """
            return schemas.MediaInfo(
                type="电视剧",
                title=series_info.get("name"),
                year=series_info.get("year"),
                title_year=f"{series_info.get('name')} ({series_info.get('year')})",
                media_source=MediaSource.TVDB,
                media_id=str(series_info.get("id")),
                release_date=series_info.get("firstAired"),
                poster_path=image_url(series_info.get("image")),
                popularity=series_info.get("score"),
                runtime=series_info.get("averageRuntime"),
                overview=series_info.get("overview")
            )

        if apikey != settings.API_TOKEN:
            return schemas.Response(success=True, data=[])
        if mtype not in {"series", "movies"} or page < 1 or not 1 <= count <= 500:
            return schemas.Response(success=False, message="无效的类型或分页参数", data=[])
        start = (page - 1) * count
        req_page, offset = divmod(start, 500)
        try:
            result = self.__request(
                mtype,
                company=company,
                contentRating=contentRating,
                country=country,
                genre=genre,
                lang=lang,
                sort=sort,
                sortType=sortType,
                status=status,
                year=year,
                page=req_page
            ) or []
            if offset + count > 500 and len(result) == 500:
                result = result + (self.__request(
                    mtype, company=company, contentRating=contentRating, country=country,
                    genre=genre, lang=lang, sort=sort, sortType=sortType,
                    status=status, year=year, page=req_page + 1,
                ) or [])
        except Exception as err:
            logger.error(str(err))
            return schemas.Response(success=False, message=str(err), data=[])
        if not result:
            return schemas.Response(success=True, data=[])
        if mtype == "movies":
            results = [__movie_to_media(movie) for movie in result]
        else:
            results = [__series_to_media(series) for series in result]
        return schemas.Response(
            success=True,
            data=results[offset:offset + count],
        )

    @staticmethod
    def tvdb_filter_ui() -> List[dict]:
        """
        TheTVDB过滤参数UI配置
        """
        # 国家字典
        country_dict = {
            "usa": "美国",
            "chn": "中国",
            "jpn": "日本",
            "kor": "韩国",
            "ind": "印度",
            "fra": "法国",
            "ger": "德国",
            "ita": "意大利",
            "esp": "西班牙",
            "uk": "英国",
            "aus": "澳大利亚",
            "can": "加拿大",
            "rus": "俄罗斯",
            "bra": "巴西",
            "mex": "墨西哥",
            "arg": "阿根廷",
            "other": "其他"
        }

        cuntry_ui = [
            {
                "component": "VChip",
                "props": {
                    "filter": True,
                    "tile": True,
                    "value": key
                },
                "text": value
            } for key, value in country_dict.items()
        ]

        # 原始语种字典
        lang_dict = {
            "eng": "英语",
            "chi": "中文",
            "jpn": "日语",
            "kor": "韩语",
            "hin": "印地语",
            "fra": "法语",
            "deu": "德语",
            "ita": "意大利语",
            "spa": "西班牙语",
            "por": "葡萄牙语",
            "rus": "俄语",
            "other": "其他"
        }

        lang_ui = [
            {
                "component": "VChip",
                "props": {
                    "filter": True,
                    "tile": True,
                    "value": key
                },
                "text": value
            } for key, value in lang_dict.items()
        ]

        # 风格字典
        genre_dict = {
            "1": "Soap",
            "2": "Science Fiction",
            "3": "Reality",
            "4": "News",
            "5": "Mini-Series",
            "6": "Horror",
            "7": "Home and Garden",
            "8": "Game Show",
            "9": "Food",
            "10": "Fantasy",
            "11": "Family",
            "12": "Drama",
            "13": "Documentary",
            "14": "Crime",
            "15": "Comedy",
            "16": "Children",
            "17": "Animation",
            "18": "Adventure",
            "19": "Action",
            "21": "Sport",
            "22": "Suspense",
            "23": "Talk Show",
            "24": "Thriller",
            "25": "Travel",
            "26": "Western",
            "27": "Anime",
            "28": "Romance",
            "29": "Musical",
            "30": "Podcast",
            "31": "Mystery",
            "32": "Indie",
            "33": "History",
            "34": "War",
            "35": "Martial Arts",
            "36": "Awards Show"
        }

        genre_ui = [
            {
                "component": "VChip",
                "props": {
                    "filter": True,
                    "tile": True,
                    "value": key
                },
                "text": value
            } for key, value in genre_dict.items()
        ]

        # 排序字典
        sort_dict = {
            "score": "评分",
            "firstAired": "首播日期",
            "name": "名称"
        }

        sort_ui = [
            {
                "component": "VChip",
                "props": {
                    "filter": True,
                    "tile": True,
                    "value": key
                },
                "text": value
            } for key, value in sort_dict.items()
        ]

        return [
            {
                "component": "div",
                "props": {
                    "class": "flex justify-start items-center"
                },
                "content": [
                    {
                        "component": "div",
                        "props": {
                            "class": "mr-5"
                        },
                        "content": [
                            {
                                "component": "VLabel",
                                "text": "类型"
                            }
                        ]
                    },
                    {
                        "component": "VChipGroup",
                        "props": {
                            "model": "mtype"
                        },
                        "content": [
                            {
                                "component": "VChip",
                                "props": {
                                    "filter": True,
                                    "tile": True,
                                    "value": "movies"
                                },
                                "text": "电影"
                            },
                            {
                                "component": "VChip",
                                "props": {
                                    "filter": True,
                                    "tile": True,
                                    "value": "series"
                                },
                                "text": "电视剧"
                            }
                        ]
                    }
                ]
            },
            {
                "component": "div",
                "props": {
                    "class": "flex justify-start items-center"
                },
                "content": [
                    {
                        "component": "div",
                        "props": {
                            "class": "mr-5"
                        },
                        "content": [
                            {
                                "component": "VLabel",
                                "text": "风格"
                            }
                        ]
                    },
                    {
                        "component": "VChipGroup",
                        "props": {
                            "model": "genre"
                        },
                        "content": genre_ui
                    }
                ]
            },
            {
                "component": "div",
                "props": {
                    "class": "flex justify-start items-center"
                },
                "content": [
                    {
                        "component": "div",
                        "props": {
                            "class": "mr-5"
                        },
                        "content": [
                            {
                                "component": "VLabel",
                                "text": "国家"
                            }
                        ]
                    },
                    {
                        "component": "VChipGroup",
                        "props": {
                            "model": "country"
                        },
                        "content": cuntry_ui
                    }
                ]
            },
            {
                "component": "div",
                "props": {
                    "class": "flex justify-start items-center"
                },
                "content": [
                    {
                        "component": "div",
                        "props": {
                            "class": "mr-5"
                        },
                        "content": [
                            {
                                "component": "VLabel",
                                "text": "语言"
                            }
                        ]
                    },
                    {
                        "component": "VChipGroup",
                        "props": {
                            "model": "lang"
                        },
                        "content": lang_ui
                    }
                ]
            },
            {
                "component": "div",
                "props": {
                    "class": "flex justify-start items-center"
                },
                "content": [
                    {
                        "component": "div",
                        "props": {
                            "class": "mr-5"
                        },
                        "content": [
                            {
                                "component": "VLabel",
                                "text": "排序"
                            }
                        ]
                    },
                    {
                        "component": "VChipGroup",
                        "props": {
                            "model": "sort"
                        },
                        "content": sort_ui
                    }
                ]
            }
        ]

    @eventmanager.register(ChainEventType.DiscoverSource)
    def discover_source(self, event: Event):
        """
        注册 TheTVDB 探索数据源
        """
        if not self._enabled or not self._api_key:
            return
        event_data: DiscoverSourceEventData = event.event_data
        tvdb_source = schemas.DiscoverMediaSource(
            name="TheTVDB",
            media_source=MediaSource.TVDB,
            api_path=f"plugin/TvdbDiscover/tvdb_discover?apikey={settings.API_TOKEN}",
            filter_params={
                "mtype": "series",
                "company": None,
                "contentRating": None,
                "country": "usa",
                "genre": None,
                "lang": "eng",
                "sort": "score",
                "sortType": "desc",
                "status": None,
                "year": None,
            },
            filter_ui=self.tvdb_filter_ui()
        )
        if not event_data.extra_sources:
            event_data.extra_sources = [tvdb_source]
        else:
            event_data.extra_sources.append(tvdb_source)

    def stop_service(self):
        """
        退出插件
        """
        pass
