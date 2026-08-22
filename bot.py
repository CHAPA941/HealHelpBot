import asyncio
import logging
import sqlite3
import os
from aiogram import Bot, Dispatcher, types
from aiogram.contrib.middlewares.logging import LoggingMiddleware
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils import executor
from aiogram.contrib.fsm_storage.memory import MemoryStorage
from aiogram.dispatcher import FSMContext
from aiogram.dispatcher.filters.state import State, StatesGroup

# --- НАСТРОЙКИ ---
API_TOKEN = os.getenv("BOT_TOKEN")
SUPER_ADMIN_ID = int(os.getenv("SUPER_ADMIN_ID"))
BOT_NAME = "HealHelp"

storage = MemoryStorage()
bot = Bot(token=API_TOKEN)
dp = Dispatcher(bot, storage=storage)
dp.middleware.setup(LoggingMiddleware())
logging.basicConfig(level=logging.INFO)

# --- СОСТОЯНИЯ FSM ---
class RejectReason(StatesGroup):
    waiting_for_reason = State()

class ReportState(StatesGroup):
    waiting_for_report_text = State()

# --- КАТЕГОРИИ ОБРАЩЕНИЙ ---
REQUEST_TYPES = [
    ("🗣 Просто поговорить", "chat"),
    ("🆘 Нужна помощь", "help"),
    ("💬 Консультация", "consult"),
]

# --- БАЗА ДАННЫХ ---
def init_db():
    conn = sqlite3.connect('data.db')
    cur = conn.cursor()
    cur.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            full_name TEXT,
            role TEXT DEFAULT 'user',
            current_admin_id INTEGER,
            status TEXT DEFAULT 'none',
            reject_reason TEXT,
            request_type TEXT
        )
    ''')
    cur.execute('''
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            from_user INTEGER,
            to_user INTEGER,
            text TEXT,
            date TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    conn.commit()
    conn.close()
    conn = sqlite3.connect('data.db')
    cur = conn.cursor()
    cur.execute('INSERT OR IGNORE INTO users (user_id, role) VALUES (?, ?)', (SUPER_ADMIN_ID, 'super_admin'))
    conn.commit()
    conn.close()

def save_user(user_id, username, full_name):
    conn = sqlite3.connect('data.db')
    cur = conn.cursor()
    cur.execute('INSERT OR IGNORE INTO users (user_id, username, full_name, role, status) VALUES (?, ?, ?, ?, ?)',
                (user_id, username, full_name, 'user', 'none'))
    conn.commit()
    conn.close()

def get_user_role(user_id):
    conn = sqlite3.connect('data.db')
    cur = conn.cursor()
    cur.execute('SELECT role FROM users WHERE user_id = ?', (user_id,))
    row = cur.fetchone()
    conn.close()
    return row[0] if row else 'user'

def get_user_status(user_id):
    conn = sqlite3.connect('data.db')
    cur = conn.cursor()
    cur.execute('SELECT current_admin_id, status, reject_reason, request_type FROM users WHERE user_id = ?', (user_id,))
    row = cur.fetchone()
    conn.close()
    return row  # (admin_id, status, reason, req_type) or None

def set_user_status(user_id, admin_id, status, reason=None, req_type=None):
    conn = sqlite3.connect('data.db')
    cur = conn.cursor()
    cur.execute('UPDATE users SET current_admin_id = ?, status = ?, reject_reason = ?, request_type = ? WHERE user_id = ?',
                (admin_id, status, reason, req_type, user_id))
    conn.commit()
    conn.close()

def reset_user_connection(user_id):
    conn = sqlite3.connect('data.db')
    cur = conn.cursor()
    cur.execute('UPDATE users SET current_admin_id = NULL, status = "none", reject_reason = NULL, request_type = NULL WHERE user_id = ?', (user_id,))
    conn.commit()
    conn.close()

def get_all_admins():
    conn = sqlite3.connect('data.db')
    cur = conn.cursor()
    cur.execute('SELECT user_id, full_name FROM users WHERE role = "admin" AND user_id != ?', (SUPER_ADMIN_ID,))
    admins = cur.fetchall()
    conn.close()
    return admins

def get_user_info(user_id):
    conn = sqlite3.connect('data.db')
    cur = conn.cursor()
    cur.execute('SELECT full_name, username FROM users WHERE user_id = ?', (user_id,))
    row = cur.fetchone()
    conn.close()
    return row if row else ("Неизвестный", "нет")

