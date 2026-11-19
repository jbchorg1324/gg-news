"""경기남부 뉴스 모니터링 RSS 수집기.

뉴스 제목/발행시각만 수집합니다. 사고 발생시각, 실제 긴급성, 조회수를 추정하지 않습니다.
기존 index.html 이 읽는 data/news.json 스키마를 유지합니다.
"""
from __future__ import annotations

import calendar
import datetime as dt
import json
import re
import time
from difflib import SequenceMatcher
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import quote
from zoneinfo import ZoneInfo

import feedparser
import requests

KST = ZoneInfo('Asia/Seoul')
NOW = dt.datetime.now(dt.timezone.utc)
OUTPUT = Path('data/news.json')
# 뉴스지도의 핵심 도시: 용인시 추가
CORE = ('수원', '화성', '오산', '용인')
SOUTH = ('수원', '화성', '오산', '용인', '평택', '안산', '안성', '이천', '과천')
CORE_QUERY = {
    '수원': '수원시 OR 수원경찰 OR 수원시청 OR 영통구',
    '화성': '화성시 OR 화성경찰 OR 동탄 OR 향남',
    '오산': '오산시 OR 오산경찰 OR 세교',
    '용인': '용인시 OR 용인시청 OR 용인경찰 OR 처인구 OR 기흥구 OR 수지구',
}
# 제목 기준: '경찰'은 경찰 관련 보도이며 범죄 확정 분류가 아닙니다.
INCIDENT_WORDS = (
    '화재', '불길', '불이 나', '큰불', '산불', '폭발', '붕괴', '추락', '사망',
    '숨져', '숨진', '숨졌', '부상', '중상', '사고', '충돌', '전복', '침수',
    '실종', '구조', '대피', '검거', '체포', '살인', '강도', '흉기', '폭행',
    '재난', '지진', '폭우', '호우경보', '정전', '가스누출', '경찰', '소방',
    '방화', '절도', '사기', '마약', '성범죄', '성폭력', '폭행', '폭력',
    '피싱', '보이스피싱', '수사', '입건', '기소', '송치', '구속', '실종자',
    '인명피해', '응급', '구급', '교통사고', '뺑소니', '음주운전', '사건', '범죄',
    '119', '112', '구조대', '경찰서', '소방서', '테러', '유괴', '납치'
)
INCIDENT_RE = re.compile('|'.join(re.escape(w) for w in INCIDENT_WORDS), re.I)
AREA_ALIASES = {
    '수원': ('수원', '영통구', '팔달구', '장안구', '권선구', '광교'),
    '화성': ('화성', '동탄', '향남', '봉담', '병점', '남양읍', '정남면'),
    '오산': ('오산', '세교', '오산천'),
    '용인': ('용인', '처인구', '기흥구', '수지구', '동백동', '죽전동', '역북동'),
    '평택': ('평택', '송탄', '고덕국제'),
    '안산': ('안산', '단원구', '상록구'),
    '안성': ('안성',), '이천': ('이천',), '과천': ('과천',),
}
AREA_RE = {city: re.compile('|'.join(map(re.escape, names))) for city, names in AREA_ALIASES.items()}
# 검색어를 나누어 RSS 한 회의 결과 제한 때문에 개별 사건 보도가 밀리는 것을 줄입니다.
SEARCH_GROUPS = (
    '(경찰 OR 사건 OR 범죄 OR 수사 OR 검거 OR 체포 OR 구속 OR 송치 OR 입건 OR 사기 OR 절도 OR 폭행 OR 흉기)',
    '(화재 OR 소방 OR 119 OR 불길 OR 산불 OR 폭발 OR 붕괴 OR 대피 OR 구조 OR 실종)',
    '(교통사고 OR 추락 OR 사망 OR 숨져 OR 부상 OR 충돌 OR 전복 OR 침수 OR 음주운전 OR 뺑소니)',
)
# 조용한 키워드도 검색하지만 '경찰 지원'을 범죄로 단정하지 않습니다.
RETENTION_HOURS = 72
GENERAL_HOURS = 36

PLACES = {
    '수원': (37.2636, 127.0286), '화성': (37.1995, 126.8312),
    '용인': (37.2411, 127.1776), '오산': (37.1498, 127.0772),
    '안산': (37.3219, 126.8309), '평택': (36.9921, 127.1129),
    '이천': (37.272, 127.435), '과천': (37.4292, 126.9877),
}
SESSION = requests.Session()
SESSION.headers.update({'User-Agent': 'Mozilla/5.0 (compatible; gg-news-monitor/3.0)'})


def iso(value: dt.datetime) -> str:
    return value.astimezone(KST).isoformat(timespec='seconds')


