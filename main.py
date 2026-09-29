from fastapi import FastAPI, Form
from fastapi.responses import HTMLResponse, RedirectResponse
import uvicorn
import sqlite3

app = FastAPI()

def init_db():
    with sqlite3.connect("calories.db") as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS history (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, cal INTEGER NOT NULL)")
        conn.execute("CREATE TABLE IF NOT EXISTS settings (id INTEGER PRIMARY KEY CHECK (id = 1), daily_goal INTEGER NOT NULL)")
        conn.execute("CREATE TABLE IF NOT EXISTS friends (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL)")
        conn.execute("INSERT OR IGNORE INTO settings (id, daily_goal) VALUES (1, 2000)")
        
        try:
            conn.execute("ALTER TABLE history ADD COLUMN p INTEGER DEFAULT 0")
        except sqlite3.OperationalError:
            pass
            
        try:
            conn.execute("ALTER TABLE history ADD COLUMN f INTEGER DEFAULT 0")
        except sqlite3.OperationalError:
            pass
            
        try:
            conn.execute("ALTER TABLE history ADD COLUMN c INTEGER DEFAULT 0")
        except sqlite3.OperationalError:
            pass
            
        try:
            conn.execute("ALTER TABLE settings ADD COLUMN level INTEGER DEFAULT 1")
        except sqlite3.OperationalError:
            pass
            
        try:
            conn.execute("ALTER TABLE settings ADD COLUMN xp INTEGER DEFAULT 0")
        except sqlite3.OperationalError:
            pass

init_db()


