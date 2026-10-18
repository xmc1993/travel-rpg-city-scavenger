#!/usr/bin/env python3
# 旅行RPG剧情智能体 · 后端代理（纯标准库，单文件）
# 作用：隐藏百炼/飞猪两个 API Key；H5 前端只与本代理通信。
import os, json, re, math, time, hashlib, subprocess, threading, urllib.request, urllib.parse, urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# 读取同目录 .env 注入环境变量（不覆盖已有）
_env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env')
if os.path.exists(_env_path):
    with open(_env_path, encoding='utf-8') as _f:
        for _line in _f:
            _line = _line.strip()
            if _line and not _line.startswith('#') and '=' in _line:
                _k, _v = _line.split('=', 1)
                os.environ.setdefault(_k.strip(), _v.strip())

PORT = int(os.environ.get('PORT', '8788'))
MOCK = os.environ.get('MOCK') == '1'
WORKSPACE = os.environ.get('WORKSPACE_ID', 'llm-h3wk0m102rseo57k')
BASE = f'https://{WORKSPACE}.cn-beijing.maas.aliyuncs.com/api/v1/agentstudio'
AGENT_ID = os.environ.get('AGENT_ID', 'agent_01M4Z5V4KX8WMJ2FDSW9DTVC6Z')
ENV_ID = os.environ.get('ENV_ID', 'env_MzEyNDM4NzJjYzE1NDgwYj')
FLYAI_KEY = os.environ.get('FLYAI_API_KEY', '')
BAILIAN_KEY = os.environ.get('BAILIAN_API_KEY', '')

FLYAI_BIN = os.environ.get('FLYAI_BIN', 'flyai')

# 访问口令（比赛对外防白嫖额度）：设了 ACCESS_KEY 才启用；留空=不鉴权
ACCESS_KEY = os.environ.get('ACCESS_KEY', '').strip()
AUTH_TOKEN = hashlib.sha256(('trpg:' + ACCESS_KEY).encode('utf-8')).hexdigest()[:40] if ACCESS_KEY else ''
AUTH_COOKIE = 'trpg_auth'
AUTH_TTL = 30 * 86400
# 需要鉴权的接口（会创建/推进百炼会话、耗额度）
AUTH_REQUIRED = ('/api/session', '/api/chat', '/api/resume', '/api/finish', '/api/stream/')

# 局长（时间控制）：开局可选长度，映射到最大节点数；full=走完整条线路
LENGTH_PRESET = {'short': 3, 'standard': 5, 'epic': 8}

# 离线素材库（城市级）：优先离线组织剧情，缺失城市回退实时 flyai
OFFLINE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'offline')
OFFLINE_ENABLED = os.environ.get('OFFLINE') != '0'
_OFFLINE_CACHE = {}   # path -> {'mtime': float, 'doc': dict}

# 开局 prompt：硬性要求模型只从真实素材包中选点、保持地理连贯、只输出规定 JSON 对象
OPEN_PROMPT = (
    '你是"旅行RPG·城市拾遗"的剧情引擎。玩家当前位置：{city}{district}，经纬度 {lat},{lng}。\n'
    '下面是后端从离线素材库或飞猪真实检索得到、并已按玩家位置做距离过滤的【素材包】（均为真实存在的地点）：\n'
    '{pois_json}\n\n'
    '素材包每条可能含 hook（该地点的真实看点）与 seeds（预写剧情种子）；请把它们当作灵感，围绕对应地点展开剧情，'
    '但出现的地点名仍必须逐字取自素材包。\n'
    '请严格遵守以下硬性规则：\n'
    '1) 剧情中出现的每一个真实地点，都必须逐字取自上面素材包，且只能在输出里以 poi_id 引用；'
    '绝对禁止编造素材包以外的任何地点/店名/建筑/公园，哪怕听起来更合理也不行。'
    '注意：poi_id 只能写进 bound_poi_id 和各 target_poi_id 这些字段里，'
    '绝不能把 poi_id、素材包 等字样写进 story 或选项 text；文案里提到地点时直接用它的 name。\n'
    '2) 地理连贯：本节点绑定地点与每个分叉目标地点，都应是玩家此刻附近、步行可达的真实地点'
    '（距离见素材包 distance_m/walk_min，不要跨城区瞬移）。\n'
    '3) 模式为「{mode}」：探索型偏自由漫游、氛围与支线；闯关型把地点串成有目标的动线任务。\n'
    '4) 先写有代入感、有悬念的第一个剧情节点，再给 2-3 个分叉选项。\n'
    '5) 只输出一个 JSON 对象，不要任何解释文字、不要 markdown 代码块围栏。字段严格如下：\n'
    '{{"node_title":"","story":"","bound_poi_id":"素材包内的poi_id","choices":'
    '[{{"key":"A","text":"","target_poi_id":"素材包内的poi_id","walk_min":0}}],'
    '"checkin":{{"mode":"arrive或free","radius_m":80}}}}'
)

# 推进 prompt：每次基于"上一节点新位置重新检索的素材包"继续，杜绝瞬移与幻觉
RESUME_PROMPT = (
    '继续上一局《旅行RPG》。玩家刚选择了 {key}：{label}，前往 {target_name}'
    '（{target_lat},{target_lng}，距上一个位置约 {target_dist} 米），现在玩家已到达/正在靠近该地点。\n'
    '基于该新位置重新检索到的【素材包】（真实、已做距离过滤，每条可能含 hook/seeds 作灵感）：\n{pois_json}\n\n'
    '约束与开局完全一致：每个真实地点只能取自素材包并以 poi_id 引用、禁止任何编造、保持地理连贯、'
    '模式仍为「{mode}」。请生成下一个剧情节点。\n'
    '只输出与开局相同格式的单个 JSON 对象，不要解释文字、不要代码块围栏：\n'
    '{{"node_title":"","story":"","bound_poi_id":"...","choices":'
    '[{{"key":"A","text":"","target_poi_id":"...","walk_min":0}}],'
    '"checkin":{{"mode":"arrive或free","radius_m":80}}}}'
)

