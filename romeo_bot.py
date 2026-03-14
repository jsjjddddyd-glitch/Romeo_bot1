# -*- coding: utf-8 -*-
import json
import os
import re
import asyncio
import aiohttp
from aiohttp import web

TOKEN = '8785959754:AAFWbDWNkBeT42CzqNn_m1g7eqGFp6XdBps'
API = f'https://api.telegram.org/bot{TOKEN}'
DATA_FILE = './data.json'
STATE_FILE = './state.json'

# ===========================
# DATA HELPERS
# ===========================

def load_data():
    try:
        with open(DATA_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except:
        return {'custom_replies': {}, 'group_settings': {}, 'user_ranks': {}}

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
        data['group_settings'][id_] = {
            'lock_swear': False, 'lock_links': False, 'lock_forward': False, 'lock_clutter': False,
            'lock_english': False, 'lock_chinese': False, 'lock_russian': False, 'lock_photos': False,
            'lock_videos': False, 'lock_media_edit': False, 'lock_audio': False, 'lock_music': False,
            'lock_repeat': False, 'lock_mention': False, 'lock_numbers': False, 'lock_stickers': False,
            'lock_animated': False, 'lock_chat': False, 'lock_join': False,
            'disable_id': False, 'disable_service': False, 'disable_fun': False, 'disable_welcome': False, 'disable_link': False
        }
    return data['group_settings'][id_]

def get_rank(data, chat_id, user_id):
    return (data['user_ranks'].get(str(chat_id)) or {}).get(str(user_id), 'عضو')

def set_rank(data, chat_id, user_id, rank):
    if str(chat_id) not in data['user_ranks']:
        data['user_ranks'][str(chat_id)] = {}
    data['user_ranks'][str(chat_id)][str(user_id)] = rank

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

async def edit_msg(chat_id, msg_id, text):
    return await api_call('editMessageText', {'chat_id': chat_id, 'message_id': msg_id, 'text': text, 'parse_mode': 'HTML'})

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

# ===========================
# CALLBACKS
# ===========================

menu_texts = {
    'menu_service': '🔧 <b>أوامر الخدمية:</b>\n\n• <b>بايو</b> - يرسل بايو كاتب الكلمة\n• <b>اسمي</b> - يرسل اسمك\n• <b>اسمه</b> (رد) - اسم الشخص\n• <b>يوزري</b> - يرسل يوزرك\n• <b>يوزره</b> (رد) - يوزر الشخص\n• <b>المالك</b> - يذكر مالك المجموعة\n• <b>ايدي</b> - معرفك\n• <b>الرابط</b> - رابط المجموعة\n• <b>رتبة / رتبته</b> - رتبتك أو رتبة الشخص\n\n🔴 تعطيل الخدمية | تفعيل الخدمية',
    'menu_fun': '🎉 <b>أوامر التسليه:</b>\n\n• <b>رفع [كلمة]</b> (رد) - يرفع لقب للشخص\n\n🔴 تعطيل التسليه | تفعيل التسليه',
    'menu_locks': '🔒 <b>أوامر القفل والفتح:</b>\n\nقفل السب | قفل التكرار | قفل الروابط\nقفل التوجيه | قفل الكلايش | قفل الانجليزية\nقفل الصينية | قفل الروسية | قفل الصور\nقفل الفيديوهات | قفل تعديل الميديا\nقفل الصوتيات | قفل الاغاني | قفل التحويل\nقفل الدخول | قفل التاك | قفل الارقام\nقفل الملصقات | قفل المتحركة | قفل الشات\n\n(استبدل قفل بـ فتح للفتح)\n\n🔴 تعطيل الايدي | تعطيل الترحيب | تعطيل الرابط',
    'menu_settings': '⚙️ <b>أوامر الإعدادات (رد على رسالة شخص):</b>\n\n• رفع مالك أساسي / تنزيل مالك أساسي\n• رفع مالك / تنزيل مالك\n• رفع مدير / تنزيل مدير\n• رفع ادمن / تنزيل ادمن\n• رفع مميز / تنزيل مميز\n\n🔴 كتم | تقييد | طرد | رفع القيود | مسح'
}

async def handle_callback(cb):
    chat_id = cb['message']['chat']['id']
    msg_id = cb['message']['message_id']
    await answer_cb(cb['id'])
    if cb['data'] in menu_texts:
        await edit_msg(chat_id, msg_id, menu_texts[cb['data']])

# ===========================
# MESSAGE HANDLER
# ===========================

async def handle_update(update):
    if 'callback_query' in update:
        await handle_callback(update['callback_query'])
        return

    msg = update.get('message')
    if not msg:
        return

    chat_id = msg['chat']['id']
    from_ = msg['from']
    user_id = from_['id']
    text = (msg.get('text') or msg.get('caption') or '').strip()
    msg_id = msg['message_id']

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
# MEDIA MODERATION
# ===========================

async def media_mod(msg, data, settings):
    chat_id = msg['chat']['id']
    msg_id = msg['message_id']
    from_ = msg['from']
    user_id = from_['id']
    m = mention(from_)
    if await is_tg_admin(chat_id, user_id):
        return

    is_forward = msg.get('forward_from') or msg.get('forward_from_chat') or msg.get('forward_sender_name')
    if is_forward and settings['lock_forward']:
        await delete(chat_id, msg_id)
        await send(chat_id, f'⚠️ {m}\nممنوع التوجيه والتحويل هنا')
        return
    if msg.get('photo') and settings['lock_photos']:
        await delete(chat_id, msg_id)
        await send(chat_id, f'⚠️ عذرا عزيزي {m}\nممنوع ارسال الصور هنا')
        return
    if msg.get('video') and settings['lock_videos']:
        await delete(chat_id, msg_id)
        await send(chat_id, f'⚠️ عذرا عزيزي {m}\nممنوع ارسال الفيديوهات هنا')
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

# ===========================
# CONTENT MODERATION
# ===========================

async def content_mod(msg, data, settings):
    chat_id = msg['chat']['id']
    msg_id = msg['message_id']
    from_ = msg['from']
    user_id = from_['id']
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
    if settings['lock_numbers'] and re.search(r'(\+?\d{9,12})', text):
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

    if re.match(r'^بوت', text) or text == 'بوت':
        r = ['وش تبي 😒', 'اهلا 👋', 'شكد مزعج 😤', 'عندي اسم ترا 🌹']
        await send(chat_id, random.choice(r), reply)
        return
    if re.match(r'^روميو', text) or text == 'روميو':
        r = ['قول وش تبي 😊', 'هلا 👋', 'تفضل 🌹', 'لا تلح 😑']
        await send(chat_id, random.choice(r), reply)
        return
    if 'صباح الخير' in text:
        await send(chat_id, '☀️ صباح النور', reply)
        return
    if 'سلام عليكم' in text or 'السلام عليكم' in text:
        r = ['وعليكم السلام والرحمة 🌹', 'وعليكم السلام 👋', 'وعليكم السلام ورحمة الله وبركاته 🤲']
        await send(chat_id, random.choice(r), reply)
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
                    bio = of.get('bio', 'لا يوجد بايو')
                    await send(chat_id, f'👑 مالك المجموعة: {mention(of)}\n📋 البايو: {bio}')
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
        if cid not in state:
            state[cid] = {}
        state[cid][str(user_id)] = {'step': 'await_name'}
        await send(chat_id, '📝 أرسل اسم الرد:', reply)
        return
    if text == 'مسح رد' and await is_admin_up(data, chat_id, user_id):
        if cid not in state:
            state[cid] = {}
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
            'قفل السب': 'lock_swear', 'فتح السب': 'lock_swear', 'قفل التكرار': 'lock_repeat', 'فتح التكرار': 'lock_repeat',
            'قفل الروابط': 'lock_links', 'فتح الروابط': 'lock_links', 'قفل التوجيه': 'lock_forward', 'فتح التوجيه': 'lock_forward',
            'قفل التحويل': 'lock_forward', 'فتح التحويل': 'lock_forward', 'قفل الكلايش': 'lock_clutter', 'فتح الكلايش': 'lock_clutter',
            'قفل الانجليزيه': 'lock_english', 'فتح الانجليزيه': 'lock_english', 'قفل الانجليزية': 'lock_english', 'فتح الانجليزية': 'lock_english',
            'قفل الصينيه': 'lock_chinese', 'فتح الصينيه': 'lock_chinese', 'قفل الصينية': 'lock_chinese', 'فتح الصينية': 'lock_chinese',
            'قفل الروسيه': 'lock_russian', 'فتح الروسيه': 'lock_russian', 'قفل الروسية': 'lock_russian', 'فتح الروسية': 'lock_russian',
            'قفل الصور': 'lock_photos', 'فتح الصور': 'lock_photos', 'قفل الفيديوهات': 'lock_videos', 'فتح الفيديوهات': 'lock_videos',
            'قفل تعديل الميديا': 'lock_media_edit', 'فتح تعديل الميديا': 'lock_media_edit',
            'قفل الصوتيات': 'lock_audio', 'فتح الصوتيات': 'lock_audio', 'قفل الاغاني': 'lock_music', 'فتح الاغاني': 'lock_music',
            'قفل الدخول': 'lock_join', 'فتح الدخول': 'lock_join', 'قفل التاك': 'lock_mention', 'فتح التاك': 'lock_mention',
            'قفل الارقام': 'lock_numbers', 'فتح الارقام': 'lock_numbers', 'قفل الملصقات': 'lock_stickers', 'فتح الملصقات': 'lock_stickers',
            'قفل المتحركه': 'lock_animated', 'فتح المتحركه': 'lock_animated', 'قفل المتحركة': 'lock_animated', 'فتح المتحركة': 'lock_animated',
            'قفل الشات': 'lock_chat', 'فتح الشات': 'lock_chat'
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
            'تعطيل الرابط': ['disable_link', True], 'تفعيل الرابط': ['disable_link', False]
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
            photos = msg['photo']
            fid = photos[-1]['file_id']
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
            await handle_update(body)
        except Exception as e:
            print(e)
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
    await asyncio.Event().wait()

if __name__ == '__main__':
    asyncio.run(main())