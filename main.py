from fastapi import FastAPI, Form
from fastapi.responses import HTMLResponse, RedirectResponse
import uvicorn
import sqlite3

app = FastAPI()

# 1. Инициализация БД (добавлена таблица friends)
def init_db():
    with sqlite3.connect("calories.db") as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                cal INTEGER NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                daily_goal INTEGER NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS friends (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL
            )
        """)
        conn.execute("INSERT OR IGNORE INTO settings (id, daily_goal) VALUES (1, 2000)")
init_db()


# 2. Функция-шаблонизатор, чтобы не дублировать HTML-каркас
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
        <script>
            tailwind.config = {{
                darkMode: 'media',
                theme: {{ extend: {{ fontFamily: {{ sans: ['system-ui', 'sans-serif'] }} }} }}
            }}
        </script>
        <style>
            body {{ background-color: #f8fafc; }}
            @media (prefers-color-scheme: dark) {{ body {{ background-color: #0f172a; }} }}
            .custom-scrollbar::-webkit-scrollbar {{ width: 6px; }}
            .custom-scrollbar::-webkit-scrollbar-thumb {{ background-color: #cbd5e1; border-radius: 10px; }}
            .dark .custom-scrollbar::-webkit-scrollbar-thumb {{ background-color: #475569; }}
        </style>
    </head>
    <body class="text-slate-800 dark:text-slate-200 transition-colors duration-300 min-h-screen py-10 px-4">
        <div class="max-w-md mx-auto p-6 sm:p-8 bg-white dark:bg-slate-900 rounded-[2.5rem] shadow-2xl border border-slate-100 dark:border-slate-800/60 overflow-hidden">
            {content}
        </div>
        {scripts}
    </body>
    </html>
    """