# 结局 prompt：达到局长上限或玩家主动结束时，收束为一个大结局节点（无分叉）
END_PROMPT = (
    '《旅行RPG》本局即将落幕。玩家「{mode}」线路为「{route}」，'
    '此程依次走过的真实地点：{trail}。\n'
    '请为这段以真实城市为舞台的冒险写一个有余韵、能收束伏笔的大结局：\n'
    '呼应开头与途中悬而未决的线索，点明玩家此刻所在的最后一个地点，情绪饱满但克制，'
    '不要引入素材/线路之外的新地点，也不要留待选项。\n'
    '只输出一个 JSON 对象，不要解释文字、不要代码块围栏：\n'
    '{{"ending":true,"node_title":"","story":"","summary":""}}'
)

# 进程级共享状态
PLAY = {}    # sid -> 开局消息是否已发出
PACKS = {}   # sid -> {'by_id': {poi_id: poi}, 'valid': set(poi_id), 'last_pos': (lat,lng), 'mode': str}
USED = {}    # sid -> 最近使用时间(epoch)，用于回收最旧会话

# 持久化：把 PLAY/PACKS/USED 落一份到 state.json，进程重启后可凭 sessionId 续局
STATE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'state.json')
KEEP_SESSIONS = int(os.environ.get('KEEP_SESSIONS', '10'))   # 自动回收：最多保留最近 N 局
_STATE_LOCK = threading.Lock()


def _ser_pack(p):
    """把 pack 序列化成可 JSON 化的 dict（set->list）。"""
    q = dict(p)
    if isinstance(q.get('valid'), (set, frozenset)):
        q['valid'] = sorted(q['valid'])
    return q


def _prune_locked():
    """回收：1) 去掉与 PACKS 不同步的孤儿 PLAY/USED；2) 按最近使用时间只保留最近 KEEP_SESSIONS 局。
    调用方须持锁。返回被删 sid 列表。"""
    dropped = set()
    # 先清理孤儿（有 play/used 但没有 pack 的历史残留）
    for sid in list(PLAY.keys()):
        if sid not in PACKS:
            PLAY.pop(sid, None); USED.pop(sid, None); dropped.add(sid)
    for sid in list(USED.keys()):
        if sid not in PACKS:
            USED.pop(sid, None); dropped.add(sid)
    if len(PACKS) <= KEEP_SESSIONS:
        return list(dropped)
    # 以 USED 降序；缺时间戳的视为最旧。保留前 KEEP_SESSIONS 个
    order = sorted(PACKS.keys(), key=lambda s: USED.get(s, 0), reverse=True)
    for sid in order[KEEP_SESSIONS:]:
        PACKS.pop(sid, None); PLAY.pop(sid, None); USED.pop(sid, None)
        dropped.add(sid)
    return list(dropped)


def save_state():
    """原子写 state.json；先持锁裁剪+快照，再在锁外落盘（不长期占锁）。"""
    try:
        with _STATE_LOCK:
            dropped = _prune_locked()
            data = {'play': dict(PLAY), 'used': dict(USED),
                    'packs': {sid: _ser_pack(p) for sid, p in PACKS.items()}}
        tmp = STATE_PATH + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False)
        os.replace(tmp, STATE_PATH)
        return dropped
    except Exception:
        return []


def load_state():
    """启动时回填 PLAY/PACKS/USED（list->set、坐标 list->tuple），并裁剪到上限。"""
    if not os.path.exists(STATE_PATH):
        return
    try:
        with open(STATE_PATH, encoding='utf-8') as f:
            data = json.load(f)
    except Exception:
        return
    for sid, v in (data.get('play') or {}).items():
        PLAY[sid] = bool(v)
    for sid, ts in (data.get('used') or {}).items():
        try:
            USED[sid] = float(ts)
        except Exception:
            USED[sid] = 0.0
    for sid, p in (data.get('packs') or {}).items():
        if not isinstance(p, dict):
            continue
        if isinstance(p.get('valid'), list):
            p['valid'] = set(p['valid'])
        for key in ('base', 'last_pos'):
            if isinstance(p.get(key), list):
                p[key] = tuple(p[key])
        PACKS[sid] = p
    with _STATE_LOCK:
        _prune_locked()


def set_pack(sid, pack):
    """线程安全地写入 pack，并刷新最近使用时间。"""
    with _STATE_LOCK:
        PACKS[sid] = pack
        USED[sid] = time.time()


def set_play(sid):
    """线程安全地标记开局消息已发出，并刷新最近使用时间。"""
    with _STATE_LOCK:
        PLAY[sid] = True
        USED[sid] = time.time()


def mock_pois():
    names = ['示例公园', '钟楼', '老街市集', '河边码头', '古寺']
    return [{'poi_id': f'M{i}', 'flyai_id': f'M{i}', 'name': n, 'category': '示例',
             'address': 'MOCK 地址', 'lat': 30.0 + i * 0.001, 'lng': 120.0 + i * 0.001,
             'distance_m': 200 + i * 150, 'walk_min': 3 + i,
             'highlight': 'MOCK 内置地点', 'free': True}
            for i, n in enumerate(names)]


MOCK_NODE = {
    'node_title': '节点一 · 河边旧码头',
    'story': '傍晚的河面泛着碎金。你在桥头，风里有桂花香。',
    'bound_poi_id': 'M3',
    'choices': [
        {'key': 'A', 'text': '去钟楼', 'target_poi_id': 'M1', 'walk_min': 5},
        {'key': 'B', 'text': '去老街市集', 'target_poi_id': 'M2', 'walk_min': 9},
    ],
    'checkin': {'mode': 'free', 'radius_m': 80},
}


