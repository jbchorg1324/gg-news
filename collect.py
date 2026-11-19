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
INCIDENT_WORDS = ('화재', '불길', '불이 나', '큰불', '산불', '폭발', '붕괴', '추락', '사망',
                  '숨져', '숨진', '부상', '중상', '사고', '충돌', '전복', '침수', '실종',
                  '구조', '대피', '검거', '체포', '살인', '강도', '흉기', '폭행',
                  '재난', '지진', '폭우', '호우경보', '정전', '가스누출')
INCIDENT_RE = re.compile('|'.join(re.escape(w) for w in INCIDENT_WORDS), re.I)
AREA_RE = {c: re.compile(re.escape(c)) for c in SOUTH}
# 구글 뉴스 검색에 나오는 시·군명과 제목을 동시에 확인해 유사 지명을 거릅니다.
ALERT_QUERY = '(화재 OR 교통사고 OR 추락 OR 붕괴 OR 폭발 OR 구조 OR 실종 OR 사망 OR 대피 OR 검거 OR 산불 OR 침수 OR 충돌 OR 사건)'
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
    url = ('https://news.google.com/rss/search?q=' + quote(f'({query}) when:2d')
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
            for entry in feed.entries[:90]:
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
                               'publishedAt': iso(date)})
            return result, None
        except (requests.RequestException, ValueError, OverflowError) as exc:
            last_error = str(exc)[:140]
            if attempt == 0:
                time.sleep(1)
    return [], last_error or '수집 실패'


def normalized(title: str) -> str:
    # 주요 단어를 없애지 않고 매체명·공백·일부 문장부호만 정리합니다.
    title = re.sub(r'\[[^\]]{1,24}\]', '', title).lower()
    return re.sub(r'[^0-9a-z가-힣]', '', title)


def dedupe(articles):
    out = []
    seen = {}
    for item in sorted(articles, key=lambda x: x['publishedAt'], reverse=True):
        key = normalized(item['title'])
        if not key:
            continue
        bucket = (item['city'], key[:10])
        existing = seen.setdefault(bucket, [])
        if any(key == k or (min(len(key), len(k)) >= 25 and
                              SequenceMatcher(None, key, k).ratio() >= .91)
               for k in existing):
            continue
        existing.append(key)
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


def main():
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    try:
        previous = json.loads(OUTPUT.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        previous = {}
    previous_articles = previous.get('articles', [])
    errors = []
    status = {}
    general = []
    alerts = []
    ok_count = 0
    for city in CORE:
        items, err = rss(CORE_QUERY[city], city, 48)
        if err:
            errors.append(f'일반뉴스 {city}: {err}')
            items = [a for a in previous_articles if a.get('city') == city]
            status[city] = '오류 (이전 데이터 유지)'
        else:
            ok_count += 1
            status[city] = '정상'
        general.extend(items)

    for city in SOUTH:
        items, err = rss(f'{city} {ALERT_QUERY}', city, 24, alert_only=True)
        if err:
            errors.append(f'사건검색 {city}: {err}')
            items = [a for a in previous_articles if a.get('city') == city
                     and INCIDENT_RE.search(a.get('title', ''))]
        else:
            ok_count += 1
        alerts.extend(items)

    # 모든 뉴스 수집이 실패한 경우 갱신시각을 거짓으로 갱신하지 않습니다.
    if ok_count == 0:
        print('모든 RSS 수집 실패: 기존 news.json 보존; ' + '; '.join(errors))
        raise RuntimeError('모든 뉴스 RSS 수집 실패; 기존 데이터를 보존했습니다')

    core_news = dedupe(general + [a for a in alerts if a['city'] in CORE])
    south_alerts = dedupe([a for a in alerts if a['city'] not in CORE])
    # 핵심지역 4개 도시의 뉴스가 충분히 표시되도록 한도를 늘렸습니다.
    articles = sorted(core_news[:180] + south_alerts[:60],
                      key=lambda a: a['publishedAt'], reverse=True)
    w, err = get_weather(previous)
    if err:
        errors.append('날씨: ' + err)
    data = {'updatedAt': iso(NOW),
            'newsSource': 'Google 뉴스 RSS (기사 제목·발행시각·링크)',
            'status': status, 'errors': errors, 'articles': articles,
            'weather': w}
    OUTPUT.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(f'완료: 총 {len(articles)}건 (핵심지역 {len(core_news[:180])}, 주변 사건 {len(south_alerts[:60])}) / 오류 {len(errors)}건')


if __name__ == '__main__':
    main()
