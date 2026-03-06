import os, time, json, random, re, requests, math, threading
from datetime import datetime, timedelta

# ─────────────────────────────────────────
#  НАСТРОЙКИ — вставь в Replit Secrets:
#  TELEGRAM_TOKEN   — токен от BotFather
#  GROQ_API_KEY     — ключ от groq.com
#  WEATHER_API_KEY  — ключ от openweathermap.org (бесплатно)
#  BOT_USERNAME     — username бота без @
# ─────────────────────────────────────────

TOKEN         = os.getenv("TELEGRAM_TOKEN")
GROQ_KEY      = os.getenv("GROQ_API_KEY")
WEATHER_KEY   = os.getenv("WEATHER_API_KEY", "")
BOT_USERNAME  = os.getenv("BOT_USERNAME", "").lower()

if not TOKEN:
    print("❌ Нет TELEGRAM_TOKEN"); raise SystemExit(1)

API = "https://api.telegram.org/bot" + TOKEN + "/"

# ─────────────────────────────────────────
#  ФАЙЛЫ ДАННЫХ
# ─────────────────────────────────────────
DATA_FILE   = "data.json"
OFFSET_FILE = "offset.txt"

DEFAULTS = {
    "balances": {},
    "warnings": {},
    "settings": {},
    "guesses":  {},
    "reminders": [],
    "dialogue": {},      # память диалога {user_id: [messages]}
    "stats": {},         # статистика пользователей
    "duels": {},         # активные дуэли
    "work_cooldown": {}, # кулдаун работы
}

def load_data():
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                d = json.load(f)
                for k, v in DEFAULTS.items():
                    if k not in d:
                        d[k] = v
                return d
        except:
            return DEFAULTS.copy()
    return DEFAULTS.copy()

def save_data(d):
    try:
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print("save_data error:", e)

DATA = load_data()

def load_offset():
    try:
        with open(OFFSET_FILE) as f:
            return int(f.read().strip())
    except:
        return 0

def save_offset(o):
    try:
        with open(OFFSET_FILE, "w") as f:
            f.write(str(o))
    except:
        pass

# ─────────────────────────────────────────
#  TELEGRAM HELPERS
# ─────────────────────────────────────────
SPAM_CACHE = {}

def tg_post(method, payload=None, files=None, timeout=30):
    url = API + method
    try:
        if files:
            r = requests.post(url, data=payload, files=files, timeout=timeout)
        else:
            r = requests.post(url, json=payload, timeout=timeout)
        return r.json()
    except Exception as e:
        print(f"tg_post [{method}] error:", e)
        return None

def tg_send(chat, text, reply=None, parse_mode=None, keyboard=None):
    p = {"chat_id": chat, "text": text[:4000]}
    if reply:       p["reply_to_message_id"] = reply
    if parse_mode:  p["parse_mode"] = parse_mode
    if keyboard:    p["reply_markup"] = keyboard
    return tg_post("sendMessage", payload=p)

def tg_delete(chat, msg_id):
    return tg_post("deleteMessage", payload={"chat_id": chat, "message_id": msg_id})

def tg_get_member(chat, user):
    try:
        r = requests.get(API + "getChatMember",
                         params={"chat_id": chat, "user_id": user}, timeout=10)
        return r.json().get("result")
    except:
        return None

def tg_restrict(chat, user, secs):
    from datetime import timezone
    until = int((datetime.now(timezone.utc) + timedelta(seconds=secs)).timestamp())
    perm = {"can_send_messages": False, "can_send_media_messages": False,
            "can_send_other_messages": False, "can_add_web_page_previews": False}
    try:
        return requests.post(API + "restrictChatMember",
                             json={"chat_id": chat, "user_id": user,
                                   "permissions": perm, "until_date": until}, timeout=10)
    except Exception as e:
        print("tg_restrict error:", e)

def is_admin(chat, uid):
    m = tg_get_member(chat, uid)
    return m and m.get("status") in ("administrator", "creator")

def inline_keyboard(buttons):
    """buttons = [[(text, callback_data), ...], ...]"""
    return {"inline_keyboard": [[{"text": t, "callback_data": d} for t, d in row] for row in buttons]}

