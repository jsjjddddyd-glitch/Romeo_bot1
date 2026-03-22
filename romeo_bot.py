# -*- coding: utf-8 -*-
import json
import os
import re
import asyncio
import random
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

SIGHTENGINE_API_USER = '130043340'
SIGHTENGINE_API_SECRET = 'RFozDT5M3VYmccC2rcArKqnMPWPCKJfE'

DEVELOPER_USERNAME = 'c9aac'

_session = None

async def get_session():
    global _session
    if _session is None or _session.closed:
        connector = aiohttp.TCPConnector(limit=100, ttl_dns_cache=300)
        _session = aiohttp.ClientSession(connector=connector)
    return _session

clean_queue = {}
last_clean_time = {}

private_states = {}

last_messages = {}

whispers = {}

# username_map[chat_id][username_lower] = {id, first_name, last_name, username}
username_map = {}
BOT_USERNAME = None

async def get_bot_username():
    global BOT_USERNAME
    if not BOT_USERNAME:
        bot_info = await api_call('getMe', {})
        BOT_USERNAME = (bot_info or {}).get('username', '')
    return BOT_USERNAME

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

def load_data():
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

def save_data(d):
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

def load_state():
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

def save_state(s):
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

def get_settings(data, chat_id):
    id_ = str(chat_id)
    if id_ not in data['group_settings']:
        data['group_settings'][id_] = {}
    s = data['group_settings'][id_]
    defaults = {
        'lock_swear': False, 'lock_links': False, 'lock_forward': False, 'lock_clutter': False,
        'lock_english': False, 'lock_chinese': False, 'lock_russian': False, 'lock_photos': False,
        'lock_videos': False, 'lock_media_edit': False, 'lock_audio': False, 'lock_music': False,
        'lock_repeat': False, 'lock_mention': False, 'lock_numbers': False, 'lock_stickers': False,
        'lock_animated': False, 'lock_chat': False, 'lock_join': False,
        'lock_external_reply': False, 'lock_quote': False,
        'disable_id': False, 'disable_service': False, 'disable_fun': True,
        'disable_welcome': False, 'disable_link': False, 'disable_auto_replies': False,
        'disable_games': True,
        'lock_nsfw': False,
        'lock_nsfw_restrict': False,
        'lock_nsfw_warn': False,
        'lock_id_documents': False,
        'lock_files': False,
        'lock_channel_usernames': False,
        'lock_all_usernames': False,
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
    return (data['user_ranks'].get(str(chat_id)) or {}).get(str(user_id), 'عضو')

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

RANKS = {'عضو': 0, 'مميز': 1, 'ادمن': 2, 'أدمن': 2, 'مدير': 3, 'مالك': 4, 'مالك اساسي': 5, 'مالك أساسي': 5}

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
    'سيارة': 5000, 'سيارات': 5000,
    'طيارة': 50000, 'طيارات': 50000,
    'قصر': 100000, 'قصور': 100000,
    'بيت': 20000, 'بيوت': 20000,
    'جندي': 3000, 'جنود': 3000,
    'فيل': 30000, 'فيله': 30000,
    'برج': 80000, 'ابراج': 80000,
    'دبابة': 40000, 'دبابات': 40000,
}

ITEM_EMOJI = {
    'سيارة': '🚗', 'طيارة': '✈️', 'قصر': '🏰', 'بيت': '🏠',
    'جندي': '💂', 'فيل': '🐘', 'برج': '🏙️', 'دبابة': '🪖'
}

ITEM_SINGULAR = {
    'سيارات': 'سيارة', 'طيارات': 'طيارة', 'قصور': 'قصر',
    'بيوت': 'بيت', 'جنود': 'جندي', 'فيله': 'فيل',
    'ابراج': 'برج', 'دبابات': 'دبابة',
}

def normalize_item(item_name):
    item_name = item_name.strip()
    if item_name in ITEM_SINGULAR:
        return ITEM_SINGULAR[item_name]
    if item_name in ITEM_PRICES:
        return item_name
    return None

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
    try:
        session = await get_session()
        async with session.post(f'{API}/{method}', json=params) as res:
            data = await res.json()
            return data['result'] if data.get('ok') else None
    except:
        return None

async def send(chat_id, text, extra=None):
    params = {'chat_id': chat_id, 'text': text, 'parse_mode': 'HTML'}
    if extra:
        params.update(extra)
    return await api_call('sendMessage', params)

async def delete(chat_id, msg_id):
    return await api_call('deleteMessage', {'chat_id': chat_id, 'message_id': msg_id})

async def get_chat_member(chat_id, user_id):
    return await api_call('getChatMember', {'chat_id': chat_id, 'user_id': user_id})

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
    if await is_tg_admin(chat_id, user_id):
        return True
    return rank_level(get_rank(data, chat_id, user_id)) >= rank_level('ادمن')

async def is_owner_up(data, chat_id, user_id):
    m = await get_chat_member(chat_id, user_id)
    if m and m.get('status') == 'creator':
        return True
    return rank_level(get_rank(data, chat_id, user_id)) >= rank_level('مالك')

async def is_master(data, chat_id, user_id):
    m = await get_chat_member(chat_id, user_id)
    if m and m.get('status') == 'creator':
        return True
    return rank_level(get_rank(data, chat_id, user_id)) >= rank_level('مالك اساسي')

async def is_group_creator(chat_id, user_id):
    m = await get_chat_member(chat_id, user_id)
    return m and m.get('status') == 'creator'

