"""Collect public news RSS + openly attributed weather into data/news.json.
Designed for GitHub Actions. Never invents articles or incident occurrence times.
"""
import datetime as dt
import json
import os
import re
import time
from collections import defaultdict
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import quote
from zoneinfo import ZoneInfo

import feedparser
import requests

KST = ZoneInfo('Asia/Seoul')
NOW = dt.datetime.now(dt.timezone.utc)
OUTPUT = Path('data/news.json')
CITIES = ['수원', '화성', '오산']
CITY_QUERY = {
    '수원': '수원시 OR 수원경찰 OR 수원시청',
    '화성': '화성시 OR 화성경찰 OR 동탄',
    '오산': '오산시 OR 오산경찰 OR 오산시청',
}
INCIDENT = re.compile(r'화재|사망|숨져|추락|교통사고|충돌|폭발|실종|침수|붕괴|구조|경찰|검거|긴급', re.I)
BREAKING = re.compile(r'속보|긴급|대피|큰불|붕괴|폭발|지진|호우경보|산불', re.I)
PLACES = {
    '수원': (37.2636,127.0286), '화성':(37.1995,126.8312),
    '용인':(37.2411,127.1776), '오산':(37.1498,127.0772),
    '안산':(37.3219,126.8309), '평택':(36.9921,127.1129),
    '이천':(37.272,127.435), '과천':(37.4292,126.9877),
}

def iso(d):
    return d.astimezone(KST).isoformat(timespec='seconds')

def fetch_city(city):
    q = f'({CITY_QUERY[city]}) when:2d'
    url = 'https://news.google.com/rss/search?q='+quote(q)+'&hl=ko&gl=KR&ceid=KR:ko'
    try:
        res=requests.get(url,timeout=22,headers={'User-Agent':'Mozilla/5.0 (compatible; gg-news/1.0)'})
        res.raise_for_status()
        feed=feedparser.parse(res.content)
        if feed.bozo and not feed.entries:
            raise RuntimeError('RSS parsing failed')
        out=[]
        for entry in feed.entries[:70]:
            date=None
            if entry.get('published_parsed'):
                date=dt.datetime.fromtimestamp(time.mktime(entry.published_parsed), dt.timezone.utc)
            elif entry.get('published'):
                date=parsedate_to_datetime(entry.published).astimezone(dt.timezone.utc)
            if not date or not NOW-dt.timedelta(hours=48) <= date <= NOW+dt.timedelta(minutes=10):
                continue
            title=re.sub(r'<[^>]+>','',entry.get('title','')).strip()
            publisher=entry.get('source',{}).get('title','') if entry.get('source') else ''
            if publisher and title.endswith(' - '+publisher): title=title[:-(len(publisher)+3)]
            link=entry.get('link','')
            if not title or not link.startswith('https://'): continue
            out.append({'city':city,'title':title,'url':link,'publisher':publisher or 'Google 뉴스 RSS', 'publishedAt':iso(date)})
        return out, None
    except Exception as ex:
        return [], str(ex)[:150]

def weather(previous):
    old=previous.get('weather',{})
    # Update around 08 KST, reuse last successful daily value the rest of the day.
    now_kst=NOW.astimezone(KST)
    if now_kst.hour != 8 and old.get('updatedAt') and old.get('cities'):
        return old, None
    result={}
    errors=[]
    for city,(lat,lon) in PLACES.items():
        try:
            data=requests.get('https://api.open-meteo.com/v1/forecast',params={
                'latitude':lat,'longitude':lon,'current':'temperature_2m,weather_code',
                'hourly':'precipitation_probability', 'timezone':'Asia/Seoul','forecast_days':1,
            },timeout=17).json()
            current=data['current']
            hour=current['time'][:13]+':00'
            times=data['hourly']['time']
            idx=min(range(len(times)),key=lambda i:abs(dt.datetime.fromisoformat(times[i])-dt.datetime.fromisoformat(hour)))
            result[city]={'temperature':current['temperature_2m'],'weatherCode':current['weather_code'],
                          'precipitationProbability':data['hourly']['precipitation_probability'][idx]}
        except Exception as ex:
            errors.append(city+': '+str(ex)[:70])
            if city in old.get('cities',{}):result[city]=old['cities'][city]
    if not result:
        return old, '; '.join(errors) or 'weather unavailable'
    return {'updatedAt':iso(NOW),'source':'Open-Meteo','cities':result}, '; '.join(errors) if errors else None

def main():
    OUTPUT.parent.mkdir(parents=True,exist_ok=True)
    try:previous=json.loads(OUTPUT.read_text(encoding='utf-8'))
    except (OSError,ValueError):previous={}
    all_items=[]
    errors=[]
    status={}
    for city in CITIES:
        articles,err=fetch_city(city)
        if err:
            errors.append(city+': '+err)
            # Preserve old items but never change their timestamps.
            articles=[a for a in previous.get('articles',[]) if a.get('city')==city]
            status[city]='오류 (이전 데이터 유지)'
        else:status[city]='정상'
        all_items.extend(articles)
    unique={}
    for a in all_items:
        key=(a['city'],re.sub(r'\s+','',a['title']).lower())
        if key not in unique or a['publishedAt']>unique[key]['publishedAt']:unique[key]=a
    articles=sorted(unique.values(),key=lambda a:a['publishedAt'],reverse=True)
    w,err=weather(previous)
    if err:errors.append('weather: '+err)
    data={'updatedAt':iso(NOW),'newsSource':'Google 뉴스 RSS (기사 제목·발행시각·링크)',
          'status':status,'errors':errors,'articles':articles[:180], 'weather':w}
    OUTPUT.write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print('Saved',len(articles),'articles; errors:',errors)

if __name__=='__main__':main()
