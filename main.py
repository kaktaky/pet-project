from fastapi import FastAPI, Form
from fastapi.responses import HTMLResponse, RedirectResponse
import uvicorn
import sqlite3

app = FastAPI()
DAILY_GOAL = 2000 

# Автоматически создаем базу данных и таблицу при первом запуске
def init_db():
    with sqlite3.connect("calories.db") as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                cal INTEGER NOT NULL
            )
        """)
init_db()

@app.get("/", response_class=HTMLResponse)
async def get_index():
    with sqlite3.connect("calories.db") as conn:
        # Достаем все записи из БД (новые сверху)
        cursor = conn.execute("SELECT name, cal FROM history ORDER BY id DESC")
        history = [{"name": row[0], "cal": row[1]} for row in cursor.fetchall()]
        
        # Заставляем саму базу данных посчитать сумму калорий
        cursor = conn.execute("SELECT SUM(cal) FROM history")
        total_calories = cursor.fetchone()[0] or 0

    items_html = "".join(
        f"<li><span>{item['name']}</span> <b>{item['cal']} ккал</b></li>" 
        for item in history
    )
    
    remaining = max(0, DAILY_GOAL - total_calories)
    percent = min(100, int((total_calories / DAILY_GOAL) * 100)) if DAILY_GOAL else 0
    color = "#10b981" if total_calories <= DAILY_GOAL else "#ef4444"

    html = f"""
    <!DOCTYPE html>
    <html lang="ru">
    <head>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Счетчик калорий</title>
        <style>
            /* CSS-переменные для автоматического переключения светлой/темной темы */
            :root {{ --bg: #ffffff; --text: #333; --dash-bg: #f4f4f5; --item-bg: #fff; --border: #eee; }}
            @media (prefers-color-scheme: dark) {{
                :root {{ --bg: #1e1e2e; --text: #cdd6f4; --dash-bg: #313244; --item-bg: #45475a; --border: #313244; }}
            }}
            
            body {{ font-family: system-ui, sans-serif; max-width: 500px; margin: 40px auto; padding: 20px; background: var(--bg); color: var(--text); transition: background 0.3s; }}
            .dashboard {{ background: var(--dash-bg); padding: 30px; border-radius: 12px; text-align: center; margin-bottom: 20px; }}
            .calories {{ font-size: 48px; color: {color}; font-weight: bold; margin: 10px 0; }}
            .goal-info {{ color: #a6adc8; margin-bottom: 15px; font-size: 14px; }}
            
            .progress-bar {{ width: 100%; height: 12px; background: var(--border); border-radius: 6px; overflow: hidden; }}
            .progress-fill {{ width: {percent}%; height: 100%; background: {color}; transition: width 0.4s ease-out; }}
            
            form {{ display: flex; gap: 10px; margin-bottom: 20px; flex-wrap: wrap; }}
            input {{ flex: 1; min-width: 120px; padding: 10px; border: 1px solid var(--border); border-radius: 6px; font-size: 16px; background: var(--item-bg); color: var(--text); }}
            
            .btn-add {{ padding: 10px 20px; background: #10b981; color: white; border: none; border-radius: 6px; cursor: pointer; font-size: 16px; font-weight: bold; }}
            .btn-reset {{ padding: 12px; background: #ef4444; color: white; border: none; border-radius: 6px; cursor: pointer; font-size: 15px; width: 100%; margin-top: 20px; font-weight: bold; }}
            
            ul {{ list-style: none; padding: 0; }}
            li {{ background: var(--item-bg); padding: 12px; border: 1px solid var(--border); margin-bottom: 8px; border-radius: 6px; display: flex; justify-content: space-between; }}
        </style>
    </head>
    <body>
        <div class="dashboard">
            <h3>Съедено за сегодня</h3>
            <div class="calories">{total_calories}</div>
            <div class="goal-info">Осталось: {remaining} ккал из {DAILY_GOAL}</div>
            <div class="progress-bar"><div class="progress-fill"></div></div>
        </div>

        <form action="/add" method="post">
            <input type="text" name="food" placeholder="Название продукта" required>
            <input type="number" name="cal" placeholder="Ккал" required>
            <button type="submit" class="btn-add">Добавить</button>
        </form>

        <ul>{items_html}</ul>

        <form action="/reset" method="post">
            <button type="submit" class="btn-reset">Сбросить счетчик (Новый день)</button>
        </form>
    </body>
    </html>
    """
    return html

@app.post("/add")
async def add_record(food: str = Form(...), cal: int = Form(...)):
    with sqlite3.connect("calories.db") as conn:
        # Записываем новые данные прямо в файл БД
        conn.execute("INSERT INTO history (name, cal) VALUES (?, ?)", (food, cal))
    return RedirectResponse(url="/", status_code=303)

@app.post("/reset")
async def reset_records():
    with sqlite3.connect("calories.db") as conn:
        # Очищаем таблицу
        conn.execute("DELETE FROM history")
    return RedirectResponse(url="/", status_code=303)

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)