async def resolve_target(msg, text_after_cmd):
    """يرجع (from_dict, error_text) - يقبل رد على رسالة أو @يوزر أو ID"""
    chat_id = str(msg['chat']['id'])

    # 1) إذا كان رد على رسالة - الأولوية القصوى
    if msg.get('reply_to_message'):
        tf = msg['reply_to_message'].get('from', {})
        if tf:
            return tf, None

    target_str = (text_after_cmd or '').strip()

    # 2) فحص entities الرسالة - text_mention يعطينا المستخدم مباشرة بدون يوزرنيم
    all_entities = msg.get('entities') or msg.get('caption_entities') or []
    msg_text = msg.get('text') or msg.get('caption') or ''
    for ent in all_entities:
        ent_type = ent.get('type')
        if ent_type == 'text_mention':
            u = ent.get('user', {})
            if u and u.get('id'):
                return u, None
        if ent_type == 'mention':
            offset = ent.get('offset', 0)
            length = ent.get('length', 0)
            mentioned_raw = msg_text[offset:offset + length]  # مثال: @username
            mentioned_uname = mentioned_raw.lstrip('@').lower()
            # ابحث في username_map أولاً
            cmap = username_map.get(chat_id, {})
            if mentioned_uname in cmap:
                return cmap[mentioned_uname], None
            # جرب getChat كحل أخير
            try:
                info = await api_call('getChat', {'chat_id': f'@{mentioned_uname}'})
                if info and info.get('id'):
                    return {
                        'id': info['id'],
                        'first_name': info.get('first_name') or info.get('title') or mentioned_raw,
                        'last_name': info.get('last_name', ''),
                        'username': info.get('username', mentioned_raw)
                    }, None
            except:
                pass
            return None, f'⚠️ لم أجد المستخدم {mentioned_raw} - يجب أن يكتب في المجموعة أولاً حتى يتعرف عليه البوت'

    # 3) إذا كتب @يوزر في نص الأمر بدون entity (نادر)
    if target_str.startswith('@'):
        username = target_str.lstrip('@').lower()
        cmap = username_map.get(chat_id, {})
        if username in cmap:
            return cmap[username], None
        try:
            info = await api_call('getChat', {'chat_id': f'@{username}'})
            if info and info.get('id'):
                return {
                    'id': info['id'],
                    'first_name': info.get('first_name') or info.get('title') or target_str,
                    'last_name': info.get('last_name', ''),
                    'username': info.get('username', username)
                }, None
        except:
            pass
        return None, f'⚠️ لم أجد المستخدم @{username} - يجب أن يكتب في المجموعة أولاً'

    # 4) إذا كان ID رقمي
    if target_str.isdigit():
        uid = int(target_str)
        cmap = username_map.get(chat_id, {})
        if str(uid) in cmap:
            return cmap[str(uid)], None
        return {'id': uid, 'first_name': str(uid), 'last_name': '', 'username': ''}, None

    return None, '⚠️ رد على رسالة الشخص أو اكتب @يوزره'

# ===========================
# NSFW IMAGE DETECTION
# ===========================