# ─────────────────────────────────────────
#  GROQ AI  (умный + память диалога)
# ─────────────────────────────────────────
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODEL = "llama-3.3-70b-versatile"
MAX_HISTORY = 10  # сообщений в памяти

SYSTEM_PROMPT = """Ты умный, дружелюбный и немного весёлый Telegram-бот-ассистент.
Отвечай на русском языке, кратко и по делу (максимум 3-4 предложения если не просят подробнее).
Ты помнишь контекст разговора и можешь ссылаться на предыдущие сообщения.
Умеешь шутить, помогаешь с вопросами, объясняешь сложные темы просто."""

def groq_chat(uid, user_message):
    """Умный ИИ с памятью диалога"""
    if not GROQ_KEY:
        return "❌ Нет GROQ_API_KEY. Добавь в Secrets на Replit."

    DATA.setdefault("dialogue", {})
    history = DATA["dialogue"].get(str(uid), [])

    history.append({"role": "user", "content": user_message})
    if len(history) > MAX_HISTORY * 2:
        history = history[-(MAX_HISTORY * 2):]

    messages = [{"role": "system", "content": SYSTEM_PROMPT}] + history

    headers = {
        "Authorization": "Bearer " + GROQ_KEY,
        "Content-Type": "application/json"
    }
    body = {
        "model": GROQ_MODEL,
        "messages": messages,
        "max_tokens": 500,
        "temperature": 0.8
    }
    try:
        r = requests.post(GROQ_URL, headers=headers, json=body, timeout=30)
        j = r.json()
        if "choices" in j:
            reply = j["choices"][0]["message"]["content"].strip()
            history.append({"role": "assistant", "content": reply})
            DATA["dialogue"][str(uid)] = history
            save_data(DATA)
            return reply
        if "error" in j:
            return "⚠️ Ошибка ИИ: " + str(j["error"].get("message", j["error"]))
    except Exception as e:
        return "⚠️ Ошибка соединения с ИИ: " + str(e)
    return "⚠️ Неизвестная ошибка ИИ"

def clear_dialogue(uid):
    DATA.setdefault("dialogue", {})
    DATA["dialogue"].pop(str(uid), None)
    save_data(DATA)

# ─────────────────────────────────────────
#  ЭКОНОМИКА
# ─────────────────────────────────────────
WORK_COOLDOWN_SEC = 3600  # 1 час

def get_balance(uid):
    return DATA.get("balances", {}).get(str(uid), 0)

def add_balance(uid, amount):
    DATA.setdefault("balances", {})
    DATA["balances"][str(uid)] = get_balance(uid) + amount
    save_data(DATA)

def set_balance(uid, amount):
    DATA.setdefault("balances", {})
    DATA["balances"][str(uid)] = max(0, amount)
    save_data(DATA)

def earn_work(uid):
    now = time.time()
    last = DATA.get("work_cooldown", {}).get(str(uid), 0)
    if now - last < WORK_COOLDOWN_SEC:
        remaining = int(WORK_COOLDOWN_SEC - (now - last))
        mins = remaining // 60
        return None, mins
    amt = random.randint(10, 50)
    add_balance(uid, amt)
    DATA.setdefault("work_cooldown", {})[str(uid)] = now
    save_data(DATA)
    return amt, 0

JOBS = [
    "программист 💻", "повар 🍳", "таксист 🚗", "врач 🏥",
    "блогер 📱", "строитель 🔨", "учитель 📚", "музыкант 🎵"
]

def get_top(n=10):
    balances = DATA.get("balances", {})
    sorted_b = sorted(balances.items(), key=lambda x: x[1], reverse=True)
    return sorted_b[:n]

# ─────────────────────────────────────────
#  СТАТИСТИКА ПОЛЬЗОВАТЕЛЕЙ
# ─────────────────────────────────────────
def track_stat(uid, key, amount=1):
    DATA.setdefault("stats", {})
    DATA["stats"].setdefault(str(uid), {})
    DATA["stats"][str(uid)][key] = DATA["stats"][str(uid)].get(key, 0) + amount

def get_stat(uid, key):
    return DATA.get("stats", {}).get(str(uid), {}).get(key, 0)

