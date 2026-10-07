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
            id INTEGER PRIMARY KEY AUTOINCREMENT, 
            date TIMESTAMP DEFAULT CURRENT_TIMESTAMP, 
            cal INTEGER, p INTEGER, f INTEGER, c INTEGER, water INTEGER
        )""")
        # Новые таблицы для 9.0
        conn.execute("""CREATE TABLE IF NOT EXISTS weight_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT, 
            date TIMESTAMP DEFAULT CURRENT_TIMESTAMP, 
            weight REAL
        )""")
        conn.execute("""CREATE TABLE IF NOT EXISTS favorites (
            id INTEGER PRIMARY KEY AUTOINCREMENT, 
            name TEXT, cal INTEGER, p INTEGER, f INTEGER, c INTEGER
        )""")
        
        conn.execute("INSERT OR IGNORE INTO settings (id, daily_goal) VALUES (1, 2000)")
        
        # Сид начальных данных для избранного, если таблица пуста
        cursor = conn.execute("SELECT COUNT(*) FROM favorites")
        if cursor.fetchone()[0] == 0:
            conn.execute("INSERT INTO favorites (name, cal, p, f, c) VALUES ('☕ Капучино', 120, 4, 4, 10)")
            conn.execute("INSERT INTO favorites (name, cal, p, f, c) VALUES ('🍌 Банан', 105, 1, 0, 27)")
            conn.execute("INSERT INTO favorites (name, cal, p, f, c) VALUES ('🍳 Яичница (2 шт)', 180, 14, 14, 1)")
        
        # Сид начального веса, если пуста
        cursor = conn.execute("SELECT COUNT(*) FROM weight_history")
        if cursor.fetchone()[0] == 0:
            conn.execute("INSERT INTO weight_history (weight) VALUES (70.0)")
            
        cols = {
            "history": ["p INTEGER DEFAULT 0", "f INTEGER DEFAULT 0", "c INTEGER DEFAULT 0"],
            "settings": ["level INTEGER DEFAULT 1", "xp INTEGER DEFAULT 0", "water INTEGER DEFAULT 0", "weight REAL DEFAULT 70.0", "streak INTEGER DEFAULT 0"]
        }
        for table, columns in cols.items():
            for col in columns:
                try: conn.execute(f"ALTER TABLE {table} ADD COLUMN {col}")
                except sqlite3.OperationalError: pass
init_db()

# ================= 2. ЛОГИКА ФИТНЕС-ПРИЛОЖЕНИЯ =================
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

# ================= 3. ШАБЛОНИЗАТОР =================
def render_page(title: str, content: str, active_tab: str = "home", scripts: str = ""):
    home_active = "text-emerald-500" if active_tab == "home" else "text-slate-400 hover:text-emerald-500"
    stats_active = "text-emerald-500" if active_tab == "stats" else "text-slate-400 hover:text-emerald-500"
    prof_active = "text-emerald-500" if active_tab == "profile" else "text-slate-400 hover:text-emerald-500"
    
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
        
        <div id="toast-container" class="fixed top-4 left-1/2 -translate-x-1/2 z-[100] flex flex-col gap-2 w-[90%] max-w-sm pointer-events-none"></div>

        <div class="w-full max-w-md bg-white dark:bg-slate-900 h-full flex flex-col relative shadow-2xl border-x border-slate-100 dark:border-slate-800">
            
            <div class="flex-1 overflow-y-auto pb-24 px-5 pt-6 custom-scrollbar">
                {content}
            </div>

            <div class="absolute bottom-0 w-full h-[75px] bg-white/95 dark:bg-slate-900/95 backdrop-blur-md border-t border-slate-100 dark:border-slate-800 flex justify-around items-center z-50 pb-safe">
                <a href="/" onclick="vibrateBtn()" class="flex flex-col items-center gap-1 transition-colors {home_active} active:scale-95 w-1/3">
                    <svg xmlns="http://www.w3.org/2000/svg" width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="18" height="18" rx="2" ry="2"></rect><line x1="3" y1="9" x2="21" y2="9"></line><line x1="9" y1="21" x2="9" y2="9"></line></svg>
                    <span class="text-[10px] font-semibold tracking-wide">Дневник</span>
                </a>
                <a href="/stats" onclick="vibrateBtn()" class="flex flex-col items-center gap-1 transition-colors {stats_active} active:scale-95 w-1/3">
                    <svg xmlns="http://www.w3.org/2000/svg" width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="20" x2="18" y2="10"></line><line x1="12" y1="20" x2="12" y2="4"></line><line x1="6" y1="20" x2="6" y2="14"></line></svg>
                    <span class="text-[10px] font-semibold tracking-wide">Аналитика</span>
                </a>
                <a href="/profile" onclick="vibrateBtn()" class="flex flex-col items-center gap-1 transition-colors {prof_active} active:scale-95 w-1/3">
                    <svg xmlns="http://www.w3.org/2000/svg" width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"></path><circle cx="12" cy="7" r="4"></circle></svg>
                    <span class="text-[10px] font-semibold tracking-wide">Профиль</span>
                </a>
            </div>
        </div>

        <script>
            function vibrateBtn() {{ if(navigator.vibrate) navigator.vibrate(20); }}

            function showToast(message, type = 'success') {{
                const container = document.getElementById('toast-container');
                const toast = document.createElement('div');
                const bgColor = type === 'success' ? 'bg-emerald-500' : (type === 'info' ? 'bg-blue-500' : 'bg-red-500');
                toast.className = `toast-anim flex items-center gap-3 px-5 py-3 rounded-xl shadow-lg text-white text-sm font-medium ${{bgColor}} w-full`;
                let icon = type === 'success' ? `<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="20 6 9 17 4 12"></polyline></svg>` : (type === 'info' ? `<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 2.69l5.66 5.66a8 8 0 1 1-11.31 0z"></path></svg>` : `<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="18" y1="6" x2="6" y2="18"></line><line x1="6" y1="6" x2="18" y2="18"></line></svg>`);
                toast.innerHTML = `${{icon}} ${{message}}`;
                container.appendChild(toast);
                setTimeout(() => {{ toast.style.opacity = '0'; toast.style.transform = 'translate(-50%, -20px)'; toast.style.transition = 'all 0.3s'; setTimeout(() => toast.remove(), 300); }}, 3000);
            }}
            
            const urlParams = new URLSearchParams(window.location.search);
            if (urlParams.has('added')) {{ showToast('Запись добавлена', 'success'); }}
            else if (urlParams.has('water')) {{ showToast('+250 мл воды', 'info'); }}
            else if (urlParams.has('deleted')) {{ showToast('Запись удалена', 'error'); }}
            else if (urlParams.has('archived')) {{ confetti({{ particleCount: 100, spread: 70, origin: {{ y: 0.5 }}, colors: ['#10b981', '#3b82f6'] }}); showToast('День завершен!', 'success'); }}
            else if (urlParams.has('saved')) {{ showToast('Сохранено', 'success'); }}
            window.history.replaceState({{}}, document.title, window.location.pathname);
        </script>
        {scripts}
    </body>
    </html>
    """