async def check_image_nsfw(file_id):
    if not SIGHTENGINE_API_USER or not SIGHTENGINE_API_SECRET:
        return False, None
    try:
        file_info = await get_file(file_id)
        if not file_info:
            return False, None
        file_path = file_info.get('file_path')
        if not file_path:
            return False, None
        file_url = f'https://api.telegram.org/file/bot{TOKEN}/{file_path}'
        session = await get_session()
        base_params = {
            'url': file_url,
            'api_user': SIGHTENGINE_API_USER,
            'api_secret': SIGHTENGINE_API_SECRET
        }

        # ==============================
        # استدعاء 1: الموديلات الأساسية (دائماً تشتغل)
        # ==============================
        result = None
        params1 = dict(base_params)
        params1['models'] = 'nudity-2.1,weapon,recreational_drug,gore-2.0,text-content'
        async with session.get('https://api.sightengine.com/1.0/check.json', params=params1) as res:
            if res.status == 200:
                r = await res.json()
                if r.get('status') == 'success':
                    result = r

        if result is None:
            return False, None

        # ==============================
        # استدعاء 2: موديل id-document (مستقل - إذا فشل نكمل بدونه)
        # ==============================
        try:
            params2 = dict(base_params)
            params2['models'] = 'id-document'
            async with session.get('https://api.sightengine.com/1.0/check.json', params=params2) as res2:
                if res2.status == 200:
                    r2 = await res2.json()
                    if r2.get('status') == 'success':
                        id_doc = r2.get('id-document', {}) or {}
                        doc_prob = id_doc.get('prob', 0)
                        doc_type = id_doc.get('type', '')
                        if doc_prob > 0.15 or doc_type in (
                            'id', 'passport', 'driver_license', 'id_card',
                            'residence_permit', 'visa', 'national_id'
                        ):
                            return True, 'وثيقة حكومية (هوية/جواز)'
        except Exception:
            pass

        # ==============================
        # فحص المحتوى الإباحي - أعلى حساسية
        # ==============================
        nudity = result.get('nudity', {})
        suggestive_classes = nudity.get('suggestive_classes', {})
        if (
            nudity.get('sexual_activity', 0) > 0.02
            or nudity.get('sexual_display', 0) > 0.02
            or nudity.get('erotica', 0) > 0.03
            or nudity.get('very_suggestive', 0) > 0.05
            or nudity.get('suggestive', 0) > 0.07
            or nudity.get('mildly_suggestive', 0) > 0.15
            or suggestive_classes.get('suggestive_focus_body_part', 0) > 0.05
            or suggestive_classes.get('lingerie', 0) > 0.05
            or suggestive_classes.get('cleavage', 0) > 0.07
            or suggestive_classes.get('bikini', 0) > 0.07
            or suggestive_classes.get('miniskirt', 0) > 0.1
            or suggestive_classes.get('nudity_art', 0) > 0.05
            or suggestive_classes.get('male_chest_bare', 0) > 0.2
        ):
            return True, 'إباحي'

        # ==============================
        # فحص الأسلحة - أعلى حساسية
        # ==============================
        weapon = result.get('weapon', {})
        weapon_classes = weapon.get('classes', {})
        if (
            weapon_classes.get('firearm', 0) > 0.1
            or weapon_classes.get('knife', 0) > 0.1
            or weapon_classes.get('gun', 0) > 0.1
            or weapon_classes.get('rifle', 0) > 0.1
            or weapon_classes.get('handgun', 0) > 0.1
            or weapon.get('prob', 0) > 0.1
        ):
            return True, 'أسلحة'

        # ==============================
        # فحص المواد الممنوعة - أعلى حساسية
        # ==============================
        drug = result.get('recreational_drug', {})
        drug_classes = drug.get('classes', {})
        if drug.get('prob', 0) > 0.01 or any(v > 0.01 for v in drug_classes.values()):
            return True, 'مواد ممنوعة'

        # ==============================
        # فحص المحتوى العنيف - أعلى حساسية
        # ==============================
        gore = result.get('gore', {})
        if gore.get('prob', 0) > 0.02:
            return True, 'محتوى عنيف (دماء)'

        # ==============================
        # رصد الوثائق الحكومية عبر النص (text-content)
        # ==============================
        text_content = result.get('text', {})
        if isinstance(text_content, dict):
            detected_items = text_content.get('detected', [])
            all_text_parts = [t.get('content', '') for t in detected_items]
            detected_text = ' '.join(all_text_parts).lower()
            raw_text_joined = ' '.join(all_text_parts)

            # كلمات قاطعة - كلمة واحدة تكفي
            strong_id_keywords = [
                'passport', 'passeport', 'reisepass', 'passaporto', 'pasaporte',
                'national id', 'national identity', 'nationalausweis',
                'driver license', "driver's license", 'driving licence', 'drivers license',
                'identity card', 'carte nationale', "carte d'identite", 'carte d identite',
                'personalausweis', 'bundesrepublik', 'republique francaise',
                'cedula de identidad', 'cedula ciudadania', 'cedula de ciudadania',
                'dowod osobisty', 'id card', 'id number', 'government id',
                'national card', 'residence permit', 'permanent resident',
                'رقم الهوية', 'هوية وطنية', 'بطاقة هوية', 'بطاقه هويه',
                'جواز السفر', 'رخصة القيادة', 'رخصه القياده', 'بطاقة شخصية',
                'الرقم القومي', 'رقم جواز', 'وثيقة سفر',
                'kingdom of saudi', 'المملكة العربية', 'الجمهورية العربية',
                'جمهورية العراق', 'جمهورية مصر', 'دولة الإمارات',
                'جمهورية تونس', 'المملكة المغربية', 'الجمهورية الجزائرية',
                'rzeczpospolita', 'polska', 'republic of poland',
                'bundesrepublik deutschland', 'united kingdom',
                'carte de sejour', 'permis de conduire', 'fuhrerschein',
                'tarjeta de identidad', 'documento nacional',
            ]
            for kw in strong_id_keywords:
                if kw in detected_text:
                    return True, 'وثيقة حكومية (هوية/جواز)'

            # كلمات تراكمية - يكفي واحدة مع MRZ أو 2 منها بدون MRZ
            soft_id_keywords = [
                'republic', 'nationality', 'date of birth', 'dob', 'expiry', 'expires',
                'expiration', 'valid until', 'surname', 'given name', 'given names',
                'personal number', 'personal no', 'place of birth', 'identification',
                'citizen', 'document no', 'doc no', 'document number', 'sex / sexe',
                'sex/sexe', 'gender', 'ausweis', 'republique', 'dni', 'pesel',
                'nazwisko', 'imiona', 'obywatelstwo', 'organ wydajacy',
                'data wydania', 'data urodzenia', 'data waznosci',
                'الجنسية', 'تاريخ الميلاد', 'تاريخ الانتهاء', 'تاريخ الإصدار',
                'مكان الميلاد', 'نمرة الوثيقة', 'رقم الوثيقة', 'تاريخ الانتها',
                'الاسم الأول', 'اسم الأب', 'الاسم الكامل', 'الجنس',
            ]
            soft_count = sum(1 for kw in soft_id_keywords if kw in detected_text)
            # رصد نمط MRZ (سطر الماكينة في أسفل الجواز/الهوية)
            mrz_pattern = re.search(r'[A-Z0-9<]{10,}', raw_text_joined)
            if mrz_pattern and soft_count >= 1:
                return True, 'وثيقة حكومية (هوية/جواز)'
            if soft_count >= 2:
                return True, 'وثيقة حكومية (هوية/جواز)'

        return False, None
    except Exception as e:
        print(f'NSFW check error: {e}')
        return False, None