def save_message(from_user, to_user, text):
    conn = sqlite3.connect('data.db')
    cur = conn.cursor()
    cur.execute('INSERT INTO messages (from_user, to_user, text) VALUES (?, ?, ?)', (from_user, to_user, text))
    conn.commit()
    conn.close()

# --- УПРАВЛЕНИЕ АДМИНАМИ ---
def add_admin(user_id):
    conn = sqlite3.connect('data.db')
    cur = conn.cursor()
    cur.execute('SELECT user_id FROM users WHERE user_id = ?', (user_id,))
    if cur.fetchone():
        cur.execute('UPDATE users SET role = "admin" WHERE user_id = ?', (user_id,))
    else:
        cur.execute('INSERT INTO users (user_id, role) VALUES (?, ?)', (user_id, 'admin'))
    conn.commit()
    conn.close()

def remove_admin(user_id):
    if user_id == SUPER_ADMIN_ID:
        return False
    conn = sqlite3.connect('data.db')
    cur = conn.cursor()
    cur.execute('UPDATE users SET role = "user" WHERE user_id = ?', (user_id,))
    cur.execute('UPDATE users SET current_admin_id = NULL, status = "none", reject_reason = NULL, request_type = NULL WHERE current_admin_id = ?', (user_id,))
    conn.commit()
    conn.close()
    return True

def get_all_users_with_role(role):
    conn = sqlite3.connect('data.db')
    cur = conn.cursor()
    cur.execute('SELECT user_id, full_name FROM users WHERE role = ?', (role,))
    rows = cur.fetchall()
    conn.close()
    return rows

# --- ПАНЕЛЬ СУПЕР-АДМИНА ---
@dp.message_handler(commands=['admin_panel'])
async def admin_panel(message: types.Message):
    if get_user_role(message.from_user.id) != 'super_admin':
        await message.answer("⛔ Нет прав доступа.")
        return
    keyboard = InlineKeyboardMarkup(row_width=2)
    keyboard.add(
        InlineKeyboardButton("➕ Добавить админа", callback_data="add_admin_btn"),
        InlineKeyboardButton("➖ Удалить админа", callback_data="remove_admin_btn"),
        InlineKeyboardButton("📋 Список админов", callback_data="list_admins_btn")
    )
    await message.answer(f"⚙️ Панель управления {BOT_NAME}:", reply_markup=keyboard)

@dp.callback_query_handler(lambda c: c.data in ["add_admin_btn", "remove_admin_btn", "list_admins_btn"])
async def handle_admin_buttons(callback: types.CallbackQuery):
    if get_user_role(callback.from_user.id) != 'super_admin':
        await callback.answer("Нет прав", show_alert=True)
        return
    if callback.data == "add_admin_btn":
        await callback.message.answer("✏️ Введите ID: `/add_admin 123456789`", parse_mode="Markdown")
        await callback.answer()
    elif callback.data == "remove_admin_btn":
        admins = get_all_users_with_role('admin')
        if not admins:
            await callback.message.answer("❗ Нет админов.")
        else:
            keyboard = InlineKeyboardMarkup(row_width=1)
            for admin_id, name in admins:
                if admin_id == SUPER_ADMIN_ID:
                    continue
                keyboard.add(InlineKeyboardButton(f"❌ {name} (ID: {admin_id})", callback_data=f"del_admin_{admin_id}"))
            await callback.message.answer("Выберите админа для удаления:", reply_markup=keyboard)
        await callback.answer()
    elif callback.data == "list_admins_btn":
        admins = get_all_users_with_role('admin')
        if not admins:
            await callback.message.answer("📭 Список пуст.")
        else:
            text = f"📋 **Список админов {BOT_NAME}:**\n"
            for admin_id, name in admins:
                text += f"• {name} (ID: `{admin_id}`)\n"
            await callback.message.answer(text, parse_mode="Markdown")
        await callback.answer()