def get_rpg_stats(total_meals: int):
    xp_total = total_meals * 15
    level = (xp_total // 100) + 1
    current_xp = xp_total % 100
    
    titles = [
        (20, "Грандмастер массы"),
        (10, "Кибер-Котлета"),
        (5, "Охотник за калориями"),
        (3, "Любитель фастфуда"),
        (1, "Голодный новичок")
    ]
    
    title = "Голодный новичок"
    for lvl, t in titles:
        if level >= lvl:
            title = t
            break
            
    return level, current_xp, title


def render_page(title: str, content: str, scripts: str = ""):
    return f"""
    <!DOCTYPE html>
    <html lang="ru" class="antialiased">
    <head>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>{title}</title>
        <script src="https://cdn.tailwindcss.com"></script>
        <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
        <script src="https://cdn.jsdelivr.net/npm/canvas-confetti@1.6.0/dist/confetti.browser.min.js"></script>
        <script>
            tailwind.config = {{
                darkMode: 'media',
                theme: {{ extend: {{ fontFamily: {{ sans: ['system-ui', 'sans-serif'] }} }} }}
            }}
        </script>
        <style>
            body {{ background-color: #f8fafc; }}
            @media (prefers-color-scheme: dark) {{ body {{ background-color: #0f172a; }} }}
            .custom-scrollbar::-webkit-scrollbar {{ height: 4px; width: 4px; }}
            .custom-scrollbar::-webkit-scrollbar-thumb {{ background-color: #cbd5e1; border-radius: 10px; }}
            .dark .custom-scrollbar::-webkit-scrollbar-thumb {{ background-color: #475569; }}
            
            @keyframes slideDown {{
                0% {{ transform: translate(-50%, -100%); opacity: 0; }}
                100% {{ transform: translate(-50%, 0); opacity: 1; }}
            }}
            .toast-anim {{ animation: slideDown 0.4s cubic-bezier(0.16, 1, 0.3, 1) forwards; }}
        </style>
    </head>
    <body class="text-slate-800 dark:text-slate-200 transition-colors duration-300 min-h-screen py-10 px-4 relative">
        
        <div id="toast-container" class="fixed top-4 left-1/2 -translate-x-1/2 z-50 flex flex-col gap-2"></div>

        <div class="max-w-md mx-auto p-6 sm:p-8 bg-white dark:bg-slate-900 rounded-[2.5rem] shadow-2xl border border-slate-100 dark:border-slate-800/60 overflow-hidden relative">
            {content}
        </div>
        
        <script>
            function showToast(message, type = 'success') {{
                const container = document.getElementById('toast-container');
                const toast = document.createElement('div');
                const bgColor = type === 'success' ? 'bg-emerald-500' : 'bg-red-500';
                
                toast.className = `toast-anim flex items-center gap-3 px-6 py-3 rounded-2xl shadow-lg text-white text-sm font-bold ${{bgColor}}`;
                toast.innerHTML = type === 'success' 
                    ? `<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"></polyline></svg> ${{message}}`
                    : `<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="6" x2="6" y2="18"></line><line x1="6" y1="6" x2="18" y2="18"></line></svg> ${{message}}`;
                
                container.appendChild(toast);
                setTimeout(() => {{ toast.style.opacity = '0'; toast.style.transform = 'translate(-50%, -20px)'; toast.style.transition = 'all 0.3s'; setTimeout(() => toast.remove(), 300); }}, 3000);
            }}

            const urlParams = new URLSearchParams(window.location.search);
            if (urlParams.has('added')) {{
                confetti({{ particleCount: 80, spread: 60, origin: {{ y: 0.8 }}, colors: ['#10b981', '#34d399', '#f59e0b'] }});
                showToast('Добавлено! +15 XP', 'success');
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


@app.get("/", response_class=HTMLResponse)
async def get_index():
    with sqlite3.connect("calories.db") as conn:
        cursor = conn.execute("SELECT id, name, cal FROM history ORDER BY id DESC")
        history = [{"id": row[0], "name": row[1], "cal": row[2]} for row in cursor.fetchall()]
        
        cursor = conn.execute("SELECT SUM(cal) FROM history")
        total_calories = cursor.fetchone()[0] or 0
        
        cursor = conn.execute("SELECT daily_goal FROM settings WHERE id = 1")
        daily_goal = cursor.fetchone()[0]

    items_html = "".join(
        f"""
        <li class="flex justify-between items-center bg-white dark:bg-slate-800 p-4 rounded-2xl shadow-sm border border-slate-100 dark:border-slate-700 mb-3 transition hover:shadow-md group">
            <div class="flex flex-col">
                <span class="font-semibold text-slate-800 dark:text-slate-100 group-hover:text-emerald-500 transition-colors">{item['name']}</span>
                <span class="text-sm font-bold text-emerald-500/80 mt-0.5">{item['cal']} ккал</span>
            </div>
            <form action="/delete/{item['id']}" method="post" class="m-0 flex items-center">
                <button type="submit" class="text-slate-300 hover:text-red-500 bg-slate-50 dark:bg-slate-900/50 hover:bg-red-50 dark:hover:bg-red-900/30 w-10 h-10 rounded-xl transition-all flex items-center justify-center shrink-0">
                    <svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M3 6h18"></path><path d="M19 6v14c0 1-1 2-2 2H7c-1 0-2-1-2-2V6"></path><path d="M8 6V4c0-1 1-2 2-2h4c1 0 2 1 2 2v2"></path></svg>
                </button>
            </form>
        </li>
        """
        for item in history
    )
    
    remaining = max(0, daily_goal - total_calories)
    is_over = total_calories > daily_goal
    color_class = "text-emerald-500" if not is_over else "text-red-500"

    content = f"""
        <div class="flex justify-between items-center mb-8">
            <h1 class="text-3xl font-black tracking-tighter text-slate-800 dark:text-white">Счетчик<span class="text-emerald-500">.</span></h1>
            <div class="flex gap-2 items-center">
                <div class="text-xs font-bold px-3 py-2 bg-slate-100 dark:bg-slate-800 rounded-full text-slate-500 dark:text-slate-400 uppercase tracking-wider hidden sm:block">
                    Цель: {daily_goal}
                </div>
                <a href="/profile" class="w-10 h-10 bg-slate-100 dark:bg-slate-800 hover:bg-emerald-500 hover:text-white text-slate-600 dark:text-slate-300 rounded-full flex items-center justify-center transition-all shadow-sm border border-slate-200 dark:border-slate-700">
                    <svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"></path><circle cx="12" cy="7" r="4"></circle></svg>
                </a>
            </div>
        </div>

        <div class="relative flex justify-center items-center mb-10 h-60">
            <canvas id="calorieChart"></canvas>
            <div class="absolute flex flex-col items-center justify-center pointer-events-none mt-2">
                <span class="text-6xl font-black {color_class} tracking-tighter drop-shadow-sm transition-colors">{total_calories}</span>
                <span class="text-xs text-slate-400 font-bold mt-2 uppercase tracking-widest bg-slate-100 dark:bg-slate-800 px-3 py-1 rounded-full">ккал</span>
            </div>
        </div>

        <div class="flex gap-2 overflow-x-auto custom-scrollbar pb-3 mb-4 -mx-2 px-2">
            <form action="/add" method="post" class="shrink-0"><input type="hidden" name="food" value="💧 Вода"><input type="hidden" name="cal" value="0"><button type="submit" class="px-4 py-2 bg-blue-50 dark:bg-blue-900/20 text-blue-600 dark:text-blue-400 text-sm font-bold rounded-xl border border-blue-100 dark:border-blue-800 hover:bg-blue-100 transition whitespace-nowrap">💧 Вода (0)</button></form>
            <form action="/add" method="post" class="shrink-0"><input type="hidden" name="food" value="☕ Кофе"><input type="hidden" name="cal" value="15"><button type="submit" class="px-4 py-2 bg-amber-50 dark:bg-amber-900/20 text-amber-700 dark:text-amber-500 text-sm font-bold rounded-xl border border-amber-100 dark:border-amber-800 hover:bg-amber-100 transition whitespace-nowrap">☕ Кофе (15)</button></form>
            <form action="/add" method="post" class="shrink-0"><input type="hidden" name="food" value="⚡ Энергетик"><input type="hidden" name="cal" value="110"><button type="submit" class="px-4 py-2 bg-fuchsia-50 dark:bg-fuchsia-900/20 text-fuchsia-600 dark:text-fuchsia-400 text-sm font-bold rounded-xl border border-fuchsia-100 dark:border-fuchsia-800 hover:bg-fuchsia-100 transition whitespace-nowrap">⚡ Энергос (110)</button></form>
            <form action="/add" method="post" class="shrink-0"><input type="hidden" name="food" value="🍔 Фастфуд"><input type="hidden" name="cal" value="500"><button type="submit" class="px-4 py-2 bg-orange-50 dark:bg-orange-900/20 text-orange-600 dark:text-orange-400 text-sm font-bold rounded-xl border border-orange-100 dark:border-orange-800 hover:bg-orange-100 transition whitespace-nowrap">🍔 Фастфуд (500)</button></form>
        </div>

        <form action="/add" method="post" class="flex gap-2 mb-10 h-14">
            <input type="text" name="food" placeholder="Что съели?" required class="flex-1 min-w-0 px-5 font-medium bg-slate-50 dark:bg-slate-800 border-2 border-slate-100 dark:border-slate-700 rounded-2xl focus:outline-none focus:border-emerald-500 dark:focus:border-emerald-500 transition placeholder-slate-400 text-slate-700 dark:text-slate-200">
            <input type="number" name="cal" placeholder="Ккал" required class="w-20 shrink-0 min-w-0 px-2 font-medium bg-slate-50 dark:bg-slate-800 border-2 border-slate-100 dark:border-slate-700 rounded-2xl focus:outline-none focus:border-emerald-500 dark:focus:border-emerald-500 transition placeholder-slate-400 text-slate-700 dark:text-slate-200 text-center">
            <button type="submit" class="w-14 shrink-0 bg-emerald-500 hover:bg-emerald-600 text-white rounded-2xl shadow-lg shadow-emerald-500/40 transition transform hover:scale-105 active:scale-95 flex items-center justify-center">
                <svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><line x1="12" y1="5" x2="12" y2="19"></line><line x1="5" y1="12" x2="19" y2="12"></line></svg>
            </button>
        </form>

        <div class="mb-4">
            <div class="flex justify-between items-end mb-4 px-1">
                <h3 class="text-lg font-black text-slate-800 dark:text-slate-200">Лента питания</h3>
                <span class="text-xs font-bold px-3 py-1.5 bg-slate-100 dark:bg-slate-800 rounded-full text-slate-500">Осталось: {remaining}</span>
            </div>
            <ul class="max-h-[280px] overflow-y-auto pr-2 custom-scrollbar">
                {items_html if items_html else '<div class="text-center text-slate-400 dark:text-slate-500 font-medium py-10 bg-slate-50 dark:bg-slate-800/50 rounded-2xl border-2 border-dashed border-slate-200 dark:border-slate-700">Тут пока пусто. Закинь топлива! ⛽</div>'}
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

            new Chart(ctx, {{
                type: 'doughnut',
                data: {{
                    labels: ['Съедено', 'Осталось'],
                    datasets: [{{
                        data: [consumed, remaining],
                        backgroundColor: [highlightColor, emptyColor],
                        borderWidth: 0,
                        cutout: '85%',
                        borderRadius: 30
                    }}]
                }},
                options: {{ responsive: true, maintainAspectRatio: false, animation: {{ animateScale: true, animateRotate: true, duration: 1500, easing: 'easeOutExpo' }}, plugins: {{ legend: {{ display: false }}, tooltip: {{ enabled: false }} }} }}
            }});
        </script>
    """
    
    return render_page("Счетчик 3.0", content, scripts)


@app.get("/profile", response_class=HTMLResponse)
async def get_profile():
    with sqlite3.connect("calories.db") as conn:
        cursor = conn.execute("SELECT id, name FROM friends ORDER BY id DESC")
        friends = [{"id": row[0], "name": row[1]} for row in cursor.fetchall()]
        
        cursor = conn.execute("SELECT COUNT(*) FROM history")
        total_meals = cursor.fetchone()[0]

    level, current_xp, title = get_rpg_stats(total_meals)
    xp_progress = current_xp

    friends_html = "".join(
        f"""
        <li class="flex justify-between items-center bg-white dark:bg-slate-800 p-3 rounded-2xl shadow-sm border border-slate-100 dark:border-slate-700 mb-2 group">
            <div class="flex items-center gap-3">
                <div class="w-10 h-10 bg-gradient-to-tr from-emerald-400 to-cyan-400 text-white rounded-full flex items-center justify-center font-black shadow-inner">
                    {friend['name'][0].upper()}
                </div>
                <span class="font-bold text-slate-800 dark:text-slate-100">{friend['name']}</span>
            </div>
            <form action="/delete_friend/{friend['id']}" method="post" class="m-0 flex items-center opacity-50 group-hover:opacity-100 transition-opacity">
                <button type="submit" class="text-slate-400 hover:text-red-500 p-2 transition-colors">
                    <svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="6" x2="6" y2="18"></line><line x1="6" y1="6" x2="18" y2="18"></line></svg>
                </button>
            </form>
        </li>
        """
        for friend in friends
    )

    content = f"""
        <div class="flex items-center gap-4 mb-8">
            <a href="/" class="w-10 h-10 bg-slate-100 dark:bg-slate-800 hover:bg-slate-200 dark:hover:bg-slate-700 text-slate-600 dark:text-slate-300 rounded-full flex items-center justify-center transition shadow-sm border border-slate-200 dark:border-slate-700">
                <svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><line x1="19" y1="12" x2="5" y2="12"></line><polyline points="12 19 5 12 12 5"></polyline></svg>
            </a>
            <h1 class="text-2xl font-black tracking-tight text-slate-800 dark:text-white">Профиль</h1>
        </div>

        <div class="relative p-6 bg-slate-900 rounded-3xl mb-8 shadow-2xl overflow-hidden border border-slate-700">
            <div class="absolute top-0 right-0 -mr-8 -mt-8 w-32 h-32 rounded-full bg-emerald-500 blur-3xl opacity-20 pointer-events-none"></div>
            <div class="absolute bottom-0 left-0 -ml-8 -mb-8 w-24 h-24 rounded-full bg-cyan-500 blur-2xl opacity-20 pointer-events-none"></div>
            
            <div class="flex items-center gap-5 relative z-10">
                <div class="w-16 h-16 bg-gradient-to-br from-emerald-400 to-cyan-500 rounded-2xl flex items-center justify-center text-3xl font-black shadow-lg shadow-emerald-500/30 text-white transform rotate-3">
                    К
                </div>
                <div>
                    <h2 class="text-2xl font-black text-white tracking-tight">Кактак</h2>
                    <p class="text-emerald-400 font-bold text-sm uppercase tracking-widest">{title}</p>
                </div>
            </div>
            
            <div class="mt-6 relative z-10">
                <div class="flex justify-between items-end mb-2">
                    <span class="text-xs font-bold text-slate-400 uppercase tracking-wider">Уровень {level}</span>
                    <span class="text-xs font-bold text-emerald-400">{current_xp} / 100 XP</span>
                </div>
                <div class="w-full h-3 bg-slate-800 rounded-full overflow-hidden border border-slate-700">
                    <div class="h-full bg-gradient-to-r from-emerald-500 to-cyan-400 rounded-full transition-all duration-1000 ease-out" style="width: {xp_progress}%"></div>
                </div>
            </div>
        </div>

        <div class="mb-8">
            <h3 class="text-lg font-black text-slate-800 dark:text-slate-200 mb-4 px-1">Контроль</h3>
            <div class="flex flex-col gap-3">
                <form action="/set_goal" method="post" class="flex gap-2 h-14">
                    <input type="number" name="new_goal" placeholder="Новая норма" required class="flex-1 min-w-0 px-5 font-medium bg-slate-50 dark:bg-slate-800 border-2 border-slate-100 dark:border-slate-700 rounded-2xl focus:outline-none focus:border-emerald-500 transition text-sm">
                    <button type="submit" class="shrink-0 px-6 bg-slate-800 dark:bg-slate-700 hover:bg-slate-700 dark:hover:bg-slate-600 text-white text-sm font-bold rounded-2xl transition shadow-lg">Задать</button>
                </form>
                <form action="/reset" method="post" class="h-14">
                    <button type="submit" class="w-full h-full px-5 bg-red-500/10 hover:bg-red-500/20 text-red-600 dark:text-red-400 text-sm font-bold rounded-2xl border border-red-500/20 transition">
                        Стереть историю за день
                    </button>
                </form>
            </div>
        </div>

        <div>
            <h3 class="text-lg font-black text-slate-800 dark:text-slate-200 mb-4 px-1">Друзья</h3>
            <form action="/add_friend" method="post" class="flex gap-2 h-12 mb-4">
                <input type="text" name="friend_name" placeholder="Ник друга" required class="flex-1 min-w-0 px-4 font-medium bg-slate-50 dark:bg-slate-800 border-2 border-slate-100 dark:border-slate-700 rounded-xl focus:outline-none focus:border-emerald-500 transition text-sm">
                <button type="submit" class="shrink-0 px-5 bg-emerald-500 hover:bg-emerald-600 text-white text-sm font-bold rounded-xl transition shadow-lg shadow-emerald-500/30">Добавить</button>
            </form>
            <ul class="max-h-[250px] overflow-y-auto pr-2 custom-scrollbar">
                {friends_html if friends_html else '<div class="text-center text-slate-400 dark:text-slate-500 font-medium py-6 bg-slate-50 dark:bg-slate-800/50 rounded-2xl border-2 border-dashed border-slate-200 dark:border-slate-700 text-sm">Добавь друзей для совместных челленджей!</div>'}
            </ul>
        </div>
    """
    return render_page("Профиль 3.0", content)


@app.post("/add")
async def add_record(food: str = Form(...), cal: int = Form(...)):
    with sqlite3.connect("calories.db") as conn:
        conn.execute("INSERT INTO history (name, cal) VALUES (?, ?)", (food, cal))
    return RedirectResponse(url="/?added=1", status_code=303)


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