# ================= 4. ГЛАВНАЯ СТРАНИЦА =================
@app.get("/", response_class=HTMLResponse)
async def get_index():
    with sqlite3.connect("calories.db") as conn:
        cursor = conn.execute("SELECT id, name, cal, p, f, c FROM history ORDER BY id DESC")
        history = [{"id": row[0], "name": row[1], "cal": row[2], "p": row[3], "f": row[4], "c": row[5]} for row in cursor.fetchall()]
        
        cursor = conn.execute("SELECT id, name, cal FROM favorites ORDER BY id ASC")
        favorites = [{"id": row[0], "name": row[1], "cal": row[2]} for row in cursor.fetchall()]
        
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

    favs_html = "".join(
        f"""
        <form action="/add_quick/{fav['id']}" method="post" class="shrink-0 m-0">
            <button type="submit" onclick="vibrateBtn()" class="px-3 py-2 bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-xl text-xs font-semibold shadow-sm hover:border-emerald-500 transition active:scale-95 flex items-center gap-1.5 text-slate-700 dark:text-slate-200">
                <span class="truncate max-w-[100px]">{fav['name']}</span>
                <span class="text-emerald-500 bg-emerald-50 dark:bg-emerald-900/30 px-1.5 py-0.5 rounded-md text-[10px]">{fav['cal']}</span>
            </button>
        </form>
        """ for fav in favorites
    )

    items_html = "".join(
        f"""
        <li class="flex justify-between items-center bg-white dark:bg-slate-800 p-4 rounded-2xl border border-slate-100 dark:border-slate-700/50 mb-2 shadow-sm">
            <div class="flex flex-col min-w-0 flex-1">
                <span class="font-semibold text-slate-800 dark:text-slate-200 truncate pr-2">{item['name']}</span>
                <div class="flex items-center gap-2 mt-1">
                    <span class="text-sm font-bold text-emerald-500">{item['cal']} ккал</span>
                    <span class="text-[10px] font-medium text-slate-500 bg-slate-50 dark:bg-slate-900 px-1.5 py-0.5 rounded-md border border-slate-100 dark:border-slate-700">Б:{item['p']} Ж:{item['f']} У:{item['c']}</span>
                </div>
            </div>
            <form action="/delete/{item['id']}" method="post" class="m-0 flex-shrink-0">
                <button type="submit" onclick="vibrateBtn()" class="text-slate-400 hover:text-red-500 bg-slate-50 dark:bg-slate-900 w-9 h-9 rounded-full transition-colors flex items-center justify-center">
                    <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="18" y1="6" x2="6" y2="18"></line><line x1="6" y1="6" x2="18" y2="18"></line></svg>
                </button>
            </form>
        </li>
        """ for item in history
    )
    
    color_class = "text-emerald-500" if total_calories <= daily_goal else "text-red-500"

    content = f"""
        <div class="flex justify-between items-center mb-6">
            <div>
                <h1 class="text-2xl font-bold tracking-tight text-slate-900 dark:text-white" id="greeting">Дневник</h1>
                <p class="text-xs text-slate-500 font-medium mt-0.5" id="date-display"></p>
            </div>
        </div>
        
        <div class="relative h-[160px] flex justify-center items-end overflow-hidden mb-6">
            <canvas id="calorieChart" class="w-full"></canvas>
            <div class="absolute bottom-2 flex flex-col items-center justify-center pointer-events-none">
                <span class="text-5xl font-bold {color_class} tracking-tight">{total_calories}</span>
                <span class="text-[11px] font-medium mt-1 text-slate-500 uppercase tracking-widest">из {daily_goal} ккал</span>
            </div>
        </div>

        <div class="bg-white dark:bg-slate-800 p-5 rounded-3xl mb-6 border border-slate-100 dark:border-slate-700/50 shadow-sm">
            <div class="mb-3">
                <div class="flex justify-between text-xs font-medium mb-1.5"><span class="text-slate-600 dark:text-slate-400">Белки</span><span class="text-slate-900 dark:text-slate-300">{total_p} / {goal_p} г</span></div>
                <div class="w-full bg-slate-100 dark:bg-slate-900 rounded-full h-1.5 overflow-hidden"><div id="bar-p" class="bg-blue-500 h-1.5 rounded-full macro-bar"></div></div>
            </div>
            <div class="mb-3">
                <div class="flex justify-between text-xs font-medium mb-1.5"><span class="text-slate-600 dark:text-slate-400">Жиры</span><span class="text-slate-900 dark:text-slate-300">{total_f} / {goal_f} г</span></div>
                <div class="w-full bg-slate-100 dark:bg-slate-900 rounded-full h-1.5 overflow-hidden"><div id="bar-f" class="bg-amber-500 h-1.5 rounded-full macro-bar"></div></div>
            </div>
            <div>
                <div class="flex justify-between text-xs font-medium mb-1.5"><span class="text-slate-600 dark:text-slate-400">Углеводы</span><span class="text-slate-900 dark:text-slate-300">{total_c} / {goal_c} г</span></div>
                <div class="w-full bg-slate-100 dark:bg-slate-900 rounded-full h-1.5 overflow-hidden"><div id="bar-c" class="bg-emerald-500 h-1.5 rounded-full macro-bar"></div></div>
            </div>
        </div>
        
        <div class="mb-6">
            <h3 class="text-xs font-semibold text-slate-800 dark:text-slate-200 uppercase tracking-wider mb-3 px-1">Избранное (1 клик)</h3>
            <div class="flex gap-2 overflow-x-auto custom-scrollbar pb-2 -mx-2 px-2">
                {favs_html if favs_html else '<span class="text-xs text-slate-400">Добавьте еду в профиле</span>'}
                <form action="/add_water" method="post" class="shrink-0 m-0">
                    <button type="submit" onclick="vibrateBtn()" class="px-3 py-2 bg-blue-50 dark:bg-blue-900/20 border border-blue-200 dark:border-blue-800/50 rounded-xl text-xs font-semibold shadow-sm hover:border-blue-500 transition active:scale-95 flex items-center gap-1.5 text-blue-600 dark:text-blue-400">
                        💧 Вода <span class="bg-blue-100 dark:bg-blue-900/50 px-1.5 py-0.5 rounded-md text-[10px]">{total_water} мл</span>
                    </button>
                </form>
            </div>
        </div>

        <form action="/add" method="post" class="bg-white dark:bg-slate-800 p-5 rounded-3xl border border-slate-100 dark:border-slate-700/50 shadow-sm mb-8">
            <div class="flex gap-2 h-12">
                <input type="text" name="food" placeholder="Название блюда" required class="flex-1 min-w-0 px-4 font-medium bg-slate-50 dark:bg-slate-900 border border-slate-200 dark:border-slate-700 rounded-xl focus:outline-none focus:border-emerald-500 transition">
                <input type="number" name="cal" placeholder="Ккал" required class="w-24 shrink-0 min-w-0 px-2 font-semibold bg-slate-50 dark:bg-slate-900 border border-slate-200 dark:border-slate-700 rounded-xl focus:outline-none focus:border-emerald-500 transition text-center">
            </div>
            <details class="group mt-2">
                <summary class="text-[11px] font-medium text-slate-500 cursor-pointer list-none text-center hover:text-emerald-500 transition py-2 rounded-xl">Указать БЖУ ▾</summary>
                <div class="flex gap-2 mt-1 h-10">
                    <input type="number" name="p" value="0" placeholder="Б" class="flex-1 min-w-0 px-1 font-medium bg-slate-50 dark:bg-slate-900 border border-slate-200 dark:border-slate-700 rounded-lg text-center">
                    <input type="number" name="f" value="0" placeholder="Ж" class="flex-1 min-w-0 px-1 font-medium bg-slate-50 dark:bg-slate-900 border border-slate-200 dark:border-slate-700 rounded-lg text-center">
                    <input type="number" name="c" value="0" placeholder="У" class="flex-1 min-w-0 px-1 font-medium bg-slate-50 dark:bg-slate-900 border border-slate-200 dark:border-slate-700 rounded-lg text-center">
                </div>
            </details>
            <button type="submit" onclick="vibrateBtn()" class="w-full mt-3 h-12 bg-emerald-500 hover:bg-emerald-600 text-white font-semibold rounded-xl shadow-sm transition active:scale-95">Сохранить</button>
        </form>

        <div class="mb-4">
            <h3 class="text-xs font-semibold text-slate-800 dark:text-slate-200 uppercase tracking-wider mb-4 px-1">История за день</h3>
            <ul class="flex flex-col gap-1">
                {items_html if items_html else '<div class="text-center text-slate-500 py-8 bg-white dark:bg-slate-800 rounded-2xl border border-slate-100 dark:border-slate-700/50 text-sm">Журнал пуст</div>'}
            </ul>
        </div>
    """

    scripts = f"""
        <script>
            // Установка времени и приветствия
            const hour = new Date().getHours();
            let greeting = 'Дневник';
            if (hour >= 5 && hour < 12) greeting = 'Доброе утро ☀️';
            else if (hour >= 12 && hour < 18) greeting = 'Добрый день 👋';
            else if (hour >= 18 && hour < 23) greeting = 'Добрый вечер 🌙';
            else greeting = 'Доброй ночи 🦉';
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
                    rotation: 270, circumference: 180, cutout: '85%',
                    responsive: true, maintainAspectRatio: false, 
                    animation: {{ animateScale: true, animateRotate: true, duration: 1000, easing: 'easeOutQuart' }}, 
                    plugins: {{ legend: {{ display: false }}, tooltip: {{ enabled: false }} }} 
                }}
            }});
        </script>
    """
    return render_page("Дневник Питания", content, active_tab="home", scripts=scripts)