def rss(query: str, city: str, window_hours: int, alert_only: bool = False):
    url = ('https://news.google.com/rss/search?q=' + quote(f'({query}) when:4d')
           + '&hl=ko&gl=KR&ceid=KR:ko')
    last_error = None
    for attempt in range(2):
        try:
            response = SESSION.get(url, timeout=(8, 22))
            response.raise_for_status()
            feed = feedparser.parse(response.content)
            if feed.bozo and not feed.entries:
                raise ValueError('RSS 파싱 실패')
            result = []
            for entry in feed.entries[:100]:
                published = entry.get('published_parsed')
                if published:
                    date = dt.datetime.fromtimestamp(calendar.timegm(published), dt.timezone.utc)
                elif entry.get('published'):
                    date = parsedate_to_datetime(entry.published).astimezone(dt.timezone.utc)
                else:
                    continue
                if not NOW - dt.timedelta(hours=window_hours) <= date <= NOW + dt.timedelta(minutes=10):
                    continue
                title = re.sub(r'<[^>]+>', '', entry.get('title', '')).strip()
                source = entry.get('source') or {}
                publisher = source.get('title', '') if hasattr(source, 'get') else ''
                if publisher and title.endswith(' - ' + publisher):
                    title = title[:-(len(publisher) + 3)]
                link = entry.get('link', '')
                if not title or not link.startswith('https://'):
                    continue
                if alert_only and (not INCIDENT_RE.search(title) or not AREA_RE[city].search(title)):
                    continue
                result.append({'city': city, 'title': title, 'url': link,
                               'publisher': publisher or 'Google 뉴스 RSS',
                               'publishedAt': iso(date), 'incident': bool(alert_only)})
            return result, None
        except (requests.RequestException, ValueError, OverflowError) as exc:
            last_error = str(exc)[:140]
            if attempt == 0:
                time.sleep(1)
    return [], last_error or '수집 실패'


def normalized(title: str) -> str:
    """분류용 정규화. 수치·지역·기관명은 유지해 다른 사건이 합쳐지지 않게 합니다."""
    title = re.sub(r'\[[^\]]{1,24}\]', '', title).lower()
    return re.sub(r'[^0-9a-z가-힣]', '', title)


def dedupe(articles):
    """같은 도시의 거의 동일한 제목만 합칩니다. 조회수·사건 동일성은 추정하지 않습니다."""
    out = []
    seen = {}  # 도시별 비교 목록
    for item in sorted(articles, key=lambda x: x['publishedAt'], reverse=True):
        key = normalized(item.get('title', ''))
        if not key:
            continue
        city = item.get('city', '')
        try:
            published = dt.datetime.fromisoformat(item['publishedAt'])
        except (ValueError, KeyError, TypeError):
            continue
        matches = seen.setdefault(city, [])
        duplicate = False
        for prior_key, prior_time in matches:
            if abs((published - prior_time).total_seconds()) > 24 * 3600:
                continue
            if key == prior_key:
                duplicate = True
                break
            # 같은 도시라도 특정 날짜·수치가 다른 후속 기사는 보존합니다.
            numbers = re.findall(r'\d+', key)
            prior_numbers = re.findall(r'\d+', prior_key)
            if numbers != prior_numbers:
                continue
            if (min(len(key), len(prior_key)) >= 26 and
                    SequenceMatcher(None, key, prior_key).ratio() >= .90):
                duplicate = True
                break
        if not duplicate:
            matches.append((key, published))
            out.append(item)
    return out


def get_weather(previous):
    old = previous.get('weather', {})
    current_day = NOW.astimezone(KST).date().isoformat()
    if (NOW.astimezone(KST).hour != 8 and old.get('updatedAt', '')[:10] == current_day
            and old.get('cities')):
        return old, None
    result = {}
    failures = []
    for city, (lat, lon) in PLACES.items():
        try:
            resp = SESSION.get('https://api.open-meteo.com/v1/forecast', params={
                'latitude': lat, 'longitude': lon,
                'current': 'temperature_2m,weather_code',
                'hourly': 'precipitation_probability',
                'timezone': 'Asia/Seoul', 'forecast_days': 1,
            }, timeout=(6, 13))
            resp.raise_for_status()
            data = resp.json()
            now = data['current']
            target = dt.datetime.fromisoformat(now['time'][:13] + ':00')
            times = [dt.datetime.fromisoformat(v) for v in data['hourly']['time']]
            idx = min(range(len(times)), key=lambda i: abs(times[i] - target))
            result[city] = {'temperature': now['temperature_2m'],
                            'weatherCode': now['weather_code'],
                            'precipitationProbability': data['hourly']['precipitation_probability'][idx]}
        except (requests.RequestException, ValueError, KeyError, IndexError, TypeError) as exc:
            failures.append(f'{city}: {str(exc)[:65]}')
            if city in old.get('cities', {}):
                result[city] = old['cities'][city]
    if not result:
        return old, '; '.join(failures) or '날씨 수집 실패'
    return {'updatedAt': iso(NOW), 'source': 'Open-Meteo', 'cities': result}, ('; '.join(failures) if failures else None)


