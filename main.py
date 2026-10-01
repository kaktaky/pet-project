from fastapi import FastAPI, Form
from fastapi.responses import HTMLResponse, RedirectResponse
import uvicorn
import sqlite3

app = FastAPI()

# ================= 1. БАЗА ДАННЫХ И МИГРАЦИИ =================
def init_db():
    with sqlite3.connect("calories.db") as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS history (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, cal INTEGER NOT NULL)")
        conn.execute("CREATE TABLE IF NOT EXISTS settings (id INTEGER PRIMARY KEY CHECK (id = 1), daily_goal INTEGER NOT NULL)")
        conn.execute("CREATE TABLE IF NOT EXISTS friends (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL)")
        conn.execute("INSERT OR IGNORE INTO settings (id, daily_goal) VALUES (1, 2000)")
        
        cols = {
            "history": ["p INTEGER DEFAULT 0", "f INTEGER DEFAULT 0", "c INTEGER DEFAULT 0"],
            "settings": [
                "level INTEGER DEFAULT 1", 
                "xp INTEGER DEFAULT 0", 
                "water INTEGER DEFAULT 0",
                "weight REAL DEFAULT 70.0"
            ]
        }
        for table, columns in cols.items():
            for col in columns:
                try:
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN {col}")
                except sqlite3.OperationalError:
                    pass
init_db()