def http_json(method, url, headers=None, body=None, timeout=30):
    data = body.encode('utf-8') if isinstance(body, str) else body
    req = urllib.request.Request(url, data=data, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode('utf-8', 'replace')
    except urllib.error.HTTPError as e:
        # 把 4xx/5xx 状态码作为返回值交调用方判断（如会话失效 404 → 触发重建），不再抛异常
        try:
            payload = e.read().decode('utf-8', 'replace')
        except Exception:
            payload = ''
        return e.code, payload


def bailian_headers():
    return {'Authorization': 'Bearer ' + BAILIAN_KEY, 'Content-Type': 'application/json'}


def haversine_m(lat1, lng1, lat2, lng2):
    R = 6371000.0
    p = math.pi / 180
    a = (0.5 - math.cos((lat2 - lat1) * p) / 2
         + math.cos(lat1 * p) * math.cos(lat2 * p) * (1 - math.cos((lng2 - lng1) * p)) / 2)
    return 2 * R * math.asin(math.sqrt(max(0.0, min(1.0, a))))


def forward_geocode(city):
    """仅有城市名时，用 Nominatim 正查一个城市中心点，作为距离基准。"""
    url = ('https://nominatim.openstreetmap.org/search?format=jsonv2&limit=1&q='
           + urllib.parse.quote(city))
    try:
        st, txt = http_json('GET', url, {'User-Agent': 'travel-rpg-contest-demo/1.0'})
        if st == 200:
            arr = json.loads(txt)
            if arr:
                return float(arr[0]['lat']), float(arr[0]['lon'])
    except Exception:
        pass
    return None


# —— 按类型的预写剧情种子库（城市级均衡，不偏袒个别 POI）——
TYPE_SEEDS = {
    '湖':   ['湖面倒影里藏着一条通往旧时的路', '有人在此沉下一件未送出的信物'],
    '公园': ['园中一座长椅只在特定时刻才会遇见关键人物', '清晨的雾气遮住了本该看见的线索'],
    '寺':   ['晨钟里混进了一句不属于僧人的话', '一炷香未烧完，愿就还不了'],
    '庙':   ['殿后古柏上系满褪色的许愿红绳', '守庙人认得你，却不说为什么'],
    '博物馆': ['一件展品今晚本不该在这里', '讲解员多讲了一句史书上没有的细节'],
    '古镇': ['更夫的梆子声比约定的时辰早了一刻', '石桥下有人用你听得懂却想不起的方言唤你'],
    '老街': ['某扇半掩的门后，摆着你失物原样的橱窗', '老铺掌柜递来一张不该给陌生人的字条'],
    '胡同': ['院门上的门墩刻着与你相关的暗记', '一阵穿堂风送来半句喊你的乳名'],
    '山':   ['半山亭里留有前一位登山人未写完的笔记', '山径的分岔口，路牌被人为转过方向'],
    '塔':   ['每上一层，塔内的光就暗一分、旧一年', '塔铃无风自动，像是在数什么'],
    '桥':   ['桥洞下刻着一串只对某人有意义的编号', '走过桥的另一端，时间会少掉一截'],
    '广场': ['人群里一个与你同行的人，忽然不见了', '地砖拼花正中，藏着半枚信物'],
    '园林': ['月洞门后是园中园，也是画中未画的一景', '假山石缝里卡着一封被雨水泡皱的信'],
    '遗迹': ['断墙下埋着不属于这个朝代的器物', '风过废墟，似有旧时的市声'],
    '故居': ['书房的墨还没干，主人却"不在"', '一件私物暴露了主人不为人知的另一面'],
    '门':   ['城门启闭有暗号，说错便进不去', '门阙下的石狮口中，衔着一枚钥匙'],
    '堤':   ['沿堤第七棵树下，坐着会给你线索的人', '堤上有回音，重复了你没出口的话'],
    '温泉': ['泉眼旁流传一个只在泡汤时才能听的故事', '热气散去前的刹那，你瞥见不该看见的'],
    '动物园': ['一只异常亲近你的动物，像是在引你去某处', '饲养员欲言又止，提到"它今天不对劲"'],
    '主题乐园': ['游乐设施停运后，园区还有"没关门"的一角', '遗失物招领处，有你掉落却从未承认的东西'],
    '地标': ['地标夜景的某一扇窗，正为你亮着', '登顶的人说，俯瞰时能认出命中注定的方向'],
    '夜景': ['夜色替你遮住了追踪者，也遮住了目标', '灯火阑珊处，有个熟悉又陌生的人影'],
    '纪念馆': ['一件捐赠品背后，是一个不能公开的名字', '留言簿最后一页，有人替你写了下一句'],
}


def _seeds_for(poi):
    """依据 POI 的 category/name/tags/hook 命中的类型，汇集 2-3 条预写剧情种子。"""
    blob = ' '.join([str(poi.get('category', '')), str(poi.get('name', '')),
                     ' '.join(poi.get('tags', []) or []), str(poi.get('hook', ''))])
    out = []
    for key, lines in TYPE_SEEDS.items():
        if key in blob:
            out.extend(lines)
        if len(out) >= 3:
            break
    return out[:3]


def _load_offline_docs():
    """扫描 offline/*.json，按 mtime 缓存。返回 {canonical_city: doc}。"""
    if not os.path.isdir(OFFLINE_DIR):
        return {}
    docs = {}
    for fn in os.listdir(OFFLINE_DIR):
        if not fn.endswith('.json'):
            continue
        path = os.path.join(OFFLINE_DIR, fn)
        try:
            mt = os.path.getmtime(path)
        except Exception:
            continue
        c = _OFFLINE_CACHE.get(path)
        if not c or c['mtime'] != mt:
            try:
                with open(path, encoding='utf-8') as f:
                    doc = json.load(f)
                _OFFLINE_CACHE[path] = {'mtime': mt, 'doc': doc}
            except Exception:
                continue
            c = _OFFLINE_CACHE[path]
        doc = c['doc']
        if isinstance(doc, dict) and doc.get('city'):
            docs[doc['city']] = doc
    return docs


def _match_offline(city_raw):
    """把传入城市串（可能是'杭州西湖区'）匹配到某个离线城市。返回 doc 或 None。"""
    if not OFFLINE_ENABLED or not city_raw:
        return None
    name = str(city_raw).strip()
    docs = _load_offline_docs()
    if not docs:
        return None
    # 1) 精确/别名命中
    for city, doc in docs.items():
        if name == city or name in (doc.get('aliases') or []):
            return doc
    # 2) 包含关系：如 '杭州西湖区' 含 '杭州'
    best = None
    for city, doc in docs.items():
        if city and (city in name or name in city):
            if best is None or len(city) > len(best[0]):
                best = (city, doc)
    return best[1] if best else None


def _offline_index(doc):
    """把离线 doc 转成与 _fetch_pois_raw 同构的 {poi_id: poi}（不带距离）。"""
    seen = {}
    for p in (doc.get('pois') or []):
        pid = str(p.get('id') or '')
        try:
            lat = float(p['lat']); lng = float(p['lng'])
        except Exception:
            continue
        if not pid:
            continue
        seen[pid] = {
            'poi_id': pid, 'flyai_id': pid,
            'name': p.get('name', ''), 'category': p.get('category', ''),
            'address': p.get('address', ''),
            'lat': lat, 'lng': lng,
            'highlight': p.get('hook', '') or _highlight(p.get('desc', '')),
            'hook': p.get('hook', ''), 'tags': p.get('tags', []) or [],
            'free': bool(p.get('free')),
            'jumpUrl': p.get('jumpUrl', ''),
        }
    return seen


# —— 精选线路（经典案例）：线路 id → 有序明星 POI 池，含本地配图 ——
ROUTES_PATH = os.path.join(OFFLINE_DIR, 'routes.json')
_ROUTES_CACHE = {'mtime': None, 'by_id': {}}


def _load_routes():
    if not os.path.exists(ROUTES_PATH):
        return {}
    try:
        mt = os.path.getmtime(ROUTES_PATH)
    except Exception:
        return dict(_ROUTES_CACHE['by_id'])
    if _ROUTES_CACHE['mtime'] != mt:
        try:
            with open(ROUTES_PATH, encoding='utf-8') as f:
                doc = json.load(f)
            _ROUTES_CACHE['by_id'] = {r['id']: r for r in doc.get('routes', []) if r.get('id')}
            _ROUTES_CACHE['mtime'] = mt
        except Exception:
            pass
    return dict(_ROUTES_CACHE['by_id'])


def _route_index(route):
    """把线路节点转成与 _fetch_pois_raw 同构的 {node_id: poi}（带配图/详细描述）。"""
    seen = {}
    for nd in (route.get('nodes') or []):
        pid = str(nd.get('node_id') or nd.get('flyai_id') or '')
        try:
            lat = float(nd['lat']); lng = float(nd['lng'])
        except Exception:
            continue
        if not pid:
            continue
        seen[pid] = {
            'poi_id': pid, 'flyai_id': str(nd.get('flyai_id') or pid),
            'name': nd.get('name', ''), 'category': nd.get('category', ''),
            'address': nd.get('address', ''), 'lat': lat, 'lng': lng,
            'hook': nd.get('hook', ''), 'desc': nd.get('desc', ''),
            'highlight': nd.get('hook', '') or _highlight(nd.get('desc', '')),
            'img': nd.get('img', ''), 'jumpUrl': nd.get('jumpUrl', ''), 'free': True,
        }
    return seen


def _flyai_raw(city, category=None, keyword=None):
    """调用 flyai CLI 做一次检索，返回 itemList（原始条目）。"""
    cmd = [FLYAI_BIN, 'search-poi', '--city-name', city]
    if category:
        cmd += ['--category', category]
    if keyword:
        cmd += ['--keyword', keyword]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=40, env=os.environ.copy())
    except Exception:
        return []
    txt = (out.stdout or '').strip()
    if not txt:
        return []
    try:
        d = json.loads(txt)
    except Exception:
        return []
    if str(d.get('status')) not in ('0', 'success', 'SUCCESS', 'ok', 'OK'):
        # 不同版本 status 语义可能不同；有 itemList 就继续用
        pass
    return (d.get('data') or {}).get('itemList') or []