@dp.callback_query_handler(lambda c: c.data.startswith('del_admin_'))
async def confirm_delete_admin(callback: types.CallbackQuery):
    if get_user_role(callback.from_user.id) != 'super_admin':
        await callback.answer("Нет прав", show_alert=True)
        return
    admin_id = int(callback.data.split('_')[2])
    if admin_id == SUPER_ADMIN_ID:
        await callback.answer("Нельзя удалить суперадмина!", show_alert=True)
        return
    if remove_admin(admin_id):
        await callback.message.edit_text(f"✅ Админ удалён.")
    else:
        await callback.message.edit_text("❌ Ошибка.")
    await callback.answer()

@dp.message_handler(commands=['add_admin'])
async def add_admin_cmd(message: types.Message):
    if get_user_role(message.from_user.id) != 'super_admin':
        await message.answer("⛔ Нет прав.")
        return
    args = message.get_args()
    if not args or not args.isdigit():
        await message.answer("⚠️ Используйте: `/add_admin <ID>`", parse_mode="Markdown")
        return
    new_admin_id = int(args)
    if new_admin_id == SUPER_ADMIN_ID:
        await message.answer("❗ Вы уже супер-админ.")
        return
    add_admin(new_admin_id)
    await message.answer(f"✅ Пользователь с ID {new_admin_id} теперь админ {BOT_NAME}.")

# --- КОМАНДЫ ПОЛЬЗОВАТЕЛЯ ---
@dp.message_handler(commands=['start'])
async def start_cmd(message: types.Message):
    user = message.from_user
    save_user(user.id, user.username, user.full_name)
    role = get_user_role(user.id)
    if role == 'super_admin':
        await message.answer(f"👋 Вы супер-админ {BOT_NAME}. Используйте /admin_panel.")
        return
    reset_user_connection(user.id)
    admins = get_all_admins()
    if not admins:
        await message.answer(f"😕 Пока нет доступных админов {BOT_NAME}. Попробуйте позже.")
        return
    keyboard = InlineKeyboardMarkup(row_width=1)
    for admin_id, name in admins:
        keyboard.add(InlineKeyboardButton(f"💬 {name}", callback_data=f"choose_admin_{admin_id}"))
    await message.answer(
        f"👋 Добро пожаловать в **{BOT_NAME}** — твоё пространство для поддержки и заботы о себе.\n\n"
        "Здесь ты можешь анонимно и безопасно поговорить с психологом или просто с добрым собеседником. "
        "Мы подберем тебе специалиста, который выслушает и поддержит в трудную минуту.\n\n"
        "🔹 **Как начать?**\n"
        "1. Выбери админа из списка ниже — это твой будущий собеседник.\n"
        "2. Укажи тему обращения (просто поговорить, помощь, консультация).\n"
        "3. Дождись подтверждения — и вы сможете общаться.\n\n"
        "🔹 **Доступные команды:**\n"
        "• /start — выбрать или сменить админа\n"
        "• /change_admin — сменить админа\n"
        "• /stop — завершить диалог\n"
        "• /report — пожаловаться на админа\n\n"
        "Выбери админа, с которым хочешь поговорить:",
        reply_markup=keyboard,
        parse_mode="Markdown"
    )

# --- НОВАЯ КОМАНДА ДЛЯ ПРОСМОТРА ПОЛЬЗОВАТЕЛЬСКОГО ИНТЕРФЕЙСА ---
@dp.message_handler(commands=['user_view'])
async def user_view_cmd(message: types.Message):
    """Тестовая команда для просмотра пользовательского интерфейса (доступна всем, включая супер-админа)"""
    user = message.from_user
    save_user(user.id, user.username, user.full_name)
    
    admins = get_all_admins()
    if not admins:
        await message.answer(f"😕 Пока нет доступных админов {BOT_NAME}. Попробуйте позже.")
        return
    
    keyboard = InlineKeyboardMarkup(row_width=1)
    for admin_id, name in admins:
        keyboard.add(InlineKeyboardButton(f"💬 {name}", callback_data=f"choose_admin_{admin_id}"))
    
    await message.answer(
        f"👋 Добро пожаловать в **{BOT_NAME}** — твоё пространство для поддержки и заботы о себе.\n\n"
        "Здесь ты можешь анонимно и безопасно поговорить с психологом или просто с добрым собеседником. "
        "Мы подберем тебе специалиста, который выслушает и поддержит в трудную минуту.\n\n"
        "🔹 **Как начать?**\n"
        "1. Выбери админа из списка ниже — это твой будущий собеседник.\n"
        "2. Укажи тему обращения (просто поговорить, помощь, консультация).\n"
        "3. Дождись подтверждения — и вы сможете общаться.\n\n"
        "🔹 **Доступные команды:**\n"
        "• /start — выбрать или сменить админа\n"
        "• /change_admin — сменить админа\n"
        "• /stop — завершить диалог\n"
        "• /report — пожаловаться на админа\n\n"
        "Выбери админа, с которым хочешь поговорить:",
        reply_markup=keyboard,
        parse_mode="Markdown"
    )

