import json, subprocess, urllib.request, urllib.parse, re, os

HERE = os.path.dirname(os.path.abspath(__file__))
OFF = os.path.join(HERE, 'offline')
IMG = os.path.join(OFF, 'img')
os.makedirs(IMG, exist_ok=True)

def raw(city, kw=None):
    cmd = ['flyai', 'search-poi', '--city-name', city]
    if kw: cmd += ['--keyword', kw]
    o = subprocess.run(cmd, capture_output=True, text=True, timeout=40, env=os.environ.copy())
    try:
        return (json.loads((o.stdout or '').strip() or '{}').get('data') or {}).get('itemList') or []
    except Exception:
        return []

def best(items, spot):
    if not items: return None
    def score(it):
        n = it.get('name') or ''; s = 0
        if spot in n: s += 100
        elif len(spot) >= 3 and spot[:3] in n: s += 60
        elif spot[:2] in n: s += 40
        if it.get('mainPic'): s += 5
        if it.get('latitude'): s += 5
        if (it.get('description') or ''): s += 3
        if it.get('poiLevel'): s += 3
        return s
    return sorted(items, key=score, reverse=True)[0]

def hook(desc):
    if not desc: return ''
    s = re.split(r'[\n。]', desc.strip())[0].strip()
    return (s or desc.strip())[:70]

def dl(url, name):
    ext = '.jpg'
    low = url.lower()
    if '.png' in low: ext = '.png'
    if 'format,webp' in low or '.webp' in low: ext = '.webp'
    fn = name + ext
    path = os.path.join(IMG, fn)
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        b = urllib.request.urlopen(req, timeout=25).read()
        with open(path, 'wb') as f: f.write(b)
        return '/img/' + fn, len(b)
    except Exception as e:
        return None, str(e)[:50]

def build_route(rid, meta, spots):
    nodes = []; anchor = None
    for i, sp in enumerate(spots):
        kw = sp.get('kw') or sp['name']
        b = best(raw(meta['city'], kw), kw)
        if not b:
            print('  !! MISS', rid, sp['name']); continue
        try:
            la, ln = float(b['latitude']), float(b['longitude'])
        except Exception:
            print('  !! NOCOORD', rid, sp['name']); continue
        disp = sp.get('disp') or re.sub(r'[（(【].*?[）)】]', '', b.get('name') or '').strip() or sp['name']
        img, info = dl(b.get('mainPic') or '', '%s_%02d' % (rid, i))
        if not img:
            print('  !! NOIMG', rid, sp['name'], info)
        nodes.append({
            'node_id': '%s-%d' % (rid, i), 'name': disp, 'flyai_id': str(b.get('id')),
            'category': b.get('category') or '', 'address': b.get('address') or '',
            'lat': la, 'lng': ln, 'hook': hook(b.get('description') or ''),
            'desc': (b.get('description') or '')[:500], 'img': img,
            'jumpUrl': b.get('jumpUrl') or '',
        })
        print('  ok %-10s d=%s img=%s' % (sp['name'], info if isinstance(info, int) else info, bool(img)))
    route = dict(meta); route['id'] = rid; route['nodes'] = nodes
    return route

ROUTES = [
  ('westlake', {'name': '西湖十景', 'subtitle': '一环湖山，半部江南', 'city': '杭州', 'intro': '沿白堤苏堤慢行，十处宋元名景次第展开，每一处都藏着一段旧事。'},
     [{'name': '断桥残雪'}, {'name': '白堤'}, {'name': '平湖秋月'}, {'name': '曲院风荷'}, {'name': '花港观鱼'},
      {'name': '苏堤春晓'}, {'name': '柳浪闻莺'}, {'name': '雷峰塔'}, {'name': '三潭印月'}, {'name': '双峰插云'}]),
  ('lingyin', {'name': '灵隐禅踪', 'subtitle': '山寺晨钟，千年一线', 'city': '杭州', 'intro': '从飞来峰造像到灵隐、永福、法喜，一条香径串起东南佛国。'},
     [{'name': '灵隐寺'}, {'name': '飞来峰'}, {'name': '永福寺'}, {'name': '法喜寺'}]),
  ('yujie', {'name': '南宋御街', 'subtitle': '御街市井，老杭州味', 'city': '杭州', 'intro': '清河坊、胡雪岩故居、吴山天风，走进八百年前临安城的烟火里。'},
     [{'name': '河坊街', 'disp': '清河坊'}, {'name': '胡雪岩故居'}, {'name': '南宋御街'}, {'name': '城隍阁'}, {'name': '吴山天风'}]),
  ('beijing', {'name': '北京中轴线', 'subtitle': '宫阙连营，帝王之轴', 'city': '北京', 'intro': '自故宫登景山、穿什刹海、至钟鼓楼，一线贯穿元明清帝都脊梁。'},
     [{'name': '故宫博物院'}, {'name': '景山公园'}, {'name': '什刹海'}, {'name': '南锣鼓巷'}, {'name': '北海公园'}, {'name': '北京钟鼓楼'}, {'name': '万宁桥'}]),
]

def main():
    out = []
    for rid, meta, spots in ROUTES:
        print('=== %s (%s) ===' % (meta['name'], rid))
        r = build_route(rid, meta, spots)
        if r['nodes']:
            out.append(r)
    doc = {'routes': out}
    tmp = os.path.join(OFF, 'routes.json') + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(doc, f, ensure_ascii=False, indent=1)
    os.replace(tmp, os.path.join(OFF, 'routes.json'))
    print('routes:', [(r['id'], len(r['nodes'])) for r in out])
    print('CURATE_DONE')

main()