def parse_date(item):
    try:
        value = dt.datetime.fromisoformat(item['publishedAt'])
        return value if value.tzinfo else value.replace(tzinfo=dt.timezone.utc)
    except (ValueError, KeyError, TypeError):
        return None


def keep_recent(items, hours):
    cutoff = NOW - dt.timedelta(hours=hours)
    return [a for a in items if isinstance(a, dict) and (d := parse_date(a))
            and cutoff <= d.astimezone(dt.timezone.utc) <= NOW + dt.timedelta(minutes=10)]


def trim_by_city(items, cap):
    """시간순 보존. 특정 도시 보도가 다른 도시를 전부 밀지 않도록 도시별 상한 적용."""
    counts, output = {}, []
    for a in sorted(dedupe(items), key=lambda x: x['publishedAt'], reverse=True):
        c = a.get('city')
        if counts.get(c, 0) >= cap:
            continue
        counts[c] = counts.get(c, 0) + 1
        output.append(a)
    return output


def main():
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    try:
        previous = json.loads(OUTPUT.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        previous = {}
    previous_articles = previous.get('articles', [])
    errors, status = [], {}
    general, alerts = [], []
    successful_requests = 0

    for city in CORE:
        items, err = rss(CORE_QUERY[city], city, GENERAL_HOURS)
        if err:
            errors.append(f'일반뉴스 {city}: {err}')
            status[city] = '일반뉴스 오류 (이전 기사 보존)'
        else:
            successful_requests += 1
            status[city] = '정상'
            general.extend(items)

    # 핵심 4개 도시는 경찰·소방·교통사고를 각각 검색합니다.
    # 주변 5개 도시는 핵심 검색어를 합친 한 번의 RSS로 수집합니다.
    for city in SOUTH:
        searches = SEARCH_GROUPS if city in CORE else (
            '(경찰 OR 화재 OR 사건 OR 범죄 OR 사고 OR 소방 OR 구조 OR 검거 OR 실종 OR 붕괴 OR 추락)',
        )
        city_success = 0
        aliases = ' OR '.join(AREA_ALIASES[city][:4] if city in CORE else AREA_ALIASES[city][:2])
        for group in searches:
            items, err = rss(f'({aliases}) {group}', city, RETENTION_HOURS, alert_only=True)
            if err:
                errors.append(f'사건검색 {city}: {err}')
            else:
                successful_requests += 1
                city_success += 1
                alerts.extend(items)
        if city in CORE and city_success == 0:
            status[city] = (status[city] + ' · 사건검색 오류')

    if successful_requests == 0:
        raise RuntimeError('모든 뉴스 RSS 수집 실패: 기존 news.json 보존')

    # 신규 결과가 없다는 이유로 이전 사건을 삭제하지 않습니다.
    # 기사 발행시각 기준으로 72시간 동안 보존합니다.
    prior_incidents = [dict(a, incident=True) for a in previous_articles
                       if (a.get('incident') is True or INCIDENT_RE.search(a.get('title', '')))
                       and AREA_RE.get(a.get('city'), re.compile('(?!)')).search(a.get('title', ''))]
    saved_incidents = keep_recent(prior_incidents + alerts, RETENTION_HOURS)
    core_incidents = trim_by_city([a for a in saved_incidents if a.get('city') in CORE], 45)
    surrounding_incidents = trim_by_city([a for a in saved_incidents if a.get('city') not in CORE], 15)

    prior_general = [a for a in previous_articles if a.get('city') in CORE]
    core_general = trim_by_city(keep_recent(general + prior_general, GENERAL_HOURS), 65)

    # 같은 제목으로 일반/사건 검색에 중복된 경우 incident=True를 유지해야 합니다.
    merged = {}
    for article in core_general + core_incidents + surrounding_incidents:
        key = (article.get('city'), normalized(article.get('title', '')))
        if not key[1]:
            continue
        earlier = merged.get(key)
        if earlier is None or article.get('incident'):
            merged[key] = dict(article, incident=bool(article.get('incident') or
                                                       (earlier or {}).get('incident')))
    articles = sorted(merged.values(), key=lambda x: x['publishedAt'], reverse=True)

    weather, weather_err = get_weather(previous)
    if weather_err:
        errors.append('날씨: ' + weather_err)
    data = {
        'updatedAt': iso(NOW),
        'newsSource': 'Google 뉴스 RSS (기사 제목·발행시각·링크)',
        'status': status, 'errors': errors,
        'articles': articles, 'weather': weather,
        'monitoring': {
            'incidentRetentionHours': RETENTION_HOURS,
            'generalRetentionHours': GENERAL_HOURS,
            'incidentCount': sum(bool(a.get('incident')) for a in articles),
            'note': '기사 발행시각 기준 보존. 사건 발생시각 및 실제 긴급성은 검증되지 않음',
        },
    }
    OUTPUT.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(f'완료: 총 {len(articles)}건 / 사건·경찰 관련 보도 {data["monitoring"]["incidentCount"]}건 / 수집 오류 {len(errors)}건')


if __name__ == '__main__':
    main()