# ─────────────────────────────────────────
#  ПОГОДА
# ─────────────────────────────────────────
def get_weather(city):
    if not WEATHER_KEY:
        return "❌ Нет WEATHER_API_KEY. Получи бесплатно на openweathermap.org и добавь в Secrets."
    try:
        url = "https://api.openweathermap.org/data/2.5/weather"
        r = requests.get(url, params={
            "q": city, "appid": WEATHER_KEY,
            "units": "metric", "lang": "ru"
        }, timeout=10)
        j = r.json()
        if j.get("cod") != 200:
            return f"❌ Город '{city}' не найден"
        name   = j["name"]
        temp   = round(j["main"]["temp"])
        feels  = round(j["main"]["feels_like"])
        desc   = j["weather"][0]["description"].capitalize()
        humid  = j["main"]["humidity"]
        wind   = round(j["wind"]["speed"])
        return (f"🌤 Погода в {name}:\n"
                f"🌡 Температура: {temp}°C (ощущается как {feels}°C)\n"
                f"☁️ {desc}\n"
                f"💧 Влажность: {humid}%\n"
                f"💨 Ветер: {wind} м/с")
    except Exception as e:
        return "⚠️ Ошибка при получении погоды: " + str(e)

# ─────────────────────────────────────────
#  НОВОСТИ
# ─────────────────────────────────────────
def get_news():
    try:
        r = requests.get(
            "https://news.google.com/rss?hl=ru&gl=RU&ceid=RU:ru",
            timeout=10, headers={"User-Agent": "Mozilla/5.0"}
        )
        items = re.findall(r"<title><!\[CDATA\[(.*?)\]\]></title>", r.text)
        items = [i for i in items if "Google" not in i][:5]
        if not items:
            return "❌ Не удалось получить новости"
        return "📰 Топ новости:\n\n" + "\n\n".join(f"{i+1}. {n}" for i, n in enumerate(items))
    except Exception as e:
        return "⚠️ Ошибка новостей: " + str(e)

# ─────────────────────────────────────────
#  МУЗЫКАЛЬНЫЙ ПОИСК (через iTunes API)
# ─────────────────────────────────────────
def search_music(query):
    try:
        r = requests.get("https://itunes.apple.com/search", params={
            "term": query, "media": "music", "limit": 5, "country": "RU"
        }, timeout=10)
        j = r.json()
        results = j.get("results", [])
        if not results:
            return "❌ Ничего не найдено"
        lines = ["🎵 Результаты поиска:\n"]
        for i, track in enumerate(results):
            artist = track.get("artistName", "Неизвестен")
            song   = track.get("trackName", "Неизвестно")
            album  = track.get("collectionName", "")
            preview = track.get("previewUrl", "")
            line = f"{i+1}. {artist} — {song}"
            if album:
                line += f" ({album})"
            if preview:
                line += f"\n🔊 Превью: {preview}"
            lines.append(line)
        return "\n\n".join(lines)
    except Exception as e:
        return "⚠️ Ошибка поиска музыки: " + str(e)

# ─────────────────────────────────────────
#  НАПОМИНАНИЯ
# ─────────────────────────────────────────
def add_reminder(uid, chat, text, delay_min):
    remind_at = time.time() + delay_min * 60
    DATA.setdefault("reminders", []).append({
        "uid": uid, "chat": chat,
        "text": text, "at": remind_at
    })
    save_data(DATA)

def check_reminders():
    while True:
        now = time.time()
        to_send = [r for r in DATA.get("reminders", []) if r["at"] <= now]
        DATA["reminders"] = [r for r in DATA.get("reminders", []) if r["at"] > now]
        for r in to_send:
            tg_send(r["chat"], f"⏰ Напоминание: {r['text']}")
            save_data(DATA)
        time.sleep(30)

# ─────────────────────────────────────────
#  МИНИ-ИГРЫ
# ─────────────────────────────────────────

# --- Казино (слоты) ---
SLOTS = ["🍒", "🍋", "🍊", "🍇", "⭐", "💎", "7️⃣"]

