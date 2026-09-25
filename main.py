from fastapi import FastAPI, Form
from fastapi.responses import HTMLResponse, RedirectResponse
import uvicorn

app = FastAPI()

# Переменные теперь включают суточную цель
total_calories = 0
history = []
DAILY_GOAL = 2000 

@app.get("/", response_class=HTMLResponse)
async def get_index():
    # Генерируем список съеденного
    items_html = "".join(
        f"<li><span>{item['name']}</span> <b>{item['cal']} ккал</b></li>" 
        for item in history
    )
    
    # Математика для прогресс-бара
    remaining = max(0, DAILY_GOAL - total_calories)
    percent = min(100, int((total_calories / DAILY_GOAL) * 100))
    # Если переели, цвет станет красным, если в норме - зеленым
    color = "#10b981" if total_calories <= DAILY_GOAL else "#ef4444"

    html = f"""
    <!DOCTYPE html>
    <html lang="ru">
    <head>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Счетчик калорий</title>
        <style>
            body {{ font-family: system-ui, sans-serif; max-width: 500px; margin: 40px auto; padding: 20px; color: #333; }}
            .dashboard {{ background: #f4f4f5; padding: 30px; border-radius: 12px; text-align: center; margin-bottom: 20px; }}
            .calories {{ font-size: 48px; color: {color}; font-weight: bold; margin: 10px 0; transition: color 0.3s; }}
            .goal-info {{ color: #666; margin-bottom: 15px; font-size: 14px; }}
            
            /* Стили прогресс-бара */
            .progress-bar {{ width: 100%; height: 12px; background: #e5e7eb; border-radius: 6px; overflow: hidden; }}
            .progress-fill {{ width: {percent}%; height: 100%; background: {color}; transition: width 0.4s ease-out, background-color 0.3s; }}
            
            form {{ display: flex; gap: 10px; margin-bottom: 20px; flex-wrap: wrap; }}
            input {{ flex: 1; min-width: 120px; padding: 10px; border: 1px solid #ccc; border-radius: 6px; font-size: 16px; }}
            .btn-add {{ padding: 10px 20px; background: #10b981; color: white; border: none; border-radius: 6px; cursor: pointer; font-size: 16px; font-weight: bold; }}
            .btn-add:hover {{ background: #059669; }}
            
            .btn- {{ padding: 12px; background: #ef4444; color: white; border: none; border-radius: 6px; cursor: pointer; font-size: 15px; width: 100%; margin-top: 20px; font-weight: bold; }}
            .btn-reset:hover {{ background: #dc2626; }}
            
            ul {{ list-style: none; padding: 0; }}
            li {{ background: #fff; padding: 12px; border: 1px solid #eee; margin-bottom: 8px; border-radius: 6px; display: flex; justify-content: space-between; }}
        </style>
    </head>
    <body>
        <div class="dashboard">
            <h3>Съедено за сегодня</h3>
            <div class="calories">{total_calories}</div>
            <div class="goal-info">Осталось: {remaining} ккал из {DAILY_GOAL}</div>
            <div class="progress-bar">
                <div class="progress-fill"></div>
            </div>
        </div>

        <form action="/add" method="post">
            <input type="text" name="food" placeholder="Название продукта" required>
            <input type="number" name="cal" placeholder="Ккал" required>
            <button type="submit" class="btn-add">Добавить</button>
        </form>

        <ul>
            {items_html}
        </ul>

        <!-- Новая форма для сброса -->
        <form action="/reset" method="post">
            <button type="submit" class="btn-reset">Сбросить счетчик (Новый день)</button>
        </form>
    </body>
    </html>
    """
    return html

@app.post("/add")
async def add_record(food: str = Form(...), cal: int = Form(...)):
    global total_calories
    total_calories += cal
    history.insert(0, {"name": food, "cal": cal})
    return RedirectResponse(url="/", status_code=303)

# Новый роут для сброса данных
@app.post("/reset")
async def reset_records():
    global total_calories, history
    total_calories = 0
    history.clear()
    return RedirectResponse(url="/", status_code=303)

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)