async def check_video_nsfw(file_id):
    if not SIGHTENGINE_API_USER or not SIGHTENGINE_API_SECRET:
        return False, None
    try:
        file_info = await get_file(file_id)
        if not file_info:
            return False, None
        file_path = file_info.get('file_path')
        if not file_path:
            return False, None
        file_url = f'https://api.telegram.org/file/bot{TOKEN}/{file_path}'
        session = await get_session()
        params = {
            'url': file_url,
            'models': 'nudity-2.1,weapon,recreational_drug,gore-2.0',
            'interval': '0.5',
            'api_user': SIGHTENGINE_API_USER,
            'api_secret': SIGHTENGINE_API_SECRET
        }
        async with session.get('https://api.sightengine.com/1.0/video/check-sync.json', params=params) as res:
            if res.status != 200:
                return False, None
            result = await res.json()
            if result.get('status') != 'success':
                return False, None
            frames = result.get('data', {}).get('frames', [])
            for frame in frames:
                nudity = frame.get('nudity', {})
                suggestive_classes = nudity.get('suggestive_classes', {})
                if (
                    nudity.get('sexual_activity', 0) > 0.05
                    or nudity.get('sexual_display', 0) > 0.05
                    or nudity.get('erotica', 0) > 0.07
                    or nudity.get('very_suggestive', 0) > 0.1
                    or nudity.get('suggestive', 0) > 0.15
                    or suggestive_classes.get('suggestive_focus_body_part', 0) > 0.1
                    or suggestive_classes.get('lingerie', 0) > 0.1
                    or suggestive_classes.get('cleavage', 0) > 0.15
                    or suggestive_classes.get('bikini', 0) > 0.15
                    or suggestive_classes.get('miniskirt', 0) > 0.2
                ):
                    return True, 'إباحي'
                weapon = frame.get('weapon', {})
                weapon_classes = weapon.get('classes', {})
                if (
                    weapon_classes.get('firearm', 0) > 0.3
                    or weapon_classes.get('knife', 0) > 0.3
                    or weapon_classes.get('gun', 0) > 0.3
                ):
                    return True, 'أسلحة'
                drug = frame.get('recreational_drug', {})
                drug_prob = drug.get('prob', 0)
                drug_classes = drug.get('classes', {})
                if drug_prob > 0.04 or any(v > 0.04 for v in drug_classes.values()):
                    return True, 'مواد ممنوعة'
                gore = frame.get('gore', {})
                if gore.get('prob', 0) > 0.04:
                    return True, 'محتوى عنيف (دماء)'
            return False, None
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
        '🤫 <b>الهمسة:</b>\n'
        '• <b>همسه / اهمس</b> (رد على رسالة شخص) - ترسل همسة خاصة له فقط\n\n'
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
        'قفل الملفات | قفل يوزرات القنوات\n'
        'قفل كل اليوزرات\n'
        'قفل الردود الخارجية | قفل الاقتباسات\n'
        'قفل المحتوى المخل | فتح المحتوى المخل\n'
        'قفل المحتوى المخل بالتقييد\n'
        'قفل المحتوى المخل بالتحذير\n'
        'قفل الوثائق الحكومية | فتح الوثائق الحكومية'
    ),
    'menu_settings': (
        '⚙️ <b>أوامر الإعدادات (رد على رسالة شخص):</b>\n\n'
        '• رفع مالك أساسي / تنزيل مالك أساسي\n'
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
        '• <b>زرف</b> (رد) - سرقة فلوس من شخص (كل 4 ساعات)\n'
        '• <b>حظ [مبلغ]</b> - المقامرة بمبلغ\n'
        '• <b>شراء [عدد] [اسم الشيء]</b> - شراء ممتلكات\n'
        '• <b>ممتلكاتي</b> - عرض ممتلكاتك\n'
        '• <b>تحويل [مبلغ] [رقم الحساب]</b> - تحويل فلوس لشخص آخر\n\n'
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

    # حفظ بيانات المستخدم في username_map لاستخدامها لاحقاً في resolve_target
    if user_id and chat_type in ('group', 'supergroup'):
        cid_str = str(chat_id)
        if cid_str not in username_map:
            username_map[cid_str] = {}
        uname = (from_.get('username') or '').lower()
        user_entry = {
            'id': user_id,
            'first_name': from_.get('first_name', ''),
            'last_name': from_.get('last_name', ''),
            'username': from_.get('username', '')
        }
        if uname:
            username_map[cid_str][uname] = user_entry
        username_map[cid_str][str(user_id)] = user_entry

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

        if text == 'info':
            from_username = (from_.get('username') or '').lower().strip('@')
            dev_check = await api_call('getChat', {'chat_id': f'@{DEVELOPER_USERNAME}'})
            dev_id = (dev_check or {}).get('id')
            is_dev = (dev_id and user_id == dev_id) or (from_username and from_username == DEVELOPER_USERNAME.lower().strip('@'))
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

    user_state = (state.get(cid) or {}).get(str(user_id))
    if user_state:
        await handle_state(msg, data, state, user_state, text)
        save_data(data)
        save_state(state)
        return

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
        [{'text': '📋 مجموعاتي', 'callback_data': 'show_my_groups'}],
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

    if await is_admin_up(data, chat_id, user_id):
        return

    if settings.get('lock_external_reply') and msg.get('external_reply'):
        await send(chat_id, f'‹‹ عذراً عزيزي ‹ {uname} ›\n‹‹ ممنوع الردود الخارجية هنا .', reply)
        await delete(chat_id, msg_id)
        return

    if settings.get('lock_quote') and msg.get('quote'):
        await send(chat_id, f'‹‹ عذراً عزيزي ‹ {uname} ›\n‹‹ ممنوع الاقتباس هنا .', reply)
        await delete(chat_id, msg_id)
        return

    is_forward = msg.get('forward_from') or msg.get('forward_from_chat') or msg.get('forward_sender_name')
    if is_forward and settings['lock_forward']:
        await send(chat_id, f'‹‹ عذراً عزيزي ‹ {uname} ›\n‹‹ ممنوع التوجيه والتحويل هنا .', reply)
        await delete(chat_id, msg_id)
        return

    if msg.get('document'):
        if settings.get('lock_files'):
            await send(chat_id, f'‹‹ عذراً عزيزي ‹ {uname} ›\n‹‹ ممنوع ارسال الملفات هنا .', reply)
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
            await send(chat_id, f'‹‹ عذراً عزيزي ‹ {uname} ›\n‹‹ ممنوع ارسال الصور المتحركة هنا .', reply)
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
            await send(chat_id, f'‹‹ عذراً عزيزي ‹ {uname} ›\n‹‹ ممنوع ارسال الصور هنا .', reply)
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
            await send(chat_id, f'‹‹ عذراً عزيزي ‹ {uname} ›\n‹‹ ممنوع ارسال الفيديوهات هنا .', reply)
            await delete(chat_id, msg_id)
            return
        if settings.get('clean_auto'):
            add_to_clean_queue(chat_id, msg_id, 'videos')
        return

    if msg.get('voice') and settings['lock_audio']:
        await send(chat_id, f'‹‹ عذراً عزيزي ‹ {uname} ›\n‹‹ ممنوع ارسال الرسائل الصوتية هنا .', reply)
        await delete(chat_id, msg_id)
        return

    if msg.get('audio') and settings['lock_music']:
        await send(chat_id, f'‹‹ عذراً عزيزي ‹ {uname} ›\n‹‹ ممنوع ارسال الاغاني هنا .', reply)
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
            await send(chat_id, f'‹‹ عذراً عزيزي ‹ {uname} ›\n‹‹ ممنوع الملصقات المتحركة هنا .', reply)
            await delete(chat_id, msg_id)
            return
        if not is_animated and settings['lock_stickers']:
            await send(chat_id, f'‹‹ عذراً عزيزي ‹ {uname} ›\n‹‹ ممنوع الملصقات هنا .', reply)
            await delete(chat_id, msg_id)
            return
        if settings.get('clean_auto'):
            add_to_clean_queue(chat_id, msg_id, 'stickers')
        return

# ===========================
# CONTENT MODERATION
# ===========================

async def content_mod(msg, data, settings):
    chat_id = msg['chat']['id']
    msg_id = msg['message_id']
    from_ = msg.get('from', {})
    user_id = from_.get('id')
    text = msg.get('text') or msg.get('caption') or ''
    m = mention(from_)
    reply = {'reply_to_message_id': msg_id}

    if await is_admin_up(data, chat_id, user_id):
        return False

    uname = name(from_)

    if settings.get('lock_external_reply') and msg.get('external_reply'):
        await send(chat_id, f'‹‹ عذراً عزيزي ‹ {uname} ›\n‹‹ ممنوع الردود الخارجية هنا .', reply)
        await delete(chat_id, msg_id)
        return True

    if settings.get('lock_quote') and msg.get('quote'):
        await send(chat_id, f'‹‹ عذراً عزيزي ‹ {uname} ›\n‹‹ ممنوع الاقتباس هنا .', reply)
        await delete(chat_id, msg_id)
        return True

    is_forward = msg.get('forward_from') or msg.get('forward_from_chat') or msg.get('forward_sender_name')
    if is_forward and settings['lock_forward']:
        await send(chat_id, f'‹‹ عذراً عزيزي ‹ {uname} ›\n‹‹ ممنوع التوجيه والتحويل هنا .', reply)
        await delete(chat_id, msg_id)
        return True

    swears = ['انيجك', 'انيج امك', 'كسمك', 'عير بابوك', 'عير بامك', 'قحبه', 'كحبه', 'شرموط', 'شرموطه', 'زبفيك', 'عيرك', 'كسي', 'زبي', 'عيري']
    if settings['lock_swear'] and any(w in text for w in swears):
        await send(chat_id, f'‹‹ عذراً عزيزي ‹ {uname} ›\n‹‹ ممنوع السب هنا .', reply)
        await delete(chat_id, msg_id)
        return True

    if settings['lock_links'] and re.search(r'(https?://|t\.me/|www\.)', text, re.IGNORECASE):
        await send(chat_id, f'‹‹ عذراً عزيزي ‹ {uname} ›\n‹‹ ممنوع ارسال الروابط هنا .', reply)
        await delete(chat_id, msg_id)
        return True

    if settings['lock_mention'] and re.search(r'@\w+', text):
        await send(chat_id, f'‹‹ عذراً عزيزي ‹ {uname} ›\n‹‹ ممنوع التاك هنا .', reply)
        await delete(chat_id, msg_id)
        return True

    if settings['lock_numbers'] and re.search(r'(?<!\d)\+?\d{9,12}(?!\d)', text):
        await send(chat_id, f'‹‹ عذراً عزيزي ‹ {uname} ›\n‹‹ ممنوع ارسال الارقام هنا .', reply)
        await delete(chat_id, msg_id)
        return True

    if settings['lock_clutter'] and len(text) > 1000:
        await send(chat_id, f'‹‹ عذراً عزيزي ‹ {uname} ›\n‹‹ ممنوع ارسال الرسائل الطويلة هنا .', reply)
        await delete(chat_id, msg_id)
        return True

    if settings['lock_english'] and re.search(r'[a-zA-Z]', text):
        await send(chat_id, f'‹‹ عذراً عزيزي ‹ {uname} ›\n‹‹ ممنوع الكتابة بالانجليزية هنا .', reply)
        await delete(chat_id, msg_id)
        return True

    if settings['lock_chinese'] and re.search(r'[\u4e00-\u9fff]', text):
        await send(chat_id, f'‹‹ عذراً عزيزي ‹ {uname} ›\n‹‹ ممنوع اللغة الصينية هنا .', reply)
        await delete(chat_id, msg_id)
        return True

    if settings['lock_russian'] and re.search(r'[\u0400-\u04FF]', text):
        await send(chat_id, f'‹‹ عذراً عزيزي ‹ {uname} ›\n‹‹ ممنوع اللغة الروسية هنا .', reply)
        await delete(chat_id, msg_id)
        return True

    if settings.get('lock_all_usernames') and re.search(r'@\w+', text):
        await send(chat_id, f'‹‹ عذراً عزيزي ‹ {uname} ›\n‹‹ ممنوع ارسال اليوزرات هنا .', reply)
        await delete(chat_id, msg_id)
        return True

    if settings.get('lock_channel_usernames'):
        mentions = re.findall(r'@(\w+)', text)
        for uname_found in mentions:
            try:
                ch = await api_call('getChat', {'chat_id': f'@{uname_found}'})
                if ch and ch.get('type', '') in ['channel', 'supergroup', 'group']:
                    await send(chat_id, f'‹‹ عذراً عزيزي ‹ {uname} ›\n‹‹ ممنوع ارسال يوزرات القنوات والمجموعات هنا .', reply)
                    await delete(chat_id, msg_id)
                    return True
            except:
                pass

    if settings['lock_repeat']:
        cid_key = str(chat_id)
        uid_key = str(user_id)
        if cid_key not in last_messages:
            last_messages[cid_key] = {}
        last_msg = last_messages[cid_key].get(uid_key, '')
        if text and text == last_msg:
            await send(chat_id, f'‹‹ عذراً عزيزي ‹ {uname} ›\n‹‹ ممنوع التكرار هنا .', reply)
            await delete(chat_id, msg_id)
            return True
        last_messages[cid_key][uid_key] = text

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
    is_member_only = rank_level(user_rank) < rank_level('ادمن') and not tg_admin

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
    ADMIN_CMD_PREFIXES = ['قفل ', 'فتح ', 'تعطيل ', 'تفعيل ',
        'رفع مالك اساسي', 'تنزيل مالك اساسي', 'رفع مالك أساسي', 'تنزيل مالك أساسي',
        'رفع مالك', 'تنزيل مالك',
        'رفع مدير', 'تنزيل مدير', 'رفع ادمن', 'تنزيل ادمن', 'رفع مميز', 'تنزيل مميز',
        'كتم ', 'تقييد ', 'طرد ', 'حظر ', 'رفع القيود ', 'الغاء الكتم ', 'الغاء التقييد ']
    ADMIN_CMD_EXACT = ['التنظيف', 'اضف رد', 'مسح رد', 'كتم', 'تقييد', 'رفع القيود',
        'الغاء الكتم', 'الغاء التقييد', 'طرد', 'حظر', 'مسح', 'قفل امر', 'اضف امر', 'الاوامر', 'اوامر']
    is_admin_cmd = text in ADMIN_CMD_EXACT or any(text.startswith(p) for p in ADMIN_CMD_PREFIXES)
    if is_member_only and is_admin_cmd:
        await send(chat_id, f'⛔ {m} رتبتك <b>عضو</b> وما تقدر تستخدم هذا الأمر', reply)
        return

    locked_cmds = settings.get('locked_commands', {})
    if text in locked_cmds:
        required_rank = locked_cmds[text]
        if rank_level(user_rank) < rank_level(required_rank) and not tg_admin:
            await send(chat_id, f'⛔ {m} رتبتك <b>{user_rank}</b> وهذا الأمر مخصص لرتبة <b>{required_rank}</b> فقط', reply)
            return

    # رتبتي / رتبته - مسموح للجميع بمن فيهم الأعضاء
    if not settings['disable_service']:
        if text in ['رتبة', 'رتبتي', 'رتب']:
            if msg.get('reply_to_message'):
                tf = msg['reply_to_message']['from']
                rank = get_rank(data, chat_id, tf['id'])
                mem2 = await get_chat_member(chat_id, tf['id'])
                if mem2 and mem2.get('status') == 'creator':
                    rank = 'مالك المجموعة'
                elif mem2 and mem2.get('status') == 'administrator' and rank == 'عضو':
                    rank = 'مشرف'
                await send(chat_id, f'🏅 رتبة {name(tf)}: <b>{rank}</b>', reply)
            else:
                await send(chat_id, f'🏅 رتبتك: <b>{get_rank(data, chat_id, user_id)}</b>', reply)
            return

        if text in ['رتبته', 'رتبتها']:
            if msg.get('reply_to_message'):
                tf = msg['reply_to_message']['from']
                rank = get_rank(data, chat_id, tf['id'])
                mem2 = await get_chat_member(chat_id, tf['id'])
                if mem2 and mem2.get('status') == 'creator':
                    rank = 'مالك المجموعة'
                elif mem2 and mem2.get('status') == 'administrator' and rank == 'عضو':
                    rank = 'مشرف'
                await send(chat_id, f'🏅 رتبة {name(tf)}: <b>{rank}</b>', reply)
            else:
                await send(chat_id, '⚠️ رد على رسالة شخص لمعرفة رتبته', reply)
            return

    # التنظيف - للمالك فقط
    if text == 'التنظيف':
        if not await is_group_creator(chat_id, user_id):
            await send(chat_id, '⛔ هذا الأمر للمالك فقط', reply)
            return
        clean_text, clean_keyboard = build_clean_menu(settings)
        await send(chat_id, clean_text, {'reply_markup': clean_keyboard})
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
    # أوامر الرتب (تُفحص قبل التسلية)
    # ===========================
    rank_cmds = {
        'رفع مالك اساسي': 'مالك اساسي', 'تنزيل مالك اساسي': 'عضو',
        'رفع مالك أساسي': 'مالك اساسي', 'تنزيل مالك أساسي': 'عضو',
        'رفع مالك': 'مالك', 'تنزيل مالك': 'عضو',
        'رفع مدير': 'مدير', 'تنزيل مدير': 'عضو',
        'رفع ادمن': 'ادمن', 'تنزيل ادمن': 'عضو',
        'رفع مميز': 'مميز', 'تنزيل مميز': 'عضو'
    }
    matched_rank_cmd = None
    after_rank_cmd = ''
    for cmd_key in rank_cmds:
        if text == cmd_key or text.startswith(cmd_key + ' '):
            matched_rank_cmd = cmd_key
            after_rank_cmd = text[len(cmd_key):].strip()
            break
    if matched_rank_cmd:
        target_rank = rank_cmds[matched_rank_cmd]
        is_up = matched_rank_cmd.startswith('رفع')
        tf, err = await resolve_target(msg, after_rank_cmd)
        if err or not tf:
            await send(chat_id, err or '⚠️ رد على رسالة الشخص أو اكتب @يوزره', reply)
            return
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
        fun_rank_starters = ['مالك', 'ادمن', 'أدمن', 'مدير', 'مميز', 'القيود', 'كتم', 'تقييد']
        if fun_match and msg.get('reply_to_message'):
            fun_label = fun_match.group(1).strip()
            is_rank_cmd = any(fun_label == kw or fun_label.startswith(kw + ' ') for kw in fun_rank_starters)
            if not is_rank_cmd:
                await send(chat_id, f'✅ تم رفع {mention(msg["reply_to_message"]["from"])} {fun_label} للتسلية 😜', reply)
                return

    # ===========================
    # قائمة الأوامر
    # ===========================
    if text in ['الاوامر', 'اوامر'] and await is_admin_up(data, chat_id, user_id):
        await send(chat_id, '🤖 <b>قائمة الأوامر</b>\n\n- أوامر ① الخدمية\n- أوامر ② التسليه\n- أوامر ③ القفل والفتح\n- أوامر ④ الإعدادات\n- أوامر ⑤ الألعاب', {
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


    # ===========================
    # أوامر القفل والإعدادات
    # ===========================
    if await is_admin_up(data, chat_id, user_id):
        lock_map = {
            'قفل السب': 'lock_swear', 'فتح السب': 'lock_swear',
            'قفل التكرار': 'lock_repeat', 'فتح التكرار': 'lock_repeat',
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
            'قفل يوزرات القنوات': 'lock_channel_usernames', 'فتح يوزرات القنوات': 'lock_channel_usernames',
            'قفل كل اليوزرات': 'lock_all_usernames', 'فتح كل اليوزرات': 'lock_all_usernames',
            'قفل الردود الخارجية': 'lock_external_reply', 'فتح الردود الخارجية': 'lock_external_reply',
            'قفل الردود الخارجيه': 'lock_external_reply', 'فتح الردود الخارجيه': 'lock_external_reply',
            'قفل الاقتباس': 'lock_quote', 'فتح الاقتباس': 'lock_quote',
            'قفل الاقتباسات': 'lock_quote', 'فتح الاقتباسات': 'lock_quote',
        }
        if text in lock_map:
            is_lock = text.startswith('قفل')
            data['group_settings'][cid][lock_map[text]] = is_lock
            await send(chat_id, f'{"🔒 تم القفل" if is_lock else "🔓 تم الفتح"}: <b>{text}</b>', reply)
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
            'تعطيل الالعاب': ['disable_games', True], 'تفعيل الالعاب': ['disable_games', False],
            'تعطيل اليوتيوب': ['youtube_enabled', False], 'تفعيل اليوتيوب': ['youtube_enabled', True],
            'تعطيل اليوتويب': ['youtube_enabled', False], 'تفعيل اليوتويب': ['youtube_enabled', True],
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
        # كتم - يقبل رد أو @يوزر
        mute_match = re.match(r'^كتم(?:\s+(.+))?$', text)
        if mute_match:
            after = mute_match.group(1) or ''
            tf, err = await resolve_target(msg, after)
            if err or not tf:
                await send(chat_id, err or '⚠️ رد على رسالة الشخص أو اكتب كتم @يوزره', reply)
                return
            await restrict(chat_id, tf['id'], {'can_send_messages': False})
            await send(chat_id, f'🔇 تم كتم {mention(tf)}\nبواسطة {m}', reply)
            return

        # تقييد - يقبل رد أو @يوزر
        restrict_match = re.match(r'^تقييد(?:\s+(.+))?$', text)
        if restrict_match:
            after = restrict_match.group(1) or ''
            tf, err = await resolve_target(msg, after)
            if err or not tf:
                await send(chat_id, err or '⚠️ رد على رسالة الشخص أو اكتب تقييد @يوزره', reply)
                return
            await restrict(chat_id, tf['id'], {
                'can_send_messages': False, 'can_send_media_messages': False,
                'can_send_polls': False, 'can_send_other_messages': False, 'can_add_web_page_previews': False
            })
            await send(chat_id, f'🚫 تم تقييد {mention(tf)}\nبواسطة {m}', reply)
            return

        # رفع القيود - يقبل رد أو @يوزر
        lift_match = re.match(r'^(رفع القيود|الغاء الكتم|الغاء التقييد)(?:\s+(.+))?$', text)
        if lift_match:
            after = lift_match.group(2) or ''
            tf, err = await resolve_target(msg, after)
            if err or not tf:
                await send(chat_id, err or '⚠️ رد على رسالة الشخص أو اكتب رفع القيود @يوزره', reply)
                return
            await restrict(chat_id, tf['id'], {
                'can_send_messages': True, 'can_send_media_messages': True,
                'can_send_polls': True, 'can_send_other_messages': True, 'can_add_web_page_previews': True
            })
            await send(chat_id, f'✅ تم رفع القيود عن {mention(tf)}\nبواسطة {m}', reply)
            return

        # طرد - يقبل رد أو @يوزر
        kick_match = re.match(r'^طرد(?:\s+(.+))?$', text)
        if kick_match:
            after = kick_match.group(1) or ''
            tf, err = await resolve_target(msg, after)
            if err or not tf:
                await send(chat_id, err or '⚠️ رد على رسالة الشخص أو اكتب طرد @يوزره', reply)
                return
            await ban(chat_id, tf['id'])
            await unban(chat_id, tf['id'])
            await send(chat_id, f'👢 تم طرد {mention(tf)}\nبواسطة {m}', reply)
            return

        # حظر (بان) - يقبل رد أو @يوزر
        ban_match = re.match(r'^حظر(?:\s+(.+))?$', text)
        if ban_match:
            after = ban_match.group(1) or ''
            tf, err = await resolve_target(msg, after)
            if err or not tf:
                await send(chat_id, err or '⚠️ رد على رسالة الشخص أو اكتب حظر @يوزره', reply)
                return
            await ban(chat_id, tf['id'])
            await send(chat_id, f'🚷 تم حظر {mention(tf)}\nبواسطة {m}', reply)
            return

        if text == 'مسح':
            if not msg.get('reply_to_message'):
                await send(chat_id, '⚠️ رد على الرسالة المراد مسحها', reply)
                return
            await delete(chat_id, msg['reply_to_message']['message_id'])
            await delete(chat_id, msg_id)
            await send(chat_id, '🗑️ تم مسح الرسالة')
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
            f'فلوسك : {acc["balance"]:,} دينار\n'
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
        await send(chat_id, f'💰 فلوسك ←  {acc["balance"]:,} دينار', reply)
        return

    if text == 'فلوسه':
        if msg.get('reply_to_message'):
            tf = msg['reply_to_message']['from']
            tf_acc = get_bank(data, chat_id, tf['id'])
            if not tf_acc:
                await send(chat_id, f'😕 {mention(tf)} ما عنده حساب بنكي', reply)
            else:
                await send(chat_id, f'💰 فلوسه ←  {tf_acc["balance"]:,} دينار', reply)
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
            f'💵 الراتب: <b>{salary:,}</b> دينار\n'
            f'💳 رصيدك الجديد: <b>{acc["balance"]:,}</b> دينار',
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
            f'💸 المبلغ المسروق: <b>{stolen:,}</b> دينار\n'
            f'💳 رصيدك الجديد: <b>{stealer_acc["balance"]:,}</b> دينار',
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
            await send(chat_id, f'💸 فلوسك ما تكفي يا فكر\n💳 رصيدك: <b>{acc.get("balance", 0):,}</b> دينار', reply)
            return
        roll = random.random()
        if roll < 0.40:
            acc['balance'] -= bet
            save_data(data)
            await send(chat_id,
                f'😢 {m} خسرت!\n\n'
                f'💸 خسرت: <b>{bet:,}</b> دينار\n'
                f'💳 رصيدك: <b>{acc["balance"]:,}</b> دينار',
                reply
            )
        elif roll < 0.95:
            gain_pct = random.randint(1, 60) / 100
            gain = int(bet * gain_pct)
            acc['balance'] += gain
            save_data(data)
            await send(chat_id,
                f'🎉 {m} ربحت!\n\n'
                f'💰 ربحت: <b>{gain:,}</b> دينار ({int(gain_pct*100)}%)\n'
                f'💳 رصيدك: <b>{acc["balance"]:,}</b> دينار',
                reply
            )
        else:
            doubled = bet * 2
            acc['balance'] += doubled
            save_data(data)
            await send(chat_id,
                f'🤑 {m} حظك جبار! المبلغ تضاعف 2x!\n\n'
                f'💰 ربحت: <b>{doubled:,}</b> دينار\n'
                f'💳 رصيدك: <b>{acc["balance"]:,}</b> دينار',
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
            items_list = '، '.join(set(ITEM_PRICES.keys()) - set(ITEM_SINGULAR.values()))
            await send(chat_id,
                f'⚠️ اسم الشيء غير صحيح\n\n'
                f'الأشياء المتاحة:\n'
                f'سيارة | طيارة | قصر | بيت | جندي | فيل | برج | دبابة',
                reply
            )
            return
        price = ITEM_PRICES[item_key] * qty
        if acc.get('balance', 0) < price:
            await send(chat_id,
                f'💸 فلوسك ما تكفي يا فكر\n'
                f'💳 رصيدك: <b>{acc.get("balance", 0):,}</b> دينار\n'
                f'💰 السعر: <b>{price:,}</b> دينار',
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
            f'💸 دفعت: <b>{price:,}</b> دينار\n'
            f'💳 رصيدك الجديد: <b>{acc["balance"]:,}</b> دينار',
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
            await send(chat_id, f'💸 فلوسك ما تكفي يا فكر\n💳 رصيدك: <b>{acc.get("balance", 0):,}</b> دينار', reply)
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
            f'💸 المبلغ المحول: <b>{amount:,}</b> دينار\n'
            f'📤 إلى حساب: <code>{target_account_number}</code>\n'
            f'💳 رصيدك الجديد: <b>{acc["balance"]:,}</b> دينار',
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
            f'\n\n💳 الرصيد: <b>{acc.get("balance", 0):,}</b> دينار',
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

    elif user_state['step'] == 'await_lock_cmd_name':
        if not text:
            await send(chat_id, '⚠️ أرسل اسم الأمر:', reply)
            return
        state[cid][uid] = {'step': 'await_lock_cmd_rank', 'cmd_name': text}
        rank_buttons = [
            [{'text': '1 - مالك أساسي', 'callback_data': f'lock_cmd_rank:{text}:مالك أساسي'}],
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

# ===========================
# HTTP SERVER
# ===========================

PORT = int(os.environ.get('PORT', 3000))

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
    return web.Response(text='Romeo Bot is running 🌹', status=200)

async def main():
    init_db()
    app = web.Application()
    app.router.add_route('*', '/{tail:.*}', webhook_handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', PORT)
    await site.start()
    print(f'Romeo Bot running on port {PORT}')
    asyncio.create_task(auto_clean_loop())
    await asyncio.Event().wait()

if __name__ == '__main__':
    asyncio.run(main())

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

    elif user_state['step'] == 'await_lock_cmd_name':
        if not text:
            await send(chat_id, '⚠️ أرسل اسم الأمر:', reply)
            return
        state[cid][uid] = {'step': 'await_lock_cmd_rank', 'cmd_name': text}
        rank_buttons = [
            [{'text': '1 - مالك أساسي', 'callback_data': f'lock_cmd_rank:{text}:مالك أساسي'}],
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

# ===========================
# HTTP SERVER
# ===========================

PORT = int(os.environ.get('PORT', 3000))

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
    return web.Response(text='Romeo Bot is running 🌹', status=200)

async def main():
    init_db()
    app = web.Application()
    app.router.add_route('*', '/{tail:.*}', webhook_handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', PORT)
    await site.start()
    print(f'Romeo Bot running on port {PORT}')
    asyncio.create_task(auto_clean_loop())
    await asyncio.Event().wait()

if __name__ == '__main__':
    asyncio.run(main())