@dp.message_handler(commands=['change_admin'])
async def change_admin_cmd(message: types.Message):
    reset_user_connection(message.from_user.id)
    admins = get_all_admins()
    keyboard = InlineKeyboardMarkup(row_width=1)
    for admin_id, name in admins:
        keyboard.add(InlineKeyboardButton(f"💬 {name}", callback_data=f"choose_admin_{admin_id}"))
    await message.answer("Выберите нового админа:", reply_markup=keyboard)

@dp.message_handler(commands=['stop'])
async def stop_cmd(message: types.Message):
    user_id = message.from_user.id
    user_data = get_user_status(user_id)
    if user_data and user_data[1] == 'active':
        admin_id = user_data[0]
        reset_user_connection(user_id)
        await message.answer("✅ Вы завершили общение с админом.")
        await bot.send_message(admin_id, f"Пользователь {message.from_user.full_name} завершил диалог.")
        await bot.send_message(SUPER_ADMIN_ID, f"🔄 Пользователь {message.from_user.full_name} завершил диалог с админом {admin_id}.")
    else:
        await message.answer("У вас нет активного диалога. Напишите /start для выбора админа.")

@dp.message_handler(commands=['report'])
async def report_cmd(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    user_data = get_user_status(user_id)
    if not user_data or user_data[1] != 'active':
        await message.answer("У вас нет активного диалога с админом, чтобы на него пожаловаться.")
        return
    admin_id = user_data[0]
    async with state.proxy() as data:
        data['reported_admin'] = admin_id
    await state.set_state(ReportState.waiting_for_report_text)
    await message.answer("✏️ Напишите текст жалобы на админа. Опишите, что произошло. (Можно отменить командой /cancel)")

@dp.message_handler(commands=['cancel'], state='*')
async def cancel_cmd(message: types.Message, state: FSMContext):
    current_state = await state.get_state()
    if current_state:
        await state.finish()
        await message.answer("Действие отменено.")
    else:
        await message.answer("Нет активных действий для отмены.")

@dp.message_handler(state=ReportState.waiting_for_report_text, content_types=['text'])
async def process_report_text(message: types.Message, state: FSMContext):
    report_text = message.text.strip()
    if not report_text:
        await message.answer("Жалоба не может быть пустой. Напишите текст.")
        return
    async with state.proxy() as data:
        admin_id = data.get('reported_admin')
    if not admin_id:
        await message.answer("Ошибка: не найден админ. Попробуйте /report заново.")
        await state.finish()
        return
    user_id = message.from_user.id
    user_info = get_user_info(user_id)
    admin_info = get_user_info(admin_id)
    report_msg = (
        f"🚨 **НОВЫЙ РЕПОРТ в {BOT_NAME}**\n"
        f"👤 Пользователь: {user_info[0]} (@{user_info[1]}) [ID: {user_id}]\n"
        f"👤 Админ: {admin_info[0]} (@{admin_info[1]}) [ID: {admin_id}]\n"
        f"📝 Текст жалобы:\n{report_text}"
    )
    await bot.send_message(SUPER_ADMIN_ID, report_msg, parse_mode="Markdown")
    await message.answer("✅ Ваша жалоба отправлена супер-админу. Спасибо, мы разберёмся.")
    await bot.send_message(admin_id, f"⚠️ На вас поступила жалоба от пользователя в {BOT_NAME}. Администрация рассмотрит её.")
    await state.finish()

# --- ВЫБОР АДМИНА -> КАТЕГОРИЯ ---
@dp.callback_query_handler(lambda c: c.data.startswith('choose_admin_'))
async def process_choose_admin(callback: types.CallbackQuery):
    admin_id = int(callback.data.split('_')[2])
    user_id = callback.from_user.id
    conn = sqlite3.connect('data.db')
    cur = conn.cursor()
    cur.execute('UPDATE users SET current_admin_id = ?, status = "none", reject_reason = NULL, request_type = NULL WHERE user_id = ?',
                (admin_id, user_id))
    conn.commit()
    conn.close()
    keyboard = InlineKeyboardMarkup(row_width=2)
    for label, value in REQUEST_TYPES:
        keyboard.add(InlineKeyboardButton(label, callback_data=f"req_type_{value}_{admin_id}"))
    keyboard.add(InlineKeyboardButton("🔙 Назад", callback_data="cancel_choose"))
    await callback.message.edit_text(
        "📌 Вы выбрали админа. Укажите **тип обращения**:",
        reply_markup=keyboard
    )
    await callback.answer()

@dp.callback_query_handler(lambda c: c.data == "cancel_choose")
async def cancel_choose(callback: types.CallbackQuery):
    reset_user_connection(callback.from_user.id)
    await callback.message.edit_text("Выбор отменён. Напишите /start заново.")
    await callback.answer()

@dp.callback_query_handler(lambda c: c.data.startswith('req_type_'))
async def process_request_type(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    data_parts = callback.data.split('_')
    req_type = data_parts[2]
    admin_id = int(data_parts[3])
    conn = sqlite3.connect('data.db')
    cur = conn.cursor()
    cur.execute('SELECT current_admin_id FROM users WHERE user_id = ?', (user_id,))
    row = cur.fetchone()
    conn.close()
    if not row or row[0] != admin_id:
        await callback.answer("Ошибка, попробуйте снова /start", show_alert=True)
        return
    set_user_status(user_id, admin_id, 'pending', req_type=req_type)
    type_name = dict(REQUEST_TYPES).get(req_type, req_type)
    user_full = callback.from_user.full_name
    user_username = callback.from_user.username
    keyboard = InlineKeyboardMarkup(row_width=2)
    keyboard.add(
        InlineKeyboardButton("✅ Принять", callback_data=f"accept_{user_id}"),
        InlineKeyboardButton("❌ Отказать", callback_data=f"reject_{user_id}")
    )
    await bot.send_message(
        admin_id,
        f"🔔 Пользователь {user_full} (@{user_username}) хочет с вами пообщаться в {BOT_NAME}.\n📌 Тема: **{type_name}**",
        reply_markup=keyboard
    )
    await callback.message.edit_text(
        f"✅ Запрос с темой «{type_name}» отправлен админу. Ожидайте подтверждения."
    )
    await callback.answer()
    await bot.send_message(
        SUPER_ADMIN_ID,
        f"🟡 Запрос от {user_full} -> админу {admin_id}, тема: {type_name}"
    )

# --- ОБРАБОТКА КНОПОК ПРИНЯТЬ / ОТКАЗАТЬ ---
@dp.callback_query_handler(lambda c: c.data.startswith('accept_'))
async def accept_user(callback: types.CallbackQuery):
    admin_id = callback.from_user.id
    user_id = int(callback.data.split('_')[1])
    conn = sqlite3.connect('data.db')
    cur = conn.cursor()
    cur.execute('SELECT current_admin_id, status FROM users WHERE user_id = ?', (user_id,))
    row = cur.fetchone()
    conn.close()
    if not row or row[0] != admin_id or row[1] != 'pending':
        await callback.answer("Запрос уже обработан.", show_alert=True)
        return
    set_user_status(user_id, admin_id, 'active')
    await bot.send_message(user_id, f"✅ Админ принял ваш запрос в {BOT_NAME}! Теперь вы можете общаться. Используйте /stop чтобы завершить диалог, /report если нужно пожаловаться.")
    await callback.message.edit_text("✅ Вы приняли пользователя.")
    await bot.send_message(SUPER_ADMIN_ID, f"🟢 Админ {callback.from_user.full_name} принял пользователя {user_id}")
    await callback.answer()

@dp.callback_query_handler(lambda c: c.data.startswith('reject_'))
async def reject_user(callback: types.CallbackQuery):
    admin_id = callback.from_user.id
    user_id = int(callback.data.split('_')[1])
    conn = sqlite3.connect('data.db')
    cur = conn.cursor()
    cur.execute('SELECT current_admin_id, status FROM users WHERE user_id = ?', (user_id,))
    row = cur.fetchone()
    conn.close()
    if not row or row[0] != admin_id or row[1] != 'pending':
        await callback.answer("Запрос уже обработан.", show_alert=True)
        return
    await callback.message.answer("✏️ Напишите причину отказа (одним сообщением).")
    state = dp.current_state(user=admin_id, chat=admin_id)
    await state.set_state(RejectReason.waiting_for_reason)
    async with state.proxy() as data:
        data['rejected_user'] = user_id
    await callback.message.edit_text("❌ Отказ отправлен. Введите причину.")
    await callback.answer()

@dp.message_handler(state=RejectReason.waiting_for_reason, content_types=['text'])
async def process_reject_reason(message: types.Message, state: FSMContext):
    admin_id = message.from_user.id
    reason = message.text.strip()
    if not reason:
        await message.answer("Причина не может быть пустой.")
        return
    async with state.proxy() as data:
        user_id = data.get('rejected_user')
    if not user_id:
        await message.answer("Ошибка. Попробуйте ещё раз.")
        await state.finish()
        return
    conn = sqlite3.connect('data.db')
    cur = conn.cursor()
    cur.execute('UPDATE users SET status = "rejected", reject_reason = ? WHERE user_id = ?', (reason, user_id))
    conn.commit()
    conn.close()
    await bot.send_message(
        user_id,
        f"❌ Админ отклонил ваш запрос в {BOT_NAME}.\nПричина: {reason}\n\nВыберите другого админа через /start или /change_admin."
    )
    await message.answer(f"✅ Причина отправлена.")
    await bot.send_message(SUPER_ADMIN_ID, f"🔴 Админ {admin_id} отклонил {user_id}. Причина: {reason}")
    await state.finish()

# --- ОБРАБОТКА СООБЩЕНИЙ ---
@dp.message_handler(content_types=['text'])
async def handle_text(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    role = get_user_role(user_id)
    
    current_state = await state.get_state()
    if current_state in [RejectReason.waiting_for_reason.state, ReportState.waiting_for_report_text.state]:
        return
    
    if role == 'super_admin':
        await message.answer(f"Вы супер-админ {BOT_NAME}. Используйте /admin_panel.")
        return
    
    if role == 'admin':
        conn = sqlite3.connect('data.db')
        cur = conn.cursor()
        cur.execute('''
            SELECT user_id FROM users 
            WHERE current_admin_id = ? AND status = 'active'
            ORDER BY (SELECT date FROM messages WHERE from_user = users.user_id AND to_user = ? ORDER BY date DESC LIMIT 1) DESC
        ''', (user_id, user_id))
        active_users = cur.fetchall()
        conn.close()
        if not active_users:
            await message.answer("У вас нет активных диалогов.")
            return
        target_user = active_users[0][0]
        save_message(user_id, target_user, message.text)
        await bot.send_message(target_user, f"✉️ Админ: {message.text}")
        await message.answer("✅ Отправлено.")
        await bot.send_message(SUPER_ADMIN_ID, f"🟢 Админ -> {target_user}:\n{message.text}")
        return
    
    if role == 'user':
        user_data = get_user_status(user_id)
        if not user_data:
            await message.answer("⚠️ Вы не выбрали админа. Напишите /start")
            return
        admin_id, status, reason, req_type = user_data
        if status == 'pending':
            await message.answer("⏳ Ожидайте ответа админа.")
            return
        elif status == 'rejected':
            await message.answer(f"❌ Отклонено. Причина: {reason}\nВыберите другого админа через /start.")
            return
        elif status == 'active':
            save_message(user_id, admin_id, message.text)
            user_name = message.from_user.full_name
            await bot.send_message(admin_id, f"✉️ {user_name} (@{message.from_user.username}):\n{message.text}")
            await message.answer("✅ Отправлено админу.")
            await bot.send_message(SUPER_ADMIN_ID, f"🔵 {user_name} -> {admin_id}:\n{message.text}")
            return
        else:
            await message.answer("⚠️ Неизвестный статус. Напишите /start.")

# --- ЗАПУСК ---
if __name__ == '__main__':
    init_db()
    from flask import Flask
    app = Flask(__name__)
    @app.route('/')
    def index():
        return f"{BOT_NAME} is running"
    import threading
    def run_flask():
        app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))
    threading.Thread(target=run_flask).start()
    executor.start_polling(dp, skip_updates=True)