# ================= 5. СТРАНИЦА: СТАТИСТИКА =================
@app.get("/stats", response_class=HTMLResponse)
async def get_stats():
    with sqlite3.connect("calories.db") as conn:
        cursor = conn.execute("SELECT daily_goal FROM settings WHERE id = 1")
        daily_goal = cursor.fetchone()[0]
        
        cursor = conn.execute("SELECT date, cal, p, f, c, water FROM archive ORDER BY id DESC LIMIT 7")
        archive_data = cursor.fetchall()
        
        cursor = conn.execute("SELECT date, weight FROM weight_history ORDER BY id DESC LIMIT 14")
        weight_data = cursor.fetchall()

    chart_labels = []
    chart_data = []
    for row in reversed(archive_data):
        chart_labels.append(row[0].split()[0][5:]) # MM-DD
        chart_data.append(row[1])
        
    w_labels = []
    w_data = []
    for row in reversed(weight_data):
        w_labels.append(row[0].split()[0][5:])
        w_data.append(row[1])

    archive_html = "".join(
        f"""
        <div class="bg-white dark:bg-slate-800 p-4 rounded-2xl border border-slate-100 dark:border-slate-700/50 mb-3 shadow-sm">
            <div class="flex justify-between items-center mb-2">
                <span class="font-semibold text-slate-800 dark:text-slate-200">{row[0].split()[0]}</span>
                <span class="text-sm font-bold text-emerald-500">{row[1]} ккал</span>
            </div>
            <div class="flex justify-between text-xs text-slate-500 font-medium">
                <span>Б:{row[2]} Ж:{row[3]} У:{row[4]}</span>
                <span class="text-blue-500 bg-blue-50 dark:bg-blue-900/30 px-2 py-0.5 rounded-md">💧 {row[5]} мл</span>
            </div>
        </div>
        """ for row in archive_data
    )

    content = f"""
        <div class="flex items-center justify-between mb-6">
            <h1 class="text-2xl font-bold tracking-tight text-slate-900 dark:text-white">Аналитика</h1>
        </div>

        <div class="bg-white dark:bg-slate-800 p-5 rounded-3xl mb-6 border border-slate-100 dark:border-slate-700/50 shadow-sm">
            <h3 class="text-xs font-semibold text-slate-800 dark:text-slate-200 uppercase tracking-wider mb-4">График калорий (7 дней)</h3>
            <div class="relative h-[180px] w-full"><canvas id="calChart"></canvas></div>
        </div>
        
        <div class="bg-white dark:bg-slate-800 p-5 rounded-3xl mb-8 border border-slate-100 dark:border-slate-700/50 shadow-sm">
            <h3 class="text-xs font-semibold text-slate-800 dark:text-slate-200 uppercase tracking-wider mb-4">Динамика веса</h3>
            <div class="relative h-[180px] w-full"><canvas id="weightChart"></canvas></div>
        </div>

        <div class="mb-4">
            <h3 class="text-xs font-semibold text-slate-800 dark:text-slate-200 uppercase tracking-wider mb-4 px-1">Архив дней</h3>
            <div class="flex flex-col">
                {archive_html if archive_html else '<div class="text-center text-slate-500 py-8 bg-white dark:bg-slate-800 rounded-2xl border border-slate-100 dark:border-slate-700/50 text-sm">Архив пуст</div>'}
            </div>
        </div>
    """

    scripts = f"""
        <script>
            const isDark = window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches;
            const gridColor = isDark ? '#334155' : '#f1f5f9';
            const tickColor = isDark ? '#64748b' : '#94a3b8';

            // График калорий
            new Chart(document.getElementById('calChart').getContext('2d'), {{
                type: 'bar',
                data: {{ labels: {json.dumps(chart_labels)}, datasets: [{{ label: 'Ккал', data: {json.dumps(chart_data)}, backgroundColor: '#10b981', borderRadius: 6 }}] }},
                options: {{ responsive: true, maintainAspectRatio: false, plugins: {{ legend: {{ display: false }} }}, scales: {{ y: {{ grid: {{ color: gridColor }}, ticks: {{ color: tickColor }} }}, x: {{ grid: {{ display: false }}, ticks: {{ color: tickColor }} }} }} }}
            }});
            
            // График веса
            new Chart(document.getElementById('weightChart').getContext('2d'), {{
                type: 'line',
                data: {{ labels: {json.dumps(w_labels)}, datasets: [{{ label: 'Вес (кг)', data: {json.dumps(w_data)}, borderColor: '#3b82f6', backgroundColor: 'rgba(59, 130, 246, 0.1)', tension: 0.4, fill: true, pointRadius: 4, pointBackgroundColor: '#3b82f6' }}] }},
                options: {{ responsive: true, maintainAspectRatio: false, plugins: {{ legend: {{ display: false }} }}, scales: {{ y: {{ grid: {{ color: gridColor }}, ticks: {{ color: tickColor }} }}, x: {{ grid: {{ display: false }}, ticks: {{ color: tickColor }} }} }} }}
            }});
        </script>
    """
    return render_page("Аналитика", content, active_tab="stats", scripts=scripts)


