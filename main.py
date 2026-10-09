from fastapi import FastAPI, Form
from fastapi.responses import HTMLResponse, RedirectResponse
import uvicorn
import sqlite3
import json

app = FastAPI()

# ================= 1. БАЗА ДАННЫХ И МИГРАЦИИ =================
def init_db():
    with sqlite3.connect("calories.db") as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS history (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, cal INTEGER NOT NULL)")
        conn.execute("CREATE TABLE IF NOT EXISTS settings (id INTEGER PRIMARY KEY CHECK (id = 1), daily_goal INTEGER NOT NULL)")
        conn.execute("CREATE TABLE IF NOT EXISTS friends (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL)")
        conn.execute("""CREATE TABLE IF NOT EXISTS archive (
            id INTEGER PRIMARY KEY AUTOINCREMENT, date TIMESTAMP DEFAULT CURRENT_TIMESTAMP, 
            cal INTEGER, p INTEGER, f INTEGER, c INTEGER, water INTEGER
        )""")
        conn.execute("""CREATE TABLE IF NOT EXISTS weight_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT, date TIMESTAMP DEFAULT CURRENT_TIMESTAMP, weight REAL
        )""")
        conn.execute("""CREATE TABLE IF NOT EXISTS favorites (
            id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, cal INTEGER, p INTEGER, f INTEGER, c INTEGER
        )""")
        
        conn.execute("INSERT OR IGNORE INTO settings (id, daily_goal) VALUES (1, 2000)")
        
        cursor = conn.execute("SELECT COUNT(*) FROM favorites")
        if cursor.fetchone()[0] == 0:
            conn.execute("INSERT INTO favorites (name, cal, p, f, c) VALUES ('☕ Капучино', 120, 4, 4, 10)")
            conn.execute("INSERT INTO favorites (name, cal, p, f, c) VALUES ('🍌 Банан', 105, 1, 0, 27)")
            conn.execute("INSERT INTO favorites (name, cal, p, f, c) VALUES ('🍳 Яичница (2 шт)', 180, 14, 14, 1)")
        
        cursor = conn.execute("SELECT COUNT(*) FROM weight_history")
        if cursor.fetchone()[0] == 0:
            conn.execute("INSERT INTO weight_history (weight) VALUES (70.0)")
            
        cols = {
            "history": ["p INTEGER DEFAULT 0", "f INTEGER DEFAULT 0", "c INTEGER DEFAULT 0"],
            "settings": [
                "level INTEGER DEFAULT 1", "xp INTEGER DEFAULT 0", "water INTEGER DEFAULT 0", "weight REAL DEFAULT 70.0", "streak INTEGER DEFAULT 0",
                "age INTEGER DEFAULT 20", "height REAL DEFAULT 175.0", "gender TEXT DEFAULT 'male'", "activity REAL DEFAULT 1.2", "goal_type TEXT DEFAULT 'maintain'"
            ]
        }
        for table, columns in cols.items():
            for col in columns:
                try: conn.execute(f"ALTER TABLE {table} ADD COLUMN {col}")
                except sqlite3.OperationalError: pass
init_db()