def play_slots(uid, bet):
    bal = get_balance(uid)
    if bet <= 0:
        return "❌ Ставка должна быть больше 0"
    if bet > bal:
        return f"❌ Недостаточно монет. У тебя {bal} 💰"
    
    s = [random.choice(SLOTS) for _ in range(3)]
    line = " | ".join(s)
    
    if s[0] == s[1] == s[2]:
        if s[0] == "💎":
            mult, msg = 10, "💎 ДЖЕКПОТ! x10"
        elif s[0] == "7️⃣":
            mult, msg = 7, "7️⃣ СЕМЁРКИ! x7"
        elif s[0] == "⭐":
            mult, msg = 5, "⭐ ЗВЁЗДЫ! x5"
        else:
            mult, msg = 3, "🎉 Три одинаковых! x3"
        win = bet * mult
        add_balance(uid, win - bet)
        track_stat(uid, "slots_wins")
        return f"🎰 {line}\n{msg}\n+{win - bet} 💰 (было {bal} → {get_balance(uid)})"
    elif s[0] == s[1] or s[1] == s[2]:
        add_balance(uid, 0)  # возврат ставки
        return f"🎰 {line}\n🔄 Два одинаковых — ставка возвращена\n💰 Баланс: {get_balance(uid)}"
    else:
        add_balance(uid, -bet)
        track_stat(uid, "slots_losses")
        return f"🎰 {line}\n😔 Не повезло, -{bet} 💰\n💰 Баланс: {get_balance(uid)}"

# --- Монетка ---
def flip_coin(uid, bet, choice):
    bal = get_balance(uid)
    if bet <= 0:
        return "❌ Ставка должна быть больше 0"
    if bet > bal:
        return f"❌ Недостаточно монет. У тебя {bal} 💰"
    result = random.choice(["орёл", "решка"])
    won = result == choice
    change = bet if won else -bet
    add_balance(uid, change)
    emoji = "✅" if won else "❌"
    return (f"🪙 Выпал: {result}\n"
            f"{emoji} Ты поставил: {choice}\n"
            f"{'Выиграл' if won else 'Проиграл'} {bet} 💰\n"
            f"💰 Баланс: {get_balance(uid)}")

# --- Дуэль ---
def create_duel(uid, chat, bet):
    bal = get_balance(uid)
    if bet > bal:
        return f"❌ Недостаточно монет. У тебя {bal} 💰"
    DATA.setdefault("duels", {})[str(chat)] = {
        "challenger": uid, "bet": bet, "created": time.time()
    }
    save_data(DATA)
    return True

def accept_duel(uid, chat):
    duel = DATA.get("duels", {}).get(str(chat))
    if not duel:
        return "❌ Нет активной дуэли"
    if duel["challenger"] == uid:
        return "❌ Нельзя принять свою дуэль"
    bet = duel["bet"]
    challenger = duel["challenger"]
    if get_balance(uid) < bet:
        return f"❌ Недостаточно монет для дуэли. Нужно {bet} 💰"
    
    winner = random.choice([uid, challenger])
    loser  = challenger if winner == uid else uid
    add_balance(winner, bet)
    add_balance(loser, -bet)
    DATA["duels"].pop(str(chat), None)
    save_data(DATA)
    track_stat(winner, "duels_won")
    track_stat(loser, "duels_lost")
    winner_tag = f"id{winner}"
    loser_tag  = f"id{loser}"
    return (f"⚔️ Дуэль завершена!\n"
            f"🏆 Победитель: [{winner_tag}](tg://user?id={winner}) +{bet} 💰\n"
            f"💀 Проигравший: [{loser_tag}](tg://user?id={loser}) -{bet} 💰")

# --- RPS (камень-ножницы-бумага) ---
RPS_OPTS = {"камень": 0, "ножницы": 1, "бумага": 2}
RPS_NAMES = {0: "камень 🪨", 1: "ножницы ✂️", 2: "бумага 📄"}
RPS_BEAT = {"камень": "ножницы", "ножницы": "бумага", "бумага": "камень"}

