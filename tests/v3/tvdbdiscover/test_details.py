"""使用离线 TVDB 响应验证原生身份、分页、季集信息和认证重试。"""

import asyncio
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app import schemas
from app.plugins import tvdbdiscover
from app.plugins.tvdbdiscover import TvdbDiscover
from app.plugins.tvdbdiscover.details import to_media, to_season, to_episode
from app.sdk.config import settings
from app.schemas.types import MediaSource, MediaType


@pytest.fixture
def plugin():
    instance = TvdbDiscover()
    instance.init_plugin({'enabled': True, 'api_key': 'test-key', 'proxy': False})
    yield instance
    TvdbDiscover._api_get.cache_clear()
    TvdbDiscover._TvdbDiscover__get_token.cache_clear()
    instance.stop_service()


@pytest.fixture
def series():
    return {
        'id': 42, 'name': 'Original', 'firstAired': '2024-01-01', 'score': 9000,
        'image': '/banners/poster.jpg', 'defaultSeasonType': 1,
        'translations': {'nameTranslations': [{'language': 'zho', 'name': '中文剧名'}]},
        'seasons': [
            {'id': 100, 'number': 0, 'type': {'id': 1}},
            {'id': 101, 'number': 1, 'type': {'id': 1}},
            {'id': 999, 'number': 1, 'type': {'id': 2}},
        ],
    }


@pytest.fixture
def season():
    return {
        'id': 101, 'seriesId': 42, 'number': 1,
        'translations': {'overviewTranslations': [{'language': 'zho', 'overview': '季简介'}]},
        'episodes': [
            {'id': 12, 'seriesId': 42, 'seasonNumber': 1, 'number': 2, 'name': 'Second', 'aired': '2024-01-08'},
            {'id': 11, 'seriesId': 42, 'seasonNumber': 1, 'number': 1, 'name': 'First',
             'overview': 'Original plot', 'aired': '2024-01-01', 'image': 'banners/episode.jpg',
             'nameTranslations': ['zho'], 'overviewTranslations': ['zho']},
            {'id': 11, 'seasonNumber': 1, 'number': 1},
            {'id': 99, 'seasonNumber': 2, 'number': 1},
        ],
    }


def test_native_media_identity_and_popularity(series):
    media = to_media(series, MediaType.TV)
    assert media.media_source == MediaSource.TVDB and media.media_id == '42'
    assert media.title == '中文剧名' and media.original_title == 'Original'
    assert media.poster_path == 'https://artworks.thetvdb.com/banners/poster.jpg'
    assert media.popularity == 9000 and media.vote_average is None
    assert media.number_of_seasons == 1
    assert [s['season_number'] for s in media.season_info] == [0, 1]


def test_movie_translation_fallback_and_remote_ids():
    media = to_media({'id': 7, 'name': 'Original', 'translations': {
        'nameTranslations': [{'language': 'eng', 'name': 'English'}]},
        'first_release': {'date': '2020-05-01'},
        'remoteIds': [{'sourceName': 'IMDB', 'id': 'tt123'}],
        'characters': [{'personName': 'Actor', 'peopleType': 'Actor', 'personId': 123}],
    }, MediaType.MOVIE, 'ja')
    assert media.title == 'English' and media.year == '2020'
    assert media.imdb_id == 'tt123' and media.media_id == '7'
    assert 'id' not in media.actors[0]


def test_season_and_episode_projection(season):
    summary = to_season(season)
    assert summary['episode_count'] == 2 and summary['air_date'] == '2024-01-01'
    assert summary['overview'] == '季简介'
    episode = to_episode(season['episodes'][1], {'name': '第一集'})
    assert episode['name'] == '第一集' and episode['overview'] == 'Original plot'
    assert episode['still_path'] == 'https://artworks.thetvdb.com/banners/episode.jpg'


@pytest.mark.parametrize('page,count,pages,first', [(1, 30, [0], 0), (17, 30, [0, 1], 480), (18, 30, [1], 510), (2, 500, [1], 500)])
@pytest.mark.parametrize('mtype', ['series', 'movies'])
def test_discovery_pagination(plugin, monkeypatch, page, count, pages, first, mtype):
    requested = []

    def request(path, **params):
        requested.append(params['page'])
        return [{'id': i + 1, 'name': f'Title {i}', 'score': 1234, 'image': '/banners/a.jpg'}
                for i in range(params['page'] * 500, (params['page'] + 1) * 500)]

    monkeypatch.setattr(plugin, '_api_get', request)
    response = plugin.tvdb_discover(settings.API_TOKEN, mtype=mtype, page=page, count=count)
    assert response.success and requested == pages
    assert [m.media_id for m in response.data] == [str(i + 1) for i in range(first, first + count)]
    assert response.data[0].popularity == 1234
    assert 'vote_average' not in response.data[0].model_fields_set