# ================= 2. ЛОГИКА И СИСТЕМА =================
def get_user_level(total_meals: int):
    xp_total = total_meals * 15
    level = (xp_total // 100) + 1
    current_xp = xp_total % 100
    titles = [(20, "Атлет"), (10, "Спортсмен"), (5, "Любитель ЗОЖ"), (3, "Начинающий"), (1, "Новичок")]
    title = next((t for lvl, t in titles if level >= lvl), "Новичок")
    return level, current_xp, title

def check_achievements(cal, p, f, c, water, goal):
    return [
        {"id": "goal", "icon": "🎯", "name": "Цель достигнута", "desc": "Норма калорий", "unlocked": goal * 0.9 <= cal <= goal * 1.1},
        {"id": "water", "icon": "💧", "name": "Водный баланс", "desc": "Выпито 1.5 л", "unlocked": water >= 1500},
        {"id": "protein", "icon": "🥚", "name": "Протеин", "desc": "Собрано 100г белка", "unlocked": p >= 100},
        {"id": "over", "icon": "📈", "name": "Профицит", "desc": "Превышение на 500+", "unlocked": cal >= goal + 500}
    ]

def apply_auto_emoji(name: str):
    if any(char in name for char in "🍕🍔🌭🥞🍳🥚🧀🥩🍗🍖🥪🥗🍲🍜🍛🍙🍚🍤🍣🍱🥟🍫🍬🍭🍮🍯🍼☕🍵🥤🧃🧉💧🍎🍌🍓🍇🍉"):
        return name
    nl = name.lower()
    if any(w in nl for w in ["коф", "капуч", "латт", "эспрес"]): return "☕ " + name
    if "вод" in nl: return "💧 " + name
    if any(w in nl for w in ["яйц", "яич", "омлет"]): return "🍳 " + name
    if any(w in nl for w in ["мяс", "кур", "говяд", "свин", "стейк", "котлет"]): return "🥩 " + name
    if any(w in nl for w in ["хлеб", "бутер", "тост", "батон"]): return "🥪 " + name
    if "сыр" in nl: return "🧀 " + name
    if "яблок" in nl: return "🍏 " + name
    if "банан" in nl: return "🍌 " + name
    if "пицц" in nl: return "🍕 " + name
    if any(w in nl for w in ["бург", "мак", "кфс"]): return "🍔 " + name
    if any(w in nl for w in ["шок", "торт", "слад", "десерт", "печен"]): return "🍰 " + name
    if any(w in nl for w in ["рыб", "суши", "ролл"]): return "🍣 " + name
    if any(w in nl for w in ["салат", "овощ"]): return "🥗 " + name
    return "🍽️ " + name


# ================= 3. ГЛОБАЛЬНЫЙ ШАБЛОНИЗАТОР =================
def render_page(title: str, content: str, active_tab: str = "home", scripts: str = ""):
    home_active = "text-emerald-500" if active_tab == "home" else "text-slate-400 hover:text-emerald-500"
    stats_active = "text-emerald-500" if active_tab == "stats" else "text-slate-400 hover:text-emerald-500"
    prof_active = "text-emerald-500" if active_tab == "profile" else "text-slate-400 hover:text-emerald-500"
    
    with sqlite3.connect("calories.db") as conn:
        cursor = conn.execute("SELECT id, name, cal FROM favorites ORDER BY id ASC")
        favs = [{"id": row[0], "name": row[1], "cal": row[2]} for row in cursor.fetchall()]
        
    favs_html = "".join(
        f"""
        <form action="/add_quick/{fav['id']}" method="post" class="shrink-0 m-0">
            <button type="submit" onclick="vibrateBtn()" class="px-3 py-2 bg-slate-100 dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-xl text-xs font-semibold shadow-sm hover:border-emerald-500 transition active:scale-95 flex items-center gap-1.5 text-slate-700 dark:text-slate-200">
                <span class="truncate max-w-[100px]">{fav['name']}</span>
                <span class="text-emerald-500 bg-emerald-50 dark:bg-emerald-900/30 px-1.5 py-0.5 rounded-md text-[10px]">{fav['cal']}</span>
            </button>
        </form>
        """ for fav in favs
    )

    return f"""
    <!DOCTYPE html>
    <html lang="ru" class="antialiased">
    <head>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
        <title>{title}</title>
        <script src="https://cdn.tailwindcss.com"></script>
        <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
        <script src="https://cdn.jsdelivr.net/npm/canvas-confetti@1.6.0/dist/confetti.browser.min.js"></script>
        <script src="https://cdnjs.cloudflare.com/ajax/libs/vanilla-tilt/1.8.0/vanilla-tilt.min.js"></script>
        <script>
            tailwind.config = {{ darkMode: 'media', theme: {{ extend: {{ fontFamily: {{ sans: ['system-ui', '-apple-system', 'sans-serif'] }} }} }} }}
        </script>
        <style>
            body {{ background-color: #f8fafc; -webkit-tap-highlight-color: transparent; overflow: hidden; }}
            @media (prefers-color-scheme: dark) {{ body {{ background-color: #0f172a; }} }}
            .custom-scrollbar::-webkit-scrollbar {{ display: none; }}
            .custom-scrollbar {{ -ms-overflow-style: none; scrollbar-width: none; }}
            @keyframes slideDown {{ 0% {{ transform: translate(-50%, -100%); opacity: 0; }} 100% {{ transform: translate(-50%, 0); opacity: 1; }} }}
            .toast-anim {{ animation: slideDown 0.4s cubic-bezier(0.16, 1, 0.3, 1) forwards; }}
            .macro-bar {{ width: 0%; transition: width 1s ease-out; }}
        </style>
    </head>
    <body class="text-slate-800 dark:text-slate-200 transition-colors duration-300 w-full h-screen flex justify-center">
        
        <div id="toast-container" class="fixed top-4 left-1/2 -translate-x-1/2 z-[200] flex flex-col gap-2 w-[90%] max-w-sm pointer-events-none"></div>

        <div class="w-full max-w-md bg-white dark:bg-[#0a0a0a] h-full flex flex-col relative shadow-2xl border-x border-slate-100 dark:border-slate-800">
            
            <div class="flex-1 overflow-y-auto pb-[100px] px-5 pt-6 custom-scrollbar">
                {content}
            </div>

            <!-- Плавающая кнопка (FAB) справа внизу -->
            <div class="absolute bottom-[90px] right-6 z-50">
                <button onclick="toggleModal(); vibrateBtn()" class="w-14 h-14 bg-emerald-500 hover:bg-emerald-400 text-white rounded-full shadow-[0_8px_20px_rgba(16,185,129,0.5)] flex items-center justify-center transition-transform active:scale-95">
                    <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round"><line x1="12" y1="5" x2="12" y2="19"></line><line x1="5" y1="12" x2="19" y2="12"></line></svg>
                </button>
            </div>

            <!-- Классическое нижнее меню -->
            <div class="absolute bottom-0 w-full h-[75px] bg-white/95 dark:bg-[#0a0a0a]/95 backdrop-blur-xl border-t border-slate-200 dark:border-slate-800 flex justify-around items-center z-40 pb-safe">
                <a href="/" onclick="vibrateBtn()" class="flex flex-col items-center gap-1 transition-colors {home_active} active:scale-95 w-1/3">
                    <svg xmlns="http://www.w3.org/2000/svg" width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="18" height="18" rx="2" ry="2"></rect><line x1="3" y1="9" x2="21" y2="9"></line><line x1="9" y1="21" x2="9" y2="9"></line></svg>
                </a>
                <a href="/stats" onclick="vibrateBtn()" class="flex flex-col items-center gap-1 transition-colors {stats_active} active:scale-95 w-1/3">
                    <svg xmlns="http://www.w3.org/2000/svg" width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="20" x2="18" y2="10"></line><line x1="12" y1="20" x2="12" y2="4"></line><line x1="6" y1="20" x2="6" y2="14"></line></svg>
                </a>
                <a href="/profile" onclick="vibrateBtn()" class="flex flex-col items-center gap-1 transition-colors {prof_active} active:scale-95 w-1/3">
                    <svg xmlns="http://www.w3.org/2000/svg" width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"></path><circle cx="12" cy="7" r="4"></circle></svg>
                </a>
            </div>

            <!-- Bottom Sheet Modal (Меню добавления) -->
            <div id="addModal" class="fixed inset-0 z-[150] hidden">
                <div id="modalOverlay" onclick="toggleModal()" class="absolute inset-0 bg-black/60 opacity-0 transition-opacity duration-300"></div>
                
                <div id="modalContent" class="absolute bottom-0 left-0 w-full bg-white dark:bg-slate-900 rounded-t-3xl transform translate-y-full transition-transform duration-300 shadow-2xl flex flex-col max-h-[85vh]">
                    <div class="w-12 h-1.5 bg-slate-300 dark:bg-slate-700 rounded-full mx-auto mt-4 mb-2"></div>
                    <div class="p-6 overflow-y-auto custom-scrollbar">
                        <h2 class="text-xl font-bold text-slate-900 dark:text-white mb-4">Добавить запись</h2>
                        
                        <form action="/add" method="post" class="mb-6">
                            <div class="flex gap-2 h-14 mb-3">
                                <input type="text" name="food" placeholder="Название блюда" required class="flex-1 min-w-0 px-4 font-bold bg-slate-50 dark:bg-slate-950 border border-slate-200 dark:border-slate-800 rounded-2xl focus:outline-none focus:border-emerald-500 transition text-slate-900 dark:text-white placeholder-slate-400">
                                <input type="number" name="cal" placeholder="Ккал" required class="w-24 shrink-0 min-w-0 px-2 font-black bg-slate-50 dark:bg-slate-950 border border-slate-200 dark:border-slate-800 rounded-2xl focus:outline-none focus:border-emerald-500 transition text-center text-slate-900 dark:text-white placeholder-slate-400">
                            </div>
                            <details class="group bg-slate-50 dark:bg-slate-950 rounded-2xl border border-slate-200 dark:border-slate-800 overflow-hidden mb-4">
                                <summary class="text-xs font-semibold text-slate-500 cursor-pointer list-none px-4 py-3 flex justify-between items-center outline-none">
                                    <span>Указать БЖУ (Опционально)</span>
                                    <span class="text-emerald-500 group-open:rotate-180 transition-transform">▼</span>
                                </summary>
                                <div class="flex gap-2 px-3 pb-3 h-12">
                                    <input type="number" name="p" value="0" placeholder="Белки" class="flex-1 min-w-0 px-2 font-bold bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-700 rounded-xl text-center text-blue-500 focus:border-blue-500 outline-none">
                                    <input type="number" name="f" value="0" placeholder="Жиры" class="flex-1 min-w-0 px-2 font-bold bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-700 rounded-xl text-center text-amber-500 focus:border-amber-500 outline-none">
                                    <input type="number" name="c" value="0" placeholder="Углев" class="flex-1 min-w-0 px-2 font-bold bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-700 rounded-xl text-center text-emerald-500 focus:border-emerald-500 outline-none">
                                </div>
                            </details>
                            <button type="submit" onclick="vibrateBtn()" class="w-full h-14 bg-emerald-500 hover:bg-emerald-600 text-white font-bold uppercase tracking-widest rounded-2xl shadow-[0_8px_15px_rgba(16,185,129,0.3)] transition active:scale-95 flex items-center justify-center gap-2">
                                Сохранить
                            </button>
                        </form>

                        <div>
                            <h3 class="text-xs font-bold text-slate-500 uppercase tracking-wider mb-3">Быстрое добавление</h3>
                            <div class="flex flex-wrap gap-2">
                                {favs_html if favs_html else '<span class="text-xs text-slate-400">Добавьте еду в профиле</span>'}
                                <form action="/add_water" method="post" class="shrink-0 m-0">
                                    <button type="submit" onclick="vibrateBtn()" class="px-3 py-2 bg-blue-50 dark:bg-blue-900/20 border border-blue-200 dark:border-blue-800/50 rounded-xl text-xs font-semibold shadow-sm hover:border-blue-500 transition active:scale-95 flex items-center gap-1.5 text-blue-600 dark:text-blue-400">
                                        💧 Стакан воды (250мл)
                                    </button>
                                </form>
                            </div>
                        </div>
                    </div>
                </div>
            </div>

        </div>

        <script>
            function vibrateBtn() {{ if(navigator.vibrate) navigator.vibrate(20); }}

            function toggleModal() {{
                const modal = document.getElementById('addModal');
                const overlay = document.getElementById('modalOverlay');
                const content = document.getElementById('modalContent');
                
                if (modal.classList.contains('hidden')) {{
                    modal.classList.remove('hidden');
                    void modal.offsetWidth; 
                    overlay.classList.remove('opacity-0');
                    content.classList.remove('translate-y-full');
                }} else {{
                    overlay.classList.add('opacity-0');
                    content.classList.add('translate-y-full');
                    setTimeout(() => modal.classList.add('hidden'), 300);
                }}
            }}

            function showToast(message, type = 'success') {{
                const container = document.getElementById('toast-container');
                const toast = document.createElement('div');
                const bgColor = type === 'success' ? 'bg-emerald-500' : (type === 'info' ? 'bg-blue-500' : 'bg-red-500');
                toast.className = `toast-anim flex items-center gap-3 px-5 py-3.5 rounded-2xl shadow-2xl text-white text-sm font-bold ${{bgColor}} w-full border border-white/20`;
                let icon = type === 'success' ? `<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="20 6 9 17 4 12"></polyline></svg>` : (type === 'info' ? `<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M12 2.69l5.66 5.66a8 8 0 1 1-11.31 0z"></path></svg>` : `<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><line x1="18" y1="6" x2="6" y2="18"></line><line x1="6" y1="6" x2="18" y2="18"></line></svg>`);
                toast.innerHTML = `${{icon}} ${{message}}`;
                container.appendChild(toast);
                setTimeout(() => {{ toast.style.opacity = '0'; toast.style.transform = 'translate(-50%, -20px)'; toast.style.transition = 'all 0.3s'; setTimeout(() => toast.remove(), 300); }}, 3000);
            }}
            
            const urlParams = new URLSearchParams(window.location.search);
            if (urlParams.has('added')) {{ showToast('Запись добавлена', 'success'); }}
            else if (urlParams.has('water')) {{ showToast('+250 мл воды', 'info'); }}
            else if (urlParams.has('deleted')) {{ showToast('Запись удалена', 'error'); }}
            else if (urlParams.has('archived')) {{ confetti({{ particleCount: 150, spread: 90, origin: {{ y: 0.5 }}, colors: ['#10b981', '#3b82f6', '#f59e0b'] }}); showToast('День завершен!', 'success'); }}
            else if (urlParams.has('saved')) {{ showToast('Настройки сохранены', 'success'); }}
            else if (urlParams.has('calculated')) {{ confetti({{ particleCount: 150, spread: 90, origin: {{ y: 0.5 }}, colors: ['#10b981', '#fcd34d'] }}); showToast('Норма пересчитана!', 'success'); }}
            window.history.replaceState({{}}, document.title, window.location.pathname);
            
            VanillaTilt.init(document.querySelectorAll("[data-tilt]"));
        </script>
        {scripts}
    </body>
    </html>
    """

# ================= 4. ГЛАВНАЯ СТРАНИЦА (ДАШБОРД) =================
@app.get("/", response_class=HTMLResponse)
async def get_index():
    with sqlite3.connect("calories.db") as conn:
        cursor = conn.execute("SELECT id, name, cal, p, f, c FROM history ORDER BY id DESC")
        history = [{"id": row[0], "name": row[1], "cal": row[2], "p": row[3], "f": row[4], "c": row[5]} for row in cursor.fetchall()]
        
        cursor = conn.execute("SELECT SUM(cal), SUM(p), SUM(f), SUM(c) FROM history")
        row = cursor.fetchone()
        total_calories, total_p, total_f, total_c = row[0] or 0, row[1] or 0, row[2] or 0, row[3] or 0
        
        cursor = conn.execute("SELECT daily_goal, water FROM settings WHERE id = 1")
        settings_row = cursor.fetchone()
        daily_goal, total_water = settings_row[0], settings_row[1]

    goal_p = int((daily_goal * 0.3) / 4)
    goal_f = int((daily_goal * 0.3) / 9)
    goal_c = int((daily_goal * 0.4) / 4)
    pct_p = min(100, int((total_p / goal_p) * 100)) if goal_p > 0 else 0
    pct_f = min(100, int((total_f / goal_f) * 100)) if goal_f > 0 else 0
    pct_c = min(100, int((total_c / goal_c) * 100)) if goal_c > 0 else 0

    items_html = "".join(
        f"""
        <li class="flex justify-between items-center bg-white dark:bg-slate-800/80 p-4 rounded-2xl border border-slate-100 dark:border-slate-700/50 mb-2 shadow-sm">
            <div class="flex flex-col min-w-0 flex-1">
                <span class="font-bold text-slate-800 dark:text-slate-200 truncate pr-2 text-sm">{item['name']}</span>
                <div class="flex items-center gap-2 mt-1">
                    <span class="text-xs font-black text-emerald-500">{item['cal']} ккал</span>
                    <span class="text-[9px] font-bold text-slate-500 uppercase tracking-widest bg-slate-50 dark:bg-slate-900 px-1.5 py-0.5 rounded-md border border-slate-100 dark:border-slate-700">Б:{item['p']} Ж:{item['f']} У:{item['c']}</span>
                </div>
            </div>
            <form action="/delete/{item['id']}" method="post" class="m-0 flex-shrink-0">
                <button type="submit" onclick="vibrateBtn()" class="text-slate-300 hover:text-red-500 bg-white dark:bg-slate-900 w-10 h-10 rounded-full transition-colors flex items-center justify-center border border-slate-100 dark:border-slate-800">
                    <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="6" x2="6" y2="18"></line><line x1="6" y1="6" x2="18" y2="18"></line></svg>
                </button>
            </form>
        </li>
        """ for item in history
    )
    
    color_class = "text-emerald-500" if total_calories <= daily_goal else "text-red-500"
    
    coach_text = "Идеальный ритм! Идем по графику 🎯"
    coach_color = "text-emerald-500 bg-emerald-50 dark:bg-emerald-900/10 border-emerald-100 dark:border-emerald-900/30"
    if total_calories == 0:
        coach_text = "Двигатель не заведен. Пора поесть! 🔑"; coach_color = "text-slate-500 bg-slate-50 dark:bg-slate-800/50 border-slate-200 dark:border-slate-700"
    elif total_calories > daily_goal:
        coach_text = "ПЕРЕГРЕВ! Лимит превышен 🛑"; coach_color = "text-red-500 bg-red-50 dark:bg-red-900/10 border-red-200 dark:border-red-900/30"
    elif total_water < 750 and total_calories > goal_c * 0.5:
        coach_text = "Критический уровень жидкости! 💧"; coach_color = "text-blue-500 bg-blue-50 dark:bg-blue-900/10 border-blue-200 dark:border-blue-900/30"

    content = f"""
        <div class="flex justify-between items-center mb-6">
            <div>
                <h1 class="text-3xl font-black tracking-tighter text-slate-900 dark:text-white" id="greeting">Панель</h1>
                <p class="text-[10px] font-bold text-slate-500 uppercase tracking-widest mt-1" id="date-display"></p>
            </div>
            <div class="w-10 h-10 bg-slate-100 dark:bg-slate-800 rounded-full flex items-center justify-center shadow-sm">
                <span class="text-lg">K</span>
            </div>
        </div>
        
        <div class="flex items-center gap-3 p-3 rounded-2xl border {coach_color} mb-6 text-xs font-bold shadow-sm transition-colors uppercase tracking-wide">
            <span class="animate-pulse">●</span> {coach_text}
        </div>

        <div class="relative h-[180px] flex justify-center items-end overflow-hidden mb-8">
            <canvas id="calorieChart" class="w-full"></canvas>
            <div class="absolute bottom-2 flex flex-col items-center justify-center pointer-events-none">
                <span class="text-6xl font-black {color_class} tracking-tighter drop-shadow-sm">{total_calories}</span>
                <span class="text-[10px] font-bold mt-1 uppercase tracking-widest text-slate-500 bg-white/50 dark:bg-slate-900/50 px-2 py-0.5 rounded-md">из {daily_goal} ккал</span>
            </div>
        </div>

        <div class="bg-white dark:bg-slate-800 p-6 rounded-3xl mb-8 border border-slate-100 dark:border-slate-700/50 shadow-sm" data-tilt data-tilt-max="2" data-tilt-speed="400">
            <h3 class="text-[10px] font-black text-slate-500 uppercase tracking-widest mb-4">Макронутриенты</h3>
            <div class="mb-4">
                <div class="flex justify-between text-[11px] font-bold mb-1.5"><span class="text-blue-500">БЕЛКИ</span><span class="text-slate-900 dark:text-slate-300">{total_p} / {goal_p} г</span></div>
                <div class="w-full bg-slate-100 dark:bg-slate-900 rounded-full h-2 overflow-hidden"><div id="bar-p" class="bg-blue-500 h-2 rounded-full macro-bar"></div></div>
            </div>
            <div class="mb-4">
                <div class="flex justify-between text-[11px] font-bold mb-1.5"><span class="text-amber-500">ЖИРЫ</span><span class="text-slate-900 dark:text-slate-300">{total_f} / {goal_f} г</span></div>
                <div class="w-full bg-slate-100 dark:bg-slate-900 rounded-full h-2 overflow-hidden"><div id="bar-f" class="bg-amber-500 h-2 rounded-full macro-bar"></div></div>
            </div>
            <div>
                <div class="flex justify-between text-[11px] font-bold mb-1.5"><span class="text-emerald-500">УГЛЕВОДЫ</span><span class="text-slate-900 dark:text-slate-300">{total_c} / {goal_c} г</span></div>
                <div class="w-full bg-slate-100 dark:bg-slate-900 rounded-full h-2 overflow-hidden"><div id="bar-c" class="bg-emerald-500 h-2 rounded-full macro-bar"></div></div>
            </div>
        </div>

        <div class="mb-4">
            <h3 class="text-[11px] font-black text-slate-800 dark:text-slate-200 uppercase tracking-widest mb-4 px-1">Журнал питания</h3>
            <ul class="flex flex-col gap-1">
                {items_html if items_html else '<div class="text-center text-slate-500 py-10 bg-white dark:bg-slate-800/50 rounded-3xl border border-slate-100 dark:border-slate-700/50 text-xs font-bold uppercase tracking-widest">Список пуст</div>'}
            </ul>
        </div>
    """

    scripts = f"""
        <script>
            const hour = new Date().getHours();
            let greeting = 'Панель';
            if (hour >= 5 && hour < 12) greeting = 'Доброе утро ☀️';
            else if (hour >= 12 && hour < 18) greeting = 'Добрый день 👋';
            else if (hour >= 18 && hour < 23) greeting = 'Добрый вечер 🌙';
            document.getElementById('greeting').innerText = greeting;
            
            const options = {{ weekday: 'long', month: 'long', day: 'numeric' }};
            document.getElementById('date-display').innerText = new Date().toLocaleDateString('ru-RU', options);

            setTimeout(() => {{
                document.getElementById('bar-p').style.width = '{pct_p}%';
                document.getElementById('bar-f').style.width = '{pct_f}%';
                document.getElementById('bar-c').style.width = '{pct_c}%';
            }}, 100);

            const ctx = document.getElementById('calorieChart').getContext('2d');
            const consumed = {total_calories};
            const goal = {daily_goal};
            const remaining = Math.max(0, goal - consumed);
            const isDark = window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches;
            const emptyColor = isDark ? '#1e293b' : '#f1f5f9';
            const highlightColor = consumed > goal ? '#ef4444' : '#10b981';

            new Chart(ctx, {{
                type: 'doughnut',
                data: {{
                    labels: ['Съедено', 'Осталось'],
                    datasets: [{{
                        data: [consumed, remaining],
                        backgroundColor: [highlightColor, emptyColor],
                        borderWidth: 0,
                        borderRadius: consumed > goal ? 0 : 20
                    }}]
                }},
                options: {{ 
                    rotation: 270, circumference: 180, cutout: '82%',
                    responsive: true, maintainAspectRatio: false, 
                    animation: {{ animateScale: true, animateRotate: true, duration: 1500, easing: 'easeOutExpo' }}, 
                    plugins: {{ legend: {{ display: false }}, tooltip: {{ enabled: false }} }} 
                }}
            }});
        </script>
    """
    return render_page("Дневник Питания", content, active_tab="home", scripts=scripts)


# ================= 5. АНАЛИТИКА И ПРОФИЛЬ =================
@app.get("/stats", response_class=HTMLResponse)
async def get_stats():
    with sqlite3.connect("calories.db") as conn:
        cursor = conn.execute("SELECT daily_goal FROM settings WHERE id = 1")
        daily_goal = cursor.fetchone()[0]
        
        cursor = conn.execute("SELECT date, cal, p, f, c, water FROM archive ORDER BY id DESC LIMIT 7")
        archive_data = cursor.fetchall()
        
        cursor = conn.execute("SELECT date, weight FROM weight_history ORDER BY id DESC LIMIT 14")
        weight_data = cursor.fetchall()

    chart_labels = [row[0].split()[0][5:] for row in reversed(archive_data)]
    chart_data = [row[1] for row in reversed(archive_data)]
    w_labels = [row[0].split()[0][5:] for row in reversed(weight_data)]
    w_data = [row[1] for row in reversed(weight_data)]

    archive_html = "".join(
        f"""
        <div class="bg-white dark:bg-slate-800 p-5 rounded-3xl border border-slate-100 dark:border-slate-700/50 mb-3 shadow-sm">
            <div class="flex justify-between items-center mb-3">
                <span class="font-black text-slate-800 dark:text-slate-200 text-sm tracking-wider">{row[0].split()[0]}</span>
                <span class="text-sm font-black text-emerald-500 bg-emerald-50 dark:bg-emerald-900/20 px-2 py-1 rounded-lg">{row[1]} ккал</span>
            </div>
            <div class="flex justify-between text-[10px] font-bold text-slate-500 uppercase tracking-widest">
                <span class="bg-slate-50 dark:bg-slate-900 px-2 py-1 rounded-md border border-slate-100 dark:border-slate-700">Б:{row[2]} Ж:{row[3]} У:{row[4]}</span>
                <span class="text-blue-500 bg-blue-50 dark:bg-blue-900/30 px-2 py-1 rounded-md">💧 {row[5]} мл</span>
            </div>
        </div>
        """ for row in archive_data
    )

    content = f"""
        <div class="flex items-center justify-between mb-6">
            <h1 class="text-3xl font-black tracking-tighter text-slate-900 dark:text-white uppercase italic">Аналитика<span class="text-emerald-500">.</span></h1>
        </div>

        <div class="bg-white dark:bg-slate-800 p-6 rounded-3xl mb-6 border border-slate-100 dark:border-slate-700/50 shadow-sm" data-tilt data-tilt-max="2">
            <h3 class="text-[10px] font-black text-slate-500 uppercase tracking-widest mb-4">График калорий (7 дней)</h3>
            <div class="relative h-[180px] w-full"><canvas id="calChart"></canvas></div>
        </div>
        
        <div class="bg-white dark:bg-slate-800 p-6 rounded-3xl mb-8 border border-slate-100 dark:border-slate-700/50 shadow-sm" data-tilt data-tilt-max="2">
            <h3 class="text-[10px] font-black text-slate-500 uppercase tracking-widest mb-4">Динамика веса</h3>
            <div class="relative h-[180px] w-full"><canvas id="weightChart"></canvas></div>
        </div>

        <div class="mb-4">
            <h3 class="text-[11px] font-black text-slate-800 dark:text-slate-200 uppercase tracking-widest mb-4 px-1">Архив дней</h3>
            <div class="flex flex-col">
                {archive_html if archive_html else '<div class="text-center text-slate-500 py-10 bg-white dark:bg-slate-800/50 rounded-3xl border border-slate-100 dark:border-slate-700/50 text-xs font-bold uppercase tracking-widest">Архив пуст</div>'}
            </div>
        </div>
    """

    scripts = f"""
        <script>
            const isDark = window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches;
            const gridColor = isDark ? '#1e293b' : '#f1f5f9';
            const tickColor = isDark ? '#64748b' : '#94a3b8';

            new Chart(document.getElementById('calChart').getContext('2d'), {{
                type: 'bar',
                data: {{ labels: {json.dumps(chart_labels)}, datasets: [{{ label: 'Ккал', data: {json.dumps(chart_data)}, backgroundColor: '#10b981', borderRadius: 8, barPercentage: 0.5 }}] }},
                options: {{ responsive: true, maintainAspectRatio: false, plugins: {{ legend: {{ display: false }} }}, scales: {{ y: {{ grid: {{ color: gridColor }}, ticks: {{ color: tickColor, font: {{size: 10, weight: 'bold'}} }} }}, x: {{ grid: {{ display: false }}, ticks: {{ color: tickColor, font: {{size: 10, weight: 'bold'}} }} }} }} }}
            }});
            
            new Chart(document.getElementById('weightChart').getContext('2d'), {{
                type: 'line',
                data: {{ labels: {json.dumps(w_labels)}, datasets: [{{ label: 'Вес (кг)', data: {json.dumps(w_data)}, borderColor: '#3b82f6', backgroundColor: 'rgba(59, 130, 246, 0.15)', tension: 0.4, fill: true, pointRadius: 5, pointBackgroundColor: '#3b82f6', borderWidth: 3 }}] }},
                options: {{ responsive: true, maintainAspectRatio: false, plugins: {{ legend: {{ display: false }} }}, scales: {{ y: {{ grid: {{ color: gridColor }}, ticks: {{ color: tickColor, font: {{size: 10, weight: 'bold'}} }} }}, x: {{ grid: {{ display: false }}, ticks: {{ color: tickColor, font: {{size: 10, weight: 'bold'}} }} }} }} }}
            }});
        </script>
    """
    return render_page("Аналитика", content, active_tab="stats", scripts=scripts)


@app.get("/profile", response_class=HTMLResponse)
async def get_profile():
    with sqlite3.connect("calories.db") as conn:
        cursor = conn.execute("SELECT COUNT(*), SUM(cal), SUM(p), SUM(f), SUM(c) FROM history")
        stats = cursor.fetchone()
        t_meals, t_cal, t_p, t_f, t_c = stats[0] or 0, stats[1] or 0, stats[2] or 0, stats[3] or 0, stats[4] or 0
        
        cursor = conn.execute("SELECT daily_goal, water, streak, age, height, gender, activity, goal_type FROM settings WHERE id = 1")
        settings = cursor.fetchone()
        daily_goal, t_water, streak = settings[0], settings[1], settings[2]
        age, height, gender, activity, goal_type = settings[3], settings[4], settings[5], settings[6], settings[7]
        
        cursor = conn.execute("SELECT weight FROM weight_history ORDER BY id DESC LIMIT 1")
        w_row = cursor.fetchone()
        current_weight = w_row[0] if w_row else 70.0
        
        cursor = conn.execute("SELECT id, name, cal FROM favorites ORDER BY id ASC")
        favorites = [{"id": row[0], "name": row[1], "cal": row[2]} for row in cursor.fetchall()]

    level, current_xp, title = get_user_level(t_meals)
    achievements = check_achievements(t_cal, t_p, t_f, t_c, t_water, daily_goal)
    
    ach_html = "".join(
        f"""
        <div data-tilt data-tilt-max="10" data-tilt-glare data-tilt-max-glare="0.2" class="flex flex-col items-center p-4 rounded-3xl border { 'border-emerald-200 dark:border-emerald-900/50 bg-emerald-50 dark:bg-emerald-900/10 shadow-[0_5px_15px_rgba(16,185,129,0.1)]' if ach['unlocked'] else 'border-slate-100 dark:border-slate-800 bg-slate-50 dark:bg-slate-800/50 grayscale opacity-60' }">
            <div class="text-4xl mb-2">{ach['icon']}</div>
            <span class="text-[10px] font-black uppercase text-slate-800 dark:text-slate-200 mb-1 text-center tracking-wider">{ach['name']}</span>
            <span class="text-[9px] font-semibold text-slate-500 text-center leading-tight">{ach['desc']}</span>
        </div>
        """ for ach in achievements
    )
    
    fav_manager = "".join(
        f"""
        <li class="flex justify-between items-center py-3 border-b border-slate-100 dark:border-slate-700/50 last:border-0">
            <span class="text-sm font-bold text-slate-800 dark:text-slate-200">{fav['name']} <span class="text-emerald-500 text-[10px] bg-emerald-50 dark:bg-emerald-900/30 px-1.5 py-0.5 rounded-md">{fav['cal']}</span></span>
            <form action="/delete_fav/{fav['id']}" method="post" class="m-0"><button type="submit" class="text-slate-400 hover:text-red-500 px-3 py-1.5 text-[10px] font-black uppercase tracking-widest bg-slate-50 dark:bg-slate-900 rounded-lg">Del</button></form>
        </li>
        """ for fav in favorites
    )

    g_m = "selected" if gender == "male" else ""
    g_f = "selected" if gender == "female" else ""
    a_12 = "selected" if activity == 1.2 else ""
    a_13 = "selected" if activity == 1.375 else ""
    a_15 = "selected" if activity == 1.55 else ""
    a_17 = "selected" if activity == 1.725 else ""
    a_19 = "selected" if activity == 1.9 else ""
    gt_l = "selected" if goal_type == "lose" else ""
    gt_m = "selected" if goal_type == "maintain" else ""
    gt_g = "selected" if goal_type == "gain" else ""

    content = f"""
        <div class="flex items-center justify-between mb-6">
            <h1 class="text-3xl font-black tracking-tighter text-slate-900 dark:text-white uppercase italic">Профиль<span class="text-emerald-500">.</span></h1>
            <div class="flex items-center gap-2 bg-orange-50 dark:bg-orange-900/20 border border-orange-100 dark:border-orange-900/30 px-3 py-1.5 rounded-xl shadow-[0_0_15px_rgba(249,115,22,0.1)]">
                <span class="text-lg">🔥</span>
                <div class="flex flex-col">
                    <span class="text-[8px] font-black text-orange-600 dark:text-orange-400 uppercase tracking-widest leading-none">Стрик</span>
                    <span class="text-sm font-black text-slate-900 dark:text-white leading-none mt-0.5">{streak}</span>
                </div>
            </div>
        </div>

        <div data-tilt data-tilt-max="3" data-tilt-glare data-tilt-max-glare="0.2" class="p-6 bg-slate-900 rounded-3xl mb-8 shadow-2xl border border-slate-800 relative overflow-hidden">
            <div class="absolute top-0 right-0 w-40 h-40 bg-emerald-500/20 rounded-full blur-3xl pointer-events-none"></div>
            <div class="flex items-center gap-5 relative z-10 pointer-events-none">
                <div class="w-20 h-20 bg-[#050505] border border-emerald-500/50 rounded-2xl flex items-center justify-center text-4xl font-black text-emerald-400 shadow-[0_0_20px_rgba(16,185,129,0.3)]">K</div>
                <div>
                    <h2 class="text-2xl font-black text-white uppercase tracking-widest">Кактак</h2>
                    <p class="text-emerald-400 font-bold text-[9px] uppercase tracking-[0.2em] mt-1 bg-emerald-900/30 inline-block px-2 py-1 rounded-md border border-emerald-500/20">{title}</p>
                </div>
            </div>
            <div class="mt-8 relative z-10 pointer-events-none">
                <div class="flex justify-between items-end mb-2">
                    <span class="text-[9px] font-bold text-slate-400 uppercase tracking-widest">Уровень {level}</span>
                    <span class="text-[10px] font-black text-emerald-400">{current_xp}/100 XP</span>
                </div>
                <div class="w-full h-2 bg-slate-800 rounded-full overflow-hidden border border-slate-700/50">
                    <div class="h-full bg-emerald-500 rounded-full shadow-[0_0_10px_rgba(16,185,129,1)] macro-bar" id="xp-bar"></div>
                </div>
            </div>
        </div>

        <div class="mb-8">
            <h3 class="text-[10px] font-black text-slate-500 uppercase tracking-widest mb-4 px-1">Умный Калькулятор (BMR)</h3>
            <details class="group bg-white dark:bg-slate-800 rounded-3xl border border-slate-100 dark:border-slate-700/50 shadow-sm overflow-hidden mb-4">
                <summary class="text-xs font-bold text-slate-800 dark:text-slate-200 cursor-pointer list-none p-5 flex items-center justify-between outline-none">
                    <div class="flex items-center gap-3">
                        <span class="text-xl">🤖</span> Рассчитать норму автоматически
                    </div>
                    <span class="text-emerald-500 font-black group-open:rotate-180 transition-transform duration-300">▼</span>
                </summary>
                <div class="px-5 pb-5">
                    <form action="/calculate_goal" method="post" class="flex flex-col gap-4">
                        <div class="flex gap-3">
                            <div class="flex-1"><label class="text-[9px] font-bold text-slate-400 uppercase tracking-widest mb-1.5 block">Пол</label>
                                <select name="gender" class="w-full h-12 px-3 font-bold bg-slate-50 dark:bg-slate-900 border border-slate-200 dark:border-slate-700 rounded-xl focus:border-emerald-500 text-xs dark:text-white outline-none"><option value="male" {g_m}>Мужской</option><option value="female" {g_f}>Женский</option></select>
                            </div>
                            <div class="flex-1"><label class="text-[9px] font-bold text-slate-400 uppercase tracking-widest mb-1.5 block">Возраст</label>
                                <input type="number" name="age" value="{age}" required class="w-full h-12 px-4 font-bold bg-slate-50 dark:bg-slate-900 border border-slate-200 dark:border-slate-700 rounded-xl focus:border-emerald-500 text-xs dark:text-white outline-none">
                            </div>
                        </div>
                        <div class="flex gap-3">
                            <div class="flex-1"><label class="text-[9px] font-bold text-slate-400 uppercase tracking-widest mb-1.5 block">Рост (см)</label>
                                <input type="number" name="height" value="{height}" required class="w-full h-12 px-4 font-bold bg-slate-50 dark:bg-slate-900 border border-slate-200 dark:border-slate-700 rounded-xl focus:border-emerald-500 text-xs dark:text-white outline-none">
                            </div>
                            <div class="flex-1"><label class="text-[9px] font-bold text-slate-400 uppercase tracking-widest mb-1.5 block">Вес (кг)</label>
                                <input type="number" step="0.1" name="weight" value="{current_weight}" required class="w-full h-12 px-4 font-bold bg-slate-50 dark:bg-slate-900 border border-slate-200 dark:border-slate-700 rounded-xl focus:border-emerald-500 text-xs dark:text-white outline-none">
                            </div>
                        </div>
                        <div><label class="text-[9px] font-bold text-slate-400 uppercase tracking-widest mb-1.5 block">Активность</label>
                            <select name="activity" class="w-full h-12 px-3 font-bold bg-slate-50 dark:bg-slate-900 border border-slate-200 dark:border-slate-700 rounded-xl focus:border-emerald-500 text-xs dark:text-white outline-none"><option value="1.2" {a_12}>Минимум (Офис)</option><option value="1.375" {a_13}>Легкая (1-3 тренировки)</option><option value="1.55" {a_15}>Средняя (3-5 тренировок)</option><option value="1.725" {a_17}>Высокая (6-7 тренировок)</option><option value="1.9" {a_19}>Экстрим (Физ. работа)</option></select>
                        </div>
                        <div><label class="text-[9px] font-bold text-slate-400 uppercase tracking-widest mb-1.5 block">Цель</label>
                            <select name="goal_type" class="w-full h-12 px-3 font-bold bg-slate-50 dark:bg-slate-900 border border-slate-200 dark:border-slate-700 rounded-xl focus:border-emerald-500 text-xs dark:text-white outline-none"><option value="lose" {gt_l}>Снижение веса (-500 ккал)</option><option value="maintain" {gt_m}>Поддержание формы</option><option value="gain" {gt_g}>Набор массы (+500 ккал)</option></select>
                        </div>
                        <button type="submit" onclick="vibrateBtn()" class="w-full mt-2 h-14 bg-slate-900 dark:bg-white text-white dark:text-slate-900 font-black uppercase tracking-widest rounded-xl shadow-sm transition active:scale-95 text-xs">Применить параметры</button>
                    </form>
                </div>
            </details>
            
            <form action="/update_settings" method="post" class="bg-white dark:bg-slate-800 p-5 rounded-3xl border border-slate-100 dark:border-slate-700/50 shadow-sm flex gap-3">
                <div class="flex-1">
                    <label class="text-[9px] font-bold text-slate-400 uppercase tracking-widest mb-1.5 block">Ручной лимит</label>
                    <input type="number" name="new_goal" value="{daily_goal}" required class="w-full h-12 px-4 font-bold bg-slate-50 dark:bg-slate-900 border border-slate-200 dark:border-slate-700 rounded-xl outline-none text-slate-900 dark:text-white">
                </div>
                <div class="flex-1">
                    <label class="text-[9px] font-bold text-slate-400 uppercase tracking-widest mb-1.5 block">Текущий вес</label>
                    <input type="number" step="0.1" name="new_weight" value="{current_weight}" required class="w-full h-12 px-4 font-bold bg-slate-50 dark:bg-slate-900 border border-slate-200 dark:border-slate-700 rounded-xl outline-none text-slate-900 dark:text-white">
                </div>
                <button type="submit" onclick="vibrateBtn()" class="mt-5 px-5 h-12 bg-emerald-500 text-white font-black uppercase rounded-xl transition active:scale-95 text-xs">Save</button>
            </form>
        </div>
        
        <div class="mb-8">
            <h3 class="text-[10px] font-black text-slate-500 uppercase tracking-widest mb-4 px-1">Менеджер Избранного</h3>
            <div class="bg-white dark:bg-slate-800 p-5 rounded-3xl border border-slate-100 dark:border-slate-700/50 shadow-sm">
                <form action="/add_fav" method="post" class="flex gap-2 mb-4">
                    <input type="text" name="name" placeholder="Название" required class="flex-1 min-w-0 px-4 h-12 font-bold bg-slate-50 dark:bg-slate-900 border border-slate-200 dark:border-slate-700 rounded-xl text-xs outline-none">
                    <input type="number" name="cal" placeholder="Ккал" required class="w-20 min-w-0 px-2 h-12 font-black bg-slate-50 dark:bg-slate-900 border border-slate-200 dark:border-slate-700 rounded-xl text-xs text-center outline-none">
                    <button type="submit" onclick="vibrateBtn()" class="px-5 bg-emerald-500 text-white rounded-xl text-xl font-bold hover:bg-emerald-600 active:scale-95 transition shadow-sm">+</button>
                </form>
                <ul class="flex flex-col">{fav_manager if fav_manager else '<span class="text-xs text-slate-400 text-center py-4 font-bold uppercase tracking-widest">Нет сохраненной еды</span>'}</ul>
            </div>
        </div>
        
        <div class="mb-8">
            <h3 class="text-[10px] font-black text-slate-500 uppercase tracking-widest mb-4 px-1">Достижения (Сегодня)</h3>
            <div class="grid grid-cols-2 gap-3 mb-10">
                {ach_html}
            </div>
            
            <form action="/archive_day" method="post">
                <button type="submit" onclick="vibrateBtn()" class="w-full h-16 bg-red-500/10 text-red-500 font-black uppercase tracking-widest rounded-2xl transition active:scale-95 text-xs flex items-center justify-center gap-2 border border-red-500/30 shadow-[0_0_15px_rgba(239,68,68,0.1)] hover:bg-red-500/20">
                    <svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"></path><polyline points="7 10 12 15 17 10"></polyline><line x1="12" y1="15" x2="12" y2="3"></line></svg>
                    Завершить день в архив
                </button>
            </form>
            <p class="text-[10px] text-center text-slate-500 font-bold uppercase tracking-widest mt-4 px-4">Данные будут очищены и перенесены в Аналитику.</p>
        </div>
    """
    
    scripts = f"""
        <script>
            setTimeout(() => {{
                document.getElementById('xp-bar').style.width = '{current_xp}%';
            }}, 100);
        </script>
    """
    return render_page("Профиль", content, active_tab="profile", scripts=scripts)


# ================= 7. РОУТЫ (API) =================
@app.post("/add")
async def add_record(food: str = Form(...), cal: int = Form(...), p: int = Form(0), f: int = Form(0), c: int = Form(0)):
    food = apply_auto_emoji(food)
    with sqlite3.connect("calories.db") as conn:
        conn.execute("INSERT INTO history (name, cal, p, f, c) VALUES (?, ?, ?, ?, ?)", (food, cal, p, f, c))
    return RedirectResponse(url="/?added=1", status_code=303)

@app.post("/add_quick/{fav_id}")
async def add_quick(fav_id: int):
    with sqlite3.connect("calories.db") as conn:
        cursor = conn.execute("SELECT name, cal, p, f, c FROM favorites WHERE id = ?", (fav_id,))
        fav = cursor.fetchone()
        if fav:
            conn.execute("INSERT INTO history (name, cal, p, f, c) VALUES (?, ?, ?, ?, ?)", fav)
    return RedirectResponse(url="/?added=1", status_code=303)

@app.post("/add_fav")
async def add_fav(name: str = Form(...), cal: int = Form(...)):
    name = apply_auto_emoji(name)
    with sqlite3.connect("calories.db") as conn:
        conn.execute("INSERT INTO favorites (name, cal, p, f, c) VALUES (?, ?, 0, 0, 0)", (name, cal))
    return RedirectResponse(url="/profile?saved=1", status_code=303)

@app.post("/delete_fav/{fav_id}")
async def delete_fav(fav_id: int):
    with sqlite3.connect("calories.db") as conn:
        conn.execute("DELETE FROM favorites WHERE id = ?", (fav_id,))
    return RedirectResponse(url="/profile?saved=1", status_code=303)

@app.post("/add_water")
async def add_water():
    with sqlite3.connect("calories.db") as conn:
        conn.execute("UPDATE settings SET water = water + 250 WHERE id = 1")
    return RedirectResponse(url="/?water=1", status_code=303)

@app.post("/delete/{item_id}")
async def delete_record(item_id: int):
    with sqlite3.connect("calories.db") as conn:
        conn.execute("DELETE FROM history WHERE id = ?", (item_id,))
    return RedirectResponse(url="/?deleted=1", status_code=303)

@app.post("/update_settings")
async def update_settings(new_goal: int = Form(...), new_weight: float = Form(...)):
    with sqlite3.connect("calories.db") as conn:
        conn.execute("UPDATE settings SET daily_goal = ? WHERE id = 1", (new_goal,))
        cursor = conn.execute("SELECT weight FROM weight_history ORDER BY id DESC LIMIT 1")
        last_weight = cursor.fetchone()
        if not last_weight or last_weight[0] != new_weight:
            conn.execute("UPDATE settings SET weight = ? WHERE id = 1", (new_weight,))
            conn.execute("INSERT INTO weight_history (weight) VALUES (?)", (new_weight,))
    return RedirectResponse(url="/profile?saved=1", status_code=303)

@app.post("/calculate_goal")
async def calculate_goal(
    gender: str = Form(...), age: int = Form(...), height: float = Form(...),
    weight: float = Form(...), activity: float = Form(...), goal_type: str = Form(...)
):
    if gender == "male": bmr = (10 * weight) + (6.25 * height) - (5 * age) + 5
    else: bmr = (10 * weight) + (6.25 * height) - (5 * age) - 161
    tdee = bmr * activity
    if goal_type == "lose": final_goal = int(tdee - 500)
    elif goal_type == "gain": final_goal = int(tdee + 500)
    else: final_goal = int(tdee)

    with sqlite3.connect("calories.db") as conn:
        conn.execute("""
            UPDATE settings SET daily_goal = ?, weight = ?, age = ?, height = ?, gender = ?, activity = ?, goal_type = ? WHERE id = 1
        """, (final_goal, weight, age, height, gender, activity, goal_type))
        cursor = conn.execute("SELECT weight FROM weight_history ORDER BY id DESC LIMIT 1")
        last_weight = cursor.fetchone()
        if not last_weight or last_weight[0] != weight:
            conn.execute("INSERT INTO weight_history (weight) VALUES (?)", (weight,))
    return RedirectResponse(url="/profile?calculated=1", status_code=303)

@app.post("/archive_day")
async def archive_day():
    with sqlite3.connect("calories.db") as conn:
        cursor = conn.execute("SELECT SUM(cal), SUM(p), SUM(f), SUM(c) FROM history")
        stats = cursor.fetchone()
        t_cal, t_p, t_f, t_c = stats[0] or 0, stats[1] or 0, stats[2] or 0, stats[3] or 0
        cursor = conn.execute("SELECT water FROM settings WHERE id = 1")
        t_water = cursor.fetchone()[0]

        if t_cal > 0 or t_water > 0:
            conn.execute("INSERT INTO archive (cal, p, f, c, water) VALUES (?, ?, ?, ?, ?)", (t_cal, t_p, t_f, t_c, t_water))
            conn.execute("UPDATE settings SET streak = streak + 1 WHERE id = 1")

        conn.execute("DELETE FROM history")
        conn.execute("UPDATE settings SET water = 0 WHERE id = 1")
    return RedirectResponse(url="/profile?archived=1", status_code=303)

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)