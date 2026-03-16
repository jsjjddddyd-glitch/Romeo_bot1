# -*- coding: utf-8 -*-
import json
import os
import re
import asyncio
import aiohttp
from aiohttp import web
from datetime import datetime

TOKEN = '8785959754:AAFWbDWNkBeT42CzqNn_m1g7eqGFp6XdBps'
API = f'https://api.telegram.org/bot{TOKEN}'
DATA_FILE = './data.json'
STATE_FILE = './state.json'

SIGHTENGINE_API_USER = '130043340'
SIGHTENGINE_API_SECRET = 'RFozDT5M3VYmccC2rcArKqnMPWPCKJfE'

# In-memory clean queue per chat
# {chat_id: {'photos': [msg_id,...], 'videos': [...], 'stickers': [...], 'numbers': [...], 'clutter': [...], 'edited': [...]}}
clean_queue = {}
last_clean_time = {}

# ===========================
# DATA HELPERS
# ===========================

def load_data():
    try:
        with open(DATA_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except:
        return {'custom_replies': {}, 'group_settings': {}, 'user_ranks': {}, 'user_warnings': {}}

def save_data(d):
    with open(DATA_FILE, 'w', encoding='utf-8') as f:
        json.dump(d, f, ensure_ascii=False, indent=2)

def load_state():
    try:
        with open(STATE_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except:
        return {}

def save_state(s):
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
        'disable_id': False, 'disable_service': False, 'disable_fun': False,
        'disable_welcome': False, 'disable_link': False, 'disable_auto_replies': False,
        'lock_nsfw': False,
        'lock_nsfw_restrict': False,
        'lock_nsfw_warn': False,
        'lock_files': False,
        'clean_auto': False,
        'clean_interval': 1,
        'clean_numbers': False,
        'clean_clutter': False,
        'clean_edited': False,
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

RANKS = {'عضو': 0, 'مميز': 1, 'ادمن': 2, 'أدمن': 2, 'مدير': 3, 'مالك': 4, 'مالك أساسي': 5}

def rank_level(r):
    return RANKS.get(r, 0)

# ===========================
# TELEGRAM API
# ===========================

async def api_call(method, params):
    try:
        async with aiohttp.ClientSession() as session:
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
    return rank_level(get_rank(data, chat_id, user_id)) >= rank_level('مالك أساسي')

async def is_group_creator(chat_id, user_id):
    m = await get_chat_member(chat_id, user_id)
    return m and m.get('status') == 'creator'

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
        async with aiohttp.ClientSession() as session:
            params = {
                'url': file_url,
                'models': 'nudity-2.1,weapon,recreational_drug,gore-2.0,text-content,face-attributes',
                'api_user': SIGHTENGINE_API_USER,
                'api_secret': SIGHTENGINE_API_SECRET
            }
            async with session.get('https://api.sightengine.com/1.0/check.json', params=params) as res:
                if res.status != 200:
                    return False, None
                result = await res.json()
                if result.get('status') != 'success':
                    return False, None
                nudity = result.get('nudity', {})
                if nudity.get('sexual_activity', 0) > 0.5 or nudity.get('sexual_display', 0) > 0.5 or nudity.get('erotica', 0) > 0.6:
                    return True, 'إباحي'
                weapon = result.get('weapon', {})
                if weapon.get('classes', {}).get('firearm', 0) > 0.7 or weapon.get('classes', {}).get('knife', 0) > 0.8:
                    return True, 'أسلحة'
                drug = result.get('recreational_drug', {})
                if drug.get('prob', 0) > 0.7:
                    return True, 'مواد ممنوعة'
                face = result.get('faces', [])
                for f in face:
                    age_info = f.get('attributes', {}).get('age', {})
                    avg_age = (age_info.get('min', 20) + age_info.get('max', 20)) / 2
                    nudity_raw = result.get('nudity', {})
                    if avg_age < 18 and (nudity_raw.get('suggestive', 0) > 0.5 or nudity_raw.get('sexual_display', 0) > 0.3):
                        return True, 'محتوى يخص قاصرين'
                classes_text = result.get('text', {}).get('classes', {})
                if classes_text.get('gov_id', 0) > 0.6 or classes_text.get('personal', 0) > 0.7:
                    return True, 'وثائق رسمية/هوية'
                return False, None
    except Exception as e:
        print(f'NSFW check error: {e}')
        return False, None

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
    try:
        parts = date_str.split('-')
        month_name = ARABIC_MONTHS.get(int(parts[1]), parts[1])
        return f'📅 تقريباً: {month_name} {parts[0]}'
    except:
        return f'📅 {date_str}'

# ===========================
# CLEAN MENU BUILDER
# ===========================

def build_clean_menu(settings):
    auto = '✓' if settings.get('clean_auto') else '✗'
    numbers_mark = ' ✓' if settings.get('clean_numbers') else ''
    clutter_mark = ' ✓' if settings.get('clean_clutter') else ''
    edited_mark = ' ✓' if settings.get('clean_edited') else ''
    interval = settings.get('clean_interval', 1)

    text = (
        '🧹 <b>قائمة التنظيف</b>\n\n'
        'يمكنك تفعيل خيارات التنظيف التلقائي من الأزرار أدناه.\n'
        'التنظيف يشمل دائماً: الصور، الفيديوهات، الملصقات.\n\n'
        f'• التنظيف التلقائي: {"🟢 مفعّل" if settings.get("clean_auto") else "🔴 معطّل"}\n'
        f'• وقت التنظيف: {interval} {"دقيقة" if interval == 1 else "دقائق"}\n'
        f'• الأرقام: {"✓ مفعّل" if settings.get("clean_numbers") else "معطّل"}\n'
        f'• الكلايش (رسائل طويلة): {"✓ مفعّل" if settings.get("clean_clutter") else "معطّل"}\n'
        f'• الرسائل المعدلة: {"✓ مفعّل" if settings.get("clean_edited") else "معطّل"}'
    )

    keyboard = {
        'inline_keyboard': [
            [{'text': f'• التنظيف التلقائي ({auto})', 'callback_data': 'clean_toggle_auto'}],
            [
                {'text': f'الأرقام{numbers_mark}', 'callback_data': 'clean_toggle_numbers'},
                {'text': f'الكلايش{clutter_mark}', 'callback_data': 'clean_toggle_clutter'}
            ],
            [{'text': f'• الرسائل المعدلة ({("✓" if settings.get("clean_edited") else "")})', 'callback_data': 'clean_toggle_edited'}],
            [{'text': f'• وقت التنظيف {interval} د', 'callback_data': 'clean_set_time'}],
            [{'text': '🔙 رجوع', 'callback_data': 'clean_back'}]
        ]
    }
    return text, keyboard

def add_to_clean_queue(chat_id, msg_id, msg_type):
    cid = str(chat_id)
    if cid not in clean_queue:
        clean_queue[cid] = {'photos': [], 'videos': [], 'stickers': [], 'numbers': [], 'clutter': [], 'edited': []}
    if msg_type in clean_queue[cid]:
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
        'edited': 'الرسائل المعدلة'
    }

    for msg_type, ids in queue.items():
        deleted = 0
        for mid in ids:
            result = await delete(chat_id, mid)
            if result is not False:
                deleted += 1
        if deleted > 0:
            counts[type_labels.get(msg_type, msg_type)] = deleted

    clean_queue[cid] = {'photos': [], 'videos': [], 'stickers': [], 'numbers': [], 'clutter': [], 'edited': []}

    if counts:
        lines = '\n'.join(f'  • {k} ← {v}' for k, v in counts.items())
        msg = (
            '🧹 <b>تم تنظيف المجموعة بالتنظيف التلقائي بنجاح</b>\n\n'
            f'<b>"  الرسائل المحذوفة:</b>\n{lines}\n\n'
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
    'menu_service': '🔧 <b>أوامر الخدمية:</b>\n\n• <b>بايو</b> - يرسل بايو كاتب الكلمة\n• <b>اسمي</b> - يرسل اسمك\n• <b>اسمه</b> (رد) - اسم الشخص\n• <b>يوزري</b> - يرسل يوزرك\n• <b>يوزره</b> (رد) - يوزر الشخص\n• <b>المالك</b> - يذكر مالك المجموعة\n• <b>ايدي</b> - معرفك\n• <b>الرابط</b> - رابط المجموعة\n• <b>رتبة / رتب</b> - رتبتك\n• <b>رتبته</b> (رد) - رتبة شخص\n• <b>انشاء</b> - تاريخ إنشاء الحساب',
    'menu_fun': '🎉 <b>أوامر التسليه:</b>\n\n• <b>رفع [كلمة]</b> (رد) - يرفع لقب للشخص\n\n🔴 تعطيل التسليه | تفعيل التسليه',
    'menu_locks': '🔒 <b>أوامر القفل والفتح:</b>\n\nقفل السب | قفل التكرار | قفل الروابط\nقفل التوجيه | قفل الكلايش | قفل الانجليزية\nقفل الصينية | قفل الروسية | قفل الصور\nقفل الفيديوهات | قفل تعديل الميديا\nقفل الصوتيات | قفل الاغاني | قفل التحويل\nقفل الدخول | قفل التاك | قفل الارقام\nقفل الملصقات | قفل المتحركة | قفل الشات\nقفل الملفات\nقفل المحتوى المخل | فتح المحتوى المخل\nقفل المحتوى المخل بالتقييد\nقفل المحتوى المخل بالتحذير',
    'menu_settings': '⚙️ <b>أوامر الإعدادات (رد على رسالة شخص):</b>\n\n• رفع مالك أساسي / تنزيل مالك أساسي\n• رفع مالك / تنزيل مالك\n• رفع مدير / تنزيل مدير\n• رفع ادمن / تنزيل ادمن\n• رفع مميز / تنزيل مميز\n\n🔴 كتم | تقييد | طرد | رفع القيود | مسح'
}

async def handle_callback(cb):
    chat_id = cb['message']['chat']['id']
    msg_id = cb['message']['message_id']
    user_id = cb['from']['id']
    data_cb = cb['data']
    await answer_cb(cb['id'])

    if data_cb in menu_texts:
        await edit_msg(chat_id, msg_id, menu_texts[data_cb])
        return

    # Clean menu callbacks
    if data_cb in ('clean_toggle_auto', 'clean_toggle_numbers', 'clean_toggle_clutter',
                   'clean_toggle_edited', 'clean_set_time', 'clean_back',
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

# ===========================
# MESSAGE HANDLER
# ===========================

async def handle_update(update):
    if 'callback_query' in update:
        await handle_callback(update['callback_query'])
        return

    # Handle edited messages
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

    if 'new_chat_members' in msg:
        bot_info = await api_call('getMe', {})
        bot_id = (bot_info or {}).get('id')
        for m in msg['new_chat_members']:
            if bot_id and m['id'] == bot_id:
                await send(chat_id, '✅ تم تفعيل المجموعة بنجاح 🌹\nأنا بوت روميو جاهز لخدمة المجموعة!')
                return

    if chat_type == 'private':
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
        await delete(chat_id, msg_id)
        m = mention(from_)
        await send(chat_id, f'⚠️ {m}\nممنوع تعديل الرسائل هنا')

# ===========================
# BOT ADDED AS ADMIN HANDLER
# ===========================

async def handle_my_chat_member(update):
    new_status = update.get('new_chat_member', {}).get('status')
    chat = update.get('chat', {})
    chat_type = chat.get('type', '')
    chat_id = chat.get('id')
    if new_status == 'administrator' and chat_type in ['group', 'supergroup']:
        await send(chat_id, '✅ تم تفعيل المجموعة بنجاح 🌹\nأنا بوت روميو جاهز لإدارة المجموعة!')

# ===========================
# START COMMAND - PRIVATE ONLY
# ===========================

async def handle_start(msg):
    chat_id = msg['chat']['id']
    from_ = msg['from']
    user_name = name(from_)
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

    if await is_tg_admin(chat_id, user_id):
        return

    is_forward = msg.get('forward_from') or msg.get('forward_from_chat') or msg.get('forward_sender_name')
    if is_forward and settings['lock_forward']:
        await delete(chat_id, msg_id)
        await send(chat_id, f'⚠️ {m}\nممنوع التوجيه والتحويل هنا')
        return

    # Files/Documents
    if msg.get('document'):
        if settings.get('lock_files'):
            await delete(chat_id, msg_id)
            await send(chat_id, f'⚠️ عذراً {m}\nممنوع ارسال الملفات هنا')
            return

    if msg.get('photo'):
        photo_list = msg['photo']
        file_id = photo_list[-1]['file_id']

        # NSFW with restriction
        if settings.get('lock_nsfw_restrict'):
            is_violation, violation_type = await check_image_nsfw(file_id)
            if is_violation:
                await delete(chat_id, msg_id)
                await restrict(chat_id, user_id, {
                    'can_send_messages': False, 'can_send_media_messages': False,
                    'can_send_polls': False, 'can_send_other_messages': False,
                    'can_add_web_page_previews': False
                })
                await send(chat_id, (
                    f'🚫 <b>تم حذف صورة مخالفة وتقييد العضو</b>\n\n'
                    f'👤 المرسل: {m}\n'
                    f'⚠️ نوع المخالفة: <b>{violation_type}</b>\n\n'
                    f'يُمنع إرسال هذا النوع من المحتوى في هذه المجموعة ❌'
                ))
                return

        # NSFW with warning (5 warnings = restrict)
        if settings.get('lock_nsfw_warn'):
            is_violation, violation_type = await check_image_nsfw(file_id)
            if is_violation:
                await delete(chat_id, msg_id)
                warns = add_warning(data, chat_id, user_id)
                if warns >= 5:
                    reset_warnings(data, chat_id, user_id)
                    await restrict(chat_id, user_id, {
                        'can_send_messages': False, 'can_send_media_messages': False,
                        'can_send_polls': False, 'can_send_other_messages': False,
                        'can_add_web_page_previews': False
                    })
                    await send(chat_id, (
                        f'🚫 <b>تم تقييد {m}</b>\n\n'
                        f'وصل عدد التحذيرات إلى 5 بسبب إرسال محتوى مخالف\n'
                        f'⚠️ نوع المخالفة: <b>{violation_type}</b>'
                    ))
                else:
                    await send(chat_id, (
                        f'⚠️ <b>تحذير {warns}/5</b> لـ {m}\n\n'
                        f'نوع المخالفة: <b>{violation_type}</b>\n'
                        f'عند الوصول لـ 5 تحذيرات سيتم تقييدك ❌'
                    ))
                return

        # Basic NSFW lock
        if settings.get('lock_nsfw', False):
            is_violation, violation_type = await check_image_nsfw(file_id)
            if is_violation:
                await delete(chat_id, msg_id)
                await send(chat_id, (
                    f'🚫 <b>تم حذف صورة مخالفة</b>\n\n'
                    f'👤 المرسل: {m}\n'
                    f'⚠️ نوع المخالفة: <b>{violation_type}</b>\n\n'
                    f'يُمنع إرسال هذا النوع من المحتوى في هذه المجموعة ❌'
                ))
                return

        if settings['lock_photos']:
            await delete(chat_id, msg_id)
            await send(chat_id, f'⚠️ عذرا عزيزي {m}\nممنوع ارسال الصور هنا')
            return

        # Queue for auto-clean
        if settings.get('clean_auto'):
            add_to_clean_queue(chat_id, msg_id, 'photos')
        return

    if msg.get('video'):
        if settings['lock_videos']:
            await delete(chat_id, msg_id)
            await send(chat_id, f'⚠️ عذرا عزيزي {m}\nممنوع ارسال الفيديوهات هنا')
            return
        if settings.get('clean_auto'):
            add_to_clean_queue(chat_id, msg_id, 'videos')
        return

    if msg.get('voice') and settings['lock_audio']:
        await delete(chat_id, msg_id)
        await send(chat_id, f'⚠️ {m} ممنوع ارسال الرسائل الصوتية هنا')
        return

    if msg.get('audio') and settings['lock_music']:
        await delete(chat_id, msg_id)
        await send(chat_id, f'⚠️ {m} ممنوع ارسال الاغاني هنا')
        return

    if msg.get('sticker'):
        if msg['sticker'].get('is_animated') and settings['lock_animated']:
            await delete(chat_id, msg_id)
            await send(chat_id, f'⚠️ {m} ممنوع الملصقات المتحركة هنا')
            return
        if not msg['sticker'].get('is_animated') and settings['lock_stickers']:
            await delete(chat_id, msg_id)
            await send(chat_id, f'⚠️ {m} ممنوع الملصقات هنا')
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

    if await is_tg_admin(chat_id, user_id):
        return False

    is_forward = msg.get('forward_from') or msg.get('forward_from_chat') or msg.get('forward_sender_name')
    if is_forward and settings['lock_forward']:
        await delete(chat_id, msg_id)
        await send(chat_id, f'⚠️ {m}\nممنوع التوجيه والتحويل هنا')
        return True

    swears = ['انيجك', 'انيج امك', 'كسمك', 'عير بابوك', 'عير بامك', 'قحبه', 'كحبه', 'شرموط', 'شرموطه', 'زبفيك', 'عيرك', 'كسي', 'زبي', 'عيري']
    if settings['lock_swear'] and any(w in text for w in swears):
        await delete(chat_id, msg_id)
        await send(chat_id, f'⚠️ {m}\nالسب ممنوع هنا ❌')
        return True

    if settings['lock_links'] and re.search(r'(https?://|t\.me/|www\.)', text, re.IGNORECASE):
        await delete(chat_id, msg_id)
        await send(chat_id, f'⚠️ {m}\nممنوع ارسال الروابط هنا')
        return True

    if settings['lock_mention'] and re.search(r'@\w+', text):
        await delete(chat_id, msg_id)
        await send(chat_id, f'⚠️ {m}\nممنوع التاك هنا')
        return True

    # Phone number detection: 9-12 digits
    phone_pattern = r'(?<!\d)(\+?\d[\d\s\-]{8,11}\d)(?!\d)'
    if settings['lock_numbers'] and re.search(r'(?<!\d)\+?\d{9,12}(?!\d)', text):
        await delete(chat_id, msg_id)
        await send(chat_id, f'⚠️ {m}\nممنوع ارسال الارقام هنا')
        return True

    if settings['lock_clutter'] and len(text) > 1000:
        await delete(chat_id, msg_id)
        await send(chat_id, f'⚠️ {m}\nممنوع ارسال الرسائل الطويلة هنا')
        return True

    if settings['lock_english'] and re.search(r'[a-zA-Z]', text):
        await delete(chat_id, msg_id)
        await send(chat_id, f'⚠️ {m}\nممنوع الكتابة بالانجليزية هنا')
        return True

    if settings['lock_chinese'] and re.search(r'[\u4e00-\u9fff]', text):
        await delete(chat_id, msg_id)
        await send(chat_id, f'⚠️ {m}\nممنوع اللغة الصينية هنا')
        return True

    if settings['lock_russian'] and re.search(r'[\u0400-\u04FF]', text):
        await delete(chat_id, msg_id)
        await send(chat_id, f'⚠️ {m}\nممنوع اللغة الروسية هنا')
        return True

    # Queue numbers and clutter for auto-clean if enabled
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
    import random
    chat_id = msg['chat']['id']
    msg_id = msg['message_id']
    from_ = msg['from']
    user_id = from_['id']
    m = mention(from_)
    cid = str(chat_id)
    reply = {'reply_to_message_id': msg_id}

    # ردود تلقائية على عبارات معينة
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

    # التنظيف - للمالك فقط
    if text == 'التنظيف':
        if not await is_group_creator(chat_id, user_id):
            await send(chat_id, '⛔ هذا الأمر للمالك فقط', reply)
            return
        clean_text, clean_keyboard = build_clean_menu(settings)
        await send(chat_id, clean_text, {'reply_markup': clean_keyboard})
        return

    if not settings['disable_service']:
        if text == 'بايو':
            mem = await get_chat_member(chat_id, user_id)
            bio = (mem or {}).get('user', {}).get('bio')
            await send(chat_id, f'📋 بايو {m}:\n{bio}' if bio else f'😕 {m} ما عندك بايو', reply)
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
                    await send(chat_id, f'👑 مالك المجموعة: {mention(of)}\n📋 البايو: {of.get("bio", "لا يوجد بايو")}')
                    return
            await send(chat_id, '⚠️ لم أجد مالك المجموعة', reply)
            return
        if text in ['رتبة', 'رتبتي']:
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
        if text == 'انشاء':
            if msg.get('reply_to_message'):
                tf = msg['reply_to_message']['from']
                creation = get_account_creation_text(tf)
                await send(chat_id, f'🗓️ تاريخ إنشاء حساب {mention(tf)}:\n{creation}', reply)
            else:
                creation = get_account_creation_text(from_)
                await send(chat_id, f'🗓️ تاريخ إنشاء حسابك:\n{creation}', reply)
            return

    if not settings['disable_fun'] and await is_admin_up(data, chat_id, user_id):
        fun_match = re.match(r'^رفع\s+(.+)$', text)
        if fun_match and msg.get('reply_to_message'):
            await send(chat_id, f'✅ تم رفع {mention(msg["reply_to_message"]["from"])} {fun_match.group(1).strip()} للتسلية 😜', reply)
            return

    if text in ['الاوامر', 'اوامر'] and await is_admin_up(data, chat_id, user_id):
        await send(chat_id, '🤖 <b>قائمة الأوامر</b>\n\n- أوامر ① الخدمية\n- أوامر ② التسليه\n- أوامر ③ القفل والفتح\n- أوامر ④ الإعدادات', {
            'reply_markup': {'inline_keyboard': [[
                {'text': '① خدمية', 'callback_data': 'menu_service'},
                {'text': '② تسليه', 'callback_data': 'menu_fun'},
                {'text': '③ قفل/فتح', 'callback_data': 'menu_locks'},
                {'text': '④ إعدادات', 'callback_data': 'menu_settings'}
            ]]}
        })
        return

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

    replies = data['custom_replies'].get(cid, {})
    if text in replies:
        rd = replies[text]
        if rd['type'] == 'text':
            await send(chat_id, rd['content'], reply)
        elif rd['type'] == 'photo':
            await api_call('sendPhoto', {'chat_id': chat_id, 'photo': rd['file_id'], 'caption': rd.get('caption', ''), 'parse_mode': 'HTML'})
        elif rd['type'] == 'video':
            await api_call('sendVideo', {'chat_id': chat_id, 'video': rd['file_id'], 'caption': rd.get('caption', ''), 'parse_mode': 'HTML'})
        return

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
            'قفل الملفات': 'lock_files', 'فتح الملفات': 'lock_files',
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
            'تعطيل الردود التلقائيه': ['disable_auto_replies', True], 'تفعيل الردود التلقائيه': ['disable_auto_replies', False]
        }
        if text in dis_map:
            key, val = dis_map[text]
            data['group_settings'][cid][key] = val
            await send(chat_id, f'{"🔴 تم التعطيل" if val else "🟢 تم التفعيل"}: <b>{text}</b>', reply)
            return

    rank_cmds = {
        'رفع مالك أساسي': 'مالك أساسي', 'تنزيل مالك أساسي': 'عضو',
        'رفع مالك': 'مالك', 'تنزيل مالك': 'عضو',
        'رفع مدير': 'مدير', 'تنزيل مدير': 'عضو',
        'رفع ادمن': 'ادمن', 'تنزيل ادمن': 'عضو',
        'رفع مميز': 'مميز', 'تنزيل مميز': 'عضو'
    }
    if text in rank_cmds:
        if not msg.get('reply_to_message'):
            await send(chat_id, '⚠️ رد على رسالة الشخص', reply)
            return
        if not (await is_owner_up(data, chat_id, user_id)):
            await send(chat_id, '⛔ ليس لديك صلاحية', reply)
            return
        tf = msg['reply_to_message']['from']
        set_rank(data, chat_id, tf['id'], rank_cmds[text])
        is_up = text.startswith('رفع')
        if is_up:
            await send(chat_id, f'✅ تم رفع {mention(tf)} إلى رتبة <b>{rank_cmds[text]}</b>\nبواسطة {m}')
        else:
            await send(chat_id, f'✅ تم تنزيل {mention(tf)}\nبواسطة {m}')
        return

    if await is_admin_up(data, chat_id, user_id):
        if text == 'كتم':
            if not msg.get('reply_to_message'):
                await send(chat_id, '⚠️ رد على رسالة الشخص', reply)
                return
            tf = msg['reply_to_message']['from']
            await restrict(chat_id, tf['id'], {'can_send_messages': False})
            await send(chat_id, f'🔇 تم كتم {mention(tf)}\nبواسطة {m}')
            return
        if text == 'تقييد':
            if not msg.get('reply_to_message'):
                await send(chat_id, '⚠️ رد على رسالة الشخص', reply)
                return
            tf = msg['reply_to_message']['from']
            await restrict(chat_id, tf['id'], {
                'can_send_messages': False, 'can_send_media_messages': False,
                'can_send_polls': False, 'can_send_other_messages': False, 'can_add_web_page_previews': False
            })
            await send(chat_id, f'🚫 تم تقييد {mention(tf)}\nبواسطة {m}')
            return
        if text in ['رفع القيود', 'الغاء الكتم', 'الغاء التقييد']:
            if not msg.get('reply_to_message'):
                await send(chat_id, '⚠️ رد على رسالة الشخص', reply)
                return
            tf = msg['reply_to_message']['from']
            await restrict(chat_id, tf['id'], {
                'can_send_messages': True, 'can_send_media_messages': True,
                'can_send_polls': True, 'can_send_other_messages': True, 'can_add_web_page_previews': True
            })
            await send(chat_id, f'✅ تم رفع القيود عن {mention(tf)}\nبواسطة {m}')
            return
        if text == 'طرد':
            if not msg.get('reply_to_message'):
                await send(chat_id, '⚠️ رد على رسالة الشخص', reply)
                return
            tf = msg['reply_to_message']['from']
            await ban(chat_id, tf['id'])
            await unban(chat_id, tf['id'])
            await send(chat_id, f'👢 تم طرد {mention(tf)}\nبواسطة {m}')
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
        await send(chat_id, f'✅ تم تعيين وقت التنظيف: {minutes} {"دقيقة" if minutes == 1 else "دقائق"}')
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

# ===========================
# HTTP SERVER
# ===========================

PORT = int(os.environ.get('PORT', 3000))

async def webhook_handler(request):
    if request.method == 'POST' and request.path == '/webhook':
        try:
            body = await request.json()
            if 'my_chat_member' in body:
                await handle_my_chat_member(body['my_chat_member'])
            else:
                await handle_update(body)
        except Exception as e:
            print(f'Webhook error: {e}')
        return web.Response(text='OK', status=200)
    return web.Response(text='Romeo Bot is running 🌹', status=200)

async def main():
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