# ================= 6. СТРАНИЦА ПРОФИЛЯ =================
@app.get("/profile", response_class=HTMLResponse)
async def get_profile():
    with sqlite3.connect("calories.db") as conn:
        cursor = conn.execute("SELECT COUNT(*), SUM(cal), SUM(p), SUM(f), SUM(c) FROM history")
        stats = cursor.fetchone()
        t_meals, t_cal, t_p, t_f, t_c = stats[0] or 0, stats[1] or 0, stats[2] or 0, stats[3] or 0, stats[4] or 0
        
        cursor = conn.execute("SELECT daily_goal, water, streak FROM settings WHERE id = 1")
        settings = cursor.fetchone()
        daily_goal, t_water, streak = settings[0], settings[1], settings[2]
        
        cursor = conn.execute("SELECT weight FROM weight_history ORDER BY id DESC LIMIT 1")
        w_row = cursor.fetchone()
        current_weight = w_row[0] if w_row else 70.0
        
        cursor = conn.execute("SELECT id, name, cal FROM favorites ORDER BY id ASC")
        favorites = [{"id": row[0], "name": row[1], "cal": row[2]} for row in cursor.fetchall()]

    level, current_xp, title = get_user_level(t_meals)
    achievements = check_achievements(t_cal, t_p, t_f, t_c, t_water, daily_goal)
    
    ach_html = "".join(
        f"""
        <div class="flex flex-col items-center p-4 rounded-2xl border { 'border-emerald-200 dark:border-emerald-900/50 bg-emerald-50 dark:bg-emerald-900/10' if ach['unlocked'] else 'border-slate-100 dark:border-slate-800 bg-slate-50 dark:bg-slate-800/50 grayscale opacity-60' }">
            <div class="text-3xl mb-2">{ach['icon']}</div>
            <span class="text-xs font-semibold text-slate-800 dark:text-slate-200 mb-1 text-center leading-tight">{ach['name']}</span>
            <span class="text-[10px] text-slate-500 text-center leading-tight">{ach['desc']}</span>
        </div>
        """ for ach in achievements
    )
    
    fav_manager = "".join(
        f"""
        <li class="flex justify-between items-center py-2 border-b border-slate-100 dark:border-slate-700/50 last:border-0">
            <span class="text-sm font-medium text-slate-800 dark:text-slate-200">{fav['name']} <span class="text-emerald-500 text-xs">({fav['cal']})</span></span>
            <form action="/delete_fav/{fav['id']}" method="post" class="m-0"><button type="submit" class="text-slate-400 hover:text-red-500 px-2 py-1 text-xs">Удалить</button></form>
        </li>
        """ for fav in favorites
    )

    content = f"""
        <div class="flex items-center justify-between mb-6">
            <h1 class="text-2xl font-bold tracking-tight text-slate-900 dark:text-white">Профиль</h1>
            <div class="flex items-center gap-2 bg-orange-50 dark:bg-orange-900/20 border border-orange-100 dark:border-orange-900/30 px-3 py-1.5 rounded-xl">
                <span class="text-lg">🔥</span>
                <div class="flex flex-col">
                    <span class="text-[9px] font-semibold text-orange-600 dark:text-orange-400 uppercase tracking-widest leading-none">Серия дней</span>
                    <span class="text-sm font-bold text-slate-900 dark:text-white leading-none mt-0.5">{streak}</span>
                </div>
            </div>
        </div>

        <div class="p-6 bg-white dark:bg-slate-800 rounded-3xl mb-8 shadow-sm border border-slate-100 dark:border-slate-700/50">
            <div class="flex items-center gap-5">
                <div class="w-16 h-16 bg-slate-100 dark:bg-slate-900 rounded-full flex items-center justify-center text-2xl font-bold text-slate-600 dark:text-slate-300">K</div>
                <div>
                    <h2 class="text-xl font-bold text-slate-900 dark:text-white">Пользователь</h2>
                    <p class="text-emerald-600 dark:text-emerald-400 font-medium text-sm mt-0.5">{title}</p>
                </div>
            </div>
        </div>

        <div class="mb-8">
            <h3 class="text-xs font-semibold text-slate-800 dark:text-slate-200 uppercase tracking-wider mb-4 px-1">Изменение веса и цели</h3>
            <form action="/update_settings" method="post" class="bg-white dark:bg-slate-800 p-5 rounded-3xl border border-slate-100 dark:border-slate-700/50 shadow-sm">
                <div class="flex flex-col gap-4 mb-5">
                    <div>
                        <label class="text-xs font-medium text-slate-600 dark:text-slate-400 mb-1.5 block">Суточная норма (ккал)</label>
                        <input type="number" name="new_goal" value="{daily_goal}" required class="w-full h-12 px-4 font-medium bg-slate-50 dark:bg-slate-900 border border-slate-200 dark:border-slate-700 rounded-xl focus:outline-none focus:border-emerald-500 transition text-slate-900 dark:text-white">
                    </div>
                    <div>
                        <label class="text-xs font-medium text-slate-600 dark:text-slate-400 mb-1.5 block">Текущий вес (кг)</label>
                        <input type="number" step="0.1" name="new_weight" value="{current_weight}" required class="w-full h-12 px-4 font-medium bg-slate-50 dark:bg-slate-900 border border-slate-200 dark:border-slate-700 rounded-xl focus:outline-none focus:border-emerald-500 transition text-slate-900 dark:text-white">
                    </div>
                </div>
                <button type="submit" onclick="vibrateBtn()" class="w-full h-12 bg-slate-900 dark:bg-slate-100 text-white dark:text-slate-900 font-semibold rounded-xl transition active:scale-95">Сохранить</button>
            </form>
        </div>
        
        <div class="mb-8">
            <h3 class="text-xs font-semibold text-slate-800 dark:text-slate-200 uppercase tracking-wider mb-4 px-1">Менеджер Избранного</h3>
            <div class="bg-white dark:bg-slate-800 p-5 rounded-3xl border border-slate-100 dark:border-slate-700/50 shadow-sm">
                <form action="/add_fav" method="post" class="flex gap-2 mb-4">
                    <input type="text" name="name" placeholder="Название" required class="flex-1 min-w-0 px-3 h-10 font-medium bg-slate-50 dark:bg-slate-900 border border-slate-200 dark:border-slate-700 rounded-lg text-sm">
                    <input type="number" name="cal" placeholder="Ккал" required class="w-16 min-w-0 px-2 h-10 font-medium bg-slate-50 dark:bg-slate-900 border border-slate-200 dark:border-slate-700 rounded-lg text-sm text-center">
                    <button type="submit" class="px-4 bg-emerald-500 text-white rounded-lg text-sm font-semibold">+</button>
                </form>
                <ul class="flex flex-col">{fav_manager if fav_manager else '<span class="text-xs text-slate-500">Пусто</span>'}</ul>
            </div>
        </div>
        
        <div class="mb-8">
            <h3 class="text-xs font-semibold text-slate-800 dark:text-slate-200 uppercase tracking-wider mb-4 px-1">Текущие награды</h3>
            <div class="grid grid-cols-2 gap-3 mb-8">
                {ach_html}
            </div>
            
            <form action="/archive_day" method="post">
                <button type="submit" onclick="vibrateBtn()" class="w-full h-14 bg-emerald-50 dark:bg-emerald-900/20 text-emerald-600 dark:text-emerald-500 font-semibold rounded-2xl transition active:scale-95 text-sm flex items-center justify-center gap-2 border border-emerald-200 dark:border-emerald-900/50 shadow-sm">
                    <svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"></path><polyline points="7 10 12 15 17 10"></polyline><line x1="12" y1="15" x2="12" y2="3"></line></svg>
                    Завершить день в архив
                </button>
            </form>
            <p class="text-[11px] text-center text-slate-400 mt-3 px-4">Данные будут очищены и сохранены в Аналитику.</p>
        </div>
    """
    return render_page("Профиль", content, active_tab="profile")


# ================= 7. РОУТЫ (API) =================
@app.post("/add")
async def add_record(food: str = Form(...), cal: int = Form(...), p: int = Form(0), f: int = Form(0), c: int = Form(0)):
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
        # Проверяем, изменился ли вес, чтобы не спамить график
        cursor = conn.execute("SELECT weight FROM weight_history ORDER BY id DESC LIMIT 1")
        last_weight = cursor.fetchone()
        if not last_weight or last_weight[0] != new_weight:
            conn.execute("INSERT INTO weight_history (weight) VALUES (?)", (new_weight,))
    return RedirectResponse(url="/profile?saved=1", status_code=303)

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