def play_rps(uid, choice, bet=0):
    if choice not in RPS_OPTS:
        return "❌ Выбери: камень, ножницы или бумага"
    bal = get_balance(uid)
    if bet > bal:
        return f"❌ Недостаточно монет. У тебя {bal} 💰"
    
    bot_choice = random.choice(list(RPS_OPTS.keys()))
    u = RPS_OPTS[choice]
    b = RPS_OPTS[bot_choice]
    diff = (u - b) % 3
    
    if diff == 0:
        result = "🤝 Ничья!"
    elif diff == 1:
        result = "🏆 Ты выиграл!"
        if bet: add_balance(uid, bet)
        track_stat(uid, "rps_wins")
    else:
        result = "😈 Я выиграл!"
        if bet: add_balance(uid, -bet)
        track_stat(uid, "rps_losses")
    
    msg = f"Я выбрал: {RPS_NAMES[b]}\nТы выбрал: {RPS_NAMES[u]}\n{result}"
    if bet:
        msg += f"\n💰 Баланс: {get_balance(uid)}"
    return msg

# ─────────────────────────────────────────
#  МОДЕРАЦИЯ
# ─────────────────────────────────────────
def ensure_settings(cid):
    DATA.setdefault("settings", {})
    s = DATA["settings"].get(str(cid))
    defaults = {
        "bot_name": None,
        "anti_links": True,
        "anti_swear": True,
        "swear_list": ["плохое", "ругательство"],
        "caps_threshold": 0.7,
        "spam_repeat": 3,
        "auto_mute": 300
    }
    if not s:
        DATA["settings"][str(cid)] = defaults.copy()
        save_data(DATA)
        return DATA["settings"][str(cid)]
    for k, v in defaults.items():
        if k not in s:
            s[k] = v
    return s

def inc_warn(uid):
    DATA.setdefault("warnings", {})
    DATA["warnings"][str(uid)] = DATA["warnings"].get(str(uid), 0) + 1
    save_data(DATA)
    return DATA["warnings"][str(uid)]

def contains_link(text):
    return bool(re.search(r"https?://|t\.me/|telegram\.me|\bwww\.", text, flags=re.I))

def contains_swear(text, sw):
    L = text.lower()
    return any(w and w in L for w in sw)

def caps_ratio(text):
    letters = [c for c in text if c.isalpha()]
    if not letters: return 0.0
    return sum(1 for c in letters if c.isupper()) / len(letters)

def spam_check(cid, uid, text, threshold):
    key = (cid, uid)
    now = time.time()
    st = SPAM_CACHE.get(key)
    if st and st.get("last") == text and now - st.get("ts", 0) < 60:
        st["count"] += 1; st["ts"] = now
    else:
        SPAM_CACHE[key] = {"last": text, "count": 1, "ts": now}
    return SPAM_CACHE[key]["count"] >= threshold

# ─────────────────────────────────────────
#  КОМАНДЫ
# ─────────────────────────────────────────
HELP_TEXT = """
🤖 Команды бота:

🧠 ИИ:
/ai <вопрос> — спросить ИИ
/забыть — очистить память диалога

🎮 Игры и экономика:
/работать — заработать монеты (раз в час)
/баланс — твой баланс
/топ — топ богатых
/слоты <ставка> — казино-слоты
/монетка <ставка> орёл|решка — монетка
/дуэль <ставка> — вызвать на дуэль
/принять — принять дуэль
/rps <выбор> [ставка] — камень-ножницы-бумага
/угадай — угадай число
/профиль — твоя статистика

🌍 Информация:
/погода <город> — погода
/новости — свежие новости
/музыка <запрос> — поиск музыки

⏰ Напоминания:
/напомни <мин> <текст> — напомнить через N минут

🛡 Модерация (для админов):
/мут @user [сек] — замутить
/пред @user — выдать предупреждение
/настройки — текущие настройки чата
"""

