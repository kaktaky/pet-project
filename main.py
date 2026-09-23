from fastapi import FastAPI, Form
from fastapi.responses import HTMLResponse, RedirectResponse
import uvicorn

app = FastAPI()

# Временное хранилище в оперативной памяти (сбросится при перезапуске сервера)
total_calories = 0
history = []

@app.get("/", response_class=HTMLResponse)
async def get_index():
    # Генерируем список съеденного
    items_html = "".join(
        f"<li><span>{item['name']}</span> <b>{item['cal']} ккал</b></li>" 
        for item in history
    )
    
    # HTML-шаблон со встроенным CSS
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
            .calories {{ font-size: 48px; color: #10b981; font-weight: bold; margin: 10px 0; }}
            form {{ display: flex; gap: 10px; margin-bottom: 20px; flex-wrap: wrap; }}
            input {{ flex: 1; min-width: 120px; padding: 10px; border: 1px solid #ccc; border-radius: 6px; font-size: 16px; }}
            button {{ padding: 10px 20px; background: #10b981; color: white; border: none; border-radius: 6px; cursor: pointer; font-size: 16px; font-weight: bold; }}
            button:hover {{ background: #059669; }}
            ul {{ list-style: none; padding: 0; }}
            li {{ background: #fff; padding: 12px; border: 1px solid #eee; margin-bottom: 8px; border-radius: 6px; display: flex; justify-content: space-between; }}
        </style>
    </head>
    <body>
        <div class="dashboard">
            <h3>Съедено за сегодня</h3>
            <div class="calories">{total_calories} ккал</div>
        </div>

        <form action="/add" method="post">
            <input type="text" name="food" placeholder="Название продукта" required>
            <input type="number" name="cal" placeholder="Ккал" required>
            <button type="submit">Добавить</button>
        </form>

        <ul>
            {items_html}
        </ul>
    </body>
    </html>
    """
    return html

@app.post("/add")
async def add_record(food: str = Form(...), cal: int = Form(...)):
    global total_calories
    # Обновляем счетчик и добавляем запись в начало списка
    total_calories += cal
    history.insert(0, {"name": food, "cal": cal})
    
    # Перенаправляем обратно на главную страницу, чтобы обновить интерфейс
    return RedirectResponse(url="/", status_code=303)

if __name__ == "__main__":
    # Запуск сервера
    uvicorn.run(app, host="0.0.0.0", port=8000)