@pytest.mark.parametrize('params', [{'page': 0}, {'count': 0}, {'count': 501}, {'mtype': 'unknown'}])
def test_invalid_pagination_never_requests(plugin, monkeypatch, params):
    request = Mock(side_effect=AssertionError('unexpected request'))
    monkeypatch.setattr(plugin, '_api_get', request)
    assert plugin.tvdb_discover(settings.API_TOKEN, **params).success is False
    request.assert_not_called()


def test_token_refresh_and_success_cache(plugin, monkeypatch):
    responses = iter([SimpleNamespace(status_code=401, ok=False),
                      SimpleNamespace(status_code=200, ok=True, json=lambda: {'status': 'success', 'data': {'id': 42}})])
    logins, headers = [], []

    class Client:
        def __init__(self, **kwargs):
            self.headers = kwargs['headers']

        def post_res(self, url, **kwargs):
            logins.append(kwargs['json']['apikey'])
            token = f'token-{len(logins)}'
            return SimpleNamespace(ok=True, json=lambda: {'data': {'token': token}})

        def get_res(self, url, **kwargs):
            headers.append(self.headers['Authorization'])
            return next(responses)

    monkeypatch.setattr(tvdbdiscover, 'RequestUtils', Client)
    assert plugin._api_get('series/42/extended') == {'id': 42}
    assert plugin._api_get('series/42/extended') == {'id': 42}
    assert logins == ['test-key', 'test-key']
    assert headers == ['Bearer token-1', 'Bearer token-2']


@pytest.mark.parametrize('source,identity', [(MediaSource.TMDB, '42'), (MediaSource.TVDB, '../42'), (MediaSource.TVDB, '0')])
def test_recognition_ignores_other_sources_and_invalid_ids(plugin, monkeypatch, source, identity):
    request = Mock(side_effect=AssertionError('unexpected request'))
    monkeypatch.setattr(plugin, '_api_get', request)
    assert plugin.recognize_media(media_source=source, media_id=identity, mtype=MediaType.TV) is None
    request.assert_not_called()


def test_recognition_fills_default_season_order(plugin, monkeypatch, series, season):
    def request(path, **kwargs):
        if path.startswith('series/'):
            return series
        if path == 'seasons/100/extended':
            return {'id': 100, 'seriesId': 42, 'number': 0, 'episodes': []}
        assert path == 'seasons/101/extended'
        return season

    monkeypatch.setattr(plugin, '_api_get', request)
    media = plugin.recognize_media(media_source=MediaSource.TVDB, media_id='42', mtype=MediaType.TV)
    assert media.seasons == {0: [], 1: [1, 2]}
    assert media.number_of_episodes == 2 and media.season_years[1] == '2024'
    async_media = asyncio.run(plugin.async_recognize_media(media_source=MediaSource.TVDB, media_id='42', mtype=MediaType.TV))
    assert async_media.media_id == media.media_id


def test_season_endpoint_loads_only_available_translations(plugin, monkeypatch, series, season):
    paths = []

    def request(path, **kwargs):
        paths.append(path)
        if path.startswith('series/'):
            return series
        if path.startswith('seasons/'):
            return season
        assert path == 'episodes/11/translations/zho'
        return {'name': '第一集'}

    monkeypatch.setattr(plugin, '_api_get', request)
    monkeypatch.setattr(settings, 'TMDB_LOCALE', 'zh')
    response = asyncio.run(plugin.tvdb_season(42, 1))
    assert response.success and isinstance(response.data, schemas.MediaSeason)
    assert [ep.episode_number for ep in response.data.episodes] == [1, 2]
    assert response.data.episodes[0].name == '第一集'
    assert response.data.episodes[0].overview == 'Original plot'
    assert len(paths) == 3
    assert plugin.get_api()[1]['auth'] == 'bear'


@pytest.mark.parametrize('field,value', [('id', 999), ('seriesId', 999), ('number', 9)])
def test_season_identity_mismatch_rejected(plugin, monkeypatch, series, season, field, value):
    wrong = deepcopy(season)
    wrong[field] = value
    monkeypatch.setattr(plugin, '_api_get', lambda path, **kwargs: series if path.startswith('series/') else wrong)
    response = asyncio.run(plugin.tvdb_season(42, 1))
    assert not response.success and response.data is None


def test_failed_translation_preserves_episode(plugin, monkeypatch, season):
    monkeypatch.setattr(plugin, '_api_get', Mock(side_effect=ConnectionError('offline')))
    result = plugin._episode_with_translation(season['episodes'][1], 'zho')
    assert result['name'] == 'First' and result['overview'] == 'Original plot'


def test_reinitialization_clears_enabled_state_and_module_registration(plugin):
    assert set(plugin.get_module()) == {'recognize_media', 'async_recognize_media'}
    plugin.init_plugin()
    assert not plugin.get_state() and plugin.get_module() == {}