def handle_command(msg):
    text  = msg.get("text", "").strip()
    chat  = msg["chat"]["id"]
    user  = msg.get("from", {})
    uid   = user.get("id")
    uname = user.get("first_name") or user.get("username") or str(uid)
    mid   = msg.get("message_id")

    cmd = text.split()[0].lower().split("@")[0]

    # /start
    if cmd == "/start":
        tg_send(chat, f"Привет, {uname}! 👋\nЯ умный бот с ИИ, играми и много чем ещё.\nНапиши /help чтобы увидеть все команды."); return

    # /help
    if cmd == "/help":
        tg_send(chat, HELP_TEXT); return

    # /ai
    if cmd == "/ai":
        q = text[3:].strip()
        if not q: tg_send(chat, "Напиши: /ai вопрос"); return
        tg_send(chat, "🤔 Думаю...")
        answer = groq_chat(uid, q)
        tg_send(chat, answer); return

    # /забыть
    if cmd == "/забыть":
        clear_dialogue(uid)
        tg_send(chat, "🧹 Память диалога очищена. Начинаем с чистого листа!"); return

    # /работать
    if cmd == "/работать":
        amt, mins = earn_work(uid)
        if amt is None:
            tg_send(chat, f"⏳ Ты недавно работал. Подожди ещё {mins} мин.")
        else:
            job = random.choice(JOBS)
            tg_send(chat, f"💼 Ты поработал как {job}\nЗаработал {amt} 💰\nБаланс: {get_balance(uid)} 💰")
        return

    # /баланс
    if cmd == "/баланс":
        tg_send(chat, f"💰 Баланс {uname}: {get_balance(uid)} монет"); return

    # /топ
    if cmd == "/топ":
        top = get_top(10)
        if not top: tg_send(chat, "Пока никто не зарабатывал монеты"); return
        lines = ["🏆 Топ богатейших:\n"]
        medals = ["🥇", "🥈", "🥉"] + ["4️⃣","5️⃣","6️⃣","7️⃣","8️⃣","9️⃣","🔟"]
        for i, (uid_s, bal) in enumerate(top):
            lines.append(f"{medals[i]} id{uid_s}: {bal} 💰")
        tg_send(chat, "\n".join(lines)); return

    # /профиль
    if cmd == "/профиль":
        rps_w  = get_stat(uid, "rps_wins")
        rps_l  = get_stat(uid, "rps_losses")
        sl_w   = get_stat(uid, "slots_wins")
        sl_l   = get_stat(uid, "slots_losses")
        d_w    = get_stat(uid, "duels_won")
        d_l    = get_stat(uid, "duels_lost")
        bal    = get_balance(uid)
        warns  = DATA.get("warnings", {}).get(str(uid), 0)
        tg_send(chat,
            f"👤 Профиль {uname}\n"
            f"💰 Баланс: {bal}\n"
            f"⚠️ Предупреждений: {warns}\n\n"
            f"✂️ Камень-ножницы: {rps_w}W / {rps_l}L\n"
            f"🎰 Слоты: {sl_w}W / {sl_l}L\n"
            f"⚔️ Дуэли: {d_w}W / {d_l}L"
        ); return

    # /слоты
    if cmd == "/слоты":
        parts = text.split()
        if len(parts) < 2: tg_send(chat, "Использование: /слоты <ставка>"); return
        try:
            bet = int(parts[1])
            tg_send(chat, play_slots(uid, bet))
        except ValueError:
            tg_send(chat, "❌ Ставка должна быть числом")
        return

    # /монетка
    if cmd == "/монетка":
        parts = text.split()
        if len(parts) < 3: tg_send(chat, "Использование: /монетка <ставка> орёл|решка"); return
        try:
            bet = int(parts[1])
            choice = parts[2].lower()
            if choice not in ("орёл", "решка"):
                tg_send(chat, "❌ Выбери: орёл или решка"); return
            tg_send(chat, flip_coin(uid, bet, choice))
        except ValueError:
            tg_send(chat, "❌ Ставка должна быть числом")
        return

    # /дуэль
    if cmd == "/дуэль":
        parts = text.split()
        if len(parts) < 2: tg_send(chat, "Использование: /дуэль <ставка>"); return
        try:
            bet = int(parts[1])
            result = create_duel(uid, chat, bet)
            if result is True:
                tg_send(chat, f"⚔️ {uname} вызывает на дуэль!\nСтавка: {bet} 💰\nНапиши /принять чтобы принять вызов!")
            else:
                tg_send(chat, result)
        except ValueError:
            tg_send(chat, "❌ Ставка должна быть числом")
        return

    # /принять
    if cmd == "/принять":
        result = accept_duel(uid, chat)
        tg_send(chat, result, parse_mode="Markdown"); return

    # /rps
    if cmd == "/rps":
        parts = text.split()
        if len(parts) < 2: tg_send(chat, "Использование: /rps камень|ножницы|бумага [ставка]"); return
        choice = parts[1].lower()
        bet = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0
        tg_send(chat, play_rps(uid, choice, bet)); return

    # /угадай
    if cmd == "/угадай":
        DATA.setdefault("guesses", {})[str(chat)] = {
            "number": random.randint(1, 100), "attempts": 7
        }
        save_data(DATA)
        tg_send(chat, "🎲 Загадал число от 1 до 100. У тебя 7 попыток. Пиши число!"); return

    # /погода
    if cmd == "/погода":
        city = text[7:].strip()
        if not city: tg_send(chat, "Использование: /погода <город>"); return
        tg_send(chat, get_weather(city)); return

    # /новости
    if cmd == "/новости":
        tg_send(chat, "📰 Загружаю новости...")
        tg_send(chat, get_news()); return

    # /музыка
    if cmd == "/музыка":
        q = text[7:].strip()
        if not q: tg_send(chat, "Использование: /музыка <название или исполнитель>"); return
        tg_send(chat, search_music(q)); return

    # /напомни
    if cmd == "/напомни":
        parts = text.split(maxsplit=2)
        if len(parts) < 3: tg_send(chat, "Использование: /напомни <минут> <текст>"); return
        try:
            mins = int(parts[1])
            remind_text = parts[2]
            add_reminder(uid, chat, remind_text, mins)
            tg_send(chat, f"⏰ Напомню через {mins} мин: «{remind_text}»")
        except ValueError:
            tg_send(chat, "❌ Укажи число минут")
        return

    # /мут
    if cmd == "/мут":
        if not is_admin(chat, uid):
            tg_send(chat, "❌ Только для администраторов"); return
        reply_msg = msg.get("reply_to_message")
        if not reply_msg:
            tg_send(chat, "Ответь на сообщение пользователя командой /мут [сек]"); return
        target = reply_msg["from"]["id"]
        parts = text.split()
        secs = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 300
        tg_restrict(chat, target, secs)
        tg_send(chat, f"🔇 Пользователь замьючен на {secs} сек."); return

    # /пред
    if cmd == "/пред":
        if not is_admin(chat, uid):
            tg_send(chat, "❌ Только для администраторов"); return
        reply_msg = msg.get("reply_to_message")
        if not reply_msg:
            tg_send(chat, "Ответь на сообщение пользователя командой /пред"); return
        target = reply_msg["from"]["id"]
        w = inc_warn(target)
        tg_send(chat, f"⚠️ Предупреждение выдано! Всего: {w}")
        if w >= 3:
            tg_restrict(chat, target, 600)
            tg_send(chat, f"🔇 3 предупреждения — автомут на 10 минут")
        return

    # /настройки
    if cmd == "/настройки":
        s = ensure_settings(chat)
        tg_send(chat,
            f"⚙️ Настройки чата:\n"
            f"🔗 Антиссылки: {'вкл' if s['anti_links'] else 'выкл'}\n"
            f"🤬 Антимат: {'вкл' if s['anti_swear'] else 'выкл'}\n"
            f"📢 Антикапс: {int(s['caps_threshold']*100)}%\n"
            f"🔄 Антиспам: {s['spam_repeat']} повторов\n"
            f"🔇 Автомут: {s['auto_mute']} сек"
        ); return