# ================= 2. ЛОГИКА RPG И АЧИВОК =================
def get_rpg_stats(total_meals: int):
    xp_total = total_meals * 15
    level = (xp_total // 100) + 1
    current_xp = xp_total % 100
    titles = [(20, "Грандмастер массы"), (10, "Кибер-Котлета"), (5, "Охотник за калориями"), (3, "Любитель фастфуда"), (1, "Голодный новичок")]
    title = next((t for lvl, t in titles if level >= lvl), "Голодный новичок")
    return level, current_xp, title

def check_achievements(cal, p, f, c, water, goal):
    return [
        {
            "id": "tank", "icon": "⛽", "name": "Полный бак", 
            "desc": "Собрал норму калорий", 
            "unlocked": cal >= goal * 0.9 and cal <= goal * 1.1
        },
        {
            "id": "water", "icon": "🛢️", "name": "Замена масла", 
            "desc": "Выпил 1.5 л воды", 
            "unlocked": water >= 1500
        },
        {
            "id": "turbo", "icon": "🚀", "name": "Турбо-режим", 
            "desc": "Съел 100г белка", 
            "unlocked": p >= 100
        },
        {
            "id": "overheat", "icon": "🔥", "name": "Перегрев", 
            "desc": "Превысил лимит на 500 ккал", 
            "unlocked": cal >= goal + 500
        }
    ]


# ================= 3. ШАБЛОНИЗАТОР =================
def render_page(title: str, content: str, active_tab: str = "home", scripts: str = ""):
    home_active = "text-emerald-500 scale-110 drop-shadow-[0_0_8px_rgba(16,185,129,0.5)]" if active_tab == "home" else "text-slate-400 hover:text-emerald-400"
    prof_active = "text-emerald-500 scale-110 drop-shadow-[0_0_8px_rgba(16,185,129,0.5)]" if active_tab == "profile" else "text-slate-400 hover:text-emerald-400"
    
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
            tailwind.config = {{ darkMode: 'media', theme: {{ extend: {{ fontFamily: {{ sans: ['system-ui', 'sans-serif'] }} }} }} }}
        </script>
        <style>
            body {{ background-color: #0f172a; -webkit-tap-highlight-color: transparent; overflow: hidden; }}
            .custom-scrollbar::-webkit-scrollbar {{ display: none; }}
            .custom-scrollbar {{ -ms-overflow-style: none; scrollbar-width: none; }}
            @keyframes slideDown {{ 0% {{ transform: translate(-50%, -100%); opacity: 0; }} 100% {{ transform: translate(-50%, 0); opacity: 1; }} }}
            .toast-anim {{ animation: slideDown 0.4s cubic-bezier(0.16, 1, 0.3, 1) forwards; }}
            /* Плавное заполнение баров */
            .macro-bar {{ width: 0%; transition: width 1.5s cubic-bezier(0.16, 1, 0.3, 1); }}
        </style>
    </head>
    <body class="text-slate-200 transition-colors duration-300 w-full h-screen flex justify-center bg-slate-900">
        
        <div id="toast-container" class="fixed top-4 left-1/2 -translate-x-1/2 z-[100] flex flex-col gap-2 w-[90%] max-w-sm pointer-events-none"></div>

        <div class="w-full max-w-md bg-[#0a0a0a] h-full flex flex-col relative shadow-[0_0_50px_rgba(0,0,0,0.8)] border-x border-slate-800">
            
            <div class="flex-1 overflow-y-auto pb-24 px-5 pt-8 custom-scrollbar">
                {content}
            </div>

            <div class="absolute bottom-0 w-full h-[72px] bg-[#0a0a0a]/90 backdrop-blur-xl border-t border-slate-800 flex justify-around items-center z-50 pb-safe">
                <a href="/" class="flex flex-col items-center gap-1 transition-all duration-300 {home_active} active:scale-95 w-1/2">
                    <svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M3 9l9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"></path><polyline points="9 22 9 12 15 12 15 22"></polyline></svg>
                    <span class="text-[10px] font-bold uppercase tracking-widest">Панель</span>
                </a>
                <a href="/profile" class="flex flex-col items-center gap-1 transition-all duration-300 {prof_active} active:scale-95 w-1/2 border-l border-slate-800">
                    <svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"></path><circle cx="12" cy="7" r="4"></circle></svg>
                    <span class="text-[10px] font-bold uppercase tracking-widest">Профиль</span>
                </a>
            </div>
        </div>

        <script>
            function showToast(message, type = 'success') {{
                const container = document.getElementById('toast-container');
                const toast = document.createElement('div');
                const bgColor = type === 'success' ? 'bg-emerald-500' : (type === 'info' ? 'bg-blue-500' : 'bg-red-600');
                toast.className = `toast-anim flex items-center gap-3 px-5 py-3.5 rounded-2xl shadow-2xl text-white text-sm font-bold ${{bgColor}} w-full border border-white/20`;
                
                let icon = '';
                if(type === 'success') icon = `<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3"><polyline points="20 6 9 17 4 12"></polyline></svg>`;
                else if(type === 'info') icon = `<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3"><path d="M12 2.69l5.66 5.66a8 8 0 1 1-11.31 0z"></path></svg>`;
                else icon = `<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3"><line x1="18" y1="6" x2="6" y2="18"></line><line x1="6" y1="6" x2="18" y2="18"></line></svg>`;
                
                toast.innerHTML = `${{icon}} ${{message}}`;
                container.appendChild(toast);
                setTimeout(() => {{ toast.style.opacity = '0'; toast.style.transform = 'translate(-50%, -20px)'; toast.style.transition = 'all 0.3s'; setTimeout(() => toast.remove(), 300); }}, 3000);
            }}
            
            const urlParams = new URLSearchParams(window.location.search);
            if (urlParams.has('added')) {{
                confetti({{ particleCount: 100, spread: 70, origin: {{ y: 0.8 }}, colors: ['#10b981', '#34d399', '#0ea5e9'] }});
                showToast('Данные сохранены! +15 XP', 'success');
                window.history.replaceState({{}}, document.title, window.location.pathname);
            }} else if (urlParams.has('water')) {{
                showToast('+250 мл воды в бак!', 'info');
                window.history.replaceState({{}}, document.title, window.location.pathname);
            }} else if (urlParams.has('deleted')) {{
                showToast('Запись удалена', 'error');
                window.history.replaceState({{}}, document.title, window.location.pathname);
            }} else if (urlParams.has('saved')) {{
                showToast('Настройки обновлены', 'success');
                window.history.replaceState({{}}, document.title, window.location.pathname);
            }}
        </script>
        {scripts}
    </body>
    </html>
    """


# ================= 4. ГЛАВНАЯ СТРАНИЦА (Телеметрия) =================
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

    wanted_stars = min(5, ((total_calories - daily_goal) // 150) + 1) if total_calories > daily_goal else 0
    stars_html = "".join(f'<svg class="w-5 h-5 {"text-amber-400 drop-shadow-[0_0_8px_rgba(251,191,36,0.8)]" if i < wanted_stars else "text-slate-800"}" fill="currentColor" viewBox="0 0 20 20"><path d="M9.049 2.927c.3-.921 1.603-.921 1.902 0l1.07 3.292a1 1 0 00.95.69h3.462c.969 0 1.371 1.24.588 1.81l-2.8 2.034a1 1 0 00-.364 1.118l1.07 3.292c.3.921-.755 1.688-1.54 1.118l-2.8-2.034a1 1 0 00-1.175 0l-2.8 2.034c-.784.57-1.838-.197-1.539-1.118l1.07-3.292a1 1 0 00-.364-1.118L2.98 8.72c-.783-.57-.38-1.81.588-1.81h3.461a1 1 0 00.951-.69l1.07-3.292z"/></svg>' for i in range(5))

    # Расчет целевых макросов (Белки 30%, Жиры 30%, Углеводы 40%)
    goal_p = int((daily_goal * 0.3) / 4)
    goal_f = int((daily_goal * 0.3) / 9)
    goal_c = int((daily_goal * 0.4) / 4)
    
    pct_p = min(100, int((total_p / goal_p) * 100)) if goal_p > 0 else 0
    pct_f = min(100, int((total_f / goal_f) * 100)) if goal_f > 0 else 0
    pct_c = min(100, int((total_c / goal_c) * 100)) if goal_c > 0 else 0

    items_html = "".join(
        f"""
        <li class="flex justify-between items-center bg-slate-900/50 p-4 rounded-2xl border border-slate-800 mb-2">
            <div class="flex flex-col min-w-0 flex-1">
                <span class="font-bold text-slate-200 truncate pr-2">{item['name']}</span>
                <div class="flex items-center gap-2 mt-1">
                    <span class="text-sm font-black text-emerald-500">{item['cal']} ккал</span>
                    <span class="text-[10px] font-bold text-slate-500 bg-slate-800 px-2 py-0.5 rounded-full">Б:{item['p']} Ж:{item['f']} У:{item['c']}</span>
                </div>
            </div>
            <form action="/delete/{item['id']}" method="post" class="m-0 flex-shrink-0">
                <button type="submit" class="text-slate-500 hover:text-red-500 bg-slate-950 w-10 h-10 rounded-xl transition-all flex items-center justify-center border border-slate-800 hover:border-red-500/50">
                    <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="6" x2="6" y2="18"></line><line x1="6" y1="6" x2="18" y2="18"></line></svg>
                </button>
            </form>
        </li>
        """
        for item in history
    )
    
    remaining = max(0, daily_goal - total_calories)
    color_class = "text-emerald-500" if wanted_stars == 0 else "text-red-500"

    content = f"""
        <div class="flex justify-between items-center mb-6">
            <h1 class="text-3xl font-black tracking-tighter text-white uppercase italic">Телеметрия<span class='text-emerald-500'>.</span></h1>
            <div class="flex gap-0.5">{stars_html}</div>
        </div>

        <div class="relative h-[160px] flex justify-center items-end overflow-hidden mb-4">
            <canvas id="calorieChart" class="w-full"></canvas>
            <div class="absolute bottom-2 flex flex-col items-center justify-center pointer-events-none">
                <span class="text-6xl font-black {color_class} tracking-tighter drop-shadow-[0_0_15px_currentColor]">{total_calories}</span>
                <span class="text-[10px] font-bold mt-1 uppercase tracking-widest text-slate-500">из {daily_goal} ккал</span>
            </div>
        </div>

        <!-- НОВЫЙ БЛОК: ПРОГРЕСС-БАРЫ БЖУ -->
        <div class="bg-slate-900/60 p-5 rounded-3xl mb-6 border border-slate-800 shadow-inner">
            <h3 class="text-[10px] font-black text-slate-500 uppercase tracking-widest mb-4">Макронутриенты</h3>
            
            <!-- Белки -->
            <div class="mb-4">
                <div class="flex justify-between text-xs font-bold mb-1"><span class="text-blue-400">БЕЛКИ</span><span class="text-slate-400">{total_p} / {goal_p} г</span></div>
                <div class="w-full bg-slate-950 rounded-full h-2.5 border border-slate-800 overflow-hidden">
                    <div id="bar-p" class="bg-blue-500 h-2.5 rounded-full macro-bar shadow-[0_0_10px_rgba(59,130,246,0.6)]"></div>
                </div>
            </div>
            
            <!-- Жиры -->
            <div class="mb-4">
                <div class="flex justify-between text-xs font-bold mb-1"><span class="text-amber-400">ЖИРЫ</span><span class="text-slate-400">{total_f} / {goal_f} г</span></div>
                <div class="w-full bg-slate-950 rounded-full h-2.5 border border-slate-800 overflow-hidden">
                    <div id="bar-f" class="bg-amber-500 h-2.5 rounded-full macro-bar shadow-[0_0_10px_rgba(245,158,11,0.6)]"></div>
                </div>
            </div>
            
            <!-- Углеводы -->
            <div>
                <div class="flex justify-between text-xs font-bold mb-1"><span class="text-emerald-400">УГЛЕВ</span><span class="text-slate-400">{total_c} / {goal_c} г</span></div>
                <div class="w-full bg-slate-950 rounded-full h-2.5 border border-slate-800 overflow-hidden">
                    <div id="bar-c" class="bg-emerald-500 h-2.5 rounded-full macro-bar shadow-[0_0_10px_rgba(16,185,129,0.6)]"></div>
                </div>
            </div>
        </div>
        
        <!-- Вода -->
        <div class="flex items-center justify-between bg-blue-900/10 p-4 rounded-3xl border border-blue-900/30 mb-6">
            <div class="flex items-center gap-4">
                <div class="w-12 h-12 bg-blue-950 text-blue-400 rounded-2xl flex items-center justify-center border border-blue-900/50 shadow-inner">
                    <svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M12 2.69l5.66 5.66a8 8 0 1 1-11.31 0z"></path></svg>
                </div>
                <div class="flex flex-col">
                    <span class="text-[10px] font-bold text-slate-500 uppercase tracking-widest">Уровень ОЖ</span>
                    <span class="text-xl font-black text-blue-400">{total_water} <span class="text-xs">мл</span></span>
                </div>
            </div>
            <form action="/add_water" method="post" class="m-0">
                <button type="submit" class="px-5 py-3 bg-blue-600 hover:bg-blue-500 text-white font-black uppercase tracking-wider rounded-2xl shadow-[0_0_15px_rgba(37,99,235,0.4)] transition active:scale-95 text-xs border border-blue-400/50">+ 250 мл</button>
            </form>
        </div>

        <!-- Ввод -->
        <form action="/add" method="post" class="bg-slate-900/80 p-5 rounded-3xl border border-slate-800 shadow-2xl mb-8 relative overflow-hidden">
            <div class="flex gap-2 h-14 relative z-10">
                <input type="text" name="food" placeholder="Название" required class="flex-1 min-w-0 px-5 font-bold bg-slate-950 border border-slate-800 rounded-2xl focus:outline-none focus:border-emerald-500 transition text-white placeholder-slate-600">
                <input type="number" name="cal" placeholder="Ккал" required class="w-24 shrink-0 min-w-0 px-2 font-black bg-slate-950 border border-slate-800 rounded-2xl focus:outline-none focus:border-emerald-500 transition text-center text-white placeholder-slate-600">
            </div>
            <details class="group mt-3 relative z-10">
                <summary class="text-[10px] font-bold text-slate-500 cursor-pointer list-none text-center hover:text-emerald-400 transition py-2 uppercase tracking-widest bg-slate-950/50 rounded-xl border border-slate-800/50">
                    Настройка Макросов (Опц.)
                </summary>
                <div class="flex gap-2 mt-2 h-12">
                    <input type="number" name="p" value="0" class="flex-1 min-w-0 px-1 font-bold bg-slate-950 border border-slate-800 rounded-xl text-center focus:border-blue-500 text-blue-400">
                    <input type="number" name="f" value="0" class="flex-1 min-w-0 px-1 font-bold bg-slate-950 border border-slate-800 rounded-xl text-center focus:border-amber-500 text-amber-400">
                    <input type="number" name="c" value="0" class="flex-1 min-w-0 px-1 font-bold bg-slate-950 border border-slate-800 rounded-xl text-center focus:border-emerald-500 text-emerald-400">
                </div>
            </details>
            <button type="submit" class="w-full mt-4 h-14 bg-emerald-500 text-slate-950 font-black uppercase tracking-widest rounded-2xl shadow-[0_0_20px_rgba(16,185,129,0.4)] transition active:scale-95 flex items-center justify-center gap-2 relative z-10">
                Записать <svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><polyline points="9 18 15 12 9 6"></polyline></svg>
            </button>
        </form>

        <div class="mb-4">
            <h3 class="text-[11px] font-black text-slate-500 uppercase tracking-widest mb-3 px-1">Лог данных</h3>
            <ul class="flex flex-col gap-1">
                {items_html if items_html else '<div class="text-center text-slate-600 font-bold py-10 bg-slate-900/50 rounded-2xl border border-slate-800 text-xs uppercase tracking-widest">Бортовой журнал пуст</div>'}
            </ul>
        </div>
    """

    scripts = f"""
        <script>
            // Анимация макросов при загрузке
            setTimeout(() => {{
                document.getElementById('bar-p').style.width = '{pct_p}%';
                document.getElementById('bar-f').style.width = '{pct_f}%';
                document.getElementById('bar-c').style.width = '{pct_c}%';
            }}, 100);

            const ctx = document.getElementById('calorieChart').getContext('2d');
            const consumed = {total_calories};
            const goal = {daily_goal};
            const remaining = Math.max(0, goal - consumed);
            const emptyColor = '#1e293b';
            const highlightColor = consumed > goal ? '#ef4444' : '#10b981';
            const bRadius = consumed > goal ? 0 : 20;

            new Chart(ctx, {{
                type: 'doughnut',
                data: {{
                    labels: ['Съедено', 'Осталось'],
                    datasets: [{{
                        data: [consumed, remaining],
                        backgroundColor: [highlightColor, emptyColor],
                        borderWidth: 0,
                        borderRadius: bRadius
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
    return render_page("Бортовой Компьютер 6.0", content, active_tab="home", scripts=scripts)


# ================= 5. СТРАНИЦА ПРОФИЛЯ =================
@app.get("/profile", response_class=HTMLResponse)
async def get_profile():
    with sqlite3.connect("calories.db") as conn:
        # Инфа
        cursor = conn.execute("SELECT COUNT(*), SUM(cal), SUM(p), SUM(f), SUM(c) FROM history")
        stats = cursor.fetchone()
        total_meals, total_cal, total_p, total_f, total_c = stats[0] or 0, stats[1] or 0, stats[2] or 0, stats[3] or 0, stats[4] or 0
        
        cursor = conn.execute("SELECT daily_goal, water, weight FROM settings WHERE id = 1")
        settings = cursor.fetchone()
        daily_goal, total_water, current_weight = settings[0], settings[1], settings[2]

    level, current_xp, title = get_rpg_stats(total_meals)
    
    # Генерация ачивок
    achievements = check_achievements(total_cal, total_p, total_f, total_c, total_water, daily_goal)
    ach_html = "".join(
        f"""
        <div class="flex flex-col items-center p-3 rounded-2xl border { 'border-emerald-500/50 bg-emerald-500/10 shadow-[0_0_15px_rgba(16,185,129,0.15)]' if ach['unlocked'] else 'border-slate-800 bg-slate-900/50 grayscale opacity-50' } transition-all">
            <div class="text-3xl mb-2">{ach['icon']}</div>
            <span class="text-[10px] font-black uppercase text-center text-white mb-1 leading-tight">{ach['name']}</span>
            <span class="text-[8px] font-bold text-slate-400 text-center uppercase tracking-wider">{ach['desc']}</span>
        </div>
        """
        for ach in achievements
    )

    content = f"""
        <div class="flex items-center mb-6">
            <h1 class="text-3xl font-black tracking-tighter text-white uppercase italic">Профиль<span class="text-emerald-500">.</span></h1>
        </div>

        <!-- Карточка Игрока -->
        <div class="p-6 bg-[#050505] rounded-3xl mb-8 shadow-2xl border border-slate-800 relative overflow-hidden">
            <div class="absolute top-0 right-0 w-40 h-40 bg-emerald-500/20 rounded-full blur-3xl"></div>
            <div class="flex items-center gap-5 relative z-10">
                <div class="w-20 h-20 bg-slate-950 border border-emerald-500/50 rounded-2xl flex items-center justify-center text-5xl font-black text-emerald-400 shadow-[0_0_30px_rgba(16,185,129,0.2)]">
                    K
                </div>
                <div>
                    <h2 class="text-3xl font-black text-white uppercase tracking-widest">Кактак</h2>
                    <p class="text-emerald-500 font-black text-[10px] uppercase tracking-[0.2em] mt-1 bg-emerald-500/10 inline-block px-2 py-1 rounded-md border border-emerald-500/20">{title}</p>
                </div>
            </div>
            
            <div class="mt-8 relative z-10">
                <div class="flex justify-between items-end mb-2">
                    <span class="text-[10px] font-bold text-slate-500 uppercase tracking-widest">Уровень {level}</span>
                    <span class="text-xs font-black text-emerald-500">{current_xp}/100 XP</span>
                </div>
                <div class="w-full h-3 bg-slate-900 rounded-full overflow-hidden border border-slate-800">
                    <div class="h-full bg-emerald-500 rounded-full shadow-[0_0_15px_rgba(16,185,129,1)] macro-bar" id="xp-bar"></div>
                </div>
            </div>
        </div>

        <!-- Сетка Ачивок -->
        <div class="mb-8">
            <h3 class="text-[11px] font-black text-slate-500 uppercase tracking-widest mb-3 px-1">Достижения за день</h3>
            <div class="grid grid-cols-2 gap-3">
                {ach_html}
            </div>
        </div>

        <!-- Настройки (Тюнинг) -->
        <div class="mb-8">
            <h3 class="text-[11px] font-black text-slate-500 uppercase tracking-widest mb-3 px-1">Тюнинг параметров</h3>
            <form action="/update_settings" method="post" class="bg-slate-900/60 p-5 rounded-3xl border border-slate-800">
                <div class="flex flex-col gap-4 mb-4">
                    <div>
                        <label class="text-[10px] font-bold text-slate-500 uppercase tracking-widest mb-1 block">Норма ККАЛ</label>
                        <input type="number" name="new_goal" value="{daily_goal}" required class="w-full h-12 px-4 font-bold bg-slate-950 border border-slate-800 rounded-xl focus:outline-none focus:border-emerald-500 transition text-white">
                    </div>
                    <div>
                        <label class="text-[10px] font-bold text-slate-500 uppercase tracking-widest mb-1 block">Текущий вес (кг)</label>
                        <input type="number" step="0.1" name="new_weight" value="{current_weight}" required class="w-full h-12 px-4 font-bold bg-slate-950 border border-slate-800 rounded-xl focus:outline-none focus:border-emerald-500 transition text-white">
                    </div>
                </div>
                <button type="submit" class="w-full h-12 bg-slate-800 hover:bg-slate-700 text-white font-black uppercase tracking-widest rounded-xl transition active:scale-95 border border-slate-700">Сохранить</button>
            </form>
        </div>
        
        <!-- Сброс -->
        <div class="mb-8">
            <form action="/reset" method="post">
                <button type="submit" class="w-full h-14 bg-red-950/40 text-red-500 font-black uppercase tracking-widest rounded-2xl border border-red-900/50 transition active:scale-95 text-xs hover:bg-red-900/40">
                    Сбросить кэш дня (Еда + Вода)
                </button>
            </form>
        </div>
    """
    
    scripts = f"""
        <script>
            setTimeout(() => {{
                document.getElementById('xp-bar').style.width = '{current_xp}%';
            }}, 100);
        </script>
    """
    return render_page("Профиль 6.0", content, active_tab="profile", scripts=scripts)


# ================= 6. РОУТЫ (API) =================
@app.post("/add")
async def add_record(food: str = Form(...), cal: int = Form(...), p: int = Form(0), f: int = Form(0), c: int = Form(0)):
    with sqlite3.connect("calories.db") as conn:
        conn.execute("INSERT INTO history (name, cal, p, f, c) VALUES (?, ?, ?, ?, ?)", (food, cal, p, f, c))
    return RedirectResponse(url="/?added=1", status_code=303)

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
        conn.execute("UPDATE settings SET daily_goal = ?, weight = ? WHERE id = 1", (new_goal, new_weight))
    return RedirectResponse(url="/profile?saved=1", status_code=303)

@app.post("/reset")
async def reset_records():
    with sqlite3.connect("calories.db") as conn:
        conn.execute("DELETE FROM history")
        conn.execute("UPDATE settings SET water = 0 WHERE id = 1")
    return RedirectResponse(url="/profile", status_code=303)


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)