# ================= РОУТЫ ГЛАВНОЙ СТРАНИЦЫ =================

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
        <li class="flex justify-between items-center bg-white dark:bg-slate-800 p-4 rounded-2xl shadow-sm border border-slate-100 dark:border-slate-700 mb-3 transition hover:shadow-md">
            <div class="flex flex-col">
                <span class="font-semibold text-slate-800 dark:text-slate-100">{item['name']}</span>
                <span class="text-sm font-medium text-emerald-500 mt-1">{item['cal']} ккал</span>
            </div>
            <form action="/delete/{item['id']}" method="post" class="m-0 flex items-center">
                <button type="submit" class="text-slate-400 hover:text-red-500 bg-slate-50 dark:bg-slate-900/50 hover:bg-red-50 dark:hover:bg-red-900/30 w-10 h-10 rounded-xl transition-all flex items-center justify-center shrink-0">
                    <svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 6h18"></path><path d="M19 6v14c0 1-1 2-2 2H7c-1 0-2-1-2-2V6"></path><path d="M8 6V4c0-1 1-2 2-2h4c1 0 2 1 2 2v2"></path></svg>
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
            <h1 class="text-2xl font-black tracking-tight text-slate-800 dark:text-white">Счетчик<br><span class="text-emerald-500">Калорий</span></h1>
            <div class="flex gap-2 items-center">
                <div class="text-xs font-bold px-3 py-2 bg-slate-100 dark:bg-slate-800 rounded-full text-slate-500 dark:text-slate-400 uppercase tracking-wider hidden sm:block">
                    Цель: {daily_goal}
                </div>
                <!-- Кнопка перехода в Профиль -->
                <a href="/profile" class="w-10 h-10 bg-slate-100 dark:bg-slate-800 hover:bg-slate-200 dark:hover:bg-slate-700 text-slate-600 dark:text-slate-300 rounded-full flex items-center justify-center transition shadow-sm border border-slate-200 dark:border-slate-700">
                    <svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"></path><circle cx="12" cy="7" r="4"></circle></svg>
                </a>
            </div>
        </div>

        <div class="relative flex justify-center items-center mb-10 h-56">
            <canvas id="calorieChart"></canvas>
            <div class="absolute flex flex-col items-center justify-center pointer-events-none mt-2">
                <span class="text-5xl font-black {color_class} tracking-tighter drop-shadow-sm transition-colors">{total_calories}</span>
                <span class="text-sm text-slate-400 font-semibold mt-1 uppercase tracking-widest">ккал</span>
            </div>
        </div>

        <form action="/add" method="post" class="flex gap-2 mb-10 h-14">
            <input type="text" name="food" placeholder="Что съели?" required class="flex-1 min-w-0 px-4 font-medium bg-slate-50 dark:bg-slate-800 border-2 border-slate-100 dark:border-slate-700 rounded-2xl focus:outline-none focus:border-emerald-500 dark:focus:border-emerald-500 transition placeholder-slate-400 text-slate-700 dark:text-slate-200 shadow-inner">
            <input type="number" name="cal" placeholder="Ккал" required class="w-20 shrink-0 min-w-0 px-2 font-medium bg-slate-50 dark:bg-slate-800 border-2 border-slate-100 dark:border-slate-700 rounded-2xl focus:outline-none focus:border-emerald-500 dark:focus:border-emerald-500 transition placeholder-slate-400 text-slate-700 dark:text-slate-200 shadow-inner text-center">
            <button type="submit" class="w-14 shrink-0 bg-emerald-500 hover:bg-emerald-600 text-white rounded-2xl shadow-lg shadow-emerald-500/40 transition transform hover:-translate-y-1 active:translate-y-0 flex items-center justify-center">
                <svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><line x1="12" y1="5" x2="12" y2="19"></line><line x1="5" y1="12" x2="19" y2="12"></line></svg>
            </button>
        </form>

        <div class="mb-8">
            <div class="flex justify-between items-end mb-4">
                <h3 class="text-lg font-bold text-slate-700 dark:text-slate-300">История</h3>
                <span class="text-sm font-semibold text-slate-400">Осталось: {remaining}</span>
            </div>
            <ul class="max-h-[300px] overflow-y-auto pr-2 custom-scrollbar">
                {items_html if items_html else '<div class="text-center text-slate-400 dark:text-slate-500 font-medium py-8 bg-slate-50 dark:bg-slate-800/50 rounded-2xl border-2 border-dashed border-slate-200 dark:border-slate-700">Тут пока пусто. Пора перекусить! 🥪</div>'}
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
                        cutout: '82%',
                        borderRadius: 20
                    }}]
                }},
                options: {{ responsive: true, maintainAspectRatio: false, animation: {{ animateScale: true, animateRotate: true, duration: 1200 }}, plugins: {{ legend: {{ display: false }}, tooltip: {{ enabled: false }} }} }}
            }});
        </script>
    """
    
    return render_page("Счетчик Калорий", content, scripts)


# ================= РОУТЫ ПРОФИЛЯ =================

@app.get("/profile", response_class=HTMLResponse)
async def get_profile():
    with sqlite3.connect("calories.db") as conn:
        cursor = conn.execute("SELECT id, name FROM friends ORDER BY id DESC")
        friends = [{"id": row[0], "name": row[1]} for row in cursor.fetchall()]
        
        cursor = conn.execute("SELECT COUNT(*) FROM history")
        total_meals = cursor.fetchone()[0]

    friends_html = "".join(
        f"""
        <li class="flex justify-between items-center bg-white dark:bg-slate-800 p-3 rounded-2xl shadow-sm border border-slate-100 dark:border-slate-700 mb-2">
            <div class="flex items-center gap-3">
                <div class="w-10 h-10 bg-slate-100 dark:bg-slate-700 text-slate-500 dark:text-slate-300 rounded-full flex items-center justify-center font-bold text-lg border border-slate-200 dark:border-slate-600">
                    {friend['name'][0].upper()}
                </div>
                <span class="font-semibold text-slate-800 dark:text-slate-100">{friend['name']}</span>
            </div>
            <form action="/delete_friend/{friend['id']}" method="post" class="m-0 flex items-center">
                <button type="submit" class="text-slate-400 hover:text-red-500 p-2 transition-colors">
                    <svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="6" x2="6" y2="18"></line><line x1="6" y1="6" x2="18" y2="18"></line></svg>
                </button>
            </form>
        </li>
        """
        for friend in friends
    )

    content = f"""
        <!-- Шапка с кнопкой Назад -->
        <div class="flex items-center gap-4 mb-8">
            <a href="/" class="w-10 h-10 bg-slate-100 dark:bg-slate-800 hover:bg-slate-200 dark:hover:bg-slate-700 text-slate-600 dark:text-slate-300 rounded-full flex items-center justify-center transition shadow-sm border border-slate-200 dark:border-slate-700">
                <svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><line x1="19" y1="12" x2="5" y2="12"></line><polyline points="12 19 5 12 12 5"></polyline></svg>
            </a>
            <h1 class="text-2xl font-black tracking-tight text-slate-800 dark:text-white">Профиль</h1>
        </div>

        <!-- Карточка пользователя -->
        <div class="flex items-center gap-5 p-5 bg-gradient-to-br from-emerald-400 to-teal-500 rounded-3xl mb-8 shadow-lg shadow-emerald-500/30 text-white">
            <div class="w-16 h-16 bg-white/20 backdrop-blur-sm rounded-full flex items-center justify-center text-3xl font-black border border-white/30 shadow-inner">
                К
            </div>
            <div>
                <h2 class="text-xl font-bold">Кактак</h2>
                <p class="text-emerald-50 font-medium text-sm mt-0.5">Добавлено приемов пищи: {total_meals}</p>
            </div>
        </div>

        <!-- Раздел настроек (перенесен сюда для красоты) -->
        <div class="mb-8">
            <h3 class="text-lg font-bold text-slate-700 dark:text-slate-300 mb-4">Настройки</h3>
            <div class="flex flex-col gap-3">
                <form action="/set_goal" method="post" class="flex gap-2 h-12">
                    <input type="number" name="new_goal" placeholder="Новая суточная норма" required class="flex-1 min-w-0 px-4 font-medium bg-slate-50 dark:bg-slate-800 border-2 border-slate-100 dark:border-slate-700 rounded-xl focus:outline-none focus:border-emerald-500 transition text-sm">
                    <button type="submit" class="shrink-0 px-5 bg-slate-200 dark:bg-slate-700 hover:bg-slate-300 dark:hover:bg-slate-600 text-slate-700 dark:text-slate-300 text-sm font-bold rounded-xl transition">Изменить</button>
                </form>
                <form action="/reset" method="post" class="h-12">
                    <button type="submit" class="w-full h-full px-5 bg-red-100 dark:bg-red-900/30 text-red-600 dark:text-red-400 hover:bg-red-200 dark:hover:bg-red-900/50 text-sm font-bold rounded-xl transition">
                        Сбросить всю историю за день
                    </button>
                </form>
            </div>
        </div>

        <!-- Раздел Друзей -->
        <div>
            <h3 class="text-lg font-bold text-slate-700 dark:text-slate-300 mb-4">Друзья</h3>
            
            <form action="/add_friend" method="post" class="flex gap-2 h-12 mb-4">
                <input type="text" name="friend_name" placeholder="Имя друга" required class="flex-1 min-w-0 px-4 font-medium bg-slate-50 dark:bg-slate-800 border-2 border-slate-100 dark:border-slate-700 rounded-xl focus:outline-none focus:border-emerald-500 transition text-sm">
                <button type="submit" class="shrink-0 px-5 bg-emerald-500 hover:bg-emerald-600 text-white text-sm font-bold rounded-xl transition shadow-lg shadow-emerald-500/30">Добавить</button>
            </form>

            <ul class="max-h-[250px] overflow-y-auto pr-2 custom-scrollbar">
                {friends_html if friends_html else '<div class="text-center text-slate-400 dark:text-slate-500 font-medium py-6 bg-slate-50 dark:bg-slate-800/50 rounded-2xl border-2 border-dashed border-slate-200 dark:border-slate-700 text-sm">У вас пока нет друзей в приложении 😔</div>'}
            </ul>
        </div>
    """
    return render_page("Профиль - Счетчик Калорий", content)


# ================= ЭНДПОИНТЫ ДЕЙСТВИЙ =================

@app.post("/add")
async def add_record(food: str = Form(...), cal: int = Form(...)):
    with sqlite3.connect("calories.db") as conn:
        conn.execute("INSERT INTO history (name, cal) VALUES (?, ?)", (food, cal))
    return RedirectResponse(url="/", status_code=303)

@app.post("/delete/{item_id}")
async def delete_record(item_id: int):
    with sqlite3.connect("calories.db") as conn:
        conn.execute("DELETE FROM history WHERE id = ?", (item_id,))
    return RedirectResponse(url="/", status_code=303)

@app.post("/set_goal")
async def set_goal(new_goal: int = Form(...)):
    with sqlite3.connect("calories.db") as conn:
        conn.execute("UPDATE settings SET daily_goal = ? WHERE id = 1", (new_goal,))
    # Возвращаемся в профиль, так как форма теперь там
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