# ─────────────────────────────────────────
#  ОБРАБОТКА ТЕКСТА (не команды)
# ─────────────────────────────────────────
def handle_text(msg):
    text  = (msg.get("text") or msg.get("caption") or "").strip()
    if not text: return

    chat  = msg["chat"]["id"]
    user  = msg.get("from", {})
    uid   = user.get("id")
    uname = user.get("first_name") or user.get("username") or str(uid)
    s     = ensure_settings(chat)

    # --- Модерация ---
    if s.get("anti_links") and contains_link(text) and not is_admin(chat, uid):
        tg_delete(chat, msg["message_id"])
        tg_send(chat, f"🚫 {uname}, ссылки запрещены!"); return

    if s.get("anti_swear") and contains_swear(text, s.get("swear_list", [])) and not is_admin(chat, uid):
        tg_delete(chat, msg["message_id"])
        tg_send(chat, f"🤐 {uname}, без мата пожалуйста!"); return

    if len(text) >= 7 and caps_ratio(text) > s.get("caps_threshold", 0.7) and not is_admin(chat, uid):
        tg_delete(chat, msg["message_id"])
        tg_send(chat, f"📢 {uname}, не кричи!"); return

    if spam_check(chat, uid, text, s.get("spam_repeat", 3)) and not is_admin(chat, uid):
        tg_restrict(chat, uid, s.get("auto_mute", 300))
        tg_send(chat, f"🔇 {uname} замьючен за спам"); return

    # --- Игра: угадай число ---
    if str(chat) in DATA.get("guesses", {}):
        st = DATA["guesses"][str(chat)]
        try:
            g = int(text.strip())
            st["attempts"] -= 1
            if g == st["number"]:
                add_balance(uid, 50)
                tg_send(chat, f"🎉 {uname} угадал число {g}! +50 💰")
                DATA["guesses"].pop(str(chat), None)
                save_data(DATA); return
            elif st["attempts"] <= 0:
                tg_send(chat, f"💀 Попытки кончились. Загадал: {st['number']}")
                DATA["guesses"].pop(str(chat), None)
                save_data(DATA); return
            else:
                hint = "меньше ⬇️" if g > st["number"] else "больше ⬆️"
                tg_send(chat, f"❌ Нет, моё число {hint}. Осталось {st['attempts']} попыток")
                save_data(DATA); return
        except ValueError:
            pass

    # --- Упоминание бота по имени ---
    sname = s.get("bot_name") or ""
    triggered = False
    q = None

    if sname and text.lower().startswith(sname.lower()):
        triggered = True
        q = text[len(sname):].strip()
    if not triggered and BOT_USERNAME and ("@" + BOT_USERNAME) in text.lower():
        triggered = True
        q = re.sub(r"@" + re.escape(BOT_USERNAME), "", text, flags=re.I).strip()

    if triggered:
        if not q:
            tg_send(chat, "Да? Спроси меня что-нибудь 😊"); return
        tg_send(chat, "🤔 Думаю...")
        tg_send(chat, groq_chat(uid, q)); return

    # --- Личные сообщения — всегда отвечаем через ИИ ---
    if msg["chat"]["type"] == "private":
        tg_send(chat, "🤔 Думаю...")
        tg_send(chat, groq_chat(uid, text))

