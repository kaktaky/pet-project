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
        
        # Безопасное добавление колонок, если их еще нет
        cols = {
            "history": ["p INTEGER DEFAULT 0", "f INTEGER DEFAULT 0", "c INTEGER DEFAULT 0"],
            "settings": ["level INTEGER DEFAULT 1", "xp INTEGER DEFAULT 0", "water INTEGER DEFAULT 0"]
        }
        for table, columns in cols.items():
            for col in columns:
                try:
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN {col}")
                except sqlite3.OperationalError:
                    pass
init_db()


# ================= 2. ЛОГИКА RPG =================
def get_rpg_stats(total_meals: int):
    xp_total = total_meals * 15
    level = (xp_total // 100) + 1
    current_xp = xp_total % 100
    titles = [(20, "Грандмастер массы"), (10, "Кибер-Котлета"), (5, "Охотник за калориями"), (3, "Любитель фастфуда"), (1, "Голодный новичок")]
    title = next((t for lvl, t in titles if level >= lvl), "Голодный новичок")
    return level, current_xp, title


# ================= 3. ШАБЛОНИЗАТОР И КАРКАС (Идеальная верстка) =================
def render_page(title: str, content: str, active_tab: str = "home", scripts: str = ""):
    home_active = "text-emerald-500 scale-110" if active_tab == "home" else "text-slate-400 hover:text-emerald-400"
    prof_active = "text-emerald-500 scale-110" if active_tab == "profile" else "text-slate-400 hover:text-emerald-400"
    
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
            body {{ background-color: #f1f5f9; -webkit-tap-highlight-color: transparent; overflow: hidden; }}
            @media (prefers-color-scheme: dark) {{ body {{ background-color: #050505; }} }}
            /* Прячем скроллбар, но оставляем прокрутку для красоты */
            .custom-scrollbar::-webkit-scrollbar {{ display: none; }}
            .custom-scrollbar {{ -ms-overflow-style: none; scrollbar-width: none; }}
            
            @keyframes slideDown {{ 0% {{ transform: translate(-50%, -100%); opacity: 0; }} 100% {{ transform: translate(-50%, 0); opacity: 1; }} }}
            .toast-anim {{ animation: slideDown 0.4s cubic-bezier(0.16, 1, 0.3, 1) forwards; }}
        </style>
    </head>
    <body class="text-slate-800 dark:text-slate-200 transition-colors duration-300 w-full h-screen flex justify-center">
        
        <div id="toast-container" class="fixed top-4 left-1/2 -translate-x-1/2 z-[100] flex flex-col gap-2 w-[90%] max-w-sm pointer-events-none"></div>

        <!-- Жесткий каркас под мобилку (помогает избежать вылезания за края) -->
        <div class="w-full max-w-md bg-white dark:bg-slate-900 h-full flex flex-col relative shadow-[0_0_50px_rgba(0,0,0,0.3)]">
            
            <!-- Контент, который можно скроллить -->
            <div class="flex-1 overflow-y-auto pb-24 px-6 pt-8 custom-scrollbar">
                {content}
            </div>

            <!-- Нижнее меню (Bottom Navigation) -->
            <div class="absolute bottom-0 w-full h-[72px] bg-white/90 dark:bg-slate-900/90 backdrop-blur-xl border-t border-slate-100 dark:border-slate-800 flex justify-around items-center z-50 pb-safe">
                <a href="/" class="flex flex-col items-center gap-1 transition-all duration-300 {home_active} active:scale-95 w-1/2">
                    <svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M3 9l9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"></path><polyline points="9 22 9 12 15 12 15 22"></polyline></svg>
                    <span class="text-[10px] font-bold uppercase tracking-widest">Главная</span>
                </a>
                <a href="/profile" class="flex flex-col items-center gap-1 transition-all duration-300 {prof_active} active:scale-95 w-1/2 border-l border-slate-100 dark:border-slate-800">
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
                toast.className = `toast-anim flex items-center gap-3 px-5 py-3.5 rounded-2xl shadow-2xl text-white text-sm font-bold ${{bgColor}} w-full`;
                
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
                showToast('+250 мл воды выпито!', 'info');
                window.history.replaceState({{}}, document.title, window.location.pathname);
            }} else if (urlParams.has('deleted')) {{
                showToast('Запись удалена', 'error');
                window.history.replaceState({{}}, document.title, window.location.pathname);
            }}
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
        
        cursor = conn.execute("SELECT SUM(cal), SUM(p), SUM(f), SUM(c) FROM history")
        row = cursor.fetchone()
        total_calories, total_p, total_f, total_c = row[0] or 0, row[1] or 0, row[2] or 0, row[3] or 0
        
        cursor = conn.execute("SELECT daily_goal, water FROM settings WHERE id = 1")
        settings_row = cursor.fetchone()
        daily_goal, total_water = settings_row[0], settings_row[1]

    wanted_stars = min(5, ((total_calories - daily_goal) // 150) + 1) if total_calories > daily_goal else 0
        
    stars_html = "".join(
        f'<svg class="w-5 h-5 {"text-amber-400 drop-shadow-[0_0_8px_rgba(251,191,36,0.8)]" if i < wanted_stars else "text-slate-200 dark:text-slate-800"}" fill="currentColor" viewBox="0 0 20 20"><path d="M9.049 2.927c.3-.921 1.603-.921 1.902 0l1.07 3.292a1 1 0 00.95.69h3.462c.969 0 1.371 1.24.588 1.81l-2.8 2.034a1 1 0 00-.364 1.118l1.07 3.292c.3.921-.755 1.688-1.54 1.118l-2.8-2.034a1 1 0 00-1.175 0l-2.8 2.034c-.784.57-1.838-.197-1.539-1.118l1.07-3.292a1 1 0 00-.364-1.118L2.98 8.72c-.783-.57-.38-1.81.588-1.81h3.461a1 1 0 00.951-.69l1.07-3.292z"/></svg>'
        for i in range(5)
    )

    header_title = "Панель<span class='text-emerald-500'>.</span>" if wanted_stars == 0 else "<span class='text-red-500 animate-pulse'>WANTED</span>"

    items_html = "".join(
        f"""
        <li class="flex justify-between items-center bg-slate-50 dark:bg-slate-800/50 p-4 rounded-2xl border border-slate-100 dark:border-slate-700/50 mb-2">
            <div class="flex flex-col min-w-0 flex-1">
                <span class="font-bold text-slate-800 dark:text-slate-200 truncate pr-2">{item['name']}</span>
                <div class="flex items-center gap-2 mt-1">
                    <span class="text-sm font-black text-emerald-500/90">{item['cal']} ккал</span>
                    <span class="text-xs font-semibold text-slate-400">Б:{item['p']} Ж:{item['f']} У:{item['c']}</span>
                </div>
            </div>
            <form action="/delete/{item['id']}" method="post" class="m-0 flex-shrink-0">
                <button type="submit" class="text-slate-300 hover:text-red-500 bg-white dark:bg-slate-900 w-10 h-10 rounded-xl transition-all flex items-center justify-center shadow-sm">
                    <svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="6" x2="6" y2="18"></line><line x1="6" y1="6" x2="18" y2="18"></line></svg>
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
            <h1 class="text-3xl font-black tracking-tighter text-slate-800 dark:text-white uppercase italic">{header_title}</h1>
            <div class="flex gap-0.5">{stars_html}</div>
        </div>

        <div class="relative h-[160px] flex justify-center items-end overflow-hidden mb-2">
            <canvas id="calorieChart" class="w-full"></canvas>
            <div class="absolute bottom-2 flex flex-col items-center justify-center pointer-events-none">
                <span class="text-5xl font-black {color_class} tracking-tighter">{total_calories}</span>
                <span class="text-[10px] font-bold mt-1 uppercase tracking-widest text-slate-400">из {daily_goal} ккал</span>
            </div>
        </div>

        <div class="flex justify-between text-xs font-black text-slate-500 dark:text-slate-400 bg-slate-50 dark:bg-slate-800/60 p-4 rounded-2xl mb-6 border border-slate-100 dark:border-slate-700/50 shadow-inner">
            <div class="flex flex-col items-center gap-1"><span>БЕЛКИ</span><span class="text-blue-500 text-lg">{total_p}</span></div>
            <div class="flex flex-col items-center gap-1"><span>ЖИРЫ</span><span class="text-amber-500 text-lg">{total_f}</span></div>
            <div class="flex flex-col items-center gap-1"><span>УГЛЕВ</span><span class="text-emerald-500 text-lg">{total_c}</span></div>
        </div>
        
        <!-- Новый блок: Вода -->
        <div class="flex items-center justify-between bg-blue-50 dark:bg-blue-900/10 p-4 rounded-2xl border border-blue-100 dark:border-blue-900/30 mb-6">
            <div class="flex items-center gap-3">
                <div class="w-10 h-10 bg-blue-100 dark:bg-blue-800 text-blue-500 dark:text-blue-300 rounded-full flex items-center justify-center">
                    <svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M12 2.69l5.66 5.66a8 8 0 1 1-11.31 0z"></path></svg>
                </div>
                <div class="flex flex-col">
                    <span class="text-sm font-bold text-slate-700 dark:text-slate-300">Вода за день</span>
                    <span class="text-lg font-black text-blue-600 dark:text-blue-400">{total_water} мл</span>
                </div>
            </div>
            <form action="/add_water" method="post" class="m-0">
                <button type="submit" class="px-4 py-2 bg-blue-500 hover:bg-blue-600 text-white font-bold rounded-xl shadow-lg shadow-blue-500/30 transition active:scale-95 text-sm">+ 250 мл</button>
            </form>
        </div>

        <form action="/add" method="post" class="bg-white dark:bg-slate-900 p-4 rounded-3xl border-2 border-slate-100 dark:border-slate-800 shadow-xl mb-6">
            <div class="flex gap-2 h-14">
                <input type="text" name="food" placeholder="Что съели?" required class="flex-1 min-w-0 px-4 font-bold bg-slate-50 dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-2xl focus:outline-none focus:border-emerald-500 transition">
                <input type="number" name="cal" placeholder="Ккал" required class="w-24 shrink-0 min-w-0 px-2 font-black bg-slate-50 dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-2xl focus:outline-none focus:border-emerald-500 transition text-center">
            </div>
            <details class="group mt-2">
                <summary class="text-[11px] font-bold text-slate-400 cursor-pointer list-none text-center hover:text-emerald-500 transition py-2 uppercase tracking-widest bg-slate-50 dark:bg-slate-800/50 rounded-xl mt-2">
                    Показать БЖУ (Необязательно)
                </summary>
                <div class="flex gap-2 mt-2 h-12">
                    <input type="number" name="p" value="0" placeholder="Б" class="flex-1 min-w-0 px-1 font-bold bg-slate-50 dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-xl text-center focus:border-blue-500 text-blue-500">
                    <input type="number" name="f" value="0" placeholder="Ж" class="flex-1 min-w-0 px-1 font-bold bg-slate-50 dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-xl text-center focus:border-amber-500 text-amber-500">
                    <input type="number" name="c" value="0" placeholder="У" class="flex-1 min-w-0 px-1 font-bold bg-slate-50 dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-xl text-center focus:border-emerald-500 text-emerald-500">
                </div>
            </details>
            <button type="submit" class="w-full mt-3 h-14 bg-emerald-500 text-white font-black uppercase tracking-widest rounded-2xl shadow-[0_10px_20px_rgba(16,185,129,0.3)] transition active:scale-95 flex items-center justify-center gap-2">
                Записать <svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><polyline points="9 18 15 12 9 6"></polyline></svg>
            </button>
        </form>

        <div class="mb-4">
            <h3 class="text-sm font-black text-slate-800 dark:text-slate-200 uppercase tracking-widest mb-3 px-1">История</h3>
            <ul class="flex flex-col gap-1">
                {items_html if items_html else '<div class="text-center text-slate-400 font-bold py-10 bg-slate-50 dark:bg-slate-800/30 rounded-2xl border-2 border-dashed border-slate-200 dark:border-slate-700 text-sm">База пуста. Пора подкрепиться!</div>'}
            </ul>
        </div>
    """

    scripts = f"""
        <script>
            const ctx = document.getElementById('calorieChart').getContext('2d');
            const consumed = {total_calories};
            const goal = {daily_goal};
            const remaining = Math.max(0, goal - consumed);
            
            const isDark = window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches;
            const emptyColor = isDark ? '#1e293b' : '#f1f5f9';
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
    return render_page("Калькулятор 5.0", content, active_tab="home", scripts=scripts)


# ================= 5. СТРАНИЦА ПРОФИЛЯ =================
@app.get("/profile", response_class=HTMLResponse)
async def get_profile():
    with sqlite3.connect("calories.db") as conn:
        cursor = conn.execute("SELECT id, name FROM friends ORDER BY id DESC")
        friends = [{"id": row[0], "name": row[1]} for row in cursor.fetchall()]
        cursor = conn.execute("SELECT COUNT(*) FROM history")
        total_meals = cursor.fetchone()[0]

    level, current_xp, title = get_rpg_stats(total_meals)

    friends_html = "".join(
        f"""
        <li class="flex justify-between items-center bg-slate-50 dark:bg-slate-800/80 p-3 rounded-2xl border border-slate-100 dark:border-slate-700 mb-2">
            <div class="flex items-center gap-3">
                <div class="w-10 h-10 bg-slate-200 dark:bg-slate-900 text-emerald-500 rounded-xl flex items-center justify-center font-black shadow-inner uppercase">
                    {friend['name'][0]}
                </div>
                <span class="font-bold text-slate-800 dark:text-slate-100">{friend['name']}</span>
            </div>
            <form action="/delete_friend/{friend['id']}" method="post" class="m-0">
                <button type="submit" class="text-slate-400 hover:text-red-500 p-2 transition-colors">
                    <svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="6" x2="6" y2="18"></line><line x1="6" y1="6" x2="18" y2="18"></line></svg>
                </button>
            </form>
        </li>
        """
        for friend in friends
    )

    content = f"""
        <div class="flex items-center mb-6">
            <h1 class="text-3xl font-black tracking-tighter text-slate-800 dark:text-white uppercase italic">Система</h1>
        </div>

        <div class="p-6 bg-[#0a0a0a] rounded-3xl mb-8 shadow-2xl border border-slate-800 relative overflow-hidden">
            <div class="absolute top-0 right-0 w-32 h-32 bg-emerald-500/20 rounded-full blur-3xl"></div>
            <div class="flex items-center gap-5 relative z-10">
                <div class="w-16 h-16 bg-slate-900 border border-emerald-500/50 rounded-2xl flex items-center justify-center text-4xl font-black text-emerald-400 shadow-[0_0_20px_rgba(16,185,129,0.3)]">
                    K
                </div>
                <div>
                    <h2 class="text-2xl font-black text-white uppercase tracking-widest">Кактак</h2>
                    <p class="text-emerald-500 font-bold text-[10px] uppercase tracking-[0.2em] mt-1">{title}</p>
                </div>
            </div>
            
            <div class="mt-8 relative z-10">
                <div class="flex justify-between items-end mb-2">
                    <span class="text-[10px] font-bold text-slate-500 uppercase tracking-widest">Уровень {level}</span>
                    <span class="text-xs font-black text-emerald-500">{current_xp}/100 XP</span>
                </div>
                <div class="w-full h-2 bg-slate-800 rounded-full overflow-hidden">
                    <div class="h-full bg-emerald-500 rounded-full shadow-[0_0_10px_rgba(16,185,129,0.8)]" style="width: {current_xp}%"></div>
                </div>
            </div>
        </div>

        <div class="mb-8">
            <h3 class="text-[11px] font-black text-slate-500 uppercase tracking-widest mb-3 px-1">Настройки</h3>
            <div class="flex flex-col gap-3">
                <form action="/set_goal" method="post" class="flex gap-2 h-14">
                    <input type="number" name="new_goal" placeholder="Новый лимит ккал" required class="flex-1 min-w-0 px-5 font-bold bg-white dark:bg-slate-800 border-2 border-slate-100 dark:border-slate-700 rounded-2xl focus:outline-none focus:border-emerald-500 transition">
                    <button type="submit" class="px-6 bg-slate-800 dark:bg-slate-700 text-white font-black uppercase tracking-widest rounded-2xl transition active:scale-95">Set</button>
                </form>
                <form action="/reset" method="post" class="h-14">
                    <button type="submit" class="w-full h-full bg-red-50 dark:bg-red-500/10 text-red-600 dark:text-red-500 font-black uppercase tracking-widest rounded-2xl border border-red-200 dark:border-red-500/30 transition active:scale-95 text-sm">
                        Сбросить день (Еда + Вода)
                    </button>
                </form>
            </div>
        </div>

        <div>
            <h3 class="text-[11px] font-black text-slate-500 uppercase tracking-widest mb-3 px-1">Друзья</h3>
            <form action="/add_friend" method="post" class="flex gap-2 h-12 mb-4">
                <input type="text" name="friend_name" placeholder="ID друга" required class="flex-1 min-w-0 px-4 font-bold bg-white dark:bg-slate-800 border-2 border-slate-100 dark:border-slate-700 rounded-xl focus:outline-none focus:border-emerald-500 transition">
                <button type="submit" class="px-5 bg-emerald-500 text-white font-black uppercase rounded-xl transition active:scale-95 text-sm">Add</button>
            </form>
            <ul class="flex flex-col gap-1">
                {friends_html if friends_html else '<div class="text-center text-slate-400 font-bold py-6 bg-slate-50 dark:bg-slate-800/30 rounded-xl border-2 border-dashed border-slate-200 dark:border-slate-700 uppercase tracking-widest text-xs">Нет друзей</div>'}
            </ul>
        </div>
    """
    return render_page("Профиль 5.0", content, active_tab="profile")


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

@app.post("/set_goal")
async def set_goal(new_goal: int = Form(...)):
    with sqlite3.connect("calories.db") as conn:
        conn.execute("UPDATE settings SET daily_goal = ? WHERE id = 1", (new_goal,))
    return RedirectResponse(url="/profile", status_code=303)

@app.post("/reset")
async def reset_records():
    with sqlite3.connect("calories.db") as conn:
        conn.execute("DELETE FROM history")
        conn.execute("UPDATE settings SET water = 0 WHERE id = 1")
    return RedirectResponse(url="/profile", status_code=303)

@app.post("/add_friend")
async def add_friend(friend_name: str = Form(...)):
    with sqlite3.connect("calories.db") as conn:
        conn.execute("INSERT INTO friends (name) VALUES (?)", (friend_name,))
    return RedirectResponse(url="/profile", status_code=303)

@app.post("/delete_friend/{friend_id}")
async def delete_friend(friend_id: int):
    with sqlite3.connect("calories.db") as conn:
        conn.execute("DELETE FROM friends WHERE id = ?", (friend_id,))
    return RedirectResponse(url="/profile", status_code=303)

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)