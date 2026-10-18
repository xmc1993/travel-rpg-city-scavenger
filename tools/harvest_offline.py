#!/usr/bin/env python3
# 离线素材库采集：对指定城市做 类目×关键词×等级 广度扫描，去重、仅留带坐标的真实 POI，
# 抽取 hook（真实描述首句），写入 offline/<slug>.json。可重复运行以刷新。
import json, os, re, sys, subprocess, datetime

OUT_DIR = '/opt/travel-rpg/offline'
CATS = ['公园乐园', '文物古迹', '自然风光', '博物馆', '主题乐园', '古镇古村',
        '动植物园', '海洋馆', '纪念馆', '地标建筑', '露营地', '温泉', '园林', '寺庙道观']
KWS = ['公园', '湖', '寺', '庙', '博物馆', '老街', '胡同', '山', '塔', '桥',
       '广场', '园林', '江', '河', '林', '园', '古镇', '夜景', '网红', '遗址',
       '故居', '书院', '钟楼', '鼓楼', '城墙', '门', '渡', '堤']
LEVELS = ['5', '4', '3']
CITY_SLUG = {'杭州': 'hangzhou', '北京': 'beijing'}


def raw(city, cat=None, kw=None, lvl=None):
    cmd = ['flyai', 'search-poi', '--city-name', city]
    if cat:
        cmd += ['--category', cat]
    if kw:
        cmd += ['--keyword', kw]
    if lvl:
        cmd += ['--poi-level', lvl]
    try:
        o = subprocess.run(cmd, capture_output=True, text=True, timeout=40,
                           env=os.environ.copy())
        d = json.loads((o.stdout or '').strip() or '{}')
        return (d.get('data') or {}).get('itemList') or []
    except Exception:
        return []


def hook_of(desc):
    if not desc:
        return ''
    s = re.split(r'[\n。]', desc.strip())[0].strip()
    return (s or desc.strip())[:60]


def harvest(city):
    pool = {}          # id -> item
    tags = {}          # id -> set(keyword hits)
    def absorb(items, tag=None):
        for it in items:
            pid = str(it.get('id') or '')
            if not pid:
                continue
            if not (it.get('latitude') and it.get('longitude')):
                continue
            pool.setdefault(pid, it)
            if tag:
                tags.setdefault(pid, set()).add(tag)
    for c in CATS:
        absorb(raw(city, cat=c))
    for k in KWS:
        absorb(raw(city, kw=k), tag=k)
    for lv in LEVELS:
        absorb(raw(city, lvl=lv), tag=f'{lv}A')
    pois = []
    for pid, it in pool.items():
        try:
            lat = float(it['latitude']); lng = float(it['longitude'])
        except Exception:
            continue
        desc = it.get('description') or ''
        pois.append({
            'id': pid,
            'name': it.get('name', ''),
            'category': it.get('category', ''),
            'address': it.get('address', ''),
            'lat': lat, 'lng': lng,
            'level': it.get('poiLevel'),
            'free': it.get('freePoiStatus') == 'FREE',
            'jumpUrl': it.get('jumpUrl', ''),
            'hook': hook_of(desc),
            'tags': sorted(tags.get(pid, set())),
        })
    pois.sort(key=lambda p: p['name'])
    slug = CITY_SLUG.get(city, re.sub(r'\W+', '', city) or 'city')
    os.makedirs(OUT_DIR, exist_ok=True)
    path = os.path.join(OUT_DIR, slug + '.json')
    doc = {
        'city': city, 'aliases': [city, city + '市'],
        'generated_at': datetime.datetime.now().isoformat(timespec='seconds'),
        'count': len(pois), 'pois': pois,
    }
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(doc, f, ensure_ascii=False, indent=0)
    os.replace(tmp, path)
    print(f'{city}: POI={len(pois)} -> {path} ({os.path.getsize(path)} bytes)')
    lv = {}
    for p in pois:
        lv[str(p['level'])] = lv.get(str(p['level']), 0) + 1
    print(f'  level分布={lv}')
    have_hook = sum(1 for p in pois if p['hook'])
    print(f'  有hook={have_hook}/{len(pois)}  样例={[p["name"] for p in pois[:6]]}')


if __name__ == '__main__':
    for city in (sys.argv[1:] or ['杭州', '北京']):
        harvest(city)
    print('HARVEST_DONE')