# ─────────────────────────────────────────
#  ОСНОВНОЙ ЦИКЛ
# ─────────────────────────────────────────
def process_update(upd):
    if "message" not in upd:
        return
    m = upd["message"]
    text = m.get("text", "")
    if text.startswith("/"):
        handle_command(m)
    else:
        handle_text(m)

def get_updates(offset):
    try:
        r = requests.get(API + "getUpdates",
                         params={"timeout": 30, "offset": offset}, timeout=40)
        return r.json()
    except Exception as e:
        print("get_updates error:", e)
        return {"ok": False, "result": []}

def main():
    print("🤖 Бот запущен!")

    # Запускаем проверку напоминаний в отдельном потоке
    t = threading.Thread(target=check_reminders, daemon=True)
    t.start()

    offset = load_offset()
    while True:
        try:
            res = get_updates(offset)
            if not res.get("ok"):
                time.sleep(2); continue
            for u in res.get("result", []):
                offset = max(offset, u["update_id"] + 1)
                save_offset(offset)
                try:
                    process_update(u)
                except Exception as e:
                    print("process_update error:", e)
        except Exception as e:
            print("main loop error:", e)
            time.sleep(2)

# ─────────────────────────────────────────
#  ВЕБ-СЕРВЕР для UptimeRobot (keep-alive)
# ─────────────────────────────────────────
from http.server import HTTPServer, BaseHTTPRequestHandler

class PingHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot is alive!")
    def log_message(self, format, *args):
        pass

def run_server():
    server = HTTPServer(("0.0.0.0", 8080), PingHandler)
    server.serve_forever()

if __name__ == "__main__":
    t1 = threading.Thread(target=run_server, daemon=True)
    t1.start()
    print("🌐 Веб-сервер запущен на порту 8080")
    main()