def _highlight(desc):
    if not desc:
        return ''
    first = re.split(r'[\n。]', desc)[0].strip()
    return (first or desc)[:80]


def _fetch_pois_raw(city, cats):
    """按城市名调用 flyai 检索，去重后返回 {poi_id: poi}（不含距离）。"""
    seen = {}
    for c in cats:
        for it in _flyai_raw(city, category=c):
            pid = str(it.get('id') or '')
            if not pid or pid in seen:
                continue
            try:
                plat, plng = float(it['latitude']), float(it['longitude'])
            except Exception:
                continue
            seen[pid] = {
                'poi_id': pid, 'flyai_id': pid,
                'name': it.get('name', ''), 'category': it.get('category', c),
                'address': it.get('address', ''),
                'lat': plat, 'lng': plng,
                'highlight': _highlight(it.get('description', '')),
                'free': it.get('freePoiStatus') == 'FREE',
                'jumpUrl': it.get('jumpUrl', ''),
            }
    return seen


def build_pack(lat, lng, city, mode='探索型', radius_m=None, limit=10, route_id=None):
    """素材包来源优先级：精选线路 route_id > 城市离线库 > 实时 flyai。
    距离原点：玩家真实坐标 > Nominatim > POI 坐标中位数。只要拿到 POI 即可开局。"""
    base_r = radius_m or (3000 if mode == '探索型' else 6000)
    cats = ['公园乐园', '文物古迹', '自然风光'] if mode == '探索型' else ['公园乐园', '文物古迹', '主题乐园']
    src = 'live'
    seen = {}
    if route_id:
        route = _load_routes().get(route_id)
        if route:
            seen = _route_index(route)
            src = 'route'
    if not seen:
        doc = _match_offline(city)
        if doc:
            seen = _offline_index(doc)
            src = 'offline'
    if not seen:
        seen = _fetch_pois_raw(city, cats)
        src = 'live'
    pois_all = list(seen.values())
    if not pois_all:
        return None
    # 确定距离原点：优先玩家真实坐标；否则 Nominatim；再否则用检索结果坐标中位数兜底
    origin = None
    if lat is not None and lng is not None:
        try:
            origin = (float(lat), float(lng))
        except Exception:
            origin = None
    if origin is None:
        origin = forward_geocode(city)
    if origin is None:
        lats = sorted(p['lat'] for p in pois_all)
        lngs = sorted(p['lng'] for p in pois_all)
        origin = (lats[len(lats) // 2], lngs[len(lngs) // 2])
    lat, lng = origin
    for p in pois_all:
        d = haversine_m(lat, lng, p['lat'], p['lng'])
        p['distance_m'] = int(d)
        p['walk_min'] = max(1, int(round(d / 80)))
    pois = sorted(pois_all, key=lambda x: x['distance_m'])
    # 渐进放宽半径，保证至少能凑够可玩数量
    chosen = [p for p in pois if p['distance_m'] <= base_r]
    for relax in (1.5, 2.5, 6.0):
        if len(chosen) >= min(6, limit):
            break
        chosen = [p for p in pois if p['distance_m'] <= base_r * relax]
    chosen = chosen[:limit] or pois[:limit]
    if not chosen:
        return None
    by_id = {p['poi_id']: p for p in chosen}
    return {'base': (lat, lng), 'city': city, 'pois': chosen, 'by_id': by_id,
            'valid': set(by_id), 'source': src, 'route_id': route_id}


def pack_for_prompt(pack):
    """给模型的精简素材包（省 token，逐字可引用）。离线/线路均可附带看点与预写种子。"""
    slim = []
    for p in pack['pois']:
        entry = {'poi_id': p['poi_id'], 'name': p['name'], 'category': p['category'],
                 'distance_m': p['distance_m'], 'walk_min': p['walk_min']}
        # 线路场景有真实详细描述，优先喂更实的背景；否则用 hook 首句
        rich = (p.get('desc') or '').strip()
        hk = (p.get('hook') or p.get('highlight') or '').strip()
        if rich:
            entry['about'] = rich[:120]
        elif hk:
            entry['hook'] = hk[:48]
        seeds = _seeds_for(p)
        if seeds:
            entry['seeds'] = seeds[:2]
        slim.append(entry)
    return json.dumps(slim, ensure_ascii=False)


def extract_json_obj(text):
    """从模型回复里稳健地抽出最外层 JSON 对象。"""
    if not text:
        return None
    t = text.strip()
    t = re.sub(r'^```(?:json)?', '', t).strip()
    t = re.sub(r'```$', '', t).strip()
    i = t.find('{')
    if i < 0:
        return None
    depth = 0
    for j in range(i, len(t)):
        if t[j] == '{':
            depth += 1
        elif t[j] == '}':
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(t[i:j + 1])
                except Exception:
                    return None
    return None


def node_is_valid(node, valid_ids):
    if not isinstance(node, dict):
        return False
    if not node.get('story'):
        return False
    if node.get('bound_poi_id') not in valid_ids:
        return False
    chs = node.get('choices')
    if not isinstance(chs, list) or not chs:
        return False
    for c in chs:
        if not isinstance(c, dict) or c.get('target_poi_id') not in valid_ids:
            return False
    return True


_POI_LEAK = re.compile(
    r'[（(]?\s*(?:素材包)?\s*poi_?id\s*[:：]?\s*[\w\-]+\s*[)）]?',
    re.IGNORECASE)


def _clean(s):
    if not s:
        return s
    s = _POI_LEAK.sub('', s)
    return re.sub(r'[ \u3000]{2,}', ' ', s).strip()


def enrich_node(node, by_id):
    """把 poi_id 还原成前端渲染所需的真实地点信息（名称/坐标/距离/导航），并清洗文案里泄漏的 poi_id。"""
    def info(pid):
        p = by_id.get(pid) or {}
        return {'name': p.get('name', ''), 'lat': p.get('lat'), 'lng': p.get('lng'),
                'distance_m': p.get('distance_m'), 'walk_min': p.get('walk_min'),
                'category': p.get('category', ''), 'address': p.get('address', ''),
                'img': p.get('img', ''), 'hook': (p.get('hook') or p.get('highlight') or '')[:80],
                'jumpUrl': p.get('jumpUrl', '')}
    node['node_title'] = _clean(node.get('node_title'))
    node['story'] = _clean(node.get('story'))
    node['bound'] = info(node['bound_poi_id'])
    for c in node['choices']:
        c['text'] = _clean(c.get('text'))
        c.update(info(c['target_poi_id']))
    return node


def _routes_summary():
    """给首页用的线路摘要：id/名称/副标题/封面/景点数/前几景名与缩略图。"""
    out = []
    for rid, r in _load_routes().items():
        nodes = r.get('nodes') or []
        out.append({
            'id': rid, 'name': r.get('name', ''), 'subtitle': r.get('subtitle', ''),
            'city': r.get('city', ''), 'intro': r.get('intro', ''),
            'cover': (nodes[0].get('img') if nodes else ''), 'count': len(nodes),
            'previews': [{'name': n.get('name', ''), 'img': n.get('img', ''),
                          'hook': n.get('hook', '')} for n in nodes[:6]],
        })
    return out


def create_session(mode, city):
    if MOCK:
        return 'mock_' + str(os.getpid())
    body = json.dumps({
        'agent': {'type': 'agent', 'id': AGENT_ID},
        'environment_id': ENV_ID,
        'title': f'旅行RPG·{mode}·{city}',
        'environment_variables': {'FLYAI_API_KEY': FLYAI_KEY} if FLYAI_KEY else {},
    })
    st, txt = http_json('POST', BASE + '/sessions', bailian_headers(), body, timeout=60)
    if st not in (200, 201):
        raise RuntimeError(f'create session {st}: {txt[:200]}')
    return json.loads(txt)['id']


def send_event(sid, text):
    """向会话投递一条用户消息。返回 (status, text)。
    404 表示会话已被百炼回收，调用方应触发重建续玩。"""
    if MOCK:
        return 200, ''
    body = json.dumps({'input': [{'role': 'user', 'type': 'message',
                                  'content': [{'type': 'text', 'text': text}]}]})
    st, txt = http_json('POST', f'{BASE}/sessions/{sid}/events', bailian_headers(), body, timeout=60)
    return st, txt


def wait_idle(sid, timeout=200):
    import time
    end = time.time() + timeout
    while time.time() < end:
        st, txt = http_json('GET', f'{BASE}/sessions/{sid}', {'Authorization': 'Bearer ' + BAILIAN_KEY})
        if st == 200:
            s = json.loads(txt).get('status')
            if s == 'idle':
                return True
            if s == 'terminated':
                raise RuntimeError('session terminated')
        time.sleep(3)
    raise RuntimeError('wait idle timeout')


def rebuild_session(old_sid, pack, prompt):
    """百炼空闲会话会被回收；若投消息返回 404，则用保留的 city/mode/base 重建会话，
    发一条带当前剧情进度的开局 prompt，把素材包/进度迁移到新 sid。
    返回 (new_sid, st, txt)；非真 sid（mock）或重建失败时原样返回 (old_sid, st, txt)。"""
    if not old_sid or not old_sid.startswith('sesn_') or MOCK:
        return old_sid, 404, 'rebuild skipped'
    city = (pack or {}).get('city') or '杭州'
    mode = (pack or {}).get('mode') or '探索型'
    base = (pack or {}).get('base') or (None, None)
    last = (pack or {}).get('last_pos') or base
    new_sid = create_session(mode, city)
    cont = ('【剧情续玩·会话恢复】上一段冒险因会话回收而中断，现在无缝继续同一个故事，'
            '不要重新开局、不要打招呼。' + prompt)
    try:
        st, txt = send_event(new_sid, cont)
    except Exception as e:
        return new_sid, -1, str(e)
    set_pack(new_sid, pack)
    set_play(new_sid)
    save_state()
    with _STATE_LOCK:
        PACKS.pop(old_sid, None)
        PLAY.pop(old_sid, None)
        USED.pop(old_sid, None)
    return new_sid, st, txt


def reverse_geocode(lat, lng):
    url = (f'https://nominatim.openstreetmap.org/reverse?format=jsonv2&lat={lat}&lon={lng}'
           f'&accept-language=zh&zoom=12')
    st, txt = http_json('GET', url, {'User-Agent': 'travel-rpg-contest-demo/1.0'})
    if st != 200:
        raise RuntimeError('geocode http ' + str(st))
    a = json.loads(txt).get('address', {})
    city = a.get('city') or a.get('county') or a.get('state') or ''
    district = a.get('city_district') or a.get('district') or a.get('suburb') or a.get('town') or ''
    if not city and not district:
        raise RuntimeError('geocode empty')
    display = ' '.join(x for x in [a.get('state'), city, district] if x)
    return {'city': city, 'district': district, 'display': display}


def sse_write(wfile, obj):
    wfile.write(('data: ' + json.dumps(obj, ensure_ascii=False) + '\n\n').encode('utf-8'))
    wfile.flush()


def _forward_node(wfile, text, sid):
    """把一段 assistant 文本解析、校验、补全成结构化节点下发。返回是否成功下发节点。"""
    pack = PACKS.get(sid)
    valid = pack['valid'] if pack else set()
    by_id = pack['by_id'] if pack else {}
    node = extract_json_obj(text)
    if node and node.get('ending'):
        # 结局节点：无分叉，附最后到达地点的配图，标记本局结束
        last = (pack or {}).get('last_visited') or {}
        node['node_title'] = _clean(node.get('node_title'))
        node['story'] = _clean(node.get('story'))
        node['summary'] = _clean(node.get('summary'))
        node['choices'] = []
        node['bound'] = {'name': last.get('name', ''), 'img': last.get('img', ''),
                         'distance_m': 0, 'lat': last.get('lat'), 'lng': last.get('lng')}
        if pack is not None:
            pack['ended'] = True
        sse_write(wfile, {'type': 'node', 'node': node})
        return True
    if node and node_is_valid(node, valid):
        sse_write(wfile, {'type': 'node', 'node': enrich_node(node, by_id)})
        return True
    if node is not None:
        # 解析出 JSON 但引用了素材包外地点 -> 判定为幻觉，拒绝下发
        sse_write(wfile, {'type': 'error', 'message': '剧情引用了不存在的地点，已被拦截，请重试该步。'})
        return False
    # 完全没解析出 JSON：把原文作为降级提示，避免整局卡死
    if text and text.strip() not in ('就绪', '就绪。', 'OK', 'ok'):
        sse_write(wfile, {'type': 'error', 'message': '智能体未按格式返回，正在重试…'})
    return False


def stream_to_client(wfile, sid):
    seen = set()
    state = {'play_started': PLAY.get(sid, False)}

    def emit(ev):
        t = ev.get('type')
        if t == 'message' and ev.get('role') == 'assistant':
            if not (state['play_started'] or PLAY.get(sid, False)):
                return False
            text = ''.join(c.get('text', '') for c in ev.get('content', []) if c.get('type') == 'text')
            if text and text.strip() in ('就绪', '就绪。', 'OK', 'ok'):
                return False
            if _forward_node(wfile, text, sid):
                state['play_started'] = True
                return True
            state['play_started'] = True
            return False
        elif t == 'session_status':
            d = next((c for c in ev.get('content', []) if c.get('type') == 'data'), None)
            stt = (d or {}).get('data', {}).get('session_status', '')
            if stt in ('idle', 'terminated') and (state['play_started'] or PLAY.get(sid, False)):
                sse_write(wfile, {'type': 'done', 'stop_reason': (d or {}).get('data', {}).get('stop_reason')})
                return 'done'
        elif t == 'error':
            sse_write(wfile, {'type': 'error', 'message': (ev.get('error') or {}).get('message', 'unknown')})
        return False

    # 回填历史：只取最近一次玩家指令之后的 assistant 内容
    try:
        st, txt = http_json('GET', f'{BASE}/sessions/{sid}/events?limit=100',
                            {'Authorization': 'Bearer ' + BAILIAN_KEY})
        if st == 200:
            evs = json.loads(txt).get('data', [])
            play_idx = max((i for i, e in enumerate(evs)
                            if e.get('type') == 'message' and e.get('role') == 'user'),
                           default=-1)
            if play_idx >= 0:
                state['play_started'] = True
                got_msg = False
                for e in evs[play_idx + 1:]:
                    if e.get('id'):
                        seen.add(e['id'])
                    if e.get('type') == 'message' and e.get('role') == 'assistant':
                        text = ''.join(c.get('text', '') for c in e.get('content', []) if c.get('type') == 'text')
                        if _forward_node(wfile, text, sid):
                            got_msg = True
                    elif e.get('type') == 'session_status':
                        d = next((c for c in ev.get('content', []) if c.get('type') == 'data'), None)
                        stt = (d or {}).get('data', {}).get('session_status', '')
                        if stt in ('idle', 'terminated') and got_msg:
                            sse_write(wfile, {'type': 'done', 'stop_reason': (d or {}).get('data', {}).get('stop_reason')})
                            return
    except Exception:
        pass
    # 实时流（按行迭代：来一行给一行，避免 read(n) 攒不满阻塞；整帧解码不拆多字节中文）
    req = urllib.request.Request(f'{BASE}/sessions/{sid}/events/stream',
                                 headers={'Authorization': 'Bearer ' + BAILIAN_KEY,
                                          'Accept': 'text/event-stream'})
    buf = b''
    try:
        with urllib.request.urlopen(req, timeout=600) as r:
            for raw_line in r:
                if not raw_line:
                    break
                buf += raw_line
                while b'\n\n' in buf:
                    frame, buf = buf.split(b'\n\n', 1)
                    line = next((l for l in frame.split(b'\n') if l.startswith(b'data:')), None)
                    if not line:
                        continue
                    try:
                        ev = json.loads(line[5:].strip().decode('utf-8', 'replace'))
                    except Exception:
                        continue
                    if ev.get('id'):
                        if ev['id'] in seen:
                            continue
                        seen.add(ev['id'])
                    if emit(ev) == 'done':
                        return
    except Exception:
        pass
    sse_write(wfile, {'type': 'done'})


HTML_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'public', 'index.html')
_IMG_DIR = os.path.join(OFFLINE_DIR, 'img')


class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def log_message(self, *a):
        pass

    def _json(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(body)

    def _img(self, path):
        """安全提供 offline/img 下的本地配图：只取文件名，杜绝目录穿越。"""
        name = re.sub(r'[^A-Za-z0-9_\-.]', '', os.path.basename(urllib.parse.unquote(path)))
        ext = name.rsplit('.', 1)[-1].lower() if '.' in name else ''
        ctype = {'jpg': 'image/jpeg', 'jpeg': 'image/jpeg', 'png': 'image/png',
                 'webp': 'image/webp', 'gif': 'image/gif'}.get(ext)
        fp = os.path.join(_IMG_DIR, name)
        if not ctype or not os.path.isfile(fp):
            self._json(404, {'error': 'image not found'})
            return
        with open(fp, 'rb') as f:
            body = f.read()
        self.send_response(200)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'public, max-age=86400')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        n = int(self.headers.get('Content-Length') or 0)
        return json.loads(self.rfile.read(n) or b'{}')

    def _cookie(self, name):
        raw = self.headers.get('Cookie') or ''
        for part in raw.split(';'):
            k, _, v = part.strip().partition('=')
            if k == name:
                return urllib.parse.unquote(v)
        return ''

    def _authorized(self):
        # 未配置口令 -> 直接放行；否则校验 cookie 里的令牌
        if not ACCESS_KEY:
            return True
        return self._cookie(AUTH_COOKIE) == AUTH_TOKEN

    def _need_auth(self, path):
        return any(path.startswith(p) for p in AUTH_REQUIRED)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.send_header('Access-Control-Allow-Methods', 'GET,POST,OPTIONS')
        self.send_header('Content-Length', '0')
        self.end_headers()

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(u.query)
        if u.path == '/api/auth/check':
            self._json(200, {'required': bool(ACCESS_KEY), 'ok': self._authorized()})
            return
        if self._need_auth(u.path) and not self._authorized():
            self._json(401, {'error': 'unauthorized', 'needAuth': True})
            return
        if u.path in ('/', '/index.html'):
            with open(HTML_PATH, 'rb') as f:
                body = f.read()
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif u.path == '/api/health':
            cities = sorted(_load_offline_docs().keys())
            self._json(200, {'mock': MOCK, 'hasFlyai': bool(FLYAI_KEY), 'hasBailian': bool(BAILIAN_KEY),
                             'offlineEnabled': OFFLINE_ENABLED, 'offlineCities': cities,
                             'routes': sorted(_load_routes().keys())})
        elif u.path == '/api/routes':
            self._json(200, {'routes': _routes_summary()})
        elif u.path.startswith('/img/'):
            self._img(u.path)
        elif u.path == '/api/locate':
            try:
                self._json(200, reverse_geocode(q['lat'][0], q['lng'][0]))
            except Exception as e:
                self._json(500, {'error': str(e)})
        elif u.path.startswith('/api/stream/'):
            sid = u.path.rsplit('/', 1)[-1]
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream; charset=utf-8')
            self.send_header('Cache-Control', 'no-cache')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            if MOCK:
                pack = PACKS.get(sid)
                node = dict(MOCK_NODE)
                if pack:
                    node = enrich_node(json.loads(json.dumps(node)), pack['by_id'])
                sse_write(self.wfile, {'type': 'node', 'node': node})
                sse_write(self.wfile, {'type': 'done'})
            else:
                stream_to_client(self.wfile, sid)
        else:
            self._json(404, {'error': 'not found'})

    def do_POST(self):
        u = urllib.parse.urlparse(self.path)
        if u.path == '/api/auth':
            try:
                b = self._body()
            except Exception:
                b = {}
            key = (b.get('key') or '').strip()
            if not ACCESS_KEY or key == ACCESS_KEY:
                body = b'{"ok":true}'
                self.send_response(200)
                self.send_header('Content-Type', 'application/json; charset=utf-8')
                self.send_header('Content-Length', str(len(body)))
                self.send_header('Access-Control-Allow-Origin', '*')
                if ACCESS_KEY:
                    self.send_header('Set-Cookie',
                                     f'{AUTH_COOKIE}={AUTH_TOKEN}; Path=/; Max-Age={AUTH_TTL}; HttpOnly; SameSite=Lax')
                self.end_headers()
                self.wfile.write(body)
            else:
                self._json(401, {'error': '口令不正确'})
            return
        try:
            if self._need_auth(u.path) and not self._authorized():
                self._json(401, {'error': 'unauthorized', 'needAuth': True})
                return
            if u.path == '/api/session':
                b = self._body()
                mode = b.get('mode', '探索型')
                route_id = (b.get('route') or '').strip() or None
                route_name = ''
                n_route = None
                if route_id:
                    route = _load_routes().get(route_id)
                    if not route or not route.get('nodes'):
                        self._json(400, {'error': '线路不存在，请刷新重试'})
                        return
                    city = route.get('city', '杭州')
                    district = ''
                    lat = route['nodes'][0].get('lat')
                    lng = route['nodes'][0].get('lng')
                    route_name = route.get('name', '')
                    place_name = route_name
                    n_route = len(route['nodes'])
                else:
                    place = b.get('place') or {'city': '杭州'}
                    city = place.get('city', '杭州')
                    district = place.get('district', '')
                    lat = place.get('lat')
                    lng = place.get('lng')
                    place_name = city

                # 局长 -> 最大剧情节点数（时间控制）
                length = str(b.get('length') or 'standard')
                if length in ('full', 'complete', 'route') and n_route:
                    max_nodes = n_route
                elif length in LENGTH_PRESET:
                    max_nodes = LENGTH_PRESET[length]
                elif length.isdigit():
                    max_nodes = max(2, min(12, int(length)))
                else:
                    max_nodes = LENGTH_PRESET['standard']

                if MOCK:
                    sid = 'mock_' + str(os.getpid())
                    mp = mock_pois()
                    set_pack(sid, {'by_id': {p['poi_id']: p for p in mp},
                                   'valid': {p['poi_id'] for p in mp},
                                   'last_pos': (30.0, 120.0), 'mode': mode, 'city': city,
                                   'route_id': route_id, 'route_name': route_name, 'length': length,
                                   'max_nodes': max_nodes, 'node_index': 1,
                                   'trail': [place_name], 'last_visited': mp[0], 'ended': False})
                    set_play(sid)
                    save_state()
                    self._json(200, {'sessionId': sid, 'maxNodes': max_nodes})
                    return

                pack = build_pack(lat, lng, city, mode, route_id=route_id)
                if not pack:
                    self._json(502, {'error': '未能检索到周边真实地点，请换个更热闹的位置或手动输入城市'})
                    return
                sid = create_session(mode, city)
                pack['mode'] = mode
                pack['city'] = city
                pack['route_id'] = route_id
                pack['route_name'] = route_name
                pack['length'] = length
                pack['max_nodes'] = max_nodes
                pack['node_index'] = 1
                pack['trail'] = [place_name]
                pack['last_visited'] = pack['pois'][0]
                set_pack(sid, pack)
                save_state()

                def background():
                    try:
                        prompt = OPEN_PROMPT.format(
                            city=city, district=district,
                            lat=pack['base'][0], lng=pack['base'][1],
                            mode=mode, pois_json=pack_for_prompt(pack))
                        st, tx = send_event(sid, prompt)
                        if st not in (200, 201):
                            # 会话创建后应仍存活；异常时留痕，前端 stream 会给出反馈
                            PACKS[sid]['error'] = f'init send {st}'
                    except Exception:
                        pass
                    set_play(sid)
                    save_state()
                threading.Thread(target=background, daemon=True).start()
                self._json(200, {'sessionId': sid, 'route': route_id, 'maxNodes': max_nodes})

            elif u.path == '/api/chat':
                b = self._body()
                sid = b['sessionId']
                chosen = b.get('chosen') or {}
                pack = PACKS.get(sid)
                if MOCK:
                    set_play(sid)
                    save_state()
                    self._json(200, {'ok': True})
                    return
                if not pack:
                    self._json(500, {'error': '会话不存在或已过期，请重新开一局'})
                    return
                if pack.get('ended'):
                    self._json(409, {'error': '本局已结束，请开新的一局'})
                    return
                target = (pack.get('by_id') or {}).get(str(chosen.get('target_poi_id', '')))
                if not target:
                    self._json(500, {'error': '所选地点无效，请重试'})
                    return
                # 基于新到达的地点重新组织素材包：线路会话锁定线路池，其余按城市，保证地理连贯
                new_pack = build_pack(target['lat'], target['lng'], pack['city'], pack['mode'],
                                      route_id=pack.get('route_id')) or pack
                max_nodes = int(pack.get('max_nodes') or LENGTH_PRESET['standard'])
                new_pack['mode'] = pack['mode']
                new_pack['city'] = pack['city']
                new_pack['route_id'] = pack.get('route_id')
                new_pack['route_name'] = pack.get('route_name', '')
                new_pack['length'] = pack.get('length', 'standard')
                new_pack['max_nodes'] = max_nodes
                new_pack['node_index'] = int(pack.get('node_index') or 1) + 1
                new_pack['trail'] = (pack.get('trail') or []) + [target['name']]
                new_pack['last_visited'] = target
                new_pack['last_pos'] = (target['lat'], target['lng'])
                new_pack['ended'] = False
                set_pack(sid, new_pack)
                # 达到局长上限 -> 生成本局大结局；否则继续推进
                if new_pack['node_index'] > max_nodes:
                    prompt = END_PROMPT.format(
                        mode=pack['mode'], route=(new_pack.get('route_name') or pack['city']),
                        trail='、'.join(new_pack['trail']))
                else:
                    prompt = RESUME_PROMPT.format(
                        key=chosen.get('key', ''), label=chosen.get('text', ''),
                        target_name=target['name'], target_lat=target['lat'],
                        target_lng=target['lng'], target_dist=target['distance_m'],
                        mode=pack['mode'], pois_json=pack_for_prompt(new_pack))
                st, tx = send_event(sid, prompt)
                if st == 404:
                    # 百炼会话已被回收：同步重建并把剧情进度迁移到新会话，返回新 sid
                    sid, st, tx = rebuild_session(sid, new_pack, prompt)
                if st not in (200, 201):
                    self._json(500, {'error': f'推进失败({st})，请重试或重新开一局'})
                    return
                set_play(sid)
                save_state()
                self._json(200, {'ok': True, 'sessionId': sid,
                                 'ending': new_pack['node_index'] > max_nodes})

            elif u.path == '/api/finish':
                b = self._body()
                sid = b['sessionId']
                pack = PACKS.get(sid)
                if MOCK:
                    self._json(200, {'ok': True})
                    return
                if not pack:
                    self._json(500, {'error': '会话不存在或已过期，请重新开一局'})
                    return
                if pack.get('ended'):
                    self._json(200, {'ok': True, 'already': True})
                    return
                pack['node_index'] = int(pack.get('max_nodes') or 999)  # 强制越界，确保只出结局
                prompt = END_PROMPT.format(
                    mode=pack['mode'], route=(pack.get('route_name') or pack['city']),
                    trail='、'.join(pack.get('trail') or [pack['city']]))
                st, tx = send_event(sid, prompt)
                if st == 404:
                    sid, st, tx = rebuild_session(sid, pack, prompt)
                if st not in (200, 201):
                    self._json(500, {'error': f'结束失败({st})'})
                    return
                save_state()
                self._json(200, {'ok': True, 'sessionId': sid})

            elif u.path == '/api/resume':
                b = self._body()
                send_event(b['sessionId'], b['text'])
                set_play(b['sessionId'])
                save_state()
                self._json(200, {'ok': True})
            else:
                self._json(404, {'error': 'not found'})
        except Exception as e:
            self._json(500, {'error': str(e)})


if __name__ == '__main__':
    load_state()
    srv = ThreadingHTTPServer(('0.0.0.0', PORT), Handler)
    print(f'travel-rpg proxy on :{PORT} (MOCK={1 if MOCK else 0}, resumed={len(PACKS)} sessions)', flush=True)
    srv.serve_forever()
