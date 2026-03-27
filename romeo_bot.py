# -*- coding: utf-8 -*-
import json
import os
import re
import asyncio
import random
import time
import aiohttp
import psycopg2
import psycopg2.extras
from aiohttp import web
from datetime import datetime

TOKEN = '8785959754:AAFWbDWNkBeT42CzqNn_m1g7eqGFp6XdBps'
API = f'https://api.telegram.org/bot{TOKEN}'
DATA_FILE = './data.json'
STATE_FILE = './state.json'
DATABASE_URL = os.environ.get('DATABASE_URL', '')
WEBHOOK_URL = os.environ.get('WEBHOOK_URL', '').rstrip('/')


DEVELOPER_USERNAME = 'c9aac'

_session = None
_session_created_at = 0
SESSION_MAX_AGE = 600  # تجديد الجلسة كل 10 دقائق

# ===== إعدادات الاتصال بالـ API =====
# البوت يحاول دائماً بدون توقف حتى عند أخطاء الاتصال

async def get_session():
    global _session, _session_created_at
    now = time.time()
    # تجديد الجلسة إذا كانت مغلقة أو انتهى عمرها
    if _session is None or _session.closed or (now - _session_created_at) > SESSION_MAX_AGE:
        if _session and not _session.closed:
            try:
                await _session.close()
            except:
                pass
        connector = aiohttp.TCPConnector(
            limit=200,
            limit_per_host=50,
            ttl_dns_cache=600,
            keepalive_timeout=30,
            enable_cleanup_closed=True,
        )
        _session = aiohttp.ClientSession(
            connector=connector,
            timeout=aiohttp.ClientTimeout(total=10, connect=3, sock_connect=3, sock_read=8),
        )
        _session_created_at = now
        print(f'✅ تم إنشاء جلسة HTTP جديدة')
    return _session

clean_queue = {}
last_clean_time = {}

private_states = {}

last_messages = {}

# تتبع عدد رسائل التكرار: {chat_id: {user_id: [timestamps]}}
repeat_tracker = {}
repeat_warn_tracker = {}

# تتبع الطرد السريع (التفليش): {chat_id: {user_id: [timestamps]}}
flash_tracker = {}
# تتبع تحذيرات الكلمات المحظورة: {chat_id: {user_id: count}}
bw_warn_tracker = {}
# المحظورون مؤقتاً من الطرد: {chat_id: {user_id: unblock_timestamp}}
flash_blocked = {}

whispers = {}
BOT_USERNAME = None

# قاموس لحفظ يوزرات الأعضاء: {chat_id: {username_lower: user_id}}
username_to_id = {}
# قاموس لحفظ معلومات الأعضاء: {chat_id: {user_id: {id, first_name, last_name, username}}}
user_cache = {}

# ===== كاش البيانات في الذاكرة =====
_DATA = None
_STATE = None
_DATA_DIRTY = False
_STATE_DIRTY = False

# كاش صلاحيات المشرفين: {(chat_id, user_id): (result, timestamp)}
_admin_cache = {}
_ADMIN_CACHE_TTL = 60  # ثانية

async def get_bot_username():
    global BOT_USERNAME
    if not BOT_USERNAME:
        bot_info = await api_call('getMe', {})
        BOT_USERNAME = (bot_info or {}).get('username', '')
    return BOT_USERNAME

def register_user(chat_id, from_):
    cid = str(chat_id)
    uid = str(from_.get('id', ''))
    if not uid:
        return
    if cid not in user_cache:
        user_cache[cid] = {}
    user_cache[cid][uid] = {
        'id': from_.get('id'),
        'first_name': from_.get('first_name', ''),
        'last_name': from_.get('last_name', ''),
        'username': from_.get('username', '')
    }
    uname = (from_.get('username') or '').lower().strip('@')
    if uname:
        if cid not in username_to_id:
            username_to_id[cid] = {}
        username_to_id[cid][uname] = from_.get('id')

def find_user_by_username(chat_id, username):
    uname = username.lower().strip('@')
    cid = str(chat_id)
    uid = (username_to_id.get(cid) or {}).get(uname)
    if uid:
        cached = (user_cache.get(cid) or {}).get(str(uid))
        if cached:
            return cached
        return {'id': uid, 'first_name': username, 'last_name': '', 'username': username}
    return None

async def get_user_by_username_api(username):
    uname = username.strip('@')
    result = await api_call('getChat', {'chat_id': f'@{uname}'})
    if result and result.get('id'):
        return {
            'id': result['id'],
            'first_name': result.get('first_name', '') or result.get('title', uname),
            'last_name': result.get('last_name', ''),
            'username': result.get('username', uname)
        }
    return None

# ===========================
# DATA HELPERS
# ===========================

def _get_db_conn():
    return psycopg2.connect(DATABASE_URL, sslmode='require')

def init_db():
    if not DATABASE_URL:
        return
    try:
        conn = _get_db_conn()
        cur = conn.cursor()
        cur.execute('''
            CREATE TABLE IF NOT EXISTS bot_storage (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
        ''')
        conn.commit()
        cur.close()
        conn.close()
        print('Database initialized successfully')
    except Exception as e:
        print(f'Database init error: {e}')

def _load_data_from_db():
    if DATABASE_URL:
        try:
            conn = _get_db_conn()
            cur = conn.cursor()
            cur.execute("SELECT value FROM bot_storage WHERE key = 'data'")
            row = cur.fetchone()
            cur.close()
            conn.close()
            if row:
                return json.loads(row[0])
        except Exception as e:
            print(f'DB load_data error: {e}')
    try:
        with open(DATA_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except:
        pass
    return {
        'custom_replies': {}, 'group_settings': {},
        'user_ranks': {}, 'user_warnings': {},
        'bank_accounts': {}, 'games_state': {}
    }

def _load_state_from_db():
    if DATABASE_URL:
        try:
            conn = _get_db_conn()
            cur = conn.cursor()
            cur.execute("SELECT value FROM bot_storage WHERE key = 'state'")
            row = cur.fetchone()
            cur.close()
            conn.close()
            if row:
                return json.loads(row[0])
        except Exception as e:
            print(f'DB load_state error: {e}')
    try:
        with open(STATE_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except:
        pass
    return {}

def _flush_data_to_db(d):
    if DATABASE_URL:
        try:
            conn = _get_db_conn()
            cur = conn.cursor()
            cur.execute('''
                INSERT INTO bot_storage (key, value) VALUES ('data', %s)
                ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value
            ''', (json.dumps(d, ensure_ascii=False),))
            conn.commit()
            cur.close()
            conn.close()
            return
        except Exception as e:
            print(f'DB save_data error: {e}')
    with open(DATA_FILE, 'w', encoding='utf-8') as f:
        json.dump(d, f, ensure_ascii=False, indent=2)

def _flush_state_to_db(s):
    if DATABASE_URL:
        try:
            conn = _get_db_conn()
            cur = conn.cursor()
            cur.execute('''
                INSERT INTO bot_storage (key, value) VALUES ('state', %s)
                ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value
            ''', (json.dumps(s, ensure_ascii=False),))
            conn.commit()
            cur.close()
            conn.close()
            return
        except Exception as e:
            print(f'DB save_state error: {e}')
    with open(STATE_FILE, 'w', encoding='utf-8') as f:
        json.dump(s, f, ensure_ascii=False)

def load_data():
    global _DATA
    if _DATA is None:
        _DATA = _load_data_from_db()
    return _DATA

def save_data(d):
    global _DATA, _DATA_DIRTY
    _DATA = d
    _DATA_DIRTY = True

def load_state():
    global _STATE
    if _STATE is None:
        _STATE = _load_state_from_db()
    return _STATE

def save_state(s):
    global _STATE, _STATE_DIRTY
    _STATE = s
    _STATE_DIRTY = True

async def _db_flush_loop():
    global _DATA_DIRTY, _STATE_DIRTY
    loop = asyncio.get_event_loop()
    while True:
        await asyncio.sleep(4)
        try:
            if _DATA_DIRTY and _DATA is not None:
                data_snapshot = json.loads(json.dumps(_DATA, ensure_ascii=False))
                _DATA_DIRTY = False
                await loop.run_in_executor(None, _flush_data_to_db, data_snapshot)
            if _STATE_DIRTY and _STATE is not None:
                state_snapshot = json.loads(json.dumps(_STATE, ensure_ascii=False))
                _STATE_DIRTY = False
                await loop.run_in_executor(None, _flush_state_to_db, state_snapshot)
        except Exception as e:
            print(f'DB flush error: {e}')

def get_settings(data, chat_id):
    id_ = str(chat_id)
    if id_ not in data['group_settings']:
        data['group_settings'][id_] = {}
    s = data['group_settings'][id_]
    defaults = {
        'lock_swear': False, 'lock_links': False, 'lock_forward': False, 'lock_clutter': False,
        'lock_english': False, 'lock_chinese': False, 'lock_russian': False, 'lock_photos': False,
        'lock_videos': False, 'lock_media_edit': False, 'lock_audio': False, 'lock_music': False,
        'lock_repeat': False, 'lock_repeat_restrict': False, 'lock_repeat_warn': False,
        'repeat_max_messages': 5, 'repeat_seconds': 7, 'repeat_warn_max': 3,
        'lock_mention': False, 'lock_numbers': False, 'lock_stickers': False,
        'lock_animated': False, 'lock_chat': False, 'lock_join': False,
        'lock_external_reply': False, 'lock_quote': False,
        'disable_id': False, 'disable_service': False, 'disable_fun': True,
        'disable_welcome': False, 'disable_link': False, 'disable_auto_replies': False,
        'disable_games': True, 'disable_top': False,
        'lock_nsfw': False,
        'lock_nsfw_restrict': False,
        'lock_nsfw_warn': False,
        'lock_id_documents': False,
        'lock_files': False,
        'lock_channel_usernames': False,
        'lock_all_usernames': False,
        'lock_contacts': False,
        'lock_online': False,
        'banned_words': [],
        'bw_warn_mode': False,
        'bw_restrict_mode': False,
        'bw_warn_max': 5,
        'lock_flash': False,
        'flash_ban_limit': 3,
        'flash_ban_seconds': 30,
        'clean_auto': False,
        'clean_interval': 1,
        'clean_numbers': False,
        'clean_clutter': False,
        'clean_edited': False,
        'clean_files': False,
        'youtube_enabled': True,
        'locked_commands': {},
    }
    for k, v in defaults.items():
        if k not in s:
            s[k] = v
    return s

def get_rank(data, chat_id, user_id):
    rank = (data['user_ranks'].get(str(chat_id)) or {}).get(str(user_id), 'عضو')
    return rank

def set_rank(data, chat_id, user_id, rank):
    if str(chat_id) not in data['user_ranks']:
        data['user_ranks'][str(chat_id)] = {}
    data['user_ranks'][str(chat_id)][str(user_id)] = rank

def get_warnings(data, chat_id, user_id):
    cid = str(chat_id)
    uid = str(user_id)
    if 'user_warnings' not in data:
        data['user_warnings'] = {}
    if cid not in data['user_warnings']:
        data['user_warnings'][cid] = {}
    return data['user_warnings'][cid].get(uid, 0)

def add_warning(data, chat_id, user_id):
    cid = str(chat_id)
    uid = str(user_id)
    if 'user_warnings' not in data:
        data['user_warnings'] = {}
    if cid not in data['user_warnings']:
        data['user_warnings'][cid] = {}
    data['user_warnings'][cid][uid] = data['user_warnings'][cid].get(uid, 0) + 1
    return data['user_warnings'][cid][uid]

def reset_warnings(data, chat_id, user_id):
    cid = str(chat_id)
    uid = str(user_id)
    if 'user_warnings' not in data:
        data['user_warnings'] = {}
    if cid not in data['user_warnings']:
        data['user_warnings'][cid] = {}
    data['user_warnings'][cid][uid] = 0

RANKS = {'عضو': 0, 'مميز': 1, 'ادمن': 2, 'أدمن': 2, 'مدير': 3, 'مالك': 4, 'مالك اساسي': 5}

def rank_level(r):
    return RANKS.get(r, 0)

# ===========================
# CUSTOM COMMAND ALIASES
# ===========================

def get_custom_commands(data, chat_id):
    cid = str(chat_id)
    if 'custom_commands' not in data:
        data['custom_commands'] = {}
    if cid not in data['custom_commands']:
        data['custom_commands'][cid] = {}
    return data['custom_commands'][cid]

def resolve_command(data, chat_id, text):
    aliases = get_custom_commands(data, chat_id)
    return aliases.get(text, text)

# ===========================
# GAME HELPERS
# ===========================

BANKS = ['بنك الاهلي', 'بنك الرافدين', 'بنك الراجحي']

JOBS = {
    'نجار': (1000, 2200),
    'حداد': (1200, 2500),
    'طيار': (3500, 6000),
    'حلاق': (800, 1800),
    'جايجي': (700, 1500),
    'شرطي': (2000, 4000),
    'موظف': (1500, 3000),
}

ITEM_PRICES = {
    'برج': 145000000, 'جزيرة': 130000000, 'بيت': 80000000,
    'طيارة': 12000000, 'سيارة': 8000000, 'سفينة': 1400000,
    'قطار': 1300000, 'قصر': 1000000, 'ماسة': 800000, 'وجبة': 20,
    'جندي': 468363, 'رشاش': 6398990, 'قنبلة': 187118491,
    'صاروخ': 7873543170, 'مدفع': 35500875115, 'مدرعة': 837216638774,
    'مضاد صواريخ': 1826756987720, 'طائرة حربية': 81606720,
}

ITEM_EMOJI = {
    'برج': '🏰', 'جزيرة': '🏝️', 'بيت': '🏠', 'طيارة': '✈️',
    'سيارة': '🚗', 'سفينة': '🛳️', 'قطار': '🚂', 'قصر': '🏯',
    'ماسة': '💎', 'وجبة': '🍔', 'جندي': '💂', 'رشاش': '🔫',
    'قنبلة': '💣', 'صاروخ': '🚀', 'مدفع': '🏹', 'مدرعة': '🪖',
    'مضاد صواريخ': '🛡️', 'طائرة حربية': '🛩️',
}

ITEM_SINGULAR = {
    'ابراج': 'برج', 'جزر': 'جزيرة', 'بيوت': 'بيت', 'طيارات': 'طيارة',
    'سيارات': 'سيارة', 'سفن': 'سفينة', 'قطارات': 'قطار', 'قصور': 'قصر',
    'ماسات': 'ماسة', 'وجبات': 'وجبة', 'جنود': 'جندي', 'رشاشات': 'رشاش',
    'قنابل': 'قنبلة', 'صواريخ': 'صاروخ', 'مدافع': 'مدفع', 'مدرعات': 'مدرعة',
    'طائرات': 'طائرة حربية',
}

KAT_QUESTIONS = [
    'لو خيروك: تعض لسانك بالغلط، ولا يسكر على صبعك الباب؟',
    'انسان م تحب تتعامل معه ابد 🤨',
    'ما أهدافك المستقبلية؟ 🎯',
    'ردك الدائم على الكلام الحلو... 🌹',
    'وش الشيء الي يكرهه أقرب صاحب لك؟',
    'أكثر ريحة تجيب راسك... 🤢',
    'لو قدرت تغير شي في حياتك وش راح تغير؟',
    'وش أكثر شي يضحكك لو فكرت فيه؟ 😂',
    'أكثر شخص أثر فيك في حياتك ومن؟',
    'لو عندك يوم بدون أي مسؤوليات وش راح تسوي؟',
    'وش الشي اللي لو عرفه الناس عنك راح يتفاجأون؟ 😯',
    'شي تتمنى لو ما سويته؟',
    'لو قدرت تسافر لأي مكان بالعالم وين راح تروح؟ ✈️',
    'أكثر شي تخاف منه في المستقبل؟',
    'وش أكثر تطبيق تفتحه على جوالك؟',
    'لو طلب منك أحد تصف نفسك بثلاث كلمات وش راح تقول؟',
    'شي تتمناه الحين بس تعرف انه مستحيل؟',
    'أكثر قرار صعب اتخذته في حياتك؟',
    'لو عندك قدرة خارقة وش تختار؟ ⚡',
    'وش أكثر شي تكرهه في البشر؟',
    'صاحبك الوفي وش صفاته عندك؟',
    'أكثر لحظة فرحان فيها في حياتك؟ 🎉',
    'لو الدنيا بكره وش راح تسوي اليوم؟',
    'وش الأكل اللي لو شفته تقوم من الطاولة؟ 🤮',
    'أكثر جملة تسمعها وتنرفز؟ 😤',
    'لو قدرت ترجع بالوقت لمتى راح ترجع؟',
    'شي تعلمته من تجربة مرت عليك؟',
    'وش تحس فيه لما تكون لحالك؟',
    'أكثر شي تقدر تصبر عليه وأكثر شي ما تقدر؟',
    'لو صاحبك خانك وش راح تسوي؟',
    'وش الأغنية اللي تعبر عن حياتك الحين؟ 🎵',
    'لو كان عندك مليون دينار وش أول شي تشتريه؟ 💰',
    'شي تتمناه لأقرب شخص لك؟ ❤️',
    'أكثر شي تندم عليه في علاقاتك مع الناس؟',
    'وش تحب الناس تتذكرك فيه بعد ما تروح؟',
    'لو قدرت تعيش في أي زمن وين كنت تختار؟',
    'أكثر إنجاز تفخر فيه لحد الحين؟ 🏆',
    'وش الحلم اللي تكرر معك وما نسيته؟',
    'لو عندك رسالة لنفسك قبل 5 سنين وش كنت تقوله؟',
]

SOWAR_QUESTIONS = [
    ('https://upload.wikimedia.org/wikipedia/commons/thumb/0/05/Flag_of_Brazil.svg/1280px-Flag_of_Brazil.svg.png', '🌍 ما هي دولة هذه العلم؟', 'البرازيل'),
    ('https://upload.wikimedia.org/wikipedia/commons/thumb/a/a9/Flag_of_Thailand.svg/1280px-Flag_of_Thailand.svg.png', '🌍 ما هي دولة هذا العلم؟', 'تايلاند'),
    ('https://upload.wikimedia.org/wikipedia/commons/thumb/f/f3/Flag_of_Russia.svg/1280px-Flag_of_Russia.svg.png', '🌍 ما هي دولة هذا العلم؟', 'روسيا'),
    ('https://upload.wikimedia.org/wikipedia/commons/thumb/b/ba/Flag_of_Germany.svg/1280px-Flag_of_Germany.svg.png', '🌍 ما هي دولة هذا العلم؟', 'المانيا'),
    ('https://upload.wikimedia.org/wikipedia/commons/thumb/c/c3/Flag_of_France.svg/1280px-Flag_of_France.svg.png', '🌍 ما هي دولة هذا العلم؟', 'فرنسا'),
    ('https://upload.wikimedia.org/wikipedia/commons/thumb/0/0d/Flag_of_Saudi_Arabia.svg/1280px-Flag_of_Saudi_Arabia.svg.png', '🌍 ما هي دولة هذا العلم؟', 'السعودية'),
    ('https://upload.wikimedia.org/wikipedia/commons/thumb/1/1a/Flag_of_Argentina.svg/1280px-Flag_of_Argentina.svg.png', '🌍 ما هي دولة هذا العلم؟', 'الارجنتين'),
    ('https://upload.wikimedia.org/wikipedia/commons/thumb/4/41/Flag_of_India.svg/1280px-Flag_of_India.svg.png', '🌍 ما هي دولة هذا العلم؟', 'الهند'),
    ('https://upload.wikimedia.org/wikipedia/commons/thumb/9/9e/Flag_of_Japan.svg/1280px-Flag_of_Japan.svg.png', '🌍 ما هي دولة هذا العلم؟', 'اليابان'),
    ('https://upload.wikimedia.org/wikipedia/commons/thumb/2/2c/Flag_of_Morocco.svg/1280px-Flag_of_Morocco.svg.png', '🌍 ما هي دولة هذا العلم؟', 'المغرب'),
    ('https://upload.wikimedia.org/wikipedia/commons/thumb/7/77/Flag_of_Algeria.svg/1280px-Flag_of_Algeria.svg.png', '🌍 ما هي دولة هذا العلم؟', 'الجزائر'),
    ('https://upload.wikimedia.org/wikipedia/commons/thumb/4/49/Flag_of_Kenya.svg/1280px-Flag_of_Kenya.svg.png', '🌍 ما هي دولة هذا العلم؟', 'كينيا'),
    ('https://upload.wikimedia.org/wikipedia/commons/thumb/3/3e/Flag_of_Turkey.svg/1280px-Flag_of_Turkey.svg.png', '🌍 ما هي دولة هذا العلم؟', 'تركيا'),
    ('https://upload.wikimedia.org/wikipedia/commons/thumb/a/aa/Flag_of_Kuwait.svg/1280px-Flag_of_Kuwait.svg.png', '🌍 ما هي دولة هذا العلم؟', 'الكويت'),
    ('https://upload.wikimedia.org/wikipedia/commons/thumb/1/11/Flag_of_Egypt.svg/1280px-Flag_of_Egypt.svg.png', '🌍 ما هي دولة هذا العلم؟', 'مصر'),
    ('https://upload.wikimedia.org/wikipedia/commons/thumb/thumb/6/6f/Taj_Mahal%2C_Agra%2C_India_edit3.jpg/1280px-Taj_Mahal%2C_Agra%2C_India_edit3.jpg', '🏛️ ما اسم هذا المعلم السياحي؟', 'تاج محل'),
    ('https://upload.wikimedia.org/wikipedia/commons/thumb/1/10/Empire_State_Building_%28aerial_view%29.jpg/800px-Empire_State_Building_%28aerial_view%29.jpg', '🏛️ ما اسم هذا البرج الشهير؟', 'برج امباير ستيت'),
    ('https://upload.wikimedia.org/wikipedia/commons/thumb/e/e6/Paris_Night.jpg/1280px-Paris_Night.jpg', '🏛️ ما اسم هذا البرج الشهير في فرنسا؟', 'برج ايفل'),
    ('https://upload.wikimedia.org/wikipedia/commons/thumb/d/de/Colosseo_2020.jpg/1280px-Colosseo_2020.jpg', '🏛️ ما اسم هذا المعلم في ايطاليا؟', 'الكولوسيوم'),
    ('https://upload.wikimedia.org/wikipedia/commons/thumb/4/47/PNG_transparency_demonstration_1.png/280px-PNG_transparency_demonstration_1.png', '🐾 ما اسم هذا الحيوان؟', 'كنغر'),
    ('https://upload.wikimedia.org/wikipedia/commons/thumb/3/3a/Cat03.jpg/1200px-Cat03.jpg', '🐾 ما اسم هذا الحيوان؟', 'قط'),
    ('https://upload.wikimedia.org/wikipedia/commons/thumb/d/d9/Collage_of_Nine_Dogs.jpg/1200px-Collage_of_Nine_Dogs.jpg', '🐾 ما اسم هذا الحيوان؟', 'كلب'),
    ('https://upload.wikimedia.org/wikipedia/commons/thumb/3/37/African_Bush_Elephant.jpg/1200px-African_Bush_Elephant.jpg', '🐾 ما اسم هذا الحيوان الضخم؟', 'فيل'),
    ('https://upload.wikimedia.org/wikipedia/commons/thumb/0/0a/The_tiger_in_the_water.jpg/1200px-The_tiger_in_the_water.jpg', '🐾 ما اسم هذا الحيوان المخطط؟', 'نمر'),
    ('https://upload.wikimedia.org/wikipedia/commons/thumb/9/9c/Giraffe_Mikumi_National_Park.jpg/1200px-Giraffe_Mikumi_National_Park.jpg', '🐾 ما اسم هذا الحيوان طويل الرقبة؟', 'زرافة'),
    ('https://upload.wikimedia.org/wikipedia/commons/thumb/1/1c/Miroslava_Duma.jpg/400px-Miroslava_Duma.jpg', '🍎 ما هذه الفاكهة الحمراء؟', 'تفاحة'),
    ('https://upload.wikimedia.org/wikipedia/commons/thumb/1/15/Red_Apple.jpg/1200px-Red_Apple.jpg', '🍎 ما هذه الفاكهة الحمراء؟', 'تفاحة'),
    ('https://upload.wikimedia.org/wikipedia/commons/thumb/8/8a/Banana-Chocolate-Chip-Cookies-Recipe.jpg/1200px-Banana-Chocolate-Chip-Cookies-Recipe.jpg', '🍌 ما هذه الفاكهة الصفراء؟', 'موز'),
    ('https://upload.wikimedia.org/wikipedia/commons/thumb/9/90/Hapus_Mango.jpg/1200px-Hapus_Mango.jpg', '🥭 ما هذه الفاكهة؟', 'مانجو'),
    ('https://upload.wikimedia.org/wikipedia/commons/thumb/1/13/Watermelon_seedless_2009_16x9.jpg/1280px-Watermelon_seedless_2009_16x9.jpg', '🍉 ما هذه الفاكهة الحمراء والخضراء؟', 'بطيخ'),
]

REGULAR_SHOP_ITEMS = [
    ('برج', '🏰', 145000000), ('جزيرة', '🏝️', 130000000), ('بيت', '🏠', 80000000),
    ('طيارة', '✈️', 12000000), ('سيارة', '🚗', 8000000), ('سفينة', '🛳️', 1400000),
    ('قطار', '🚂', 1300000), ('قصر', '🏯', 1000000), ('ماسة', '💎', 800000), ('وجبة', '🍔', 20),
]

MILITARY_SHOP_ITEMS = [
    ('جندي', '💂', 468363), ('رشاش', '🔫', 6398990), ('طائرة حربية', '🛩️', 81606720),
    ('قنبلة', '💣', 187118491), ('صاروخ', '🚀', 7873543170), ('مدفع', '🏹', 35500875115),
    ('مدرعة', '🪖', 837216638774), ('مضاد صواريخ', '🛡️', 1826756987720),
]

def normalize_item(item_name):
    item_name = item_name.strip()
    if item_name in ITEM_SINGULAR:
        return ITEM_SINGULAR[item_name]
    if item_name in ITEM_PRICES:
        return item_name
    return None

def fmt_money(n):
    return f'<code>{n}</code>'

def increment_msg_count(data, chat_id, user_id, first_name=None):
    cid = str(chat_id)
    uid = str(user_id)
    if 'msg_counts' not in data:
        data['msg_counts'] = {}
    if cid not in data['msg_counts']:
        data['msg_counts'][cid] = {}
    data['msg_counts'][cid][uid] = data['msg_counts'][cid].get(uid, 0) + 1
    if first_name:
        if 'user_names' not in data:
            data['user_names'] = {}
        data['user_names'][uid] = first_name

def get_msg_count(data, chat_id, user_id):
    return data.get('msg_counts', {}).get(str(chat_id), {}).get(str(user_id), 0)

def get_user_display(data, uid_str):
    return data.get('user_names', {}).get(uid_str, f'مستخدم {uid_str}')

def get_bank(data, chat_id, user_id):
    cid = str(chat_id)
    uid = str(user_id)
    if 'bank_accounts' not in data:
        data['bank_accounts'] = {}
    if cid not in data['bank_accounts']:
        data['bank_accounts'][cid] = {}
    return data['bank_accounts'][cid].get(uid)

def create_bank_account(data, chat_id, user_id, bank_name):
    cid = str(chat_id)
    uid = str(user_id)
    if 'bank_accounts' not in data:
        data['bank_accounts'] = {}
    if cid not in data['bank_accounts']:
        data['bank_accounts'][cid] = {}
    account_number = str(random.randint(1000000000, 9999999999))
    data['bank_accounts'][cid][uid] = {
        'bank': bank_name,
        'account_number': account_number,
        'balance': 0,
        'properties': {'سيارة': 1},
        'job': None,
        'last_salary': 0,
        'last_steal': 0,
    }
    return data['bank_accounts'][cid][uid]

def get_game_state(data, chat_id):
    cid = str(chat_id)
    if 'games_state' not in data:
        data['games_state'] = {}
    if cid not in data['games_state']:
        data['games_state'][cid] = {}
    if 'kursi' not in data['games_state'][cid]:
        data['games_state'][cid]['kursi'] = {
            'active': False, 'starter_id': None, 'players': [],
            'chosen_id': None, 'chosen_name': None, 'questions_count': 0
        }
    return data['games_state'][cid]['kursi']

def get_ahkam_state(data, chat_id):
    cid = str(chat_id)
    if 'games_state' not in data:
        data['games_state'] = {}
    if cid not in data['games_state']:
        data['games_state'][cid] = {}
    if 'ahkam' not in data['games_state'][cid]:
        data['games_state'][cid]['ahkam'] = {
            'active': False, 'waiting': False, 'starter_id': None, 'players': []
        }
    return data['games_state'][cid]['ahkam']

# ===========================
# TELEGRAM API
# ===========================

async def api_call(method, params):
    global _session

    for attempt in range(3):  # 3 محاولات دائماً بدون أي توقف للدائرة
        try:
            session = await get_session()
            async with session.post(f'{API}/{method}', json=params) as res:
                data = await res.json()
                if data.get('ok'):
                    return data['result']
                else:
                    err = data.get('description', '')
                    if 'Too Many Requests' in err:
                        retry_after = data.get('parameters', {}).get('retry_after', 2)
                        await asyncio.sleep(min(retry_after, 5))
                    return None
        except asyncio.TimeoutError:
            print(f'⏱ Timeout في {method} (محاولة {attempt+1}/3)')
            _session = None
            if attempt < 2:
                await asyncio.sleep(0.3)
        except aiohttp.ClientConnectionError as e:
            print(f'🔌 خطأ اتصال في {method}: {e} (محاولة {attempt+1}/3)')
            _session = None
            if attempt < 2:
                await asyncio.sleep(0.3)
        except Exception as e:
            print(f'❌ خطأ في {method}: {type(e).__name__}: {e}')
            return None
    return None

async def send(chat_id, text, extra=None):
    params = {'chat_id': chat_id, 'text': text, 'parse_mode': 'HTML'}
    if extra:
        params.update(extra)
    return await api_call('sendMessage', params)

async def delete(chat_id, msg_id):
    return await api_call('deleteMessage', {'chat_id': chat_id, 'message_id': msg_id})

async def get_chat_member(chat_id, user_id):
    key = (chat_id, user_id)
    now = time.time()
    cached = _admin_cache.get(key)
    if cached and (now - cached[1]) < _ADMIN_CACHE_TTL:
        return cached[0]
    result = await api_call('getChatMember', {'chat_id': chat_id, 'user_id': user_id})
    _admin_cache[key] = (result, now)
    return result

def invalidate_admin_cache(chat_id, user_id):
    _admin_cache.pop((chat_id, user_id), None)

async def get_chat(chat_id):
    return await api_call('getChat', {'chat_id': chat_id})

async def restrict(chat_id, user_id, perms):
    return await api_call('restrictChatMember', {'chat_id': chat_id, 'user_id': user_id, 'permissions': perms})

async def ban(chat_id, user_id):
    return await api_call('banChatMember', {'chat_id': chat_id, 'user_id': user_id})

async def unban(chat_id, user_id):
    return await api_call('unbanChatMember', {'chat_id': chat_id, 'user_id': user_id})

async def answer_cb(id_):
    return await api_call('answerCallbackQuery', {'callback_query_id': id_})

async def edit_msg(chat_id, msg_id, text, markup=None):
    params = {'chat_id': chat_id, 'message_id': msg_id, 'text': text, 'parse_mode': 'HTML'}
    if markup:
        params['reply_markup'] = markup
    return await api_call('editMessageText', params)

async def get_file(file_id):
    return await api_call('getFile', {'file_id': file_id})

def name(from_):
    n = ((from_.get('first_name') or '') + ' ' + (from_.get('last_name') or '')).strip()
    return n or from_.get('username') or 'مجهول'

def mention(from_):
    return f'<a href="tg://user?id={from_["id"]}">{name(from_)}</a>'

async def is_tg_admin(chat_id, user_id):
    m = await get_chat_member(chat_id, user_id)
    return m and m.get('status') in ['administrator', 'creator']

async def is_admin_up(data, chat_id, user_id):
    if await is_developer(user_id):
        return True
    if await is_tg_admin(chat_id, user_id):
        return True
    return rank_level(get_rank(data, chat_id, user_id)) >= rank_level('ادمن')

async def is_owner_up(data, chat_id, user_id):
    if await is_developer(user_id):
        return True
    m = await get_chat_member(chat_id, user_id)
    if m and m.get('status') == 'creator':
        return True
    return rank_level(get_rank(data, chat_id, user_id)) >= rank_level('مالك')

async def is_master(data, chat_id, user_id):
    if await is_developer(user_id):
        return True
    m = await get_chat_member(chat_id, user_id)
    if m and m.get('status') == 'creator':
        return True
    return rank_level(get_rank(data, chat_id, user_id)) >= rank_level('مالك اساسي')

async def is_group_creator(chat_id, user_id):
    m = await get_chat_member(chat_id, user_id)
    return m and m.get('status') == 'creator'

_developer_id_cache = None

def is_developer_by_username(from_dict):
    uname = (from_dict.get('username') or '').strip().lstrip('@').lower()
    return uname == DEVELOPER_USERNAME.lower()

async def is_developer(user_id, username=None):
    global _developer_id_cache
    if username and username.lstrip('@').lower() == DEVELOPER_USERNAME.lower():
        if user_id:
            _developer_id_cache = user_id
        return True
    if _developer_id_cache and user_id == _developer_id_cache:
        return True
    try:
        dev_check = await api_call('getChat', {'chat_id': f'@{DEVELOPER_USERNAME}'})
        if dev_check and dev_check.get('id'):
            _developer_id_cache = dev_check['id']
            if user_id == _developer_id_cache:
                return True
    except:
        pass
    return False

# ===========================
# NSFW IMAGE DETECTION (NudeNet + YOLOv8 - بدون API)
# ===========================

import tempfile
import cv2 as _cv2
from nudenet import NudeDetector as _NudeDetector
from ultralytics import YOLO as _YOLO

# تهيئة النماذج مرة واحدة عند بدء التشغيل
_nude_detector = None
_weapon_model = None

# فئات الإباحي الصريحة - عتبة حساسة جداً (0.2)
NUDE_EXPLICIT_CLASSES = {
    'EXPOSED_BREAST_F', 'EXPOSED_GENITALIA_F', 'EXPOSED_GENITALIA_M',
    'EXPOSED_BUTTOCKS', 'EXPOSED_ANUS',
}
# فئات مثيرة - عتبة متوسطة (0.45)
NUDE_SUGGESTIVE_CLASSES = {
    'COVERED_GENITALIA_F', 'COVERED_GENITALIA_M', 'COVERED_BUTTOCKS',
    'EXPOSED_BELLY', 'COVERED_BELLY',
}
# أسماء الأسلحة المدعومة في YOLOv8
WEAPON_CLASS_NAMES = {'knife', 'gun', 'pistol', 'rifle', 'firearm', 'weapon', 'handgun', 'sword'}

def get_nude_detector():
    global _nude_detector
    if _nude_detector is None:
        _nude_detector = _NudeDetector()
    return _nude_detector

def get_weapon_model():
    global _weapon_model
    if _weapon_model is None:
        _weapon_model = _YOLO('yolov8n.pt')
    return _weapon_model

async def _download_file(file_id):
    file_info = await get_file(file_id)
    if not file_info:
        return None
    file_path = file_info.get('file_path')
    if not file_path:
        return None
    file_url = f'https://api.telegram.org/file/bot{TOKEN}/{file_path}'
    session = await get_session()
    try:
        async with session.get(file_url) as resp:
            if resp.status == 200:
                return await resp.read()
    except Exception as e:
        print(f'Download error: {e}')
    return None

def _check_frame(path):
    """فحص إطار واحد بـ NudeNet وYOLOv8، يرجع (is_violation, type)"""
    try:
        detector = get_nude_detector()
        detections = detector.detect(path)
        for det in detections:
            label = det.get('class', '')
            score = det.get('score', 0)
            if label in NUDE_EXPLICIT_CLASSES and score > 0.2:
                return True, 'إباحي'
            if label in NUDE_SUGGESTIVE_CLASSES and score > 0.45:
                return True, 'إباحي'
    except Exception as e:
        print(f'NudeNet error: {e}')
    try:
        model = get_weapon_model()
        results = model(path, verbose=False, conf=0.25)
        for r in results:
            for box in r.boxes:
                cls_id = int(box.cls[0])
                conf = float(box.conf[0])
                cls_name = model.names.get(cls_id, '').lower()
                if cls_name in WEAPON_CLASS_NAMES and conf > 0.25:
                    return True, 'أسلحة'
    except Exception as e:
        print(f'YOLOv8 error: {e}')
    return False, None

async def check_image_nsfw(file_id):
    import os as _os_img
    try:
        data = await _download_file(file_id)
        if not data:
            return False, None
        with tempfile.NamedTemporaryFile(suffix='.jpg', delete=False) as tmp:
            tmp.write(data)
            tmp_path = tmp.name
        try:
            return _check_frame(tmp_path)
        finally:
            try:
                _os_img.unlink(tmp_path)
            except:
                pass
    except Exception as e:
        print(f'NSFW check error: {e}')
        return False, None


async def check_video_nsfw(file_id):
    import os as _os_vid
    try:
        data = await _download_file(file_id)
        if not data:
            return False, None
        with tempfile.NamedTemporaryFile(suffix='.mp4', delete=False) as tmp:
            tmp.write(data)
            tmp_path = tmp.name
        try:
            cap = _cv2.VideoCapture(tmp_path)
            fps = cap.get(_cv2.CAP_PROP_FPS) or 25
            frame_interval = max(1, int(fps * 0.5))
            frame_count = 0
            checked = 0
            while True:
                ret, frame = cap.read()
                if not ret:
                    break
                frame_count += 1
                if frame_count % frame_interval != 0:
                    continue
                checked += 1
                if checked > 20:
                    break
                with tempfile.NamedTemporaryFile(suffix='.jpg', delete=False) as ftmp:
                    _cv2.imwrite(ftmp.name, frame)
                    frame_path = ftmp.name
                try:
                    is_v, v_type = _check_frame(frame_path)
                    if is_v:
                        cap.release()
                        return True, v_type
                finally:
                    try:
                        _os_vid.unlink(frame_path)
                    except:
                        pass
            cap.release()
            return False, None
        finally:
            try:
                _os_vid.unlink(tmp_path)
            except:
                pass
    except Exception as e:
        print(f'Video NSFW check error: {e}')
        return False, None

# ===========================
# YOUTUBE SEARCH & DOWNLOAD
# ===========================

youtube_pending = {}

INVIDIOUS_INSTANCES = [
    'https://invidious.privacydev.net',
    'https://inv.tux.pizza',
    'https://invidious.nerdvpn.de',
    'https://invidious.fdn.fr',
    'https://vid.puffyan.us',
]

async def youtube_search(query, max_results=4):
    session = await get_session()
    headers = {'User-Agent': 'Mozilla/5.0', 'Accept': 'application/json'}
    params = {'q': query, 'type': 'video'}

    # جرب كل instance حتى تنجح واحدة
    for instance in INVIDIOUS_INSTANCES:
        try:
            url = f'{instance}/api/v1/search'
            async with session.get(url, params=params, headers=headers,
                                   timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status != 200:
                    continue
                data = await resp.json(content_type=None)
                if not isinstance(data, list) or not data:
                    continue
                results = []
                seen = set()
                for item in data:
                    # فيديوهات فقط، تجاهل القوائم والقنوات
                    if item.get('type') not in ('video', None):
                        continue
                    vid_id = item.get('videoId', '')
                    title = item.get('title', '')
                    if not vid_id or len(vid_id) != 11 or vid_id in seen or not title:
                        continue
                    seen.add(vid_id)
                    secs = item.get('lengthSeconds', 0)
                    dur = f'{secs // 60}:{secs % 60:02d}' if secs else ''
                    label = f'🎵 {title[:55]}' + (f'  [{dur}]' if dur else '')
                    results.append({'id': vid_id, 'title': label})
                    if len(results) >= max_results:
                        break
                if results:
                    return results
        except Exception as e:
            print(f'Invidious {instance} error: {e}')
            continue

    # fallback: scrape يوتيوب مباشرة
    try:
        search_url = 'https://www.youtube.com/results'
        h2 = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36',
              'Accept-Language': 'ar,en;q=0.9'}
        async with session.get(search_url, params={'search_query': query}, headers=h2,
                               timeout=aiohttp.ClientTimeout(total=15)) as resp:
            if resp.status != 200:
                return []
            html = await resp.text()
        results = []
        seen_ids = set()
        pattern = r'"videoId":"([a-zA-Z0-9_-]{11})"[^}]*?"title":\{"runs":\[\{"text":"([^"]+)"'
        for vid_id, title in re.findall(pattern, html):
            if vid_id not in seen_ids:
                seen_ids.add(vid_id)
                results.append({'id': vid_id, 'title': f'🎵 {title[:60]}'})
            if len(results) >= max_results:
                break
        return results
    except Exception as e:
        print(f'YouTube fallback error: {e}')
        return []

async def download_youtube_audio(video_id):
    try:
        session = await get_session()
        api_url = f'https://api.vevioz.com/api/button/mp3/{video_id}'
        headers = {'User-Agent': 'Mozilla/5.0'}
        async with session.get(api_url, headers=headers, allow_redirects=True) as resp:
            if resp.status == 200:
                content = await resp.read()
                if len(content) > 10000:
                    return content, 'audio/mpeg'
        api2_url = f'https://api.fabdl.com/youtube/mp3?url=https://www.youtube.com/watch?v={video_id}'
        async with session.get(api2_url, headers=headers) as resp2:
            if resp2.status == 200:
                result = await resp2.json()
                dl_url = result.get('result', {}).get('download_url') or result.get('dl_url')
                if dl_url:
                    async with session.get(dl_url, headers=headers) as resp3:
                        if resp3.status == 200:
                            content = await resp3.read()
                            if len(content) > 10000:
                                return content, 'audio/mpeg'
        return None, None
    except Exception as e:
        print(f'YouTube download error: {e}')
        return None, None

async def send_youtube_results(chat_id, msg_id, query, results):
    buttons = []
    for r in results:
        buttons.append([{'text': r['title'], 'callback_data': f'yt_dl:{r["id"]}'}])
    keyboard = {'inline_keyboard': buttons}
    await api_call('sendMessage', {
        'chat_id': chat_id,
        'text': f'• نتائج البحث ‹‹ {query}',
        'reply_to_message_id': msg_id,
        'reply_markup': keyboard,
        'parse_mode': 'HTML'
    })

# ===========================
# USER INFO - ACCOUNT CREATION
# ===========================

def format_account_date(user_id):
    try:
        uid = int(user_id)
        if uid < 100000:        year, month = 2013, 3
        elif uid < 1000000:     year, month = 2013, 8
        elif uid < 10000000:    year, month = 2014, 6
        elif uid < 50000000:    year, month = 2015, 6
        elif uid < 100000000:   year, month = 2016, 4
        elif uid < 200000000:   year, month = 2017, 3
        elif uid < 300000000:   year, month = 2017, 10
        elif uid < 400000000:   year, month = 2018, 5
        elif uid < 500000000:   year, month = 2019, 1
        elif uid < 600000000:   year, month = 2019, 8
        elif uid < 700000000:   year, month = 2020, 3
        elif uid < 800000000:   year, month = 2020, 9
        elif uid < 900000000:   year, month = 2021, 3
        elif uid < 1000000000:  year, month = 2021, 8
        elif uid < 1100000000:  year, month = 2022, 1
        elif uid < 1200000000:  year, month = 2022, 5
        elif uid < 1300000000:  year, month = 2022, 9
        elif uid < 1400000000:  year, month = 2023, 1
        elif uid < 1500000000:  year, month = 2023, 5
        elif uid < 1600000000:  year, month = 2023, 9
        elif uid < 1700000000:  year, month = 2024, 1
        elif uid < 1800000000:  year, month = 2024, 5
        else:                   year, month = 2024, 9
        return f'{year}-{month:02d}'
    except:
        return 'غير معروف'

ARABIC_MONTHS = {
    1: 'يناير', 2: 'فبراير', 3: 'مارس', 4: 'أبريل',
    5: 'مايو', 6: 'يونيو', 7: 'يوليو', 8: 'أغسطس',
    9: 'سبتمبر', 10: 'أكتوبر', 11: 'نوفمبر', 12: 'ديسمبر'
}

def get_account_creation_text(from_):
    date_str = format_account_date(from_['id'])
    return date_str

# ===========================
# CLEAN MENU BUILDER
# ===========================

def build_clean_menu(settings):
    auto = '✓' if settings.get('clean_auto') else '✗'
    numbers_mark = ' ✓' if settings.get('clean_numbers') else ''
    clutter_mark = ' ✓' if settings.get('clean_clutter') else ''
    files_mark = ' ✓' if settings.get('clean_files') else ''
    interval = settings.get('clean_interval', 1)

    text = (
        '🧹 <b>قائمة التنظيف</b>\n\n'
        'يمكنك تفعيل خيارات التنظيف التلقائي من الأزرار أدناه.\n'
        'التنظيف يشمل دائماً: الصور، الفيديوهات، الملصقات.\n\n'
        f'• التنظيف التلقائي: {"🟢 مفعّل" if settings.get("clean_auto") else "🔴 معطّل"}\n'
        f'• وقت التنظيف: {interval} {"دقيقة" if interval == 1 else "دقائق"}\n'
        f'• الأرقام: {"✓ مفعّل" if settings.get("clean_numbers") else "معطّل"}\n'
        f'• الكلايش (رسائل طويلة): {"✓ مفعّل" if settings.get("clean_clutter") else "معطّل"}\n'
        f'• الرسائل المعدلة: {"✓ مفعّل" if settings.get("clean_edited") else "معطّل"}\n'
        f'• الملفات: {"✓ مفعّل" if settings.get("clean_files") else "معطّل"}'
    )

    keyboard = {
        'inline_keyboard': [
            [{'text': f'• التنظيف التلقائي ({auto})', 'callback_data': 'clean_toggle_auto'}],
            [
                {'text': f'الأرقام{numbers_mark}', 'callback_data': 'clean_toggle_numbers'},
                {'text': f'الكلايش{clutter_mark}', 'callback_data': 'clean_toggle_clutter'}
            ],
            [
                {'text': f'• الرسائل المعدلة ({("✓" if settings.get("clean_edited") else "✗")})', 'callback_data': 'clean_toggle_edited'},
                {'text': f'الملفات{(" ✓" if settings.get("clean_files") else " ✗")}', 'callback_data': 'clean_toggle_files'}
            ],
            [{'text': f'• وقت التنظيف {interval} د', 'callback_data': 'clean_set_time'}],
            [{'text': '🔙 رجوع', 'callback_data': 'clean_back'}]
        ]
    }
    return text, keyboard

def add_to_clean_queue(chat_id, msg_id, msg_type):
    cid = str(chat_id)
    if cid not in clean_queue:
        clean_queue[cid] = {'photos': [], 'videos': [], 'stickers': [], 'numbers': [], 'clutter': [], 'edited': [], 'files': []}
    if msg_type not in clean_queue[cid]:
        clean_queue[cid][msg_type] = []
    if msg_id not in clean_queue[cid][msg_type]:
        clean_queue[cid][msg_type].append(msg_id)

async def run_clean(chat_id, settings):
    cid = str(chat_id)
    queue = clean_queue.get(cid, {})
    counts = {}

    type_labels = {
        'photos': 'الصور',
        'videos': 'الفيديوهات',
        'stickers': 'الملصقات',
        'numbers': 'الأرقام',
        'clutter': 'الكلايش',
        'edited': 'الرسائل المعدلة',
        'files': 'الملفات'
    }

    for msg_type, ids in queue.items():
        deleted = 0
        for mid in ids:
            result = await delete(chat_id, mid)
            if result is not False:
                deleted += 1
        if deleted > 0:
            counts[type_labels.get(msg_type, msg_type)] = deleted

    clean_queue[cid] = {'photos': [], 'videos': [], 'stickers': [], 'numbers': [], 'clutter': [], 'edited': [], 'files': []}

    if counts:
        lines = '\n'.join(f'  • {k} ← {v}' for k, v in counts.items())
        msg = (
            '🧹 <b>تم تنظيف المجموعة بالتنظيف التلقائي بنجاح</b>\n\n'
            f'<b>الرسائل المحذوفة:</b>\n{lines}\n\n'
            '─ تعطيل التنظيف التلقائي\n'
            '─ اخفاء رسالة التنظيف'
        )
        await send(chat_id, msg, {
            'reply_markup': {
                'inline_keyboard': [
                    [{'text': '🔴 تعطيل التنظيف التلقائي', 'callback_data': 'clean_disable_auto'}],
                    [{'text': '🗑 اخفاء رسالة التنظيف', 'callback_data': 'clean_hide_report'}]
                ]
            }
        })

# ===========================
# AUTO CLEAN BACKGROUND TASK
# ===========================

async def auto_clean_loop():
    while True:
        await asyncio.sleep(60)
        try:
            data = load_data()
            now = datetime.now().timestamp()
            for cid, settings in data.get('group_settings', {}).items():
                if not settings.get('clean_auto'):
                    continue
                interval_min = settings.get('clean_interval', 1)
                interval_sec = interval_min * 60
                last = last_clean_time.get(cid, 0)
                if now - last >= interval_sec:
                    last_clean_time[cid] = now
                    await run_clean(int(cid), settings)
        except Exception as e:
            print(f'Auto clean error: {e}')

# ===========================
# CALLBACKS
# ===========================

menu_texts = {
    'menu_service': (
        '🔧 <b>أوامر الخدمية:</b>\n\n'
        '• <b>بايو</b> - يرسل بايو كاتب الكلمة\n'
        '• <b>بايو</b> (رد) - بايو الشخص المردود عليه\n'
        '• <b>افتاري</b> - يرسل صورتك الشخصية\n'
        '• <b>اسمي</b> - يرسل اسمك\n'
        '• <b>اسمه</b> (رد) - اسم الشخص\n'
        '• <b>يوزري</b> - يرسل يوزرك\n'
        '• <b>يوزره</b> (رد) - يوزر الشخص\n'
        '• <b>المالك</b> - يذكر مالك المجموعة\n'
        '• <b>ايدي</b> - معرفك\n'
        '• <b>الرابط</b> - رابط المجموعة\n'
        '• <b>رتبة / رتب</b> - رتبتك\n'
        '• <b>رتبة</b> (رد) - رتبة الشخص المردود عليه\n'
        '• <b>رتبته</b> (رد) - رتبة شخص\n'
        '• <b>انشاء</b> - تاريخ إنشاء الحساب\n\n'
        '🏆 <b>التوب:</b>\n'
        '• <b>التوب</b> - أكثر 10 أعضاء تفاعلاً في المجموعة\n'
        '• تعطيل التوب | تفعيل التوب\n\n'
        '🤫 <b>الهمسة:</b>\n'
        '• <b>همسه / اهمس</b> (رد على رسالة شخص) - ترسل همسة خاصة له فقط\n\n'
        '🤖 <b>ردود البوت:</b>\n'
        '• <b>تعطيل ردود البوت</b> - يوقف ردود البوت التلقائية (بوت، ها، شتريد...)\n'
        '• <b>تفعيل ردود البوت</b> - يعيد تشغيلها\n\n'
        '🖼️ <b>صورة الملصق:</b>\n'
        '• رد على أي ملصق ثابت بكلمة <b>صوره</b> لتحويله إلى صورة\n\n'
        '🎵 <b>اليوتيوب:</b>\n'
        '• <b>يوت [اسم الأغنية]</b> - البحث عن أغنية وإرسالها\n'
        '• تعطيل اليوتيوب | تفعيل اليوتيوب'
    ),
    'menu_fun': (
        '🎉 <b>أوامر التسليه:</b>\n\n'
        '• <b>رفع [كلمة]</b> (رد) - يرفع لقب للشخص للتسلية\n\n'
        '🔴 تعطيل التسليه | تفعيل التسليه'
    ),
    'menu_locks': (
        '🔒 <b>أوامر القفل والفتح:</b>\n\n'
        'قفل السب | قفل التكرار | قفل الروابط\n'
        'قفل التوجيه | قفل الكلايش | قفل الانجليزية\n'
        'قفل الصينية | قفل الروسية | قفل الصور\n'
        'قفل الفيديوهات | قفل تعديل الميديا\n'
        'قفل الصوتيات | قفل الاغاني | قفل التحويل\n'
        'قفل الدخول | قفل التاك | قفل الارقام\n'
        'قفل الملصقات | قفل المتحركة | قفل الشات\n'
        'قفل الملفات | قفل الجهات\n'
        'قفل يوزرات القنوات | قفل كل اليوزرات\n'
        'قفل الردود الخارجية | قفل الاقتباسات\n'
        'قفل الاونلاين | فتح الاونلاين\n'
        'قفل المحتوى المخل | فتح المحتوى المخل\n'
        'قفل المحتوى المخل بالتقييد\n'
        'قفل المحتوى المخل بالتحذير\n'
        'قفل الوثائق الحكومية | فتح الوثائق الحكومية'
    ),
    'menu_settings': (
        '⚙️ <b>أوامر الإعدادات (رد على رسالة شخص):</b>\n\n'
        '• رفع مالك اساسي / تنزيل مالك اساسي\n'
        '• رفع مالك / تنزيل مالك\n'
        '• رفع مدير / تنزيل مدير\n'
        '• رفع ادمن / تنزيل ادمن\n'
        '• رفع مميز / تنزيل مميز\n\n'
        '🔴 كتم | تقييد | طرد | رفع القيود | مسح\n\n'
        '⚙️ <b>إعدادات الأوامر:</b>\n'
        '• <b>قفل امر</b> - قفل أمر لرتبة معينة فقط\n'
        '• <b>اضف امر</b> - إضافة اسم بديل لأمر موجود'
    ),
    'menu_games': (
        '🎮 <b>أوامر الألعاب:</b>\n\n'
        '<b>🐱 لعبة كت:</b>\n'
        '• اكتب <b>كت</b> ويسألك البوت سؤال عشوائي\n\n'
        '<b>🪑 لعبة الكرسي:</b>\n'
        '• اكتب <b>كرسي</b> لبدء اللعبة وتسجيل نفسك\n'
        '• الأعضاء يرسلون <b>انا</b> للانضمام\n'
        '• من بدأ اللعبة يرسل <b>نعم</b> لبدء الأسئلة\n'
        '• من بدأ اللعبة يرسل <b>انهاء</b> لإنهاء اللعبة\n\n'
        '<b>⚖️ لعبة الأحكام:</b>\n'
        '• اكتب <b>احكام</b> لبدء اللعبة وتسجيل نفسك\n'
        '• الأعضاء يرسلون <b>انا</b> للانضمام\n'
        '• من بدأ اللعبة يرسل <b>نعم</b> ليختار البوت حاكم ومحكوم\n'
        '• من بدأ اللعبة يرسل <b>انهاء</b> لإنهاء اللعبة\n\n'
        '<b>🏦 لعبة ممتلكاتي:</b>\n'
        '• <b>انشاء حساب بنكي</b> - فتح حساب بنكي\n'
        '• <b>حسابي</b> - عرض معلومات حسابك\n'
        '• <b>فلوسي</b> - عرض رصيدك\n'
        '• <b>فلوسه</b> (رد) - عرض رصيد شخص آخر\n'
        '• <b>راتب</b> - استلام راتبك (كل 7 ساعات)\n'
        '• <b>زرف</b> (رد) - سرقة فلوس (كل 4 ساعات)\n'
        '• <b>حظ [مبلغ]</b> - المقامرة بمبلغ\n'
        '• <b>استثمار [مبلغ]</b> - استثمار بربح 4-9% (كل 3 ساعات)\n'
        '• <b>المتجر</b> - عرض المتجر وأسعار الأشياء\n'
        '• <b>شراء [عدد] [اسم الشيء]</b> - شراء ممتلكات\n'
        '• <b>بيع [عدد] [اسم الشيء]</b> - بيع ممتلكات (60% من السعر)\n'
        '• <b>اهداء [عدد] [اسم الشيء]</b> (رد) - إهداء ممتلكات لشخص\n'
        '• <b>ممتلكاتي</b> - عرض ممتلكاتك\n'
        '• <b>تحويل [مبلغ] [رقم الحساب]</b> - تحويل فلوس لشخص آخر\n\n'
        '<b>🖼️ لعبة صور:</b>\n'
        '• اكتب <b>صور</b> لتلقي صورة عشوائية وتخمين الإجابة\n'
        '• الفائز يحصل على 140-303 دينار\n\n'
        '🔴 تعطيل الالعاب | تفعيل الالعاب'
    ),
}

async def handle_callback(cb):
    chat_id = cb['message']['chat']['id']
    msg_id = cb['message']['message_id']
    user_id = cb['from']['id']
    data_cb = cb['data']

    if data_cb.startswith('vw:'):
        whisper_id = data_cb[3:]
        w = whispers.get(whisper_id)
        if not w:
            await api_call('answerCallbackQuery', {'callback_query_id': cb['id'], 'text': '• الهمسه انتهت أو غير موجودة', 'show_alert': True})
            return
        if user_id != w['recipient_id'] and user_id != w['sender_id']:
            await api_call('answerCallbackQuery', {'callback_query_id': cb['id'], 'text': '• الهمسه لا تخصك', 'show_alert': True})
            return
        whisper_text = w.get('text') or ''
        if len(whisper_text) > 190:
            await api_call('answerCallbackQuery', {'callback_query_id': cb['id'], 'text': '📩 الهمسه طويلة، سيتم إرسالها خاص', 'show_alert': False})
            await api_call('sendMessage', {'chat_id': user_id, 'text': f'🤫 <b>همسة من {w.get("sender_name", "شخص ما")}:</b>\n\n{whisper_text}', 'parse_mode': 'HTML'})
        else:
            await api_call('answerCallbackQuery', {'callback_query_id': cb['id'], 'text': f'🤫 {whisper_text}', 'show_alert': True})
        return

    await answer_cb(cb['id'])

    if data_cb in menu_texts:
        await edit_msg(chat_id, msg_id, menu_texts[data_cb])
        return

    if data_cb.startswith('menu_banned_words:'):
        grp_id = int(data_cb.split(':', 1)[1])
        data = load_data()
        if not await is_admin_up(data, grp_id, user_id):
            await api_call('answerCallbackQuery', {'callback_query_id': cb['id'], 'text': '⛔ هذا الأمر للمشرفين فقط', 'show_alert': True})
            return
        settings = get_settings(data, grp_id)
        bw_list = settings.get('banned_words', [])
        count = len(bw_list)
        warn_on = settings.get('bw_warn_mode', False)
        restrict_on = settings.get('bw_restrict_mode', False)
        mode_txt = '🔕 بدون إجراء' if not warn_on and not restrict_on else ('⚠️ تحذير ثم تقييد' if warn_on else '🔒 تقييد مباشر')
        bw_keyboard = {
            'inline_keyboard': [
                [
                    {'text': '➕ اضافة كلمة', 'callback_data': f'bw_add:{grp_id}'},
                    {'text': '➖ ازالة كلمة', 'callback_data': f'bw_remove:{grp_id}'},
                ],
                [
                    {'text': f'📋 قائمة الكلمات ({count})', 'callback_data': f'bw_list:{grp_id}'},
                ],
                [
                    {'text': f'⚠️ بالتحذير {"✓" if warn_on else "✗"}', 'callback_data': f'bw_toggle_warn:{grp_id}'},
                    {'text': f'🔒 بالتقييد {"✓" if restrict_on else "✗"}', 'callback_data': f'bw_toggle_restrict:{grp_id}'},
                ],
            ]
        }
        await edit_msg(chat_id, msg_id,
            f'🚫 <b>الكلمات المحظورة</b>\n\nعدد الكلمات المحظورة حالياً: <b>{count}</b>\nالوضع الحالي: <b>{mode_txt}</b>\n\nاختر من الأزرار أدناه:',
            bw_keyboard)
        return

    if data_cb.startswith('bw_toggle_warn:') or data_cb.startswith('bw_toggle_restrict:'):
        action, grp_id_str = data_cb.split(':', 1)
        grp_id = int(grp_id_str)
        data = load_data()
        if not await is_admin_up(data, grp_id, user_id):
            await api_call('answerCallbackQuery', {'callback_query_id': cb['id'], 'text': '⛔ هذا الأمر للمشرفين فقط', 'show_alert': True})
            return
        settings = get_settings(data, grp_id)
        if action == 'bw_toggle_warn':
            new_val = not settings.get('bw_warn_mode', False)
            settings['bw_warn_mode'] = new_val
            if new_val:
                settings['bw_restrict_mode'] = False  # لا يمكن تفعيل الاثنين معاً
        else:
            new_val = not settings.get('bw_restrict_mode', False)
            settings['bw_restrict_mode'] = new_val
            if new_val:
                settings['bw_warn_mode'] = False  # لا يمكن تفعيل الاثنين معاً
        save_data(data)
        warn_on = settings.get('bw_warn_mode', False)
        restrict_on = settings.get('bw_restrict_mode', False)
        bw_list = settings.get('banned_words', [])
        count = len(bw_list)
        mode_txt = '🔕 بدون إجراء' if not warn_on and not restrict_on else ('⚠️ تحذير ثم تقييد' if warn_on else '🔒 تقييد مباشر')
        bw_keyboard = {
            'inline_keyboard': [
                [
                    {'text': '➕ اضافة كلمة', 'callback_data': f'bw_add:{grp_id}'},
                    {'text': '➖ ازالة كلمة', 'callback_data': f'bw_remove:{grp_id}'},
                ],
                [
                    {'text': f'📋 قائمة الكلمات ({count})', 'callback_data': f'bw_list:{grp_id}'},
                ],
                [
                    {'text': f'⚠️ بالتحذير {"✓" if warn_on else "✗"}', 'callback_data': f'bw_toggle_warn:{grp_id}'},
                    {'text': f'🔒 بالتقييد {"✓" if restrict_on else "✗"}', 'callback_data': f'bw_toggle_restrict:{grp_id}'},
                ],
            ]
        }
        await edit_msg(chat_id, msg_id,
            f'🚫 <b>الكلمات المحظورة</b>\n\nعدد الكلمات المحظورة حالياً: <b>{count}</b>\nالوضع الحالي: <b>{mode_txt}</b>\n\nاختر من الأزرار أدناه:',
            bw_keyboard)
        return

    if data_cb.startswith('bw_add:') or data_cb.startswith('bw_remove:') or data_cb.startswith('bw_list:'):
        action, grp_id_str = data_cb.split(':', 1)
        grp_id = int(grp_id_str)
        data = load_data()
        if not await is_admin_up(data, grp_id, user_id):
            await api_call('answerCallbackQuery', {'callback_query_id': cb['id'], 'text': '⛔ هذا الأمر للمشرفين فقط', 'show_alert': True})
            return
        settings = get_settings(data, grp_id)
        bw_list = settings.get('banned_words', [])
        if action == 'bw_list':
            if not bw_list:
                await api_call('answerCallbackQuery', {'callback_query_id': cb['id'], 'text': '📋 لا توجد كلمات محظورة حتى الآن', 'show_alert': True})
            else:
                words_text = '\n'.join(f'• {w}' for w in bw_list)
                await api_call('answerCallbackQuery', {'callback_query_id': cb['id']})
                await send(grp_id, f'📋 <b>الكلمات المحظورة ({len(bw_list)}):</b>\n\n{words_text}')
            return
        state = load_state()
        cid = str(grp_id)
        uid = str(user_id)
        if cid not in state:
            state[cid] = {}
        if action == 'bw_add':
            state[cid][uid] = {'step': 'await_banned_word_add'}
            save_state(state)
            await api_call('answerCallbackQuery', {'callback_query_id': cb['id']})
            await send(grp_id, '✏️ أرسل الكلمة المراد حظرها:')
        elif action == 'bw_remove':
            if not bw_list:
                await api_call('answerCallbackQuery', {'callback_query_id': cb['id'], 'text': '⚠️ لا توجد كلمات محظورة لإزالتها', 'show_alert': True})
                return
            state[cid][uid] = {'step': 'await_banned_word_remove'}
            save_state(state)
            words_text = '\n'.join(f'• {w}' for w in bw_list)
            await api_call('answerCallbackQuery', {'callback_query_id': cb['id']})
            await send(grp_id, f'📋 الكلمات المحظورة الحالية:\n\n{words_text}\n\n✏️ أرسل الكلمة المراد إزالتها:')
        return

    if data_cb in ('clean_toggle_auto', 'clean_toggle_numbers', 'clean_toggle_clutter',
                   'clean_toggle_edited', 'clean_toggle_files', 'clean_set_time', 'clean_back',
                   'clean_disable_auto', 'clean_hide_report'):

        if not await is_group_creator(chat_id, user_id):
            await api_call('answerCallbackQuery', {'callback_query_id': cb['id'], 'text': '⛔ هذا الأمر للمالك فقط', 'show_alert': True})
            return

        data = load_data()
        settings = get_settings(data, chat_id)
        cid = str(chat_id)

        if data_cb == 'clean_toggle_auto':
            settings['clean_auto'] = not settings.get('clean_auto', False)
            save_data(data)
            text, keyboard = build_clean_menu(settings)
            await edit_msg(chat_id, msg_id, text, keyboard)

        elif data_cb == 'clean_toggle_numbers':
            settings['clean_numbers'] = not settings.get('clean_numbers', False)
            save_data(data)
            text, keyboard = build_clean_menu(settings)
            await edit_msg(chat_id, msg_id, text, keyboard)

        elif data_cb == 'clean_toggle_clutter':
            settings['clean_clutter'] = not settings.get('clean_clutter', False)
            save_data(data)
            text, keyboard = build_clean_menu(settings)
            await edit_msg(chat_id, msg_id, text, keyboard)

        elif data_cb == 'clean_toggle_edited':
            settings['clean_edited'] = not settings.get('clean_edited', False)
            save_data(data)
            text, keyboard = build_clean_menu(settings)
            await edit_msg(chat_id, msg_id, text, keyboard)

        elif data_cb == 'clean_toggle_files':
            settings['clean_files'] = not settings.get('clean_files', False)
            save_data(data)
            text, keyboard = build_clean_menu(settings)
            await edit_msg(chat_id, msg_id, text, keyboard)

        elif data_cb == 'clean_set_time':
            state = load_state()
            if cid not in state:
                state[cid] = {}
            state[cid][str(user_id)] = {'step': 'await_clean_time', 'menu_msg_id': msg_id}
            save_state(state)
            await edit_msg(chat_id, msg_id,
                '⏱ <b>أرسل وقت التنظيف</b>\n\nمثال:\n• 5 (دقائق)\n• 60 (ساعة)\n\nأقل وقت هو دقيقة واحدة')

        elif data_cb == 'clean_back':
            text, keyboard = build_clean_menu(settings)
            await edit_msg(chat_id, msg_id, text, keyboard)

        elif data_cb == 'clean_disable_auto':
            settings['clean_auto'] = False
            save_data(data)
            await edit_msg(chat_id, msg_id, '✅ تم تعطيل التنظيف التلقائي')

        elif data_cb == 'clean_hide_report':
            await delete(chat_id, msg_id)

        return

    if data_cb.startswith('bank_create:'):
        bank_name = data_cb.split(':', 1)[1]
        data = load_data()
        existing = get_bank(data, chat_id, user_id)
        if existing:
            await edit_msg(chat_id, msg_id, f'⚠️ عندك حساب بنكي بالفعل في {existing["bank"]}')
            return
        acc = create_bank_account(data, chat_id, user_id, bank_name)
        save_data(data)
        await edit_msg(chat_id, msg_id,
            f'✅ <b>تم انشاء حساب بنكي</b>\n\n'
            f'🏦 البنك: {bank_name}\n'
            f'💳 رقم الحساب: <code>{acc["account_number"]}</code>\n\n'
            f'<i>(للعلم هذه العمليات لعبة وليست حقيقية)</i>'
        )
        return

    if data_cb == 'shop_regular':
        lines = ['🏪 <b>المتجر العادي</b>\n\nأهلاً بك عزيزي في قسم المتجر وتفاصيله :\n']
        for i, (item, emoji, price) in enumerate(REGULAR_SHOP_ITEMS, 1):
            lines.append(f'{i} - {item} {emoji} ← <code>{price}</code> دينار')
        lines.append('\n- تستطيع الشراء بذلك المثال : شراء 2 سيارة')
        lines.append('- تستطيع البيع بذلك المثال : بيع 2 سيارة')
        lines.append('- تستطيع الاهداء بذلك المثال : اهداء 2 سيارة (بالرد)')
        lines.append('\n<i>ملاحظة: البيع يكون بـ 60% من السعر الأصلي</i>')
        back_kb = {'inline_keyboard': [[{'text': '🔙 رجوع', 'callback_data': 'shop_back'}]]}
        await edit_msg(chat_id, msg_id, '\n'.join(lines), back_kb)
        return

    if data_cb == 'shop_military':
        lines = ['⚔️ <b>المتجر العالمي</b>\n\nأهلاً بك عزيزي في أسعار المتجر العالمي وتفاصيله :\n']
        for i, (item, emoji, price) in enumerate(MILITARY_SHOP_ITEMS, 1):
            lines.append(f'{i} - {item} {emoji} ← <code>{price}</code> دينار')
        lines.append('\n- تستطيع الشراء بذلك المثال : شراء 2 جندي')
        lines.append('- تستطيع البيع بذلك المثال : بيع 2 جندي')
        lines.append('\n<i>ملاحظة: البيع يكون بـ 60% من السعر الأصلي</i>')
        back_kb = {'inline_keyboard': [[{'text': '🔙 رجوع', 'callback_data': 'shop_back'}]]}
        await edit_msg(chat_id, msg_id, '\n'.join(lines), back_kb)
        return

    if data_cb == 'shop_back':
        keyboard = {'inline_keyboard': [
            [{'text': '🏪 المتجر العادي', 'callback_data': 'shop_regular'}],
            [{'text': '⚔️ المتجر العالمي', 'callback_data': 'shop_military'}],
        ]}
        await edit_msg(chat_id, msg_id, '🛒 <b>اختر نوع المتجر:</b>', keyboard)
        return

    if data_cb.startswith('kursi_ask:'):
        group_chat_id = int(data_cb.split(':')[1])
        data = load_data()
        game = get_game_state(data, group_chat_id)
        if not game.get('active') or not game.get('chosen_id'):
            await api_call('answerCallbackQuery', {'callback_query_id': cb['id'], 'text': 'اللعبة انتهت', 'show_alert': True})
            return
        if game['questions_count'] >= 50:
            await api_call('answerCallbackQuery', {'callback_query_id': cb['id'], 'text': 'انتهت الأسئلة المسموحة (50 سؤال)', 'show_alert': True})
            return
        private_states[str(user_id)] = {
            'step': 'await_kursi_question',
            'group_chat_id': group_chat_id,
            'chosen_id': game['chosen_id'],
            'chosen_name': game.get('chosen_name', 'الشخص المختار')
        }
        bot_info = await api_call('getMe', {})
        bot_username = (bot_info or {}).get('username', '')
        await api_call('answerCallbackQuery', {
            'callback_query_id': cb['id'],
            'text': '✅ افتح محادثة البوت الخاصة وأرسل سؤالك',
            'show_alert': True
        })
        await send(user_id,
            f'✏️ أرسل سؤالك الآن\n\n'
            f'سيُرسَل السؤال بشكل مجهول إلى المجموعة\n'
            f'اقصى عدد للأسئلة: 50 سؤال\n\n'
            f'أرسل السؤال:'
        )
        return

    if data_cb.startswith('promote_in_group:'):
        group_id = int(data_cb.split(':')[1])
        await api_call('promoteChatMember', {
            'chat_id': group_id, 'user_id': user_id,
            'can_manage_chat': True, 'can_change_info': True,
            'can_delete_messages': True, 'can_invite_users': True,
            'can_restrict_members': True, 'can_pin_messages': True,
            'can_manage_video_chats': True
        })
        await edit_msg(chat_id, msg_id, '✅ تم رفعك كمشرف في المجموعة بنجاح!')
        return

    if data_cb.startswith('select_group:'):
        group_id = int(data_cb.split(':')[1])
        group_info = await get_chat(group_id)
        group_name = (group_info or {}).get('title', 'المجموعة')
        keyboard = {'inline_keyboard': [[{'text': '👑 رفعني مشرف', 'callback_data': f'promote_in_group:{group_id}'}]]}
        await edit_msg(chat_id, msg_id, f'📌 المجموعة: <b>{group_name}</b>\n\nاختر ما تريد:', keyboard)
        return

    if data_cb == 'show_my_groups':
        groups_data = load_data()
        all_group_settings = groups_data.get('group_settings', {})
        if not all_group_settings:
            await edit_msg(chat_id, msg_id, '😕 لا توجد مجموعات مسجلة في البوت حتى الآن.\n\nأضف البوت لمجموعتك أولاً كمشرف.')
            return
        buttons = []
        for gid in all_group_settings:
            try:
                ginfo = await get_chat(int(gid))
                if ginfo and ginfo.get('title'):
                    buttons.append([{'text': ginfo['title'], 'callback_data': f'select_group:{gid}'}])
            except:
                continue
        if not buttons:
            await edit_msg(chat_id, msg_id, '😕 لا توجد مجموعات متاحة حالياً.\n\nتأكد أن البوت مضاف كمشرف في مجموعتك.')
            return
        keyboard = {'inline_keyboard': buttons}
        await edit_msg(chat_id, msg_id, '📋 <b>المجموعات المسجلة:</b>\n\nاختر المجموعة:', keyboard)
        return

    if data_cb.startswith('yt_dl:'):
        video_id = data_cb.split(':', 1)[1]
        key = f'{chat_id}:{video_id}'
        video_title = youtube_pending.get(key, video_id)
        await api_call('answerCallbackQuery', {'callback_query_id': cb['id'], 'text': '⏳ يتم التحميل...', 'show_alert': False})
        wait_msg = await api_call('sendMessage', {
            'chat_id': chat_id,
            'text': '⏳ جاري التحميل، انتظر قليلاً...',
            'reply_to_message_id': msg_id
        })
        try:
            import io as _io
            session = await get_session()
            yt_url = f'https://www.youtube.com/watch?v={video_id}'
            title = video_title
            sent = False
            headers = {'User-Agent': 'Mozilla/5.0'}

            # loader.to - يعمل على السيرفرات السحابية
            try:
                async with session.get(
                    'https://loader.to/ajax/download.php',
                    params={'format': 'mp3', 'url': yt_url},
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=15)
                ) as r1:
                    rj1 = await r1.json(content_type=None)
                    task_id = rj1.get('id') if rj1.get('success') else None

                if task_id:
                    dl_url = None
                    for _ in range(30):
                        await asyncio.sleep(3)
                        async with session.get(
                            'https://loader.to/ajax/progress.php',
                            params={'id': task_id},
                            headers=headers,
                            timeout=aiohttp.ClientTimeout(total=10)
                        ) as r2:
                            rj2 = await r2.json(content_type=None)
                            progress = rj2.get('progress', 0)
                            if rj2.get('success') and int(progress) >= 100:
                                dl_url = rj2.get('download_url')
                                break
                            if rj2.get('success') is False:
                                break

                    if dl_url:
                        async with session.get(dl_url, headers=headers, timeout=aiohttp.ClientTimeout(total=90)) as ra:
                            if ra.status == 200:
                                audio_data = await ra.read()
                                if len(audio_data) > 10000:
                                    form = aiohttp.FormData()
                                    form.add_field('chat_id', str(chat_id))
                                    form.add_field('title', title[:64])
                                    form.add_field('performer', 'YouTube')
                                    form.add_field('audio', _io.BytesIO(audio_data), filename=f'{video_id}.mp3', content_type='audio/mpeg')
                                    form.add_field('reply_to_message_id', str(msg_id))
                                    async with session.post(f'{API}/sendAudio', data=form) as sr:
                                        pass
                                    if wait_msg:
                                        await delete(chat_id, wait_msg['message_id'])
                                    sent = True
            except Exception as e:
                print(f'loader.to error: {e}')

            # إذا فشل loader.to جرب soundloaders
            if not sent:
                try:
                    async with session.get(
                        f'https://api.soundloaders.com/youtube-dl?url={yt_url}&format=mp3',
                        headers=headers,
                        timeout=aiohttp.ClientTimeout(total=30)
                    ) as r3:
                        if r3.status == 200:
                            rj3 = await r3.json(content_type=None)
                            dl_url2 = rj3.get('url') or rj3.get('download_url')
                            if dl_url2:
                                async with session.get(dl_url2, headers=headers, timeout=aiohttp.ClientTimeout(total=90)) as ra2:
                                    if ra2.status == 200:
                                        audio_data2 = await ra2.read()
                                        if len(audio_data2) > 10000:
                                            form2 = aiohttp.FormData()
                                            form2.add_field('chat_id', str(chat_id))
                                            form2.add_field('title', title[:64])
                                            form2.add_field('performer', 'YouTube')
                                            form2.add_field('audio', _io.BytesIO(audio_data2), filename=f'{video_id}.mp3', content_type='audio/mpeg')
                                            form2.add_field('reply_to_message_id', str(msg_id))
                                            async with session.post(f'{API}/sendAudio', data=form2) as sr2:
                                                pass
                                            if wait_msg:
                                                await delete(chat_id, wait_msg['message_id'])
                                            sent = True
                except Exception as e:
                    print(f'soundloaders error: {e}')

            if not sent:
                yt_link = f'https://www.youtube.com/watch?v={video_id}'
                if wait_msg:
                    await edit_msg(chat_id, wait_msg['message_id'],
                        f'⚠️ تعذر التحميل\n\n🎵 {title}\n🔗 <a href="{yt_link}">فتح في يوتيوب</a>')
        except Exception as e:
            print(f'YT download error: {e}')
            if wait_msg:
                await edit_msg(chat_id, wait_msg['message_id'], '❌ حدث خطأ أثناء التحميل')
        return

    if data_cb == 'show_repeat_settings':
        data = load_data()
        if not await is_admin_up(data, chat_id, user_id):
            await api_call('answerCallbackQuery', {'callback_query_id': cb['id'], 'text': 'هذا الامر للمشرفين فقط', 'show_alert': True})
            return
        settings = get_settings(data, chat_id)
        max_msgs = settings.get('repeat_max_messages', 5)
        secs = settings.get('repeat_seconds', 7)
        restrict_on = settings.get('lock_repeat_restrict', False)
        warn_on = settings.get('lock_repeat_warn', False)
        repeat_on = settings.get('lock_repeat', False)
        status_txt = (
            '<b>اعدادات قفل التكرار</b>\n\n'
            + 'قفل التكرار: ' + ('مفعل' if repeat_on else 'معطل') + '\n'
            + 'قفل التكرار بالتقييد: ' + ('مفعل' if restrict_on else 'معطل') + '\n'
            + 'قفل التكرار بالتحذير: ' + ('مفعل' if warn_on else 'معطل') + '\n'
            + 'عدد الرسائل المسموح: <b>' + str(max_msgs) + '</b>\n'
            + 'النافذة الزمنية: <b>' + str(secs) + '</b> ثانية'
        )
        kbd = {
            'inline_keyboard': [
                [{'text': 'عدد رسائل (' + str(max_msgs) + ')', 'callback_data': 'repeat_set_messages'}],
                [{'text': 'عدد ثواني (' + str(secs) + ')', 'callback_data': 'repeat_set_seconds'}],
                [{'text': 'بالتقييد ' + ('✓' if restrict_on else '✗'), 'callback_data': 'repeat_toggle_restrict'}],
                [{'text': 'بالتحذير ' + ('✓' if warn_on else '✗'), 'callback_data': 'repeat_toggle_warn'}],
            ]
        }
        await api_call('answerCallbackQuery', {'callback_query_id': cb['id']})
        await send(chat_id, status_txt, {'reply_markup': kbd, 'reply_to_message_id': msg_id})
        return

    if data_cb in ('repeat_set_messages', 'repeat_set_seconds', 'repeat_toggle_restrict', 'repeat_toggle_warn'):
        data = load_data()
        if not await is_admin_up(data, chat_id, user_id):
            await api_call('answerCallbackQuery', {'callback_query_id': cb['id'], 'text': 'هذا الامر للمشرفين فقط', 'show_alert': True})
            return
        settings = get_settings(data, chat_id)
        cid_str = str(chat_id)

        if data_cb == 'repeat_toggle_restrict':
            new_val = not settings.get('lock_repeat_restrict', False)
            settings['lock_repeat_restrict'] = new_val
            if new_val:
                settings['lock_repeat'] = True
            save_data(data)
            max_msgs = settings.get('repeat_max_messages', 5)
            secs = settings.get('repeat_seconds', 7)
            warn_on = settings.get('lock_repeat_warn', False)
            repeat_on = settings.get('lock_repeat', False)
            status_txt = (
                '<b>اعدادات قفل التكرار</b>\n\n'
                + 'قفل التكرار: ' + ('مفعل' if repeat_on else 'معطل') + '\n'
                + 'قفل التكرار بالتقييد: ' + ('مفعل' if new_val else 'معطل') + '\n'
                + 'قفل التكرار بالتحذير: ' + ('مفعل' if warn_on else 'معطل') + '\n'
                + 'عدد الرسائل المسموح: <b>' + str(max_msgs) + '</b>\n'
                + 'النافذة الزمنية: <b>' + str(secs) + '</b> ثانية'
            )
            kbd = {
                'inline_keyboard': [
                    [{'text': 'عدد رسائل (' + str(max_msgs) + ')', 'callback_data': 'repeat_set_messages'}],
                    [{'text': 'عدد ثواني (' + str(secs) + ')', 'callback_data': 'repeat_set_seconds'}],
                    [{'text': 'بالتقييد ' + ('✓' if new_val else '✗'), 'callback_data': 'repeat_toggle_restrict'}],
                    [{'text': 'بالتحذير ' + ('✓' if warn_on else '✗'), 'callback_data': 'repeat_toggle_warn'}],
                ]
            }
            await edit_msg(chat_id, msg_id, status_txt, kbd)
            return

        elif data_cb == 'repeat_toggle_warn':
            new_val = not settings.get('lock_repeat_warn', False)
            settings['lock_repeat_warn'] = new_val
            save_data(data)
            max_msgs = settings.get('repeat_max_messages', 5)
            secs = settings.get('repeat_seconds', 7)
            restrict_on = settings.get('lock_repeat_restrict', False)
            repeat_on = settings.get('lock_repeat', False)
            status_txt = (
                '<b>اعدادات قفل التكرار</b>\n\n'
                + 'قفل التكرار: ' + ('مفعل' if repeat_on else 'معطل') + '\n'
                + 'قفل التكرار بالتقييد: ' + ('مفعل' if restrict_on else 'معطل') + '\n'
                + 'قفل التكرار بالتحذير: ' + ('مفعل' if new_val else 'معطل') + '\n'
                + 'عدد الرسائل المسموح: <b>' + str(max_msgs) + '</b>\n'
                + 'النافذة الزمنية: <b>' + str(secs) + '</b> ثانية'
            )
            kbd = {
                'inline_keyboard': [
                    [{'text': 'عدد رسائل (' + str(max_msgs) + ')', 'callback_data': 'repeat_set_messages'}],
                    [{'text': 'عدد ثواني (' + str(secs) + ')', 'callback_data': 'repeat_set_seconds'}],
                    [{'text': 'بالتقييد ' + ('✓' if restrict_on else '✗'), 'callback_data': 'repeat_toggle_restrict'}],
                    [{'text': 'بالتحذير ' + ('✓' if new_val else '✗'), 'callback_data': 'repeat_toggle_warn'}],
                ]
            }
            await edit_msg(chat_id, msg_id, status_txt, kbd)
            return

        elif data_cb == 'repeat_set_messages':
            state = load_state()
            if cid_str not in state:
                state[cid_str] = {}
            state[cid_str][str(user_id)] = {'step': 'await_repeat_messages', 'menu_msg_id': msg_id}
            save_state(state)
            await api_call('answerCallbackQuery', {'callback_query_id': cb['id']})
            await send(chat_id, 'ارسل عدد الرسائل المسموح بها\n\nمثال: 3\n\nالعدد الادنى: 1', {'reply_to_message_id': msg_id})
            return

        elif data_cb == 'repeat_set_seconds':
            state = load_state()
            if cid_str not in state:
                state[cid_str] = {}
            state[cid_str][str(user_id)] = {'step': 'await_repeat_seconds', 'menu_msg_id': msg_id}
            save_state(state)
            await api_call('answerCallbackQuery', {'callback_query_id': cb['id']})
            await send(chat_id, 'ارسل عدد الثواني (النافذة الزمنية)\n\nمثال: 60\n\nالعدد الادنى: 5 ثواني', {'reply_to_message_id': msg_id})
            return

    if data_cb.startswith('lock_cmd_rank:'):
        parts = data_cb.split(':', 2)
        cmd_name = parts[1]
        rank_choice = parts[2]
        data = load_data()
        settings = get_settings(data, chat_id)
        if 'locked_commands' not in settings:
            settings['locked_commands'] = {}
        settings['locked_commands'][cmd_name] = rank_choice
        save_data(data)
        state = load_state()
        cid_str = str(chat_id)
        uid_str = str(user_id)
        if cid_str in state and uid_str in state[cid_str]:
            del state[cid_str][uid_str]
            save_state(state)
        await edit_msg(chat_id, msg_id,
            f'✅ تم قفل امر <b>{cmd_name}</b> للرتبة <b>{rank_choice}</b> فقط\nولا يمكن استعمال هذه الميزه للرتب اقل من <b>{rank_choice}</b>')
        return


# ===========================
# MESSAGE HANDLER
# ===========================

async def handle_update(update):
    if 'callback_query' in update:
        await handle_callback(update['callback_query'])
        return

    if 'edited_message' in update:
        await handle_edited_message(update['edited_message'])
        return

    msg = update.get('message')
    if not msg:
        return

    chat_id = msg['chat']['id']
    chat_type = msg['chat'].get('type', 'private')
    from_ = msg.get('from', {})
    user_id = from_.get('id')
    text = (msg.get('text') or msg.get('caption') or '').strip()
    msg_id = msg['message_id']

    if from_ and chat_type in ('group', 'supergroup'):
        register_user(chat_id, from_)

    if 'new_chat_members' in msg:
        bot_info = await api_call('getMe', {})
        bot_id = (bot_info or {}).get('id')
        for m in msg['new_chat_members']:
            if bot_id and m['id'] == bot_id:
                await send(chat_id, '✅ تم تفعيل المجموعة بنجاح 🌹\nأنا بوت روميو جاهز لخدمة المجموعة!')
                return

    if chat_type == 'private':
        uid_str = str(user_id)
        if uid_str in private_states:
            pstate = private_states[uid_str]
            if pstate.get('step') == 'await_kursi_question':
                if text:
                    group_chat_id = pstate['group_chat_id']
                    chosen_name = pstate.get('chosen_name', 'الشخص المختار')
                    chosen_id = pstate.get('chosen_id')
                    data = load_data()
                    game = get_game_state(data, group_chat_id)
                    bot_un = await get_bot_username()
                    if game.get('active') and game.get('chosen_id'):
                        if game['questions_count'] < 50:
                            game['questions_count'] += 1
                            save_data(data)
                            remaining = 50 - game['questions_count']
                            chosen_mention = f'<a href="tg://user?id={chosen_id}">{chosen_name}</a>'
                            await send(group_chat_id,
                                f'❓ السؤال إليك {chosen_mention} .\n\n'
                                f'<b>{text}</b>\n\n'
                                f'📊 السؤال {game["questions_count"]}/50 | المتبقي: {remaining}',
                                {
                                    'reply_markup': {
                                        'inline_keyboard': [[
                                            {'text': '❓ اسالوه هنا', 'url': f'https://t.me/{bot_un}?start=kursi_{group_chat_id}'}
                                        ]]
                                    }
                                }
                            )
                            del private_states[uid_str]
                            await send(user_id, '✅ تم إرسال سؤالك بشكل مجهول!')
                        else:
                            del private_states[uid_str]
                            await send(user_id, '⚠️ انتهت الأسئلة المسموحة (50 سؤال)')
                    else:
                        del private_states[uid_str]
                        await send(user_id, '⚠️ اللعبة انتهت')
                else:
                    await send(user_id, '⚠️ أرسل نص السؤال فقط')
                return

            if pstate.get('step') == 'await_whisper_text':
                if text:
                    whisper_id = pstate.get('whisper_id')
                    w = whispers.get(whisper_id)
                    if not w:
                        del private_states[uid_str]
                        await send(user_id, '⚠️ الهمسه انتهت')
                        return
                    w['text'] = text
                    del private_states[uid_str]
                    group_chat_id = w['group_chat_id']
                    recipient_mention = f'<a href="tg://user?id={w["recipient_id"]}">{w["recipient_name"]}</a>'
                    sender_mention = f'<a href="tg://user?id={w["sender_id"]}">{w["sender_name"]}</a>'
                    await send(group_chat_id,
                        f'‹‹ الهمسه لـ ‹‹ {recipient_mention}\n‹‹ من ‹‹ {sender_mention}\n─',
                        {
                            'reply_markup': {
                                'inline_keyboard': [[
                                    {'text': 'رؤية الهمسة', 'callback_data': f'vw:{whisper_id}'}
                                ]]
                            }
                        }
                    )
                    await send(user_id, '✅ تم إرسال الهمسه!')
                else:
                    await send(user_id, '⚠️ أرسل نص الهمسه')
                return

        if text in ('/info', 'info'):
            is_dev = await is_developer(user_id, from_.get('username'))
            if is_dev:
                data_info = load_data()
                group_settings = data_info.get('group_settings', {})
                adders_raw = data_info.get('bot_adders', {})
                total_groups = len(group_settings)
                lines_out = [f'📊 <b>إحصائيات بوت روميو</b>\n']
                lines_out.append(f'🗂 عدد المجموعات: <b>{total_groups}</b>\n')
                lines_out.append('─────────────────')
                for gid in list(group_settings.keys()):
                    try:
                        ginfo = await get_chat(int(gid))
                        if not ginfo:
                            continue
                        gtitle = ginfo.get('title', 'مجموعة مجهولة')
                        gusername = ginfo.get('username')
                        try:
                            ginvite = await api_call('exportChatInviteLink', {'chat_id': int(gid)})
                        except:
                            ginvite = None
                        glink = f'https://t.me/{gusername}' if gusername else (ginvite or 'لا يوجد رابط')
                        adder_info = adders_raw.get(str(gid), {})
                        adder_name_v = adder_info.get('name', 'غير معروف')
                        adder_username_v = adder_info.get('username', '')
                        adder_id_v = adder_info.get('id', '')
                        adder_tag = f'@{adder_username_v}' if adder_username_v else f'id:{adder_id_v}'
                        lines_out.append(
                            f'\n📌 <b>{gtitle}</b>\n'
                            f'🔗 {glink}\n'
                            f'👤 المضيف: {adder_name_v} | {adder_tag}\n'
                            f'🆔 آيدي المضيف: <code>{adder_id_v}</code>'
                        )
                    except:
                        pass
                full_text = '\n'.join(lines_out)
                if len(full_text) > 4000:
                    chunks = [full_text[i:i+4000] for i in range(0, len(full_text), 4000)]
                    for chunk in chunks:
                        await send(user_id, chunk)
                else:
                    await send(user_id, full_text)
                return

        if text and text.startswith('/start'):
            await handle_start(msg)
        return

    data = load_data()
    state = load_state()
    settings = get_settings(data, chat_id)
    cid = str(chat_id)

    if 'new_chat_members' in msg:
        if settings['lock_join']:
            for m in msg['new_chat_members']:
                await ban(chat_id, m['id'])
                await unban(chat_id, m['id'])
        elif not settings['disable_welcome']:
            chat_info = await get_chat(chat_id)
            chat_name = (chat_info or {}).get('title', 'المجموعة')
            for m in msg['new_chat_members']:
                await send(chat_id, f'🌹 أهلا بك يا {mention(m)}\nنورت في مجموعة <b>{chat_name}</b>\nأنا بوت روميو 🤖')
        save_data(data)
        return

    if settings['lock_chat'] and not (await is_tg_admin(chat_id, user_id)) and not (await is_master(data, chat_id, user_id)):
        await delete(chat_id, msg_id)
        save_data(data)
        return

    if settings.get('lock_online') and msg.get('via_bot') and not (await is_tg_admin(chat_id, user_id)) and not (await is_master(data, chat_id, user_id)):
        await delete(chat_id, msg_id)
        save_data(data)
        return

    user_state = (state.get(cid) or {}).get(str(user_id))
    if user_state:
        await handle_state(msg, data, state, user_state, text)
        save_data(data)
        save_state(state)
        return

    # ===== عداد الرسائل =====
    if user_id and not from_.get('is_bot'):
        increment_msg_count(data, chat_id, user_id, from_.get('first_name'))

    if not text:
        await media_mod(msg, data, settings)
        save_data(data)
        return

    if await content_mod(msg, data, settings):
        save_data(data)
        return

    await process_cmd(msg, data, state, text, settings)
    save_data(data)
    save_state(state)

# ===========================
# EDITED MESSAGE HANDLER
# ===========================

async def handle_edited_message(msg):
    chat_type = msg.get('chat', {}).get('type', 'private')
    if chat_type == 'private':
        return

    chat_id = msg['chat']['id']
    msg_id = msg['message_id']
    from_ = msg.get('from', {})
    user_id = from_.get('id')

    if await is_tg_admin(chat_id, user_id):
        return

    data = load_data()
    settings = get_settings(data, chat_id)

    if settings.get('clean_auto') and settings.get('clean_edited'):
        add_to_clean_queue(chat_id, msg_id, 'edited')
    elif settings.get('lock_media_edit'):
        has_photo = bool(msg.get('photo'))
        has_media = has_photo or bool(msg.get('video')) or bool(msg.get('document')) or bool(msg.get('animation'))
        if has_media:
            await delete(chat_id, msg_id)
            m = mention(from_)
            chat_info = await get_chat(chat_id)
            owner_username = None
            admins = await api_call('getChatAdministrators', {'chat_id': chat_id})
            if admins:
                owner = next((a for a in admins if a.get('status') == 'creator'), None)
                if owner:
                    owner_username = owner['user'].get('username') or name(owner['user'])
            owner_tag = f'@{owner_username}' if owner_username else 'المالك'
            await send(chat_id,
                f'⚠️ {m}\nممنوع تعديل الصور والميديا هنا\n\n{owner_tag}',
                {'reply_to_message_id': msg_id})

# ===========================
# BOT ADDED AS ADMIN HANDLER
# ===========================

async def notify_developer_group_added(chat, added_by):
    try:
        chat_id = chat.get('id')
        chat_title = chat.get('title', 'غير معروف')
        chat_username = chat.get('username')
        adder_id = added_by.get('id')
        adder_username = added_by.get('username')
        adder_name = ((added_by.get('first_name') or '') + ' ' + (added_by.get('last_name') or '')).strip() or 'مجهول'

        invite_link = None
        try:
            invite_link = await api_call('exportChatInviteLink', {'chat_id': chat_id})
        except:
            pass

        group_link = f'https://t.me/{chat_username}' if chat_username else (invite_link or 'لا يوجد رابط')

        msg_text = (
            f'📣 <b>تم إضافة البوت لمجموعة جديدة</b>\n\n'
            f'📌 اسم المجموعة: <b>{chat_title}</b>\n'
            f'🆔 آيدي المجموعة: <code>{chat_id}</code>\n'
            f'🔗 رابط المجموعة: {group_link}\n\n'
            f'👤 أضافه:\n'
            f'  الاسم: <b>{adder_name}</b>\n'
            f'  اليوزر: {"@" + adder_username if adder_username else "لا يوجد"}\n'
            f'  الآيدي: <code>{adder_id}</code>'
        )
        try:
            data_save = load_data()
            if 'bot_adders' not in data_save:
                data_save['bot_adders'] = {}
            data_save['bot_adders'][str(chat_id)] = {
                'name': adder_name,
                'username': adder_username or '',
                'id': adder_id,
            }
            save_data(data_save)
        except:
            pass
        try:
            dev_info = await api_call('getChat', {'chat_id': f'@{DEVELOPER_USERNAME}'})
            if dev_info:
                dev_id = dev_info.get('id')
                await api_call('sendMessage', {
                    'chat_id': dev_id,
                    'text': msg_text,
                    'parse_mode': 'HTML'
                })
        except:
            pass
    except Exception as e:
        print(f'Developer notify error: {e}')

async def handle_my_chat_member(update):
    new_status = update.get('new_chat_member', {}).get('status')
    chat = update.get('chat', {})
    chat_type = chat.get('type', '')
    chat_id = chat.get('id')
    from_user = update.get('from', {})
    if new_status == 'administrator' and chat_type in ['group', 'supergroup']:
        await send(chat_id, '✅ تم تفعيل المجموعة بنجاح 🌹\nأنا بوت روميو جاهز لإدارة المجموعة!')
        asyncio.create_task(notify_developer_group_added(chat, from_user))

# ===========================
# START COMMAND - PRIVATE ONLY
# ===========================

async def handle_start(msg):
    chat_id = msg['chat']['id']
    from_ = msg['from']
    user_id = from_['id']
    user_name = name(from_)
    raw_text = (msg.get('text') or '').strip()
    parts = raw_text.split(' ', 1)
    param = parts[1] if len(parts) > 1 else ''

    if param.startswith('kursi_'):
        try:
            group_chat_id = int(param[6:])
            data = load_data()
            game = get_game_state(data, group_chat_id)
            if not game.get('active') or not game.get('chosen_id'):
                await send(chat_id, '⚠️ اللعبة انتهت أو لا توجد لعبة نشطة الآن')
                return
            if game['questions_count'] >= 50:
                await send(chat_id, '⚠️ انتهت الأسئلة المسموحة (50 سؤال)')
                return
            private_states[str(user_id)] = {
                'step': 'await_kursi_question',
                'group_chat_id': group_chat_id,
                'chosen_id': game['chosen_id'],
                'chosen_name': game.get('chosen_name', 'الشخص المختار')
            }
            await send(chat_id,
                f'✏️ أرسل سؤالك الآن\n\n'
                f'سيُرسَل السؤال بشكل مجهول إلى المجموعة\n'
                f'اقصى عدد للأسئلة: 50 سؤال\n\n'
                f'أرسل السؤال:'
            )
            return
        except:
            pass

    if param.startswith('w_'):
        whisper_id = param[2:]
        w = whispers.get(whisper_id)
        if not w:
            await send(chat_id, '⚠️ الهمسه انتهت أو غير موجودة')
            return
        if user_id != w['sender_id']:
            await send(chat_id, '⚠️ هذه الهمسه ليست لك')
            return
        private_states[str(user_id)] = {
            'step': 'await_whisper_text',
            'whisper_id': whisper_id
        }
        await send(chat_id, f'✏️ أرسل نص الهمسه لـ {w.get("recipient_name", "الشخص")}:')
        return

    bot_info = await api_call('getMe', {})
    bot_username = (bot_info or {}).get('username', '')
    add_url = f'https://t.me/{bot_username}?startgroup=true&admin=change_info+delete_messages+restrict_members+invite_users+pin_messages+manage_topics+manage_video_chats'
    keyboard = {'inline_keyboard': [
        [{'text': '➕ اضفني لمجموعتك', 'url': add_url}],
        [{'text': '👨‍💻 المطور', 'url': 'https://t.me/c9aac'}]
    ]}
    text = (
        f'👋 أهلاً عزيزي <b>{user_name}</b>\n\n'
        f'🌹 أنا بوت <b>روميو</b>\n\n'
        f'يمكنك إضافتي لأي مجموعة كمشرف لإدارتها\n\n'
        f'💡 اضغط الزر أدناه لاختيار مجموعة وإدارتها'
    )
    await send(chat_id, text, {'reply_markup': keyboard})

# ===========================
# MEDIA MODERATION
# ===========================

async def media_mod(msg, data, settings):
    chat_id = msg['chat']['id']
    msg_id = msg['message_id']
    from_ = msg.get('from', {})
    user_id = from_.get('id')
    m = mention(from_)
    uname = name(from_)
    reply = {'reply_to_message_id': msg_id}

    if await is_master(data, chat_id, user_id):
        return

    if settings.get('lock_repeat'):
        cid_key = str(chat_id)
        uid_key = str(user_id)
        now_ts = time.time()
        max_msgs = settings.get('repeat_max_messages', 5)
        seconds = settings.get('repeat_seconds', 7)
        if cid_key not in repeat_tracker:
            repeat_tracker[cid_key] = {}
        if uid_key not in repeat_tracker[cid_key]:
            repeat_tracker[cid_key][uid_key] = []
        repeat_tracker[cid_key][uid_key] = [
            t for t in repeat_tracker[cid_key][uid_key]
            if now_ts - t < seconds
        ]
        repeat_tracker[cid_key][uid_key].append(now_ts)
        if len(repeat_tracker[cid_key][uid_key]) > max_msgs:
            repeat_tracker[cid_key][uid_key] = []
            await delete(chat_id, msg_id)
            if settings.get('lock_repeat_restrict'):
                await restrict(chat_id, user_id, {
                    'can_send_messages': False, 'can_send_media_messages': False,
                    'can_send_polls': False, 'can_send_other_messages': False,
                    'can_add_web_page_previews': False
                })
                await send(chat_id, '‹‹ تم تقييد العضو ' + m + ' بسبب التكرار .')
            elif settings.get('lock_repeat_warn'):
                warn_max = settings.get('repeat_warn_max', 3)
                if cid_key not in repeat_warn_tracker:
                    repeat_warn_tracker[cid_key] = {}
                if uid_key not in repeat_warn_tracker[cid_key]:
                    repeat_warn_tracker[cid_key][uid_key] = {'count': 0, 'timestamps': []}
                user_warn = repeat_warn_tracker[cid_key][uid_key]
                if 'last_text' in user_warn and 'timestamps' not in user_warn:
                    user_warn['timestamps'] = []
                    del user_warn['last_text']
                user_warn['count'] = user_warn.get('count', 0) + 1
                if user_warn['count'] >= warn_max:
                    user_warn['count'] = 0
                    await restrict(chat_id, user_id, {
                        'can_send_messages': False, 'can_send_media_messages': False,
                        'can_send_polls': False, 'can_send_other_messages': False,
                        'can_add_web_page_previews': False
                    })
                    await send(chat_id, '‹‹ تم تقييد العضو ' + m + ' بسبب تجاوز عدد التحذيرات .')
                else:
                    await send(chat_id,
                        '‹‹ تحذير ' + str(user_warn['count']) + '/' + str(warn_max) + '\n'
                        '‹‹ ممنوع التكرار هنا .',
                        reply)
            else:
                await send(chat_id, '‹‹ ممنوع التكرار هنا .', reply)
            return

    if settings.get('lock_external_reply') and msg.get('external_reply'):
        await delete(chat_id, msg_id)
        return

    if settings.get('lock_quote') and _is_quote_message(msg):
        await delete(chat_id, msg_id)
        return

    is_forward = msg.get('forward_from') or msg.get('forward_from_chat') or msg.get('forward_sender_name')
    if is_forward and settings['lock_forward']:
        await delete(chat_id, msg_id)
        return

    if msg.get('document'):
        if settings.get('lock_files'):
            await delete(chat_id, msg_id)
            return
        if settings.get('clean_auto') and settings.get('clean_files'):
            add_to_clean_queue(chat_id, msg_id, 'files')
            return

    if msg.get('animation'):
        anim = msg['animation']
        nsfw_active = settings.get('lock_id_documents') or settings.get('lock_nsfw') or settings.get('lock_nsfw_restrict') or settings.get('lock_nsfw_warn')
        if nsfw_active:
            thumb_id = (anim.get('thumbnail') or {}).get('file_id') or anim.get('file_id')
            if thumb_id:
                is_violation, violation_type = await check_image_nsfw(thumb_id)
                if is_violation:
                    if violation_type == 'وثيقة حكومية (هوية/جواز)':
                        await send(chat_id, (
                            f'🚫 <b>تم حذف صورة متحركة مخالفة</b>\n\n'
                            f'👤 المرسل: {m}\n'
                            f'⚠️ نوع المخالفة: <b>{violation_type}</b>\n\n'
                            f'يُمنع إرسال هذا النوع من المحتوى في هذه المجموعة ❌'
                        ), reply)
                        await delete(chat_id, msg_id)
                        return
                    if settings.get('lock_nsfw_restrict'):
                        await send(chat_id, (
                            f'🚫 <b>تم حذف صورة متحركة مخالفة وتقييد العضو</b>\n\n'
                            f'👤 المرسل: {m}\n'
                            f'⚠️ نوع المخالفة: <b>{violation_type}</b>\n\n'
                            f'يُمنع إرسال هذا النوع من المحتوى في هذه المجموعة ❌'
                        ), reply)
                        await delete(chat_id, msg_id)
                        await restrict(chat_id, user_id, {
                            'can_send_messages': False, 'can_send_media_messages': False,
                            'can_send_polls': False, 'can_send_other_messages': False,
                            'can_add_web_page_previews': False
                        })
                        return
                    if settings.get('lock_nsfw_warn'):
                        warns = add_warning(data, chat_id, user_id)
                        if warns >= 5:
                            reset_warnings(data, chat_id, user_id)
                            await send(chat_id, (
                                f'🚫 <b>تم تقييد {m}</b>\n\n'
                                f'وصل عدد التحذيرات إلى 5 بسبب إرسال محتوى مخالف\n'
                                f'⚠️ نوع المخالفة: <b>{violation_type}</b>'
                            ), reply)
                            await delete(chat_id, msg_id)
                            await restrict(chat_id, user_id, {
                                'can_send_messages': False, 'can_send_media_messages': False,
                                'can_send_polls': False, 'can_send_other_messages': False,
                                'can_add_web_page_previews': False
                            })
                        else:
                            await send(chat_id, (
                                f'⚠️ <b>تحذير {warns}/5</b> لـ {m}\n\n'
                                f'نوع المخالفة: <b>{violation_type}</b>\n'
                                f'عند الوصول لـ 5 تحذيرات سيتم تقييدك ❌'
                            ), reply)
                            await delete(chat_id, msg_id)
                        return
                    await send(chat_id, (
                        f'🚫 <b>تم حذف صورة متحركة مخالفة</b>\n\n'
                        f'👤 المرسل: {m}\n'
                        f'⚠️ نوع المخالفة: <b>{violation_type}</b>\n\n'
                        f'يُمنع إرسال هذا النوع من المحتوى في هذه المجموعة ❌'
                    ), reply)
                    await delete(chat_id, msg_id)
                    return
        if settings['lock_videos']:
            await delete(chat_id, msg_id)
            return
        if settings.get('clean_auto'):
            add_to_clean_queue(chat_id, msg_id, 'videos')
        return

    if msg.get('photo'):
        photo_list = msg['photo']
        file_id = photo_list[-1]['file_id']

        nsfw_active = settings.get('lock_id_documents') or settings.get('lock_nsfw') or settings.get('lock_nsfw_restrict') or settings.get('lock_nsfw_warn')
        if nsfw_active:
            is_violation, violation_type = await check_image_nsfw(file_id)
            if is_violation:
                if violation_type == 'وثيقة حكومية (هوية/جواز)':
                    await send(chat_id, (
                        f'🚫 <b>تم حذف صورة مخالفة</b>\n\n'
                        f'👤 المرسل: {m}\n'
                        f'⚠️ نوع المخالفة: <b>{violation_type}</b>\n\n'
                        f'يُمنع إرسال هذا النوع من المحتوى في هذه المجموعة ❌'
                    ), reply)
                    await delete(chat_id, msg_id)
                    return
                if settings.get('lock_nsfw_restrict'):
                    await send(chat_id, (
                        f'🚫 <b>تم حذف صورة مخالفة وتقييد العضو</b>\n\n'
                        f'👤 المرسل: {m}\n'
                        f'⚠️ نوع المخالفة: <b>{violation_type}</b>\n\n'
                        f'يُمنع إرسال هذا النوع من المحتوى في هذه المجموعة ❌'
                    ), reply)
                    await delete(chat_id, msg_id)
                    await restrict(chat_id, user_id, {
                        'can_send_messages': False, 'can_send_media_messages': False,
                        'can_send_polls': False, 'can_send_other_messages': False,
                        'can_add_web_page_previews': False
                    })
                    return
                if settings.get('lock_nsfw_warn'):
                    warns = add_warning(data, chat_id, user_id)
                    if warns >= 5:
                        reset_warnings(data, chat_id, user_id)
                        await send(chat_id, (
                            f'🚫 <b>تم تقييد {m}</b>\n\n'
                            f'وصل عدد التحذيرات إلى 5 بسبب إرسال محتوى مخالف\n'
                            f'⚠️ نوع المخالفة: <b>{violation_type}</b>'
                        ), reply)
                        await delete(chat_id, msg_id)
                        await restrict(chat_id, user_id, {
                            'can_send_messages': False, 'can_send_media_messages': False,
                            'can_send_polls': False, 'can_send_other_messages': False,
                            'can_add_web_page_previews': False
                        })
                    else:
                        await send(chat_id, (
                            f'⚠️ <b>تحذير {warns}/5</b> لـ {m}\n\n'
                            f'نوع المخالفة: <b>{violation_type}</b>\n'
                            f'عند الوصول لـ 5 تحذيرات سيتم تقييدك ❌'
                        ), reply)
                        await delete(chat_id, msg_id)
                    return
                if settings.get('lock_nsfw'):
                    await send(chat_id, (
                        f'🚫 <b>تم حذف صورة مخالفة</b>\n\n'
                        f'👤 المرسل: {m}\n'
                        f'⚠️ نوع المخالفة: <b>{violation_type}</b>\n\n'
                        f'يُمنع إرسال هذا النوع من المحتوى في هذه المجموعة ❌'
                    ), reply)
                    await delete(chat_id, msg_id)
                    return

        if settings['lock_photos']:
            await delete(chat_id, msg_id)
            return

        if settings.get('clean_auto'):
            add_to_clean_queue(chat_id, msg_id, 'photos')
        return

    if msg.get('video'):
        video = msg['video']
        nsfw_active = settings.get('lock_id_documents') or settings.get('lock_nsfw') or settings.get('lock_nsfw_restrict') or settings.get('lock_nsfw_warn')
        if nsfw_active:
            thumb_id = (video.get('thumbnail') or {}).get('file_id')
            vid_file_id = video.get('file_id')
            is_violation, violation_type = False, None
            if thumb_id:
                is_violation, violation_type = await check_image_nsfw(thumb_id)
            if not is_violation and vid_file_id:
                is_violation, violation_type = await check_video_nsfw(vid_file_id)
            if is_violation:
                if violation_type == 'وثيقة حكومية (هوية/جواز)':
                    await send(chat_id, (
                        f'🚫 <b>تم حذف فيديو مخالف</b>\n\n'
                        f'👤 المرسل: {m}\n'
                        f'⚠️ نوع المخالفة: <b>{violation_type}</b>\n\n'
                        f'يُمنع إرسال هذا النوع من المحتوى في هذه المجموعة ❌'
                    ), reply)
                    await delete(chat_id, msg_id)
                    return
                if settings.get('lock_nsfw_restrict'):
                    await send(chat_id, (
                        f'🚫 <b>تم حذف فيديو مخالف وتقييد العضو</b>\n\n'
                        f'👤 المرسل: {m}\n'
                        f'⚠️ نوع المخالفة: <b>{violation_type}</b>\n\n'
                        f'يُمنع إرسال هذا النوع من المحتوى في هذه المجموعة ❌'
                    ), reply)
                    await delete(chat_id, msg_id)
                    await restrict(chat_id, user_id, {
                        'can_send_messages': False, 'can_send_media_messages': False,
                        'can_send_polls': False, 'can_send_other_messages': False,
                        'can_add_web_page_previews': False
                    })
                    return
                if settings.get('lock_nsfw_warn'):
                    warns = add_warning(data, chat_id, user_id)
                    if warns >= 5:
                        reset_warnings(data, chat_id, user_id)
                        await send(chat_id, (
                            f'🚫 <b>تم تقييد {m}</b>\n\n'
                            f'وصل عدد التحذيرات إلى 5 بسبب إرسال محتوى مخالف\n'
                            f'⚠️ نوع المخالفة: <b>{violation_type}</b>'
                        ), reply)
                        await delete(chat_id, msg_id)
                        await restrict(chat_id, user_id, {
                            'can_send_messages': False, 'can_send_media_messages': False,
                            'can_send_polls': False, 'can_send_other_messages': False,
                            'can_add_web_page_previews': False
                        })
                    else:
                        await send(chat_id, (
                            f'⚠️ <b>تحذير {warns}/5</b> لـ {m}\n\n'
                            f'نوع المخالفة: <b>{violation_type}</b>\n'
                            f'عند الوصول لـ 5 تحذيرات سيتم تقييدك ❌'
                        ), reply)
                        await delete(chat_id, msg_id)
                    return
                await send(chat_id, (
                    f'🚫 <b>تم حذف فيديو مخالف</b>\n\n'
                    f'👤 المرسل: {m}\n'
                    f'⚠️ نوع المخالفة: <b>{violation_type}</b>\n\n'
                    f'يُمنع إرسال هذا النوع من المحتوى في هذه المجموعة ❌'
                ), reply)
                await delete(chat_id, msg_id)
                return
        if settings['lock_videos']:
            await delete(chat_id, msg_id)
            return
        if settings.get('clean_auto'):
            add_to_clean_queue(chat_id, msg_id, 'videos')
        return

    if msg.get('voice') and settings['lock_audio']:
        await delete(chat_id, msg_id)
        return

    if msg.get('audio') and settings['lock_music']:
        await delete(chat_id, msg_id)
        return

    if msg.get('contact') and settings.get('lock_contacts'):
        await delete(chat_id, msg_id)
        return

    if msg.get('sticker'):
        sticker = msg['sticker']
        is_animated = sticker.get('is_animated') or sticker.get('is_video')

        if settings.get('lock_id_documents') or settings.get('lock_nsfw') or settings.get('lock_nsfw_restrict') or settings.get('lock_nsfw_warn'):
            if sticker.get('is_video'):
                is_violation, violation_type = await check_video_nsfw(sticker.get('file_id'))
                if not is_violation:
                    thumb_id = (sticker.get('thumbnail') or {}).get('file_id')
                    if thumb_id:
                        is_violation, violation_type = await check_image_nsfw(thumb_id)
            else:
                sticker_file_id = (sticker.get('thumbnail') or {}).get('file_id') or sticker.get('file_id')
                is_violation, violation_type = await check_image_nsfw(sticker_file_id) if sticker_file_id else (False, None)
            if is_violation and violation_type == 'وثيقة حكومية (هوية/جواز)':
                await send(chat_id, f'🚫 <b>تم حذف ملصق مخالف</b>\n\n👤 المرسل: {m}\n⚠️ نوع المخالفة: <b>{violation_type}</b>\n\nيُمنع إرسال هذا النوع من المحتوى في هذه المجموعة ❌', reply)
                await delete(chat_id, msg_id)
                return
            if is_violation:
                if settings.get('lock_nsfw_restrict'):
                    await send(chat_id, f'🚫 <b>تم حذف ملصق مخالف وتقييد العضو</b>\n\n👤 المرسل: {m}\n⚠️ نوع المخالفة: <b>{violation_type}</b>', reply)
                    await delete(chat_id, msg_id)
                    await restrict(chat_id, user_id, {
                        'can_send_messages': False, 'can_send_media_messages': False,
                        'can_send_polls': False, 'can_send_other_messages': False,
                        'can_add_web_page_previews': False
                    })
                elif settings.get('lock_nsfw_warn'):
                    warns = add_warning(data, chat_id, user_id)
                    if warns >= 5:
                        reset_warnings(data, chat_id, user_id)
                        await send(chat_id, f'🚫 <b>تم تقييد {m}</b>\n\nوصل عدد التحذيرات إلى 5 بسبب إرسال ملصق مخالف', reply)
                        await delete(chat_id, msg_id)
                        await restrict(chat_id, user_id, {
                            'can_send_messages': False, 'can_send_media_messages': False,
                            'can_send_polls': False, 'can_send_other_messages': False,
                            'can_add_web_page_previews': False
                        })
                    else:
                        await send(chat_id, f'⚠️ <b>تحذير {warns}/5</b> لـ {m}\n\nنوع المخالفة: <b>{violation_type}</b>', reply)
                        await delete(chat_id, msg_id)
                else:
                    await send(chat_id, f'🚫 <b>تم حذف ملصق مخالف</b>\n\n👤 المرسل: {m}\n⚠️ نوع المخالفة: <b>{violation_type}</b>', reply)
                    await delete(chat_id, msg_id)
                return

        if is_animated and settings['lock_animated']:
            await delete(chat_id, msg_id)
            return
        if not is_animated and settings['lock_stickers']:
            await delete(chat_id, msg_id)
            return
        if settings.get('clean_auto'):
            add_to_clean_queue(chat_id, msg_id, 'stickers')
        return

# ===========================
# CONTENT MODERATION
# ===========================

def _is_quote_message(msg):
    """
    يرصد جميع أشكال الاقتباس:
    1. اقتباس Telegram الحقيقي (quote field)
    2. اقتباس مزيف بعلامات خاصة مثل: " ها اين انت
    """
    # الاقتباس الحقيقي من Telegram
    if msg.get('quote'):
        return True
    # الاقتباس المزيف: النص يبدأ بعلامة اقتباس خاصة
    text = (msg.get('text') or msg.get('caption') or '').lstrip()
    FAKE_QUOTE_CHARS = (
        '\u201c',  # " LEFT DOUBLE QUOTATION MARK
        '\u201d',  # " RIGHT DOUBLE QUOTATION MARK
        '\u275d',  # ❝ HEAVY DOUBLE TURNED COMMA QUOTATION MARK
        '\u275e',  # ❞ HEAVY DOUBLE COMMA QUOTATION MARK
        '\u00bb',  # » RIGHT-POINTING DOUBLE ANGLE QUOTATION
        '\u00ab',  # « LEFT-POINTING DOUBLE ANGLE QUOTATION
        '\u2018',  # ' LEFT SINGLE QUOTATION MARK
        '\u2019',  # ' RIGHT SINGLE QUOTATION MARK
        '\u276e',  # ❮
        '\u276f',  # ❯
        '|',       # | شرطة عمودية تُستخدم أحياناً لتقليد الاقتباس
    )
    if text.startswith(FAKE_QUOTE_CHARS):
        return True
    return False

def _has_phone_number(text):
    """
    يرصد أرقام الهواتف بجميع الأشكال:
    - 07758483023
    - +9647758483023
    - +964 775 848 3023
    - 775 848 3023
    - 0775-848-3023
    """
    # الشكل الكلاسيكي: أرقام متصلة 9-13 رقم
    if re.search(r'(?<!\d)\+?\d{9,13}(?!\d)', text):
        return True
    # الشكل مع مسافات أو شرطات بين المجموعات
    for match in re.finditer(r'\+?(?:\d[ \-]?){8,15}\d', text):
        digits_only = re.sub(r'[^\d]', '', match.group())
        if 9 <= len(digits_only) <= 14:
            return True
    return False

async def content_mod(msg, data, settings):
    chat_id = msg['chat']['id']
    msg_id = msg['message_id']
    from_ = msg.get('from', {})
    user_id = from_.get('id')
    text = msg.get('text') or msg.get('caption') or ''
    m = mention(from_)
    reply = {'reply_to_message_id': msg_id}

    if await is_master(data, chat_id, user_id):
        return False

    uname = name(from_)

    if settings.get('lock_external_reply') and msg.get('external_reply'):
        await delete(chat_id, msg_id)
        return True

    if settings.get('lock_quote') and _is_quote_message(msg):
        await delete(chat_id, msg_id)
        return True

    is_forward = msg.get('forward_from') or msg.get('forward_from_chat') or msg.get('forward_sender_name')
    if is_forward and settings['lock_forward']:
        await delete(chat_id, msg_id)
        return True

    swears = ['انيجك', 'انيج امك', 'كسمك', 'عير بابوك', 'عير بامك', 'قحبه', 'كحبه', 'شرموط', 'شرموطه', 'زبفيك', 'عيرك', 'كسي', 'زبي', 'عيري', 'كس', 'عيربمك', 'عير', 'عيربيك', 'ابن الشرموطه', 'ممحونه', 'ممحون', 'كسختك', 'كسعرضك', 'ابنلكحبه']
    if settings['lock_swear'] and any(w in text for w in swears):
        await delete(chat_id, msg_id)
        return True

    if settings['lock_links'] and re.search(r'(https?://|t\.me/|www\.)', text, re.IGNORECASE):
        await delete(chat_id, msg_id)
        return True

    if settings['lock_mention'] and re.search(r'@\w+', text):
        await delete(chat_id, msg_id)
        return True

    if settings['lock_numbers'] and _has_phone_number(text):
        await delete(chat_id, msg_id)
        return True

    if settings['lock_clutter'] and len(text) > 1000:
        await delete(chat_id, msg_id)
        return True

    if settings['lock_english'] and re.search(r'[a-zA-Z]', text):
        await delete(chat_id, msg_id)
        return True

    if settings['lock_chinese'] and re.search(r'[\u4e00-\u9fff]', text):
        await delete(chat_id, msg_id)
        return True

    if settings['lock_russian'] and re.search(r'[\u0400-\u04FF]', text):
        await delete(chat_id, msg_id)
        return True

    if settings.get('lock_all_usernames') and re.search(r'@\w+', text):
        await delete(chat_id, msg_id)
        return True

    if settings.get('lock_channel_usernames'):
        mentions = re.findall(r'@(\w+)', text)
        for uname_found in mentions:
            try:
                ch = await api_call('getChat', {'chat_id': f'@{uname_found}'})
                if ch and ch.get('type', '') in ['channel', 'supergroup', 'group']:
                    await delete(chat_id, msg_id)
                    return True
            except:
                pass

    bw_list = settings.get('banned_words', [])
    if bw_list and text:
        text_lower = text.lower()
        for bw in bw_list:
            if bw in text_lower:
                await delete(chat_id, msg_id)
                if settings.get('bw_restrict_mode'):
                    # تقييد مباشر عند استخدام كلمة محظورة
                    await restrict(chat_id, user_id, {
                        'can_send_messages': False,
                        'can_send_audios': False,
                        'can_send_documents': False,
                        'can_send_photos': False,
                        'can_send_videos': False,
                        'can_send_video_notes': False,
                        'can_send_voice_notes': False,
                        'can_send_polls': False,
                        'can_send_other_messages': False,
                    })
                    await send(chat_id, f'🚫 {mention(from_)} تم تقييده بسبب استخدام كلمة محظورة', reply)
                elif settings.get('bw_warn_mode'):
                    # تحذير وتقييد بعد 5 تحذيرات
                    cid_key = str(chat_id)
                    uid_key = str(user_id)
                    warn_max = settings.get('bw_warn_max', 5)
                    if cid_key not in bw_warn_tracker:
                        bw_warn_tracker[cid_key] = {}
                    bw_warn_tracker[cid_key][uid_key] = bw_warn_tracker[cid_key].get(uid_key, 0) + 1
                    warns = bw_warn_tracker[cid_key][uid_key]
                    if warns >= warn_max:
                        bw_warn_tracker[cid_key][uid_key] = 0
                        await restrict(chat_id, user_id, {
                            'can_send_messages': False,
                            'can_send_audios': False,
                            'can_send_documents': False,
                            'can_send_photos': False,
                            'can_send_videos': False,
                            'can_send_video_notes': False,
                            'can_send_voice_notes': False,
                            'can_send_polls': False,
                            'can_send_other_messages': False,
                        })
                        await send(chat_id, f'🚫 {mention(from_)} تم تقييده بعد تجاوز {warn_max} تحذيرات بسبب الكلمات المحظورة', reply)
                    else:
                        remaining = warn_max - warns
                        await send(chat_id,
                            f'⚠️ {mention(from_)} | تحذير <b>{warns}/{warn_max}</b> بسبب كلمة محظورة\n'
                            f'متبقي <b>{remaining}</b> تحذير قبل التقييد', reply)
                return True

    if settings['lock_repeat']:
        cid_key = str(chat_id)
        uid_key = str(user_id)
        now_ts = time.time()
        if settings.get('lock_repeat_restrict'):
            max_msgs = settings.get('repeat_max_messages', 3)
            seconds = settings.get('repeat_seconds', 60)
            if cid_key not in repeat_tracker:
                repeat_tracker[cid_key] = {}
            if uid_key not in repeat_tracker[cid_key]:
                repeat_tracker[cid_key][uid_key] = []
            repeat_tracker[cid_key][uid_key] = [
                t for t in repeat_tracker[cid_key][uid_key]
                if now_ts - t < seconds
            ]
            repeat_tracker[cid_key][uid_key].append(now_ts)
            if len(repeat_tracker[cid_key][uid_key]) > max_msgs:
                await delete(chat_id, msg_id)
                await restrict(chat_id, user_id, {
                    'can_send_messages': False, 'can_send_media_messages': False,
                    'can_send_polls': False, 'can_send_other_messages': False,
                    'can_add_web_page_previews': False
                })
                await send(chat_id, '‹‹ تم تقييد العضو ' + mention(from_) + ' بسبب التكرار .')
                return True
        else:
            max_msgs = settings.get('repeat_max_messages', 5)
            seconds = settings.get('repeat_seconds', 7)
            if cid_key not in repeat_tracker:
                repeat_tracker[cid_key] = {}
            if uid_key not in repeat_tracker[cid_key]:
                repeat_tracker[cid_key][uid_key] = []
            repeat_tracker[cid_key][uid_key] = [
                t for t in repeat_tracker[cid_key][uid_key]
                if now_ts - t < seconds
            ]
            repeat_tracker[cid_key][uid_key].append(now_ts)
            if len(repeat_tracker[cid_key][uid_key]) > max_msgs:
                repeat_tracker[cid_key][uid_key] = []
                await send(chat_id, '‹‹ ممنوع التكرار هنا .', reply)
                await delete(chat_id, msg_id)
                return True

    if settings.get('lock_repeat_warn') and not settings.get('lock_repeat_restrict'):
        warn_max = settings.get('repeat_warn_max', 3)
        max_msgs = settings.get('repeat_max_messages', 5)
        seconds = settings.get('repeat_seconds', 7)
        if cid_key not in repeat_warn_tracker:
            repeat_warn_tracker[cid_key] = {}
        if uid_key not in repeat_warn_tracker[cid_key]:
            repeat_warn_tracker[cid_key][uid_key] = {'count': 0, 'timestamps': []}
        user_warn = repeat_warn_tracker[cid_key][uid_key]
        if 'last_text' in user_warn and 'timestamps' not in user_warn:
            user_warn['timestamps'] = []
            del user_warn['last_text']
        user_warn['timestamps'] = [t for t in user_warn.get('timestamps', []) if now_ts - t < seconds]
        user_warn['timestamps'].append(now_ts)
        if len(user_warn['timestamps']) > max_msgs:
            user_warn['timestamps'] = []
            user_warn['count'] = user_warn.get('count', 0) + 1
            if user_warn['count'] >= warn_max:
                await delete(chat_id, msg_id)
                await restrict(chat_id, user_id, {
                    'can_send_messages': False, 'can_send_media_messages': False,
                    'can_send_polls': False, 'can_send_other_messages': False,
                    'can_add_web_page_previews': False
                })
                user_warn['count'] = 0
                await send(chat_id, '‹‹ تم تقييد العضو ' + mention(from_) + ' بسبب تجاوز عدد التحذيرات .')
                return True
            else:
                await delete(chat_id, msg_id)
                await send(chat_id,
                    '‹‹ تحذير ' + str(user_warn['count']) + '/' + str(warn_max) + '\n'
                    '‹‹ ممنوع التكرار هنا .',
                    reply)
                return True

    if settings.get('clean_auto'):
        if settings.get('clean_numbers') and re.search(r'(?<!\d)\+?\d{9,12}(?!\d)', text):
            add_to_clean_queue(chat_id, msg_id, 'numbers')
        if settings.get('clean_clutter') and len(text) > 1000:
            add_to_clean_queue(chat_id, msg_id, 'clutter')

    return False

# ===========================
# COMMAND PROCESSOR
# ===========================

async def process_cmd(msg, data, state, text, settings):
    chat_id = msg['chat']['id']
    msg_id = msg['message_id']
    from_ = msg['from']
    user_id = from_['id']
    m = mention(from_)
    cid = str(chat_id)
    reply = {'reply_to_message_id': msg_id}

    aliases = get_custom_commands(data, chat_id)
    if text in aliases:
        text = aliases[text]

    user_rank = get_rank(data, chat_id, user_id)
    tg_admin = await is_tg_admin(chat_id, user_id)
    global _developer_id_cache
    dev = is_developer_by_username(from_)
    if dev and _developer_id_cache is None:
        _developer_id_cache = user_id
    is_member_only = rank_level(user_rank) < rank_level('ادمن') and not tg_admin and not dev

    # ردود تلقائية - للجميع بمن فيهم الأعضاء
    if not settings.get('disable_auto_replies'):
        if re.match(r'^بوت', text) or text == 'بوت':
            await send(chat_id, random.choice(['وش تبي 😒', 'اهلا 👋', 'شكد مزعج 😤', 'عندي اسم ترا 🌹']), reply)
            return
        if re.match(r'^روميو', text) or text == 'روميو':
            await send(chat_id, random.choice(['قول وش تبي 😊', 'هلا 👋', 'تفضل 🌹', 'لا تلح 😑']), reply)
            return
        if 'صباح الخير' in text:
            await send(chat_id, '☀️ صباح النور', reply)
            return
        if 'سلام عليكم' in text or 'السلام عليكم' in text:
            await send(chat_id, random.choice(['وعليكم السلام والرحمة 🌹', 'وعليكم السلام 👋', 'وعليكم السلام ورحمة الله وبركاته 🤲']), reply)
            return
        if text == 'ها':
            await send(chat_id, 'وجعا 😐', reply)
            return
        if text == 'شتريد':
            await send(chat_id, 'كلشي ماريد 🙂', reply)
            return
        if text == 'انجب':
            await send(chat_id, 'هاي اخلاقك يعني 😑', reply)
            return

    # الردود المخصصة - للجميع
    replies_early = data['custom_replies'].get(cid, {})
    if text in replies_early:
        rd = replies_early[text]
        if rd['type'] == 'text':
            await send(chat_id, rd['content'], reply)
        elif rd['type'] == 'photo':
            await api_call('sendPhoto', {'chat_id': chat_id, 'photo': rd['file_id'], 'caption': rd.get('caption', ''), 'parse_mode': 'HTML', 'reply_to_message_id': msg_id})
        elif rd['type'] == 'video':
            await api_call('sendVideo', {'chat_id': chat_id, 'video': rd['file_id'], 'caption': rd.get('caption', ''), 'parse_mode': 'HTML', 'reply_to_message_id': msg_id})
        return

    # الأعضاء العاديون: إذا حاولوا استخدام أوامر الأدمن، أخبرهم برتبتهم
    ADMIN_CMD_PREFIXES = ['قفل ', 'فتح ', 'تعطيل ', 'تفعيل ', 'رفع مالك', 'تنزيل مالك',
        'رفع مدير', 'تنزيل مدير', 'رفع ادمن', 'تنزيل ادمن', 'رفع مميز', 'تنزيل مميز']
    ADMIN_CMD_EXACT = ['التنظيف', 'اضف رد', 'مسح رد', 'كتم', 'تقييد', 'رفع القيود',
        'الغاء الكتم', 'الغاء التقييد', 'طرد', 'مسح', 'قفل امر', 'اضف امر', 'الاوامر', 'اوامر',
        'الكلمات المحظورة']
    is_admin_cmd = text in ADMIN_CMD_EXACT or any(text.startswith(p) for p in ADMIN_CMD_PREFIXES)

    # نفحص الأوامر المقفولة أولاً لأنها تأخذ الأولوية
    locked_cmds = settings.get('locked_commands', {})
    if text in locked_cmds:
        required_rank = locked_cmds[text]
        if rank_level(user_rank) < rank_level(required_rank) and not tg_admin and not dev:
            await send(chat_id, f'• عذراً الامر يخص ‹ {required_rank} › فقط .', reply)
            return

    if is_member_only and is_admin_cmd:
        await send(chat_id, '• عذراً الامر يخص ‹ ادمن › فقط .', reply)
        return

    # رتبتي / رتبته - مسموح للجميع دائماً بغض النظر عن الخدمية
    if text in ['رتبة', 'رتبتي', 'رتب']:
        if msg.get('reply_to_message'):
            tf = msg['reply_to_message']['from']
            rank = get_rank(data, chat_id, tf['id'])
            if await is_developer(tf['id'], tf.get('username')):
                rank = 'مطور'
            else:
                mem2 = await get_chat_member(chat_id, tf['id'])
                if mem2 and mem2.get('status') == 'creator':
                    rank = 'مالك المجموعة'
                elif mem2 and mem2.get('status') == 'administrator' and rank == 'عضو':
                    rank = 'مشرف'
            await send(chat_id, f'• رتبته هي ← <b>{rank}</b>', reply)
        else:
            if await is_developer(user_id, from_.get('username')):
                await send(chat_id, f'• رتبتك ← <b>مطور</b>', reply)
            else:
                rank_me = get_rank(data, chat_id, user_id)
                mem_me = await get_chat_member(chat_id, user_id)
                if mem_me and mem_me.get('status') == 'creator':
                    rank_me = 'مالك المجموعة'
                elif mem_me and mem_me.get('status') == 'administrator' and rank_me == 'عضو':
                    rank_me = 'مشرف'
                await send(chat_id, f'• رتبتك ← <b>{rank_me}</b>', reply)
        return

    if text in ['رتبته', 'رتبتها']:
        if msg.get('reply_to_message'):
            tf = msg['reply_to_message']['from']
            rank = get_rank(data, chat_id, tf['id'])
            if await is_developer(tf['id'], tf.get('username')):
                rank = 'مطور'
            else:
                mem2 = await get_chat_member(chat_id, tf['id'])
                if mem2 and mem2.get('status') == 'creator':
                    rank = 'مالك المجموعة'
                elif mem2 and mem2.get('status') == 'administrator' and rank == 'عضو':
                    rank = 'مشرف'
            await send(chat_id, f'• رتبته هي ← <b>{rank}</b>', reply)
        else:
            await send(chat_id, '⚠️ رد على رسالة شخص لمعرفة رتبته', reply)
        return

    # رسائلي / رسايلي - مسموح للجميع دائماً
    if text in ['رسائلي', 'رسايلي', 'رسالتي']:
        if msg.get('reply_to_message'):
            tf = msg['reply_to_message'].get('from', {})
            if tf.get('is_bot'):
                await send(chat_id, '• البوتات لا تُحسب رسائلها', reply)
                return
            tuid = tf['id']
            tname = tf.get('first_name', 'هذا الشخص')
            count = get_msg_count(data, chat_id, tuid)
            await send(chat_id,
                f'• عدد رسائل <a href="tg://user?id={tuid}">{tname}</a> ←  {fmt_money(count)} رسالة',
                reply)
        else:
            count = get_msg_count(data, chat_id, user_id)
            await send(chat_id,
                f'• عدد رسائلك ←  {fmt_money(count)} رسالة',
                reply)
        return

    if text in ['رسائله', 'رسايله', 'رسائلها', 'رسايلها']:
        if msg.get('reply_to_message'):
            tf = msg['reply_to_message'].get('from', {})
            if tf.get('is_bot'):
                await send(chat_id, '• البوتات لا تُحسب رسائلها', reply)
                return
            tuid = tf['id']
            tname = tf.get('first_name', 'هذا الشخص')
            count = get_msg_count(data, chat_id, tuid)
            await send(chat_id,
                f'• عدد رسائل <a href="tg://user?id={tuid}">{tname}</a> ←  {fmt_money(count)} رسالة',
                reply)
        else:
            await send(chat_id, '⚠️ رد على رسالة شخص لمعرفة عدد رسائله', reply)
        return

    # التوب / المتفاعلين - للمشرفين وما فوق
    if text in ['التوب', 'توب المتفاعلين', 'المتفاعلين', 'توب']:
        if settings.get('disable_top'):
            return
        if not await is_admin_up(data, chat_id, user_id):
            await send(chat_id, '• هذا الأمر للمشرفين فقط', reply)
            return
        counts = data.get('msg_counts', {}).get(str(chat_id), {})
        if not counts:
            await send(chat_id, '• لا توجد إحصائيات بعد، ابدأ بإرسال الرسائل!', reply)
            return
        sorted_users = sorted(counts.items(), key=lambda x: x[1], reverse=True)[:10]
        lines = ['🏆 <b>توب المتفاعلين</b>\n']
        medals = ['🥇', '🥈', '🥉']
        for i, (uid_str, cnt) in enumerate(sorted_users):
            icon = medals[i] if i < 3 else f'{i+1}.'
            name = get_user_display(data, uid_str)
            lines.append(f'{icon} <a href="tg://user?id={uid_str}">{name}</a> ← {fmt_money(cnt)} رسالة')
        await send(chat_id, '\n'.join(lines), reply)
        return

    # التنظيف - للمالك فقط
    if text == 'التنظيف':
        if not await is_group_creator(chat_id, user_id):
            await send(chat_id, '⛔ هذا الأمر للمالك فقط', reply)
            return
        clean_text, clean_keyboard = build_clean_menu(settings)
        await send(chat_id, clean_text, {'reply_markup': clean_keyboard})
        return

    # الكلمات المحظورة - للمشرفين والمالك
    if text == 'الكلمات المحظورة':
        if not await is_admin_up(data, chat_id, user_id):
            await send(chat_id, '⛔ هذا الأمر للمشرفين فقط', reply)
            return
        bw_list = settings.get('banned_words', [])
        count = len(bw_list)
        warn_on = settings.get('bw_warn_mode', False)
        restrict_on = settings.get('bw_restrict_mode', False)
        mode_txt = '🔕 بدون إجراء' if not warn_on and not restrict_on else ('⚠️ تحذير ثم تقييد' if warn_on else '🔒 تقييد مباشر')
        bw_keyboard = {
            'inline_keyboard': [
                [
                    {'text': '➕ اضافة كلمة', 'callback_data': f'bw_add:{chat_id}'},
                    {'text': '➖ ازالة كلمة', 'callback_data': f'bw_remove:{chat_id}'},
                ],
                [
                    {'text': f'📋 قائمة الكلمات ({count})', 'callback_data': f'bw_list:{chat_id}'},
                ],
                [
                    {'text': f'⚠️ بالتحذير {"✓" if warn_on else "✗"}', 'callback_data': f'bw_toggle_warn:{chat_id}'},
                    {'text': f'🔒 بالتقييد {"✓" if restrict_on else "✗"}', 'callback_data': f'bw_toggle_restrict:{chat_id}'},
                ],
            ]
        }
        await send(chat_id,
            f'🚫 <b>الكلمات المحظورة</b>\n\nعدد الكلمات المحظورة حالياً: <b>{count}</b>\nالوضع الحالي: <b>{mode_txt}</b>\n\nاختر من الأزرار أدناه:',
            {'reply_markup': bw_keyboard, 'reply_to_message_id': msg_id})
        return

    # ===========================
    # أوامر الخدمية
    # ===========================
    if not settings['disable_service']:
        if text == 'بايو':
            if msg.get('reply_to_message'):
                tf = msg['reply_to_message']['from']
                tf_info = await api_call('getChat', {'chat_id': tf['id']})
                bio = (tf_info or {}).get('bio')
                await send(chat_id, f'📋 بايو {mention(tf)}:\n{bio}' if bio else f'😕 {mention(tf)} ما عنده بايو', reply)
            else:
                user_info = await api_call('getChat', {'chat_id': user_id})
                bio = (user_info or {}).get('bio')
                await send(chat_id, f'📋 بايو {m}:\n{bio}' if bio else f'😕 {m} ما عندك بايو', reply)
            return

        if text == 'افتاري':
            photos = await api_call('getUserProfilePhotos', {'user_id': user_id, 'limit': 1})
            if photos and photos.get('total_count', 0) > 0:
                file_id = photos['photos'][0][-1]['file_id']
                await api_call('sendPhoto', {'chat_id': chat_id, 'photo': file_id, 'reply_to_message_id': msg_id})
            else:
                await send(chat_id, f'😕 {m} ما عندك صورة شخصية', reply)
            return

        if text == 'اسمي':
            await send(chat_id, f'👤 اسمك: <b>{name(from_)}</b>', reply)
            return

        if text in ['اسمه', 'اسمها']:
            if msg.get('reply_to_message'):
                await send(chat_id, f'👤 اسم الشخص: <b>{name(msg["reply_to_message"]["from"])}</b>', reply)
            else:
                await send(chat_id, '⚠️ رد على رسالة شخص لمعرفة اسمه', reply)
            return

        if text == 'يوزري':
            uname = from_.get('username')
            await send(chat_id, f'🔗 يوزرك: @{uname}' if uname else '😕 ما عندك يوزر', reply)
            return

        if text == 'يوزره':
            if msg.get('reply_to_message'):
                u = msg['reply_to_message']['from'].get('username')
                await send(chat_id, f'🔗 يوزره: @{u}' if u else '😕 ما عنده يوزر', reply)
            else:
                await send(chat_id, '⚠️ رد على رسالة شخص لمعرفة يوزره', reply)
            return

        if text == 'ايدي' and not settings['disable_id']:
            if msg.get('reply_to_message'):
                tf = msg['reply_to_message']['from']
                await send(chat_id, f'🆔 ايدي {name(tf)}: <code>{tf["id"]}</code>', reply)
            else:
                await send(chat_id, f'🆔 ايدك: <code>{user_id}</code>', reply)
            return

        if text == 'الرابط' and not settings['disable_link']:
            link = await api_call('exportChatInviteLink', {'chat_id': chat_id})
            await send(chat_id, f'🔗 رابط المجموعة:\n{link}' if link else '⚠️ تعذر الحصول على الرابط', reply)
            return

        if text == 'المالك':
            admins = await api_call('getChatAdministrators', {'chat_id': chat_id})
            if admins:
                owner = next((a for a in admins if a.get('status') == 'creator'), None)
                if owner:
                    of = owner['user']
                    await send(chat_id, f'👑 مالك المجموعة: {mention(of)}', reply)
                    return
            await send(chat_id, '⚠️ لم أجد مالك المجموعة', reply)
            return

        if text == 'انشاء':
            if msg.get('reply_to_message'):
                tf = msg['reply_to_message']['from']
                creation = get_account_creation_text(tf)
                tf_name = name(tf)
                await send(chat_id,
                    f'‹‹ تاريخ انشاء حساب {tf_name} هو\n‹‹ {creation}',
                    reply)
            else:
                creation = get_account_creation_text(from_)
                await send(chat_id,
                    f'‹‹ تاريخ انشاء حسابك هو\n‹‹ {creation}',
                    reply)
            return

        if text in ['همسه', 'اهمس']:
            if not msg.get('reply_to_message'):
                await send(chat_id, '⚠️ رد على رسالة الشخص الذي تريد همسه', reply)
                return
            tf = msg['reply_to_message']['from']
            bot_un = await get_bot_username()
            import uuid as _uuid
            whisper_id = str(_uuid.uuid4())[:8]
            whispers[whisper_id] = {
                'sender_id': user_id,
                'sender_name': name(from_),
                'recipient_id': tf['id'],
                'recipient_name': name(tf),
                'group_chat_id': chat_id,
                'text': None
            }
            recipient_mention = f'<a href="tg://user?id={tf["id"]}">{name(tf)}</a>'
            await send(chat_id,
                f'• تم تحديد الهمسه لـ ‹‹ {recipient_mention}\n• اضغط الزر لكتابة الهمسه\n─',
                {
                    'reply_markup': {
                        'inline_keyboard': [[
                            {'text': '🤫 اهمس هنا', 'url': f'https://t.me/{bot_un}?start=w_{whisper_id}'}
                        ]]
                    },
                    'reply_to_message_id': msg_id
                }
            )
            return

        yt_match = re.match(r'^يوت\s+(.+)$', text)
        if yt_match and settings.get('youtube_enabled', True):
            query = yt_match.group(1).strip()
            search_msg = await api_call('sendMessage', {
                'chat_id': chat_id,
                'text': f'🔍 جاري البحث عن: <b>{query}</b>',
                'reply_to_message_id': msg_id,
                'parse_mode': 'HTML'
            })
            results = await youtube_search(query, max_results=4)
            if search_msg:
                await delete(chat_id, search_msg['message_id'])
            if not results:
                await send(chat_id, f'❌ لم أجد نتائج لـ: <b>{query}</b>', reply)
                return
            for r in results:
                key = f'{chat_id}:{r["id"]}'
                youtube_pending[key] = r['title']
            await send_youtube_results(chat_id, msg_id, query, results)
            return

        # ===========================
        # تحويل الملصق الثابت إلى صورة
        # ===========================
        if text == 'صوره':
            replied = msg.get('reply_to_message')
            if replied:
                sticker = replied.get('sticker')
                if sticker and not sticker.get('is_animated') and not sticker.get('is_video'):
                    file_info = await get_file(sticker['file_id'])
                    if file_info:
                        fp = file_info.get('file_path')
                        if fp:
                            file_url = f'https://api.telegram.org/file/bot{TOKEN}/{fp}'
                            try:
                                session = await get_session()
                                async with session.get(file_url) as resp:
                                    img_bytes = await resp.read()
                                form = aiohttp.FormData()
                                form.add_field('chat_id', str(chat_id))
                                form.add_field('reply_to_message_id', str(msg_id))
                                form.add_field('photo', img_bytes, filename='sticker.webp', content_type='image/webp')
                                async with session.post(f'{API}/sendPhoto', data=form) as r:
                                    pass
                            except Exception as e:
                                print(f'صوره error: {e}')
                                await send(chat_id, '❌ تعذّر تحويل الملصق إلى صورة', reply)
                    return
                else:
                    await send(chat_id, '⚠️ رد على ملصق ثابت (غير متحرك) لتحويله إلى صورة', reply)
                    return
            else:
                await send(chat_id, '⚠️ رد على ملصق ثابت لتحويله إلى صورة', reply)
                return

    # ===========================
    # أوامر الرتب (تُفحص قبل التسلية)
    # ===========================
    rank_cmds = {
        'رفع مالك اساسي': 'مالك اساسي', 'تنزيل مالك اساسي': 'عضو',
        'رفع مالك': 'مالك', 'تنزيل مالك': 'عضو',
        'رفع مدير': 'مدير', 'تنزيل مدير': 'عضو',
        'رفع ادمن': 'ادمن', 'تنزيل ادمن': 'عضو',
        'رفع مميز': 'مميز', 'تنزيل مميز': 'عضو'
    }

    # دعم الأوامر مع @يوزر مثل: رفع مالك اساسي @يوزر
    rank_cmd_match = None
    rank_cmd_key = None
    for rcmd in rank_cmds:
        pattern = r'^' + re.escape(rcmd) + r'\s+@(\w+)$'
        m2 = re.match(pattern, text)
        if m2:
            rank_cmd_match = m2
            rank_cmd_key = rcmd
            break

    if rank_cmd_key and rank_cmd_match:
        target_username = rank_cmd_match.group(1)
        tf = find_user_by_username(chat_id, target_username)
        if not tf:
            tf = await get_user_by_username_api(target_username)
        if not tf:
            await send(chat_id, f'⚠️ لم أتعرف على @{target_username}\nيجب أن يرسل العضو رسالة في المجموعة أولاً حتى يتعرف عليه البوت', reply)
            return
        target_rank = rank_cmds[rank_cmd_key]
        is_up = rank_cmd_key.startswith('رفع')
        if target_rank == 'مالك اساسي':
            if not await is_group_creator(chat_id, user_id):
                await send(chat_id, '⛔ رفع مالك اساسي للمالك الأصلي للمجموعة فقط', reply)
                return
        else:
            if not (await is_master(data, chat_id, user_id)):
                await send(chat_id, '⛔ ليس لديك صلاحية', reply)
                return
        set_rank(data, chat_id, tf['id'], target_rank)
        if is_up:
            await send(chat_id, f'✅ تم رفع {mention(tf)} إلى رتبة <b>{target_rank}</b>\nبواسطة {m}', reply)
        else:
            await send(chat_id, f'✅ تم تنزيل {mention(tf)}\nبواسطة {m}', reply)
        return

    if text in rank_cmds:
        if not msg.get('reply_to_message'):
            await send(chat_id, '⚠️ رد على رسالة الشخص أو اكتب @يوزره بعد الأمر', reply)
            return
        tf = msg['reply_to_message']['from']
        target_rank = rank_cmds[text]
        is_up = text.startswith('رفع')
        if target_rank == 'مالك اساسي':
            if not await is_group_creator(chat_id, user_id):
                await send(chat_id, '⛔ رفع مالك اساسي للمالك الأصلي للمجموعة فقط', reply)
                return
        else:
            if not (await is_master(data, chat_id, user_id)):
                await send(chat_id, '⛔ ليس لديك صلاحية', reply)
                return
        set_rank(data, chat_id, tf['id'], target_rank)
        if is_up:
            await send(chat_id, f'✅ تم رفع {mention(tf)} إلى رتبة <b>{target_rank}</b>\nبواسطة {m}', reply)
        else:
            await send(chat_id, f'✅ تم تنزيل {mention(tf)}\nبواسطة {m}', reply)
        return

    # ===========================
    # أوامر التسلية
    # ===========================
    if not settings['disable_fun']:
        fun_match = re.match(r'^رفع\s+(.+)$', text)
        fun_rank_keywords = ['مالك', 'ادمن', 'مدير', 'مميز', 'القيود', 'كتم', 'تقييد']
        if fun_match and msg.get('reply_to_message') and fun_match.group(1).strip() not in fun_rank_keywords:
            await send(chat_id, f'✅ تم رفع {mention(msg["reply_to_message"]["from"])} {fun_match.group(1).strip()} للتسلية 😜', reply)
            return

    # ===========================
    # قائمة الأوامر
    # ===========================
    if text in ['الاوامر', 'اوامر'] and await is_admin_up(data, chat_id, user_id):
        await send(chat_id, '🤖 <b>قائمة الأوامر</b>\n\n- أوامر ① الخدمية\n- أوامر ② التسليه\n- أوامر ③ القفل والفتح\n- أوامر ④ الإعدادات\n- أوامر ⑤ الألعاب\n- أوامر ⑥ الكلمات المحظورة', {
            'reply_markup': {'inline_keyboard': [
                [
                    {'text': '① خدمية', 'callback_data': 'menu_service'},
                    {'text': '② تسليه', 'callback_data': 'menu_fun'},
                ],
                [
                    {'text': '③ قفل/فتح', 'callback_data': 'menu_locks'},
                    {'text': '④ إعدادات', 'callback_data': 'menu_settings'},
                ],
                [
                    {'text': '⑤ الألعاب', 'callback_data': 'menu_games'},
                    {'text': '⑥ كلمات محظورة', 'callback_data': f'menu_banned_words:{chat_id}'},
                ]
            ]},
            'reply_to_message_id': msg_id
        })
        return

    # ===========================
    # الردود المخصصة
    # ===========================
    if text == 'اضف رد' and await is_admin_up(data, chat_id, user_id):
        if cid not in state: state[cid] = {}
        state[cid][str(user_id)] = {'step': 'await_name'}
        await send(chat_id, '📝 أرسل اسم الرد:', reply)
        return

    if text == 'مسح رد' and await is_admin_up(data, chat_id, user_id):
        if cid not in state: state[cid] = {}
        state[cid][str(user_id)] = {'step': 'await_delete_name'}
        await send(chat_id, '🗑️ أرسل اسم الرد الذي تريد حذفه:', reply)
        return

    if text == 'الردود' and (rank_level(user_rank) >= rank_level('مدير') or tg_admin or dev):
        custom = data.get('custom_replies', {}).get(cid, {})
        if not custom:
            await send(chat_id, '📋 لا توجد ردود مضافة في هذه المجموعة حتى الآن', reply)
            return
        names = list(custom.keys())
        lines = [f'📋 <b>قائمة الردود المضافة ({len(names)}):</b>\n']
        for i, name in enumerate(names, 1):
            rtype = custom[name].get('type', 'text')
            icon = '🖼️' if rtype == 'photo' else ('🎬' if rtype == 'video' else '💬')
            lines.append(f'{i}. {icon} <code>{name}</code>')
        lines.append('\n<i>اكتب اسم الرد لتشغيله</i>')
        await send(chat_id, '\n'.join(lines), reply)
        return

    # ===========================
    # أوامر القفل والإعدادات
    # ===========================
    if await is_admin_up(data, chat_id, user_id):
        lock_map = {
            'قفل السب': 'lock_swear', 'فتح السب': 'lock_swear',
            'قفل التكرار': 'lock_repeat', 'فتح التكرار': 'lock_repeat',
            'قفل التكرار بالتقييد': 'lock_repeat_restrict', 'فتح التكرار بالتقييد': 'lock_repeat_restrict',
            'قفل التكرار بالتحذير': 'lock_repeat_warn', 'فتح التكرار بالتحذير': 'lock_repeat_warn',
            'قفل الروابط': 'lock_links', 'فتح الروابط': 'lock_links',
            'قفل التوجيه': 'lock_forward', 'فتح التوجيه': 'lock_forward',
            'قفل التحويل': 'lock_forward', 'فتح التحويل': 'lock_forward',
            'قفل الكلايش': 'lock_clutter', 'فتح الكلايش': 'lock_clutter',
            'قفل الانجليزيه': 'lock_english', 'فتح الانجليزيه': 'lock_english',
            'قفل الانجليزية': 'lock_english', 'فتح الانجليزية': 'lock_english',
            'قفل الصينيه': 'lock_chinese', 'فتح الصينيه': 'lock_chinese',
            'قفل الصينية': 'lock_chinese', 'فتح الصينية': 'lock_chinese',
            'قفل الروسيه': 'lock_russian', 'فتح الروسيه': 'lock_russian',
            'قفل الروسية': 'lock_russian', 'فتح الروسية': 'lock_russian',
            'قفل الصور': 'lock_photos', 'فتح الصور': 'lock_photos',
            'قفل الفيديوهات': 'lock_videos', 'فتح الفيديوهات': 'lock_videos',
            'قفل تعديل الميديا': 'lock_media_edit', 'فتح تعديل الميديا': 'lock_media_edit',
            'قفل الصوتيات': 'lock_audio', 'فتح الصوتيات': 'lock_audio',
            'قفل الاغاني': 'lock_music', 'فتح الاغاني': 'lock_music',
            'قفل الدخول': 'lock_join', 'فتح الدخول': 'lock_join',
            'قفل التاك': 'lock_mention', 'فتح التاك': 'lock_mention',
            'قفل الارقام': 'lock_numbers', 'فتح الارقام': 'lock_numbers',
            'قفل الملصقات': 'lock_stickers', 'فتح الملصقات': 'lock_stickers',
            'قفل المتحركه': 'lock_animated', 'فتح المتحركه': 'lock_animated',
            'قفل المتحركة': 'lock_animated', 'فتح المتحركة': 'lock_animated',
            'قفل الشات': 'lock_chat', 'فتح الشات': 'lock_chat',
            'قفل المحتوى المخل': 'lock_nsfw', 'فتح المحتوى المخل': 'lock_nsfw',
            'قفل المحتوى المخل بالتقييد': 'lock_nsfw_restrict', 'فتح المحتوى المخل بالتقييد': 'lock_nsfw_restrict',
            'قفل المحتوى المخل بالتحذير': 'lock_nsfw_warn', 'فتح المحتوى المخل بالتحذير': 'lock_nsfw_warn',
            'قفل الوثائق الحكومية': 'lock_id_documents', 'فتح الوثائق الحكومية': 'lock_id_documents',
            'قفل الملفات': 'lock_files', 'فتح الملفات': 'lock_files',
            'قفل الجهات': 'lock_contacts', 'فتح الجهات': 'lock_contacts',
            'قفل يوزرات القنوات': 'lock_channel_usernames', 'فتح يوزرات القنوات': 'lock_channel_usernames',
            'قفل كل اليوزرات': 'lock_all_usernames', 'فتح كل اليوزرات': 'lock_all_usernames',
            'قفل الردود الخارجية': 'lock_external_reply', 'فتح الردود الخارجية': 'lock_external_reply',
            'قفل الردود الخارجيه': 'lock_external_reply', 'فتح الردود الخارجيه': 'lock_external_reply',
            'قفل الاقتباس': 'lock_quote', 'فتح الاقتباس': 'lock_quote',
            'قفل الاقتباسات': 'lock_quote', 'فتح الاقتباسات': 'lock_quote',
            'تفعيل حماية التفليش': 'lock_flash', 'تعطيل حماية التفليش': 'lock_flash',
            'تفعيل حمايه التفليش': 'lock_flash', 'تعطيل حمايه التفليش': 'lock_flash',
            'قفل الاونلاين': 'lock_online', 'فتح الاونلاين': 'lock_online',
            'قفل الأونلاين': 'lock_online', 'فتح الأونلاين': 'lock_online',
        }
        if text in lock_map:
            is_lock = text.startswith('قفل') or text.startswith('تفعيل')
            data['group_settings'][cid][lock_map[text]] = is_lock
            if lock_map[text] == 'lock_repeat_restrict' and is_lock:
                data['group_settings'][cid]['lock_repeat'] = True
            save_data(data)
            if lock_map[text] == 'lock_flash':
                lock_label = ('🛡️ تم تفعيل حماية التفليش' if is_lock else '🔓 تم تعطيل حماية التفليش')
            else:
                lock_label = ('🔒 تم القفل' if is_lock else '🔓 تم الفتح') + ': <b>' + text + '</b>'
            if lock_map[text] in ('lock_repeat', 'lock_repeat_restrict', 'lock_repeat_warn'):
                kbd = {'inline_keyboard': [[{'text': '⚙️ اعدادات التكرار', 'callback_data': 'show_repeat_settings'}]]}
                await send(chat_id, lock_label, {'reply_markup': kbd, 'reply_to_message_id': msg_id})
            else:
                await send(chat_id, lock_label, reply)
            return

        if text == 'اعدادات التكرار':
            max_msgs = settings.get('repeat_max_messages', 5)
            secs = settings.get('repeat_seconds', 7)
            restrict_on = settings.get('lock_repeat_restrict', False)
            warn_on = settings.get('lock_repeat_warn', False)
            repeat_on = settings.get('lock_repeat', False)
            status_txt = (
                '<b>اعدادات قفل التكرار</b>\n\n'
                + 'قفل التكرار: ' + ('مفعل' if repeat_on else 'معطل') + '\n'
                + 'قفل التكرار بالتقييد: ' + ('مفعل' if restrict_on else 'معطل') + '\n'
                + 'قفل التكرار بالتحذير: ' + ('مفعل' if warn_on else 'معطل') + '\n'
                + 'عدد الرسائل المسموح: <b>' + str(max_msgs) + '</b>\n'
                + 'النافذة الزمنية: <b>' + str(secs) + '</b> ثانية'
            )
            kbd = {
                'inline_keyboard': [
                    [{'text': 'عدد رسائل (' + str(max_msgs) + ')', 'callback_data': 'repeat_set_messages'}],
                    [{'text': 'عدد ثواني (' + str(secs) + ')', 'callback_data': 'repeat_set_seconds'}],
                    [{'text': 'بالتقييد ' + ('✓' if restrict_on else '✗'), 'callback_data': 'repeat_toggle_restrict'}],
                    [{'text': 'بالتحذير ' + ('✓' if warn_on else '✗'), 'callback_data': 'repeat_toggle_warn'}],
                ]
            }
            await send(chat_id, status_txt, {'reply_markup': kbd, 'reply_to_message_id': msg_id})
            return

        dis_map = {
            'تعطيل الايدي': ['disable_id', True], 'تفعيل الايدي': ['disable_id', False],
            'تعطيل الخدميه': ['disable_service', True], 'تفعيل الخدميه': ['disable_service', False],
            'تعطيل الخدمية': ['disable_service', True], 'تفعيل الخدمية': ['disable_service', False],
            'تعطيل التسليه': ['disable_fun', True], 'تفعيل التسليه': ['disable_fun', False],
            'تعطيل التسلية': ['disable_fun', True], 'تفعيل التسلية': ['disable_fun', False],
            'تعطيل الترحيب': ['disable_welcome', True], 'تفعيل الترحيب': ['disable_welcome', False],
            'تعطيل الرابط': ['disable_link', True], 'تفعيل الرابط': ['disable_link', False],
            'تعطيل الردود التلقائية': ['disable_auto_replies', True], 'تفعيل الردود التلقائية': ['disable_auto_replies', False],
            'تعطيل الردود التلقائيه': ['disable_auto_replies', True], 'تفعيل الردود التلقائيه': ['disable_auto_replies', False],
            'تعطيل ردود البوت': ['disable_auto_replies', True], 'تفعيل ردود البوت': ['disable_auto_replies', False],
            'تعطيل الالعاب': ['disable_games', True], 'تفعيل الالعاب': ['disable_games', False],
            'تعطيل اليوتيوب': ['youtube_enabled', False], 'تفعيل اليوتيوب': ['youtube_enabled', True],
            'تعطيل اليوتويب': ['youtube_enabled', False], 'تفعيل اليوتويب': ['youtube_enabled', True],
            'تعطيل التوب': ['disable_top', True], 'تفعيل التوب': ['disable_top', False],
        }
        if text in dis_map:
            key, val = dis_map[text]
            data['group_settings'][cid][key] = val
            save_data(data)
            if key == 'youtube_enabled':
                await send(chat_id, f'{"🔴 تم تعطيل ميزة اليوتيوب" if not val else "🟢 تم تفعيل ميزة اليوتيوب"}', reply)
            else:
                await send(chat_id, f'{"🔴 تم التعطيل" if val else "🟢 تم التفعيل"}: <b>{text}</b>', reply)
            return

        if text == 'قفل امر' and await is_owner_up(data, chat_id, user_id):
            if cid not in state: state[cid] = {}
            state[cid][str(user_id)] = {'step': 'await_lock_cmd_name'}
            await send(chat_id, '🔒 أرسل اسم الأمر الذي تريد قفله:', reply)
            return

        if text == 'اضف امر' and await is_owner_up(data, chat_id, user_id):
            if cid not in state: state[cid] = {}
            state[cid][str(user_id)] = {'step': 'await_add_cmd_real'}
            await send(chat_id, '📝 أرسل الأمر الحقيقي:', reply)
            return

    # ===========================
    # أوامر الإدارة
    # ===========================
    if await is_admin_up(data, chat_id, user_id):

        # دالة مساعدة: البحث عن الهدف (رد أو @يوزر)
        async def resolve_target(cmd_text):
            uname_match = re.match(r'^' + re.escape(cmd_text) + r'\s+@(\w+)$', text)
            if uname_match:
                target_uname = uname_match.group(1)
                tf = find_user_by_username(chat_id, target_uname)
                if not tf:
                    tf = await get_user_by_username_api(target_uname)
                if not tf:
                    await send(chat_id, f'⚠️ لم أتعرف على @{target_uname}\nيجب أن يرسل العضو رسالة في المجموعة أولاً حتى يتعرف عليه البوت', reply)
                    return None, True
                return tf, False
            elif msg.get('reply_to_message'):
                return msg['reply_to_message']['from'], False
            else:
                await send(chat_id, f'⚠️ رد على رسالة الشخص أو اكتب @يوزره بعد الأمر\nمثال: {cmd_text} @يوزر', reply)
                return None, True

        if text == 'كتم' or text.startswith('كتم @'):
            tf, handled = await resolve_target('كتم')
            if handled:
                return
            if tf is None:
                return
            await restrict(chat_id, tf['id'], {'can_send_messages': False})
            await send(chat_id, f'🔇 تم كتم {mention(tf)}\nبواسطة {m}', reply)
            return

        if text == 'تقييد' or text.startswith('تقييد @'):
            tf, handled = await resolve_target('تقييد')
            if handled:
                return
            if tf is None:
                return
            await restrict(chat_id, tf['id'], {
                'can_send_messages': False, 'can_send_media_messages': False,
                'can_send_polls': False, 'can_send_other_messages': False, 'can_add_web_page_previews': False
            })
            await send(chat_id, f'🚫 تم تقييد {mention(tf)}\nبواسطة {m}', reply)
            return

        if text in ['رفع القيود', 'الغاء الكتم', 'الغاء التقييد'] or \
           text.startswith('رفع القيود @') or text.startswith('الغاء الكتم @') or text.startswith('الغاء التقييد @'):
            base_cmd = text.split('@')[0].strip() if '@' in text else text
            tf, handled = await resolve_target(base_cmd)
            if handled:
                return
            if tf is None:
                return
            await restrict(chat_id, tf['id'], {
                'can_send_messages': True, 'can_send_media_messages': True,
                'can_send_polls': True, 'can_send_other_messages': True, 'can_add_web_page_previews': True
            })
            await send(chat_id, f'✅ تم رفع القيود عن {mention(tf)}\nبواسطة {m}', reply)
            return

        if text == 'طرد' or text.startswith('طرد @'):
            tf, handled = await resolve_target('طرد')
            if handled:
                return
            if tf is None:
                return

            if settings.get('lock_flash') and not await is_developer(user_id):
                now_ts = time.time()
                cid_key = str(chat_id)
                uid_key = str(user_id)
                blocked_until = (flash_blocked.get(cid_key) or {}).get(uid_key, 0)
                if now_ts < blocked_until:
                    remaining = int(blocked_until - now_ts)
                    await send(chat_id,
                        f'🚫 {m} ممنوع من الطرد بسبب التفليش\n'
                        f'⏱ انتظر <b>{remaining}</b> ثانية', reply)
                    return
                ban_limit = settings.get('flash_ban_limit', 3)
                ban_seconds = settings.get('flash_ban_seconds', 30)
                if cid_key not in flash_tracker:
                    flash_tracker[cid_key] = {}
                if uid_key not in flash_tracker[cid_key]:
                    flash_tracker[cid_key][uid_key] = []
                flash_tracker[cid_key][uid_key] = [
                    t for t in flash_tracker[cid_key][uid_key]
                    if now_ts - t < ban_seconds
                ]
                flash_tracker[cid_key][uid_key].append(now_ts)
                if len(flash_tracker[cid_key][uid_key]) >= ban_limit:
                    flash_tracker[cid_key][uid_key] = []
                    if cid_key not in flash_blocked:
                        flash_blocked[cid_key] = {}
                    flash_blocked[cid_key][uid_key] = now_ts + 60
                    owner_tag = ''
                    try:
                        admins = await api_call('getChatAdministrators', {'chat_id': chat_id})
                        if admins:
                            owner = next((a for a in admins if a.get('status') == 'creator'), None)
                            if owner:
                                ou = owner['user'].get('username')
                                owner_tag = f'@{ou}' if ou else mention(owner['user'])
                    except:
                        pass
                    await send(chat_id,
                        f'⚠️ <b>تنبيه تفليش!</b>\n\n'
                        f'🔴 المستخدم {m} يقوم بالتفليش!\n'
                        f'تم طرد {ban_limit} أعضاء بسرعة كبيرة\n\n'
                        f'📢 {owner_tag}\n'
                        f'🔒 تم منع {m} من الطرد لمدة دقيقة')
                    return

            await ban(chat_id, tf['id'])
            await unban(chat_id, tf['id'])
            await send(chat_id, f'👢 تم طرد {mention(tf)}\nبواسطة {m}', reply)
            return

        if text == 'مسح':
            if not msg.get('reply_to_message'):
                await send(chat_id, '⚠️ رد على الرسالة المراد مسحها', reply)
                return
            await delete(chat_id, msg['reply_to_message']['message_id'])
            await delete(chat_id, msg_id)
            await send(chat_id, '🗑️ تم مسح الرسالة')
            return

        masc_match = re.match(r'^مسح\s+(\d+)$', text)
        if masc_match:
            count = min(int(masc_match.group(1)), 500)
            ids_to_delete = list(range(msg_id, msg_id - count, -1))
            results = await asyncio.gather(
                *[delete(chat_id, mid) for mid in ids_to_delete],
                return_exceptions=True
            )
            deleted = sum(1 for r in results if r is not False and not isinstance(r, Exception))
            await send(chat_id, f'🗑️ تم مسح <b>{deleted}</b> رسالة')
            return

        if text == 'مسح الكل':
            if not await is_owner_up(data, chat_id, user_id):
                await send(chat_id, '⛔ هذا الأمر للمالك فقط', reply)
                return
            ranks = data.get('user_ranks', {}).get(cid, {})
            count = 0
            for uid_key in list(ranks.keys()):
                if ranks[uid_key] != 'عضو':
                    ranks[uid_key] = 'عضو'
                    count += 1
            save_data(data)
            await delete(chat_id, msg_id)
            await send(chat_id, f'✅ تم تصفير رتب <b>{count}</b> عضو، الجميع أصبح عضو الآن')
            return

    # ===========================
    # الألعاب
    # ===========================
    if not settings.get('disable_games'):
        await handle_games(msg, data, text, chat_id, msg_id, from_, user_id, m, cid, reply)

# ===========================
# GAMES HANDLER
# ===========================

async def handle_games(msg, data, text, chat_id, msg_id, from_, user_id, m, cid, reply):
    now = datetime.now().timestamp()

    # ===========================
    # فحص إجابة لعبة الصور
    # ===========================
    if 'games_state' in data and cid in data['games_state']:
        sowar = data['games_state'][cid].get('sowar', {})
        if sowar.get('active'):
            elapsed = now - sowar.get('started_at', now)
            if elapsed > 60:
                data['games_state'][cid]['sowar'] = {'active': False}
                save_data(data)
                await send(chat_id, f'⏰ انتهى الوقت! الإجابة الصحيحة كانت: <b>{sowar["answer"]}</b>')
            elif text and text.strip() == sowar.get('answer', '').strip():
                prize = random.randint(140, 303)
                data['games_state'][cid]['sowar'] = {'active': False}
                acc = get_bank(data, chat_id, user_id)
                if acc:
                    acc['balance'] = acc.get('balance', 0) + prize
                save_data(data)
                await send(chat_id,
                    f'🎉 {m} أجاب صح!\n\n'
                    f'✅ الإجابة: <b>{sowar["answer"]}</b>\n'
                    f'💰 ربحت: <b>{prize}</b> دينار',
                    reply
                )
                return

    # ===========================
    # لعبة كت
    # ===========================
    if text == 'كت':
        question = random.choice(KAT_QUESTIONS)
        await send(chat_id,
            f'‹ {m} ›\n{question}',
            reply
        )
        return

    # ===========================
    # لعبة الصور
    # ===========================
    if text == 'صور':
        if 'games_state' not in data:
            data['games_state'] = {}
        if cid not in data['games_state']:
            data['games_state'][cid] = {}
        sowar = data['games_state'][cid].get('sowar', {})
        if sowar.get('active'):
            elapsed = now - sowar.get('started_at', now)
            if elapsed <= 60:
                await send(chat_id, '⚠️ في لعبة صور جارية الحين، اكتب الإجابة!', reply)
                return
        img_url, question, answer = random.choice(SOWAR_QUESTIONS)
        data['games_state'][cid]['sowar'] = {
            'active': True,
            'answer': answer,
            'started_at': now
        }
        save_data(data)
        await api_call('sendPhoto', {
            'chat_id': chat_id,
            'photo': img_url,
            'caption': (
                f'🖼️ <b>لعبة الصور</b>\n\n'
                f'❓ {question}\n\n'
                f'⏰ عندك دقيقة للإجابة!'
            ),
            'parse_mode': 'HTML'
        })
        return

    # ===========================
    # لعبة الكرسي
    # ===========================
    if text == 'كرسي':
        game = get_game_state(data, chat_id)
        if game.get('active'):
            await send(chat_id, '⚠️ هناك لعبة جارية بالفعل، انتظر حتى تنتهي', reply)
            return
        game['active'] = False
        game['starter_id'] = user_id
        game['players'] = [{'id': user_id, 'name': name(from_)}]
        game['chosen_id'] = None
        game['chosen_name'] = None
        game['questions_count'] = 0
        game['waiting'] = True
        save_data(data)
        await send(chat_id,
            f'↢ تم بداء اللعبة وتم تسجيلك {m}\n'
            f'↢ اللي بيلعب يرسل ( انا ) .',
            reply
        )
        return

    if text == 'انا':
        game = get_game_state(data, chat_id)
        ahkam = get_ahkam_state(data, chat_id)

        if ahkam.get('waiting'):
            players = ahkam.get('players', [])
            already = any(p['id'] == user_id for p in players)
            if already:
                await send(chat_id, f'⚠️ {m} أنت مسجل بالفعل', reply)
                return
            players.append({'id': user_id, 'name': name(from_)})
            ahkam['players'] = players
            save_data(data)
            await send(chat_id,
                f'↢ تم ضفتك للعبة {m}\n'
                f'↢ للانتهاء يرسل نعم اللي بداء اللعبة .',
                reply
            )
            return

        if not game.get('waiting'):
            return
        players = game.get('players', [])
        already = any(p['id'] == user_id for p in players)
        if already:
            await send(chat_id, f'⚠️ {m} أنت مسجل بالفعل', reply)
            return
        players.append({'id': user_id, 'name': name(from_)})
        game['players'] = players
        save_data(data)
        await send(chat_id,
            f'↢ تم ضفتك للعبة {m}\n'
            f'↢ للانتهاء يرسل نعم اللي بداء اللعبة .',
            reply
        )
        return

    if text == 'نعم':
        game = get_game_state(data, chat_id)
        ahkam = get_ahkam_state(data, chat_id)

        if ahkam.get('waiting'):
            if ahkam.get('starter_id') != user_id:
                await send(chat_id, '⚠️ فقط من بدأ اللعبة يقدر يبدأها', reply)
                return
            players = ahkam.get('players', [])
            if len(players) < 2:
                await send(chat_id, '⚠️ يجب أن ينضم على الأقل لاعب واحد آخر', reply)
                return
            chosen_two = random.sample(players, 2)
            mahkoom = chosen_two[0]
            hakim = chosen_two[1]
            ahkam['active'] = True
            ahkam['waiting'] = False
            save_data(data)
            mahkoom_mention = f'<a href="tg://user?id={mahkoom["id"]}">{mahkoom["name"]}</a>'
            hakim_mention = f'<a href="tg://user?id={hakim["id"]}">{hakim["name"]}</a>'
            await send(chat_id,
                f'↢ اخترت الشخص ‹‹ {mahkoom_mention} ›› ليتم الحكم عليه\n'
                f'↢ الحاكم ‹‹ {hakim_mention} ›',
                reply
            )
            return

        if not game.get('waiting'):
            return
        if game.get('starter_id') != user_id:
            await send(chat_id, '⚠️ فقط من بدأ اللعبة يقدر يبدأها', reply)
            return
        players = game.get('players', [])
        if len(players) < 2:
            await send(chat_id, '⚠️ يجب أن ينضم على الأقل لاعب واحد آخر', reply)
            return
        chosen = random.choice(players)
        game['active'] = True
        game['waiting'] = False
        game['chosen_id'] = chosen['id']
        game['chosen_name'] = chosen['name']
        game['questions_count'] = 0
        save_data(data)
        chosen_mention = f'<a href="tg://user?id={chosen["id"]}">{chosen["name"]}</a>'
        bot_un = await get_bot_username()
        await send(chat_id,
            f'↢ اخترت الشخص ↢ {chosen_mention} لديكم بس 50 اسئله',
            {
                'reply_markup': {
                    'inline_keyboard': [[
                        {'text': '❓ اسالوه هنا', 'url': f'https://t.me/{bot_un}?start=kursi_{chat_id}'}
                    ]]
                },
                'reply_to_message_id': msg_id
            }
        )
        return

    if text == 'انهاء':
        game = get_game_state(data, chat_id)
        ahkam = get_ahkam_state(data, chat_id)

        if ahkam.get('active') or ahkam.get('waiting'):
            if ahkam.get('starter_id') != user_id:
                await send(chat_id, '⚠️ فقط من بدأ اللعبة يقدر ينهيها', reply)
                return
            ahkam['active'] = False
            ahkam['waiting'] = False
            ahkam['starter_id'] = None
            ahkam['players'] = []
            save_data(data)
            await send(chat_id, f'✅ تم إنهاء لعبة الأحكام بواسطة {m}', reply)
            return

        if not (game.get('active') or game.get('waiting')):
            return
        if game.get('starter_id') != user_id:
            await send(chat_id, '⚠️ فقط من بدأ اللعبة يقدر ينهيها', reply)
            return
        game['active'] = False
        game['waiting'] = False
        game['starter_id'] = None
        game['players'] = []
        game['chosen_id'] = None
        game['chosen_name'] = None
        game['questions_count'] = 0
        save_data(data)
        await send(chat_id, f'✅ تم إنهاء لعبة الكرسي بواسطة {m}', reply)
        return

    # ===========================
    # لعبة الأحكام
    # ===========================
    if text == 'احكام':
        ahkam = get_ahkam_state(data, chat_id)
        if ahkam.get('active') or ahkam.get('waiting'):
            await send(chat_id, '⚠️ هناك لعبة أحكام جارية بالفعل، انتظر حتى تنتهي', reply)
            return
        ahkam['active'] = False
        ahkam['waiting'] = True
        ahkam['starter_id'] = user_id
        ahkam['players'] = [{'id': user_id, 'name': name(from_)}]
        save_data(data)
        await send(chat_id,
            f'↢ تم بداء اللعبة وتم تسجيلك {m}\n'
            f'↢ اللي بيلعب يرسل ( انا ) .',
            reply
        )
        return

    # ===========================
    # لعبة ممتلكاتي - إنشاء حساب
    # ===========================
    if text == 'انشاء حساب بنكي':
        existing = get_bank(data, chat_id, user_id)
        if existing:
            await send(chat_id, f'⚠️ {m} عندك حساب بنكي بالفعل في {existing["bank"]}\nاكتب <b>حسابي</b> لعرض حسابك', reply)
            return
        keyboard = {
            'inline_keyboard': [
                [{'text': '🏦 بنك الاهلي', 'callback_data': 'bank_create:بنك الاهلي'}],
                [{'text': '🏦 بنك الرافدين', 'callback_data': 'bank_create:بنك الرافدين'}],
                [{'text': '🏦 بنك الراجحي', 'callback_data': 'bank_create:بنك الراجحي'}],
            ]
        }
        await send(chat_id,
            f'🏦 اختر أي حساب تريد {m}:\n\n-> بنك الاهلي\n-> بنك الرافدين\n-> بنك الراجحي',
            {'reply_markup': keyboard, 'reply_to_message_id': msg_id}
        )
        return

    # ===========================
    # عرض الحساب البنكي
    # ===========================
    if text == 'حسابي':
        acc = get_bank(data, chat_id, user_id)
        if not acc:
            await send(chat_id, f'😕 {m} ما عندك حساب بنكي\nاكتب <b>انشاء حساب بنكي</b> لفتح حساب', reply)
            return
        job_text = acc.get('job') or 'بدون وظيفة'
        await send(chat_id,
            f'💳 <b>حسابك البنكي</b>\n\n'
            f'~{acc["account_number"]}\n'
            f'فلوسك : {fmt_money(acc["balance"])} دينار\n'
            f'اسم البنك : {acc["bank"]}\n'
            f'الوظيفة : {job_text}',
            reply
        )
        return

    # ===========================
    # عرض الفلوس
    # ===========================
    if text == 'فلوسي':
        acc = get_bank(data, chat_id, user_id)
        if not acc:
            await send(chat_id, f'😕 {m} ما عندك حساب بنكي', reply)
            return
        await send(chat_id, f'💰 فلوسك ←  {fmt_money(acc["balance"])} دينار', reply)
        return

    if text == 'فلوسه':
        if msg.get('reply_to_message'):
            tf = msg['reply_to_message']['from']
            tf_acc = get_bank(data, chat_id, tf['id'])
            if not tf_acc:
                await send(chat_id, f'😕 {mention(tf)} ما عنده حساب بنكي', reply)
            else:
                await send(chat_id, f'💰 فلوسه ←  {fmt_money(tf_acc["balance"])} دينار', reply)
        else:
            await send(chat_id, '⚠️ رد على رسالة شخص لمعرفة فلوسه', reply)
        return

    # ===========================
    # الراتب
    # ===========================
    if text == 'راتب':
        acc = get_bank(data, chat_id, user_id)
        if not acc:
            await send(chat_id, f'😕 {m} ما عندك حساب بنكي\nاكتب <b>انشاء حساب بنكي</b> أولاً', reply)
            return
        last_salary = acc.get('last_salary', 0)
        cooldown = 7 * 3600
        elapsed = now - last_salary
        if last_salary > 0 and elapsed < cooldown:
            remaining = int(cooldown - elapsed)
            h = remaining // 3600
            mn = (remaining % 3600) // 60
            await send(chat_id, f'~~~استلمت راتب ارجع وره 7 ساعات\n⏰ المتبقي: {h} ساعة و {mn} دقيقة', reply)
            return
        job_name = random.choice(list(JOBS.keys()))
        sal_min, sal_max = JOBS[job_name]
        salary = random.randint(sal_min, sal_max)
        acc['job'] = job_name
        acc['balance'] = acc.get('balance', 0) + salary
        acc['last_salary'] = now
        save_data(data)
        await send(chat_id,
            f'💰 {m} استلمت راتبك!\n\n'
            f'👷 الوظيفة: <b>{job_name}</b>\n'
            f'💵 الراتب: <b>{fmt_money(salary)}</b> دينار\n'
            f'💳 رصيدك الجديد: <b>{fmt_money(acc["balance"])}</b> دينار',
            reply
        )
        return

    # ===========================
    # الزرف (السرقة)
    # ===========================
    if text == 'زرف' and msg.get('reply_to_message'):
        tf = msg['reply_to_message']['from']
        if tf['id'] == user_id:
            await send(chat_id, '⚠️ ما تقدر تزرف نفسك 😑', reply)
            return
        stealer_acc = get_bank(data, chat_id, user_id)
        if not stealer_acc:
            await send(chat_id, f'😕 {m} ما عندك حساب بنكي', reply)
            return
        victim_acc = get_bank(data, chat_id, tf['id'])
        if not victim_acc:
            await send(chat_id, f'😕 {mention(tf)} ما عنده حساب بنكي', reply)
            return
        last_steal = stealer_acc.get('last_steal', 0)
        cooldown = 4 * 3600
        elapsed = now - last_steal
        if last_steal > 0 and elapsed < cooldown:
            remaining = int(cooldown - elapsed)
            h = remaining // 3600
            mn = (remaining % 3600) // 60
            await send(chat_id, f'⚠️ {m} ما تقدر تزرف الحين\n⏰ المتبقي: {h} ساعة و {mn} دقيقة', reply)
            return
        stolen = random.randint(1000, 2500)
        if victim_acc['balance'] < stolen:
            stolen = victim_acc['balance']
        if stolen <= 0:
            await send(chat_id, f'😅 {mention(tf)} ما عنده فلوس تستاهل السرقة', reply)
            return
        victim_acc['balance'] -= stolen
        stealer_acc['balance'] = stealer_acc.get('balance', 0) + stolen
        stealer_acc['last_steal'] = now
        save_data(data)
        await send(chat_id,
            f'🦹 تم زرف العضو {mention(tf)}\n'
            f'💸 المبلغ المسروق: <b>{fmt_money(stolen)}</b> دينار\n'
            f'💳 رصيدك الجديد: <b>{fmt_money(stealer_acc["balance"])}</b> دينار',
            reply
        )
        return

    # ===========================
    # الحظ (المقامرة)
    # ===========================
    luck_match = re.match(r'^حظ\s+(\d+)$', text)
    if luck_match:
        acc = get_bank(data, chat_id, user_id)
        if not acc:
            await send(chat_id, f'😕 {m} ما عندك حساب بنكي', reply)
            return
        bet = int(luck_match.group(1))
        if bet <= 0:
            await send(chat_id, '⚠️ المبلغ يجب أن يكون أكبر من صفر', reply)
            return
        if acc.get('balance', 0) < bet:
            await send(chat_id, f'💸 فلوسك ما تكفي يا فكر\n💳 رصيدك: <b>{fmt_money(acc.get("balance", 0))}</b> دينار', reply)
            return
        roll = random.random()
        if roll < 0.40:
            acc['balance'] -= bet
            save_data(data)
            await send(chat_id,
                f'😢 {m} خسرت!\n\n'
                f'💸 خسرت: <b>{fmt_money(bet)}</b> دينار\n'
                f'💳 رصيدك: <b>{fmt_money(acc["balance"])}</b> دينار',
                reply
            )
        elif roll < 0.95:
            gain_pct = random.randint(1, 60) / 100
            gain = int(bet * gain_pct)
            acc['balance'] += gain
            save_data(data)
            await send(chat_id,
                f'🎉 {m} ربحت!\n\n'
                f'💰 ربحت: <b>{fmt_money(gain)}</b> دينار ({int(gain_pct*100)}%)\n'
                f'💳 رصيدك: <b>{fmt_money(acc["balance"])}</b> دينار',
                reply
            )
        else:
            doubled = bet * 2
            acc['balance'] += doubled
            save_data(data)
            await send(chat_id,
                f'🤑 {m} حظك جبار! المبلغ تضاعف 2x!\n\n'
                f'💰 ربحت: <b>{fmt_money(doubled)}</b> دينار\n'
                f'💳 رصيدك: <b>{fmt_money(acc["balance"])}</b> دينار',
                reply
            )
        return

    # ===========================
    # المتجر
    # ===========================
    if text == 'المتجر':
        acc = get_bank(data, chat_id, user_id)
        if not acc:
            await send(chat_id, f'😕 {m} ما عندك حساب بنكي\nاكتب <b>انشاء حساب بنكي</b> أولاً', reply)
            return
        keyboard = {'inline_keyboard': [
            [{'text': '🏪 المتجر العادي', 'callback_data': 'shop_regular'}],
            [{'text': '⚔️ المتجر العالمي', 'callback_data': 'shop_military'}],
        ]}
        await send(chat_id,
            f'🛒 <b>مرحباً في المتجر {m}</b>\n\nاختر نوع المتجر:',
            {'reply_markup': keyboard, 'reply_to_message_id': msg_id}
        )
        return

    # ===========================
    # استثمار
    # ===========================
    invest_match = re.match(r'^استثمار\s+(\d+)$', text)
    if invest_match:
        acc = get_bank(data, chat_id, user_id)
        if not acc:
            await send(chat_id, f'😕 {m} ما عندك حساب بنكي', reply)
            return
        amount = int(invest_match.group(1))
        if amount <= 0:
            await send(chat_id, '⚠️ المبلغ يجب أن يكون أكبر من صفر', reply)
            return
        if acc.get('balance', 0) < amount:
            await send(chat_id,
                f'💸 فلوسك ما تكفي\n💳 رصيدك: <b>{fmt_money(acc.get("balance", 0))}</b> دينار', reply)
            return
        last_invest = acc.get('last_invest', 0)
        cooldown = 3 * 3600
        elapsed = now - last_invest
        if last_invest > 0 and elapsed < cooldown:
            remaining = int(cooldown - elapsed)
            h = remaining // 3600
            mn = (remaining % 3600) // 60
            await send(chat_id, f'📈 {m} استثمرت مؤخراً، ارجع بعد قليل\n⏰ المتبقي: {h} ساعة و {mn} دقيقة', reply)
            return
        profit_pct = random.randint(4, 9) / 100
        profit = int(amount * profit_pct)
        acc['balance'] = acc.get('balance', 0) + profit
        acc['last_invest'] = now
        save_data(data)
        await send(chat_id,
            f'📈 <b>تمت عملية الاستثمار</b>\n\n'
            f'💰 المبلغ المستثمر: <b>{fmt_money(amount)}</b> دينار\n'
            f'📊 نسبة الربح: <b>{int(profit_pct * 100)}%</b>\n'
            f'✅ الربح: <b>{fmt_money(profit)}</b> دينار\n'
            f'💳 رصيدك الجديد: <b>{fmt_money(acc["balance"])}</b> دينار\n\n'
            f'⏰ الاستثمار القادم بعد 3 ساعات',
            reply
        )
        return

    # ===========================
    # الشراء
    # ===========================
    buy_match = re.match(r'^شراء\s+(\d+)\s+(.+)$', text)
    if buy_match:
        acc = get_bank(data, chat_id, user_id)
        if not acc:
            await send(chat_id, f'😕 {m} ما عندك حساب بنكي', reply)
            return
        qty = int(buy_match.group(1))
        item_raw = buy_match.group(2).strip()
        item_key = normalize_item(item_raw)
        if not item_key or item_key not in ITEM_PRICES:
            await send(chat_id,
                f'⚠️ اسم الشيء غير صحيح\n\n'
                f'اكتب <b>المتجر</b> لعرض الأشياء المتاحة وأسعارها',
                reply
            )
            return
        price = ITEM_PRICES[item_key] * qty
        if acc.get('balance', 0) < price:
            await send(chat_id,
                f'💸 فلوسك ما تكفي يا فكر\n'
                f'💳 رصيدك: <b>{fmt_money(acc.get("balance", 0))}</b> دينار\n'
                f'💰 السعر: <b>{fmt_money(price)}</b> دينار',
                reply
            )
            return
        acc['balance'] -= price
        if 'properties' not in acc:
            acc['properties'] = {}
        acc['properties'][item_key] = acc['properties'].get(item_key, 0) + qty
        save_data(data)
        emoji = ITEM_EMOJI.get(item_key, '🏷️')
        await send(chat_id,
            f'✅ {m} اشتريت <b>{qty} {item_key}</b> {emoji}\n\n'
            f'💸 دفعت: <b>{fmt_money(price)}</b> دينار\n'
            f'💳 رصيدك الجديد: <b>{fmt_money(acc["balance"])}</b> دينار',
            reply
        )
        return

    # ===========================
    # البيع
    # ===========================
    sell_match = re.match(r'^بيع\s+(\d+)\s+(.+)$', text)
    if sell_match:
        acc = get_bank(data, chat_id, user_id)
        if not acc:
            await send(chat_id, f'😕 {m} ما عندك حساب بنكي', reply)
            return
        qty = int(sell_match.group(1))
        item_raw = sell_match.group(2).strip()
        item_key = normalize_item(item_raw)
        if not item_key or item_key not in ITEM_PRICES:
            await send(chat_id, f'⚠️ اسم الشيء غير صحيح\nاكتب <b>المتجر</b> لعرض الأشياء المتاحة', reply)
            return
        props = acc.get('properties', {})
        owned = props.get(item_key, 0)
        if owned < qty:
            await send(chat_id,
                f'⚠️ ما عندك كمية كافية\n'
                f'عندك: <b>{owned} {item_key}</b> {ITEM_EMOJI.get(item_key, "")}',
                reply
            )
            return
        sell_price = int(ITEM_PRICES[item_key] * qty * 0.60)
        props[item_key] = owned - qty
        if props[item_key] == 0:
            del props[item_key]
        acc['balance'] = acc.get('balance', 0) + sell_price
        save_data(data)
        emoji = ITEM_EMOJI.get(item_key, '🏷️')
        await send(chat_id,
            f'💰 {m} بعت <b>{qty} {item_key}</b> {emoji}\n\n'
            f'💵 استلمت: <b>{fmt_money(sell_price)}</b> دينار\n'
            f'📉 (60% من السعر الأصلي)\n'
            f'💳 رصيدك الجديد: <b>{fmt_money(acc["balance"])}</b> دينار',
            reply
        )
        return

    # ===========================
    # الإهداء
    # ===========================
    gift_match = re.match(r'^اهداء\s+(\d+)\s+(.+)$', text)
    if gift_match and msg.get('reply_to_message'):
        tf = msg['reply_to_message']['from']
        if tf['id'] == user_id:
            await send(chat_id, '⚠️ ما تقدر تهدي نفسك', reply)
            return
        acc = get_bank(data, chat_id, user_id)
        if not acc:
            await send(chat_id, f'😕 {m} ما عندك حساب بنكي', reply)
            return
        tf_acc = get_bank(data, chat_id, tf['id'])
        if not tf_acc:
            await send(chat_id, f'😕 {mention(tf)} ما عنده حساب بنكي', reply)
            return
        qty = int(gift_match.group(1))
        item_raw = gift_match.group(2).strip()
        item_key = normalize_item(item_raw)
        if not item_key or item_key not in ITEM_PRICES:
            await send(chat_id, f'⚠️ اسم الشيء غير صحيح\nاكتب <b>المتجر</b> لعرض الأشياء المتاحة', reply)
            return
        props = acc.get('properties', {})
        owned = props.get(item_key, 0)
        if owned < qty:
            await send(chat_id,
                f'⚠️ ما عندك كمية كافية\nعندك: <b>{owned} {item_key}</b> {ITEM_EMOJI.get(item_key, "")}',
                reply
            )
            return
        props[item_key] = owned - qty
        if props[item_key] == 0:
            del props[item_key]
        if 'properties' not in tf_acc:
            tf_acc['properties'] = {}
        tf_acc['properties'][item_key] = tf_acc['properties'].get(item_key, 0) + qty
        save_data(data)
        emoji = ITEM_EMOJI.get(item_key, '🏷️')
        await send(chat_id,
            f'🎁 {m} أهدى {mention(tf)}\n\n'
            f'{emoji} <b>{qty} {item_key}</b>',
            reply
        )
        return

    # ===========================
    # تحويل الفلوس
    # ===========================
    text_parts = text.split()
    if len(text_parts) == 3 and text_parts[0] == 'تحويل' and text_parts[1].isdigit() and text_parts[2].isdigit():
        acc = get_bank(data, chat_id, user_id)
        if not acc:
            await send(chat_id, f'😕 {m} ما عندك حساب بنكي', reply)
            return
        amount = int(text_parts[1])
        target_account_number = text_parts[2]
        if amount <= 0:
            await send(chat_id, '⚠️ المبلغ يجب أن يكون أكبر من صفر', reply)
            return
        if acc.get('balance', 0) < amount:
            await send(chat_id, f'💸 فلوسك ما تكفي يا فكر\n💳 رصيدك: <b>{fmt_money(acc.get("balance", 0))}</b> دينار', reply)
            return
        cid_str = str(chat_id)
        target_uid = None
        target_acc = None
        if cid_str in data.get('bank_accounts', {}):
            for uid_key, acc_data in data['bank_accounts'][cid_str].items():
                if acc_data.get('account_number') == target_account_number:
                    target_uid = uid_key
                    target_acc = acc_data
                    break
        if not target_acc:
            await send(chat_id, f'⚠️ رقم الحساب <b>{target_account_number}</b> غير موجود', reply)
            return
        if target_uid == str(user_id):
            await send(chat_id, '⚠️ ما تقدر تحول لحسابك نفسه', reply)
            return
        acc['balance'] -= amount
        target_acc['balance'] = target_acc.get('balance', 0) + amount
        save_data(data)
        await send(chat_id,
            f'✅ <b>تم التحويل بنجاح</b>\n\n'
            f'💸 المبلغ المحول: <b>{fmt_money(amount)}</b> دينار\n'
            f'📤 إلى حساب: <code>{target_account_number}</code>\n'
            f'💳 رصيدك الجديد: <b>{fmt_money(acc["balance"])}</b> دينار',
            reply
        )
        return

    # ===========================
    # ممتلكاتي
    # ===========================
    if text == 'ممتلكاتي':
        acc = get_bank(data, chat_id, user_id)
        if not acc:
            await send(chat_id, f'😕 {m} ما عندك حساب بنكي', reply)
            return
        props = acc.get('properties', {})
        if not props:
            await send(chat_id, f'😕 {m} ما عندك ممتلكات', reply)
            return
        lines = []
        for item, qty in props.items():
            emoji = ITEM_EMOJI.get(item, '🏷️')
            lines.append(f'{emoji} {item}: <b>{qty}</b>')
        await send(chat_id,
            f'🏠 <b>ممتلكات {name(from_)}</b>\n\n' + '\n'.join(lines) +
            f'\n\n💳 الرصيد: <b>{fmt_money(acc.get("balance", 0))}</b> دينار',
            reply
        )
        return

# ===========================
# STATE FLOW
# ===========================

async def handle_state(msg, data, state, user_state, text):
    chat_id = msg['chat']['id']
    msg_id = msg['message_id']
    user_id = msg['from']['id']
    cid = str(chat_id)
    uid = str(user_id)
    reply = {'reply_to_message_id': msg_id}

    if user_state['step'] == 'await_clean_time':
        if not text or not text.strip().isdigit():
            await send(chat_id, '⚠️ أرسل رقم صحيح (عدد الدقائق)، مثال: 5', reply)
            return
        minutes = int(text.strip())
        if minutes < 1:
            minutes = 1
        settings = get_settings(data, chat_id)
        settings['clean_interval'] = minutes
        del state[cid][uid]
        save_state(state)
        save_data(data)
        clean_text, clean_keyboard = build_clean_menu(settings)
        await send(chat_id, f'✅ تم تعيين وقت التنظيف: {minutes} {"دقيقة" if minutes == 1 else "دقائق"}', reply)
        await send(chat_id, clean_text, {'reply_markup': clean_keyboard})
        return

    if user_state['step'] == 'await_name':
        if not text:
            await send(chat_id, '⚠️ أرسل اسم الرد:', reply)
            return
        state[cid][uid] = {'step': 'await_content', 'name': text}
        await send(chat_id, '📦 أرسل محتوى الرد (نص، صورة، فيديو...):', reply)

    elif user_state['step'] == 'await_content':
        n = user_state['name']
        if cid not in data['custom_replies']:
            data['custom_replies'][cid] = {}
        if msg.get('photo'):
            fid = msg['photo'][-1]['file_id']
            data['custom_replies'][cid][n] = {'type': 'photo', 'file_id': fid, 'caption': msg.get('caption', '')}
        elif msg.get('video'):
            data['custom_replies'][cid][n] = {'type': 'video', 'file_id': msg['video']['file_id'], 'caption': msg.get('caption', '')}
        elif text:
            data['custom_replies'][cid][n] = {'type': 'text', 'content': text}
        else:
            await send(chat_id, '⚠️ أرسل نص أو صورة أو فيديو', reply)
            return
        del state[cid][uid]
        await send(chat_id, f'✅ تم إضافة الرد <b>{n}</b> بنجاح 🌹', reply)

    elif user_state['step'] == 'await_delete_name':
        if not text:
            await send(chat_id, '⚠️ أرسل اسم الرد:', reply)
            return
        if data['custom_replies'].get(cid, {}).get(text):
            del data['custom_replies'][cid][text]
            await send(chat_id, f'✅ تم حذف الرد <b>{text}</b>', reply)
        else:
            await send(chat_id, f'⚠️ لم أجد ردًا باسم <b>{text}</b>', reply)
        del state[cid][uid]

    elif user_state['step'] == 'await_repeat_messages':
        if not text or not text.strip().isdigit():
            await send(chat_id, 'ارسل رقم صحيح، مثال: 3', reply)
            return
        val = int(text.strip())
        if val < 1:
            val = 1
        settings = get_settings(data, chat_id)
        settings['repeat_max_messages'] = val
        menu_msg_id = user_state.get('menu_msg_id')
        del state[cid][uid]
        save_state(state)
        save_data(data)
        await send(chat_id, 'تم تعيين عدد الرسائل المسموح: <b>' + str(val) + '</b>', reply)
        if menu_msg_id:
            secs = settings.get('repeat_seconds', 7)
            restrict_on = settings.get('lock_repeat_restrict', False)
            warn_on = settings.get('lock_repeat_warn', False)
            repeat_on = settings.get('lock_repeat', False)
            status_txt = (
                '<b>اعدادات قفل التكرار</b>\n\n'
                + 'قفل التكرار: ' + ('مفعل' if repeat_on else 'معطل') + '\n'
                + 'قفل التكرار بالتقييد: ' + ('مفعل' if restrict_on else 'معطل') + '\n'
                + 'قفل التكرار بالتحذير: ' + ('مفعل' if warn_on else 'معطل') + '\n'
                + 'عدد الرسائل المسموح: <b>' + str(val) + '</b>\n'
                + 'النافذة الزمنية: <b>' + str(secs) + '</b> ثانية'
            )
            kbd = {
                'inline_keyboard': [
                    [{'text': 'عدد رسائل (' + str(val) + ')', 'callback_data': 'repeat_set_messages'}],
                    [{'text': 'عدد ثواني (' + str(secs) + ')', 'callback_data': 'repeat_set_seconds'}],
                    [{'text': 'بالتقييد ' + ('✓' if restrict_on else '✗'), 'callback_data': 'repeat_toggle_restrict'}],
                    [{'text': 'بالتحذير ' + ('✓' if warn_on else '✗'), 'callback_data': 'repeat_toggle_warn'}],
                ]
            }
            await edit_msg(chat_id, menu_msg_id, status_txt, kbd)
        return

    elif user_state['step'] == 'await_repeat_seconds':
        if not text or not text.strip().isdigit():
            await send(chat_id, 'ارسل رقم صحيح، مثال: 60', reply)
            return
        val = int(text.strip())
        if val < 5:
            val = 5
        settings = get_settings(data, chat_id)
        settings['repeat_seconds'] = val
        menu_msg_id = user_state.get('menu_msg_id')
        del state[cid][uid]
        save_state(state)
        save_data(data)
        await send(chat_id, 'تم تعيين النافذة الزمنية: <b>' + str(val) + '</b> ثانية', reply)
        if menu_msg_id:
            max_msgs = settings.get('repeat_max_messages', 5)
            restrict_on = settings.get('lock_repeat_restrict', False)
            warn_on = settings.get('lock_repeat_warn', False)
            repeat_on = settings.get('lock_repeat', False)
            status_txt = (
                '<b>اعدادات قفل التكرار</b>\n\n'
                + 'قفل التكرار: ' + ('مفعل' if repeat_on else 'معطل') + '\n'
                + 'قفل التكرار بالتقييد: ' + ('مفعل' if restrict_on else 'معطل') + '\n'
                + 'قفل التكرار بالتحذير: ' + ('مفعل' if warn_on else 'معطل') + '\n'
                + 'عدد الرسائل المسموح: <b>' + str(max_msgs) + '</b>\n'
                + 'النافذة الزمنية: <b>' + str(val) + '</b> ثانية'
            )
            kbd = {
                'inline_keyboard': [
                    [{'text': 'عدد رسائل (' + str(max_msgs) + ')', 'callback_data': 'repeat_set_messages'}],
                    [{'text': 'عدد ثواني (' + str(val) + ')', 'callback_data': 'repeat_set_seconds'}],
                    [{'text': 'بالتقييد ' + ('✓' if restrict_on else '✗'), 'callback_data': 'repeat_toggle_restrict'}],
                    [{'text': 'بالتحذير ' + ('✓' if warn_on else '✗'), 'callback_data': 'repeat_toggle_warn'}],
                ]
            }
            await edit_msg(chat_id, menu_msg_id, status_txt, kbd)
        return

    elif user_state['step'] == 'await_lock_cmd_name':
        if not text:
            await send(chat_id, '⚠️ أرسل اسم الأمر:', reply)
            return
        state[cid][uid] = {'step': 'await_lock_cmd_rank', 'cmd_name': text}
        rank_buttons = [
            [{'text': '1 - مالك اساسي', 'callback_data': f'lock_cmd_rank:{text}:مالك اساسي'}],
            [{'text': '2 - مالك', 'callback_data': f'lock_cmd_rank:{text}:مالك'}],
            [{'text': '3 - مدير', 'callback_data': f'lock_cmd_rank:{text}:مدير'}],
            [{'text': '4 - ادمن', 'callback_data': f'lock_cmd_rank:{text}:ادمن'}],
            [{'text': '5 - مميز', 'callback_data': f'lock_cmd_rank:{text}:مميز'}],
        ]
        await send(chat_id,
            f'‹ حسناً عزيزي اختار نوع الرتبة :\n\n'
            f'- سيتم وضع امر ‹ <b>{text}</b> ‹ له بس',
            {'reply_markup': {'inline_keyboard': rank_buttons}, 'reply_to_message_id': msg_id})

    elif user_state['step'] == 'await_add_cmd_real':
        if not text:
            await send(chat_id, '⚠️ أرسل الأمر الحقيقي:', reply)
            return
        state[cid][uid] = {'step': 'await_add_cmd_alias', 'real_cmd': text}
        await send(chat_id, f'✏️ أرسل الأمر المراد إضافته (الاسم البديل):', reply)

    elif user_state['step'] == 'await_add_cmd_alias':
        real_cmd = user_state.get('real_cmd', '')
        if not text:
            await send(chat_id, '⚠️ أرسل الاسم البديل للأمر:', reply)
            return
        if 'custom_commands' not in data:
            data['custom_commands'] = {}
        if cid not in data['custom_commands']:
            data['custom_commands'][cid] = {}
        data['custom_commands'][cid][text] = real_cmd
        del state[cid][uid]
        await send(chat_id, f'✅ تم حفظ الامر <b>{real_cmd}</b> بامر <b>{text}</b> بنجاح', reply)

    elif user_state['step'] == 'await_banned_word_add':
        if not text:
            await send(chat_id, '⚠️ أرسل الكلمة المراد حظرها:', reply)
            return
        word = text.strip().lower()
        settings = get_settings(data, chat_id)
        bw_list = settings.get('banned_words', [])
        if word in bw_list:
            del state[cid][uid]
            await send(chat_id, f'⚠️ الكلمة <b>{word}</b> موجودة أصلاً في قائمة المحظورات', reply)
            return
        bw_list.append(word)
        settings['banned_words'] = bw_list
        del state[cid][uid]
        await send(chat_id, f'✅ تم إضافة الكلمة <b>{word}</b> إلى قائمة الكلمات المحظورة', reply)

    elif user_state['step'] == 'await_banned_word_remove':
        if not text:
            await send(chat_id, '⚠️ أرسل الكلمة المراد إزالتها:', reply)
            return
        word = text.strip().lower()
        settings = get_settings(data, chat_id)
        bw_list = settings.get('banned_words', [])
        if word not in bw_list:
            del state[cid][uid]
            await send(chat_id, f'⚠️ الكلمة <b>{word}</b> غير موجودة في قائمة المحظورات', reply)
            return
        bw_list.remove(word)
        settings['banned_words'] = bw_list
        del state[cid][uid]
        await send(chat_id, f'✅ تم إزالة الكلمة <b>{word}</b> من قائمة الكلمات المحظورة', reply)

# ===========================
# HTTP SERVER
# ===========================

PORT = int(os.environ.get('PORT', 3000))

async def register_webhook():
    if not WEBHOOK_URL:
        print('⚠️ WEBHOOK_URL غير مضبوط — البوت لن يستقبل تحديثات')
        return False
    url = f'{WEBHOOK_URL}/webhook'
    result = await api_call('setWebhook', {
        'url': url,
        'drop_pending_updates': False,
        'max_connections': 100,
    })
    if result is not None:
        print(f'✅ Webhook مسجّل بنجاح: {url}')
        return True
    else:
        print(f'❌ فشل تسجيل الـ Webhook: {url}')
        return False

async def webhook_watchdog():
    while True:
        await asyncio.sleep(180)  # كل 3 دقائق بدل 10
        try:
            info = await api_call('getWebhookInfo', {})
            url = (info or {}).get('url', '')
            pending = (info or {}).get('pending_update_count', 0)
            last_err = (info or {}).get('last_error_message', '')
            if not url or (WEBHOOK_URL and not url.startswith(WEBHOOK_URL)):
                print(f'🔄 الـ Webhook غير مسجل، يتم التسجيل...')
                await register_webhook()
            elif last_err:
                print(f'⚠️ آخر خطأ في الـ Webhook: {last_err} | تحديثات معلّقة: {pending}')
                await register_webhook()
            else:
                print(f'✅ Webhook يعمل | تحديثات معلّقة: {pending}')
        except Exception as e:
            print(f'Webhook watchdog error: {e}')

async def keep_alive_loop():
    """يحيّي الخادم كل دقيقة ويتحقق من الاتصال"""
    global _session
    await asyncio.sleep(20)
    while True:
        try:
            try:
                s = await get_session()
                async with s.get(
                    f'http://localhost:{PORT}/health',
                    timeout=aiohttp.ClientTimeout(total=5)
                ) as r:
                    pass
            except:
                pass
            # تحقق من الاتصال بتيليغرام
            try:
                sess = await get_session()
                async with sess.post(f'{API}/getMe', json={},
                    timeout=aiohttp.ClientTimeout(total=6)) as res:
                    d = await res.json()
                    if d.get('ok'):
                        print(f'💓 Keepalive OK - {datetime.now().strftime("%H:%M:%S")}')
            except Exception as e2:
                print(f'💔 Keepalive ping فشل: {e2}')
                _session = None
        except Exception as e:
            print(f'Keepalive error: {e}')
        await asyncio.sleep(60)  # كل دقيقة بدل دقيقتين

async def webhook_handler(request):
    if request.method == 'POST' and request.path == '/webhook':
        try:
            body = await request.json()
            if 'my_chat_member' in body:
                asyncio.create_task(handle_my_chat_member(body['my_chat_member']))
            else:
                asyncio.create_task(handle_update(body))
        except Exception as e:
            print(f'Webhook error: {e}')
        return web.Response(text='OK', status=200)
    if request.path == '/health':
        return web.Response(text='OK', status=200)
    return web.Response(text='Romeo Bot is running 🌹', status=200)

async def main():
    global _DATA, _STATE
    init_db()
    _DATA = _load_data_from_db()
    _STATE = _load_state_from_db()
    print('Data loaded into memory cache')
    app = web.Application()
    app.router.add_route('*', '/{tail:.*}', webhook_handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', PORT)
    await site.start()
    print(f'Romeo Bot running on port {PORT}')
    asyncio.create_task(auto_clean_loop())
    asyncio.create_task(_db_flush_loop())
    asyncio.create_task(keep_alive_loop())
    await asyncio.sleep(2)
    await register_webhook()
    asyncio.create_task(webhook_watchdog())
    print('✅ جميع المهام تعمل — البوت جاهز')
    await asyncio.Event().wait()

if __name__ == '__main__':
    asyncio.run(main())
