"""
Дневник питания — FastAPI + SQLite.

Запуск:  python main.py   (или: uvicorn main:app --reload)
Переменные окружения (н  еобязательные): CALO  RIES_DB, HOST, PORT
"""


import   csv
import   io
import json
import os
import  re
import   sqlite3
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from html import escape
from pathlib import Path
from typing import Literal, Optional
from uuid import UUID, uuid4

import uvicorn
from fastapi import FastAPI, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from suggestions import suggest_meals, scale_nutrients

app = FastAPI()
app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")

DB_PATH = os.getenv("CALORIES_DB", "calories.db")
XP_PER_MEAL = 15      # опыт за одну запись еды
WATER_STEP = 250      # размер "стакана", мл


# ================= 1. БАЗА ДАННЫХ И МИГРАЦИИ =================
@contextmanager
def db(ensure_day=True):
    """Соединение с БД: коммит при успехе, откат при ошибке и ВСЕГДА закрытие.
    (sqlite3.connect() как контекстный менеджер соединение НЕ закрывает.)"""
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute("BEGIN IMMEDIATE")
        if ensure_day:
            rollover_day(conn)
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def add_missing_columns(conn, table, columns):
    existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    for col in columns:
        if col.split()[0] not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {col}")


def init_db():
    with db(ensure_day=False) as conn:
        new_favorites = not conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'favorites'").fetchone()
        conn.execute("CREATE TABLE IF NOT EXISTS history (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, cal INTEGER NOT NULL)")
        conn.execute("CREATE TABLE IF NOT EXISTS settings (id INTEGER PRIMARY KEY CHECK (id = 1), daily_goal INTEGER NOT NULL)")
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
        conn.execute("""CREATE TABLE IF NOT EXISTS meal_plan (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            plan_date TEXT NOT NULL,
            meal_type TEXT NOT NULL DEFAULT 'Приём пищи',
            name TEXT NOT NULL,
            cal INTEGER NOT NULL,
            p INTEGER NOT NULL DEFAULT 0,
            f INTEGER NOT NULL DEFAULT 0,
            c INTEGER NOT NULL DEFAULT 0
        )""")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_meal_plan_date ON meal_plan(plan_date)")
        conn.execute("CREATE TABLE IF NOT EXISTS suggestion_requests (request_id TEXT PRIMARY KEY, day TEXT NOT NULL)")
        conn.execute("""CREATE TABLE IF NOT EXISTS archived_meals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            meal_date TEXT NOT NULL, name TEXT NOT NULL, cal INTEGER NOT NULL,
            p INTEGER NOT NULL DEFAULT 0, f INTEGER NOT NULL DEFAULT 0,
            c INTEGER NOT NULL DEFAULT 0, time TEXT
        )""")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_archived_meals_date ON archived_meals(meal_date)")
        conn.execute("""CREATE TABLE IF NOT EXISTS meal_repeat_requests (
            request_id TEXT PRIMARY KEY, meal_id INTEGER NOT NULL, day TEXT NOT NULL
        )""")

        conn.execute("INSERT OR IGNORE INTO settings (id, daily_goal) VALUES (1, 2000)")

        if new_favorites:
            conn.executemany(
                "INSERT INTO favorites (name, cal, p, f, c) VALUES (?, ?, ?, ?, ?)",
                [("☕ Капучино", 120, 4, 4, 10), ("🍌 Банан", 105, 1, 0, 27), ("🍳 Яичница (2 шт)", 180, 14, 14, 1)],
            )
        if conn.execute("SELECT COUNT(*) FROM weight_history").fetchone()[0] == 0:
            conn.execute("INSERT INTO weight_history (weight, date) VALUES (70.0, datetime('now', 'localtime'))")

        # Миграции (идемпотентные: смотрим PRAGMA table_info, а не глотаем ошибки)
        add_missing_columns(conn, "history", ["p INTEGER DEFAULT 0", "f INTEGER DEFAULT 0", "c INTEGER DEFAULT 0", "time TEXT", "meal_date TEXT"])
        add_missing_columns(conn, "settings", [
            "level INTEGER DEFAULT 1", "xp INTEGER DEFAULT 0", "water INTEGER DEFAULT 0", "weight REAL DEFAULT 70.0",
            "streak INTEGER DEFAULT 0", "age INTEGER DEFAULT 20", "height REAL DEFAULT 175.0",
            "gender TEXT DEFAULT 'male'", "activity REAL DEFAULT 1.2", "goal_type TEXT DEFAULT 'maintain'",
            "last_archive TEXT",
            "diary_date TEXT",
        ])
        add_missing_columns(conn, "archive", ["goal INTEGER", "details_complete INTEGER NOT NULL DEFAULT 0"])

        # Перенос старых данных на новую схему
        conn.execute("UPDATE settings SET xp = (SELECT COUNT(*) FROM history) * ? WHERE id = 1 AND xp = 0", (XP_PER_MEAL,))
        conn.execute("UPDATE settings SET last_archive = (SELECT date(MAX(date)) FROM archive) WHERE id = 1 AND last_archive IS NULL")
        conn.execute("UPDATE settings SET diary_date = ? WHERE diary_date IS NULL", (today_str(),))
        conn.execute("UPDATE history SET meal_date = (SELECT diary_date FROM settings WHERE id = 1) WHERE meal_date IS NULL")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_history_date ON history(meal_date)")


# ================= 2. ЛОГИКА И СИСТЕМА =================
def today_str() -> str:
    return date.today().isoformat()


def now_hm() -> str:
    return datetime.now().strftime("%H:%M")


def water_goal(weight: float) -> int:
    """Норма воды: ~30 мл на кг веса, округлено до стакана, минимум 1500 мл."""
    return max(1500, round(weight * 30 / WATER_STEP) * WATER_STEP)


def current_streak(streak: int, last_archive: Optional[str]) -> int:
    """Стрик жив, только если последний архив был сегодня или вчера."""
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    return streak if last_archive in (today_str(), yesterday) else 0


def next_streak(streak: int, last_archive: Optional[str], day: Optional[str] = None) -> int:
    current = date.fromisoformat(day) if day else date.today()
    if last_archive == current.isoformat():
        return streak                 # повторное закрытие дня в тот же день стрик не растит
    if last_archive == (current - timedelta(days=1)).isoformat():
        return streak + 1
    return 1                          # был пропуск — начинаем заново


def add_xp(conn, amount: int):
    conn.execute("UPDATE settings SET xp = MAX(0, xp + ?) WHERE id = 1", (amount,))


def record_weight(conn, weight: float):
    """Один замер веса в день: повторный ввод за сегодня перезаписывает, а не плодит точки."""
    conn.execute("UPDATE settings SET weight = ? WHERE id = 1", (weight,))
    last = conn.execute("SELECT id, weight, date(date) FROM weight_history ORDER BY id DESC LIMIT 1").fetchone()
    if last and last[2] == today_str():
        conn.execute("UPDATE weight_history SET weight = ? WHERE id = ?", (weight, last[0]))
    else:
        conn.execute("INSERT INTO weight_history (weight, date) VALUES (?, datetime('now', 'localtime'))", (weight,))


def archive_current_day(conn, day: str):
    count, cal, p, f, c = conn.execute(
        "SELECT COUNT(*), COALESCE(SUM(cal), 0), COALESCE(SUM(p), 0), COALESCE(SUM(f), 0), COALESCE(SUM(c), 0) FROM history WHERE meal_date = ?",
        (day,),
    ).fetchone()
    goal, water, streak, last = conn.execute(
        "SELECT daily_goal, water, streak, last_archive FROM settings WHERE id = 1"
    ).fetchone()
    if count or water:
        existing = conn.execute("SELECT id FROM archive WHERE date(date) = ?", (day,)).fetchone()
        if existing:
            conn.execute(
                "UPDATE archive SET cal = COALESCE(cal, 0) + ?, p = COALESCE(p, 0) + ?, f = COALESCE(f, 0) + ?, c = COALESCE(c, 0) + ?, water = COALESCE(water, 0) + ? WHERE id = ?",
                (cal, p, f, c, water, existing[0]),
            )
        else:
            conn.execute("INSERT INTO archive (date, cal, p, f, c, water, goal, details_complete) VALUES (?, ?, ?, ?, ?, ?, ?, 1)",
                         (day, cal, p, f, c, water, goal))
        conn.execute("UPDATE settings SET streak = ?, last_archive = ? WHERE id = 1",
                     (next_streak(streak, last, day), day))
    # Перенос и итог дня коммитятся вместе; повторное закрытие не копирует еду снова.
    conn.execute("""INSERT INTO archived_meals (meal_date, name, cal, p, f, c, time)
        SELECT meal_date, name, cal, COALESCE(p, 0), COALESCE(f, 0), COALESCE(c, 0), time
        FROM history WHERE meal_date = ?""", (day,))
    conn.execute("DELETE FROM history WHERE meal_date = ?", (day,))
    conn.execute("UPDATE settings SET water = 0 WHERE id = 1")


def rollover_day(conn):
    previous = conn.execute("SELECT diary_date FROM settings WHERE id = 1").fetchone()[0]
    if previous < today_str():
        archive_current_day(conn, previous)
        conn.execute("UPDATE settings SET diary_date = ? WHERE id = 1", (today_str(),))


def day_totals(conn, day: Optional[str] = None):
    day = day or today_str()
    active = conn.execute("SELECT COALESCE(SUM(cal), 0), COALESCE(SUM(p), 0), COALESCE(SUM(f), 0), COALESCE(SUM(c), 0) FROM history WHERE meal_date = ?", (day,)).fetchone()
    archived = conn.execute("SELECT COALESCE(SUM(cal), 0), COALESCE(SUM(p), 0), COALESCE(SUM(f), 0), COALESCE(SUM(c), 0), COALESCE(SUM(water), 0) FROM archive WHERE date(date) = ?", (day,)).fetchone()
    water = conn.execute("SELECT water FROM settings WHERE id = 1").fetchone()[0] if day == today_str() else 0
    return (*[active[i] + archived[i] for i in range(4)], water + archived[4])


def insert_meal(conn, name, cal, p, f, c):
    conn.execute("INSERT INTO history (name, cal, p, f, c, time, meal_date) VALUES (?, ?, ?, ?, ?, ?, ?)",
                 (name, cal, p, f, c, now_hm(), today_str()))
    add_xp(conn, XP_PER_MEAL)


init_db()


def get_user_level(xp_total: int):
    """Опыт копится в settings.xp и не сбрасывается при закрытии дня."""
    level = (xp_total // 100) + 1
    current_xp = xp_total % 100
    titles = [(20, "Атлет"), (10, "Спортсмен"), (5, "Любитель ЗОЖ"), (3, "Начинающий"), (1, "Новичок")]
    title = next((t for lvl, t in titles if level >= lvl), "Новичок")
    return level, current_xp, title


def check_achievements(cal, p, f, c, water, goal, w_goal=1500):
    return [
        {"id": "goal", "icon": "🎯", "name": "Цель достигнута", "desc": "Норма калорий", "unlocked": goal * 0.9 <= cal <= goal * 1.1},
        {"id": "water", "icon": "💧", "name": "Водный баланс", "desc": f"Выпито {w_goal / 1000:g} л", "unlocked": water >= w_goal},
        {"id": "protein", "icon": "🥚", "name": "Протеин", "desc": "Собрано 100г белка", "unlocked": p >= 100},
        {"id": "over", "icon": "📈", "name": "Профицит", "desc": "Превышение на 500+", "unlocked": cal >= goal + 500},
    ]


# Ключевые слова ищутся с НАЧАЛА слова (раньше — в любом месте: "макароны" получали 🍔)
EMOJI_RULES = [
    (r"коф|капуч|латт|эспрес", "☕"),
    (r"вод", "💧"),
    (r"яйц|яич|омлет", "🍳"),
    (r"мяс|кур|говяд|свин|стейк|котлет", "🥩"),
    (r"хлеб|бутер|тост|батон", "🥪"),
    (r"сыр", "🧀"),
    (r"яблок", "🍏"),
    (r"банан", "🍌"),
    (r"пицц", "🍕"),
    (r"бург|макдон|кфс|мак\b", "🍔"),
    (r"шок|торт|слад|десерт|печен", "🍰"),
    (r"рыб|суши|ролл", "🍣"),
    (r"салат|овощ", "🥗"),
]


def apply_auto_emoji(name: str):
    name = name.strip()
    if not name or not name[0].isalnum():   # уже начинается с эмодзи/символа
        return name
    low = name.lower()
    for pattern, emoji in EMOJI_RULES:
        if re.search(rf"\b(?:{pattern})", low):
            return f"{emoji} {name}"
    return f"🍽️ {name}"


def food_name(value: str):
    if not value.strip():
        raise HTTPException(status_code=422, detail="Введи название блюда")
    return apply_auto_emoji(value)


def nutrient_fields(values=None):
    values = values or {}
    return '<div class="grid grid-cols-3 gap-2">' + ''.join(
        f'<label class="text-xs font-bold text-slate-500">{label}<input type="number" name="{key}" min="0" max="2000" value="{values.get(key, 0)}" class="mt-1 w-full h-11 px-2 rounded-xl bg-slate-50 dark:bg-slate-900 border border-slate-200 dark:border-slate-700 text-center"></label>'
        for key, label in (("p", "Белки, г"), ("f", "Жиры, г"), ("c", "Углеводы, г"))
    ) + '</div>'


def food_form(action, name="", cal="", values=None, button="Сохранить", max_length=80):
    return f"""<form action="{action}" method="post" class="flex flex-col gap-4">
        <label class="text-xs font-bold text-slate-500">Название блюда<input name="name" value="{escape(name, quote=True)}" required maxlength="{max_length}" class="mt-1 w-full h-12 px-3 rounded-xl bg-slate-50 dark:bg-slate-900 border border-slate-200 dark:border-slate-700"></label>
        <label class="text-xs font-bold text-slate-500">Калории на одну порцию<input type="number" name="cal" value="{cal}" required min="0" max="20000" class="mt-1 w-full h-12 px-3 rounded-xl bg-slate-50 dark:bg-slate-900 border border-slate-200 dark:border-slate-700"></label>
        {nutrient_fields(values)}
        <button class="h-12 rounded-xl bg-emerald-500 text-white font-bold">{button}</button>
    </form>"""


class SuggestedPortion(BaseModel):
    id: int = Field(gt=0)
    portions: float = Field(ge=0.25, le=3, multiple_of=0.25, allow_inf_nan=False)


class SuggestedMeal(BaseModel):
    day: date
    request_id: UUID
    items: list[SuggestedPortion] = Field(min_length=1, max_length=2)


@app.get("/api/suggestions")
def get_suggestions():
    with db() as conn:
        goal = conn.execute("SELECT daily_goal FROM settings WHERE id = 1").fetchone()[0]
        remaining = max(0, goal - day_totals(conn)[0])
        rows = conn.execute("SELECT id, name, cal, p, f, c FROM favorites ORDER BY id").fetchall()
    favorites = [dict(zip(("id", "name", "cal", "p", "f", "c"), row)) for row in rows]
    return {"day": today_str(), "remaining": remaining, "variants": suggest_meals(favorites, remaining)}


@app.post("/api/suggestions/eat")
def eat_suggestion(meal: SuggestedMeal):
    with db() as conn:
        if meal.day.isoformat() != today_str():
            raise HTTPException(409, "Наступил новый день. Открой подбор заново.")
        token = str(meal.request_id)
        if conn.execute("SELECT 1 FROM suggestion_requests WHERE request_id = ?", (token,)).fetchone():
            return {"saved": True}
        if len({item.id for item in meal.items}) != len(meal.items):
            raise HTTPException(422, "В сочетании должны быть разные блюда")
        records = []
        for item in meal.items:
            row = conn.execute("SELECT name, cal, p, f, c FROM favorites WHERE id = ?", (item.id,)).fetchone()
            if not row:
                raise HTTPException(409, "Блюдо удалено из избранного. Открой подбор заново.")
            values = scale_nutrients(dict(zip(("cal", "p", "f", "c"), row[1:])), item.portions)
            if values["cal"] > 20000 or any(values[key] > 2000 for key in ("p", "f", "c")):
                raise HTTPException(422, "Порция слишком большая. Уменьши её.")
            name = row[0] if item.portions == 1 else f"{row[0]} ×{item.portions:g}"
            records.append((name, *values.values()))
        for record in records:
            insert_meal(conn, *record)
        conn.execute("INSERT INTO suggestion_requests (request_id, day) VALUES (?, ?)", (token, today_str()))
    return {"saved": True}


@app.get("/edit/{item_id}", response_class=HTMLResponse)
def edit_page(item_id: int):
    with db() as conn:
        row = conn.execute("SELECT name, cal, p, f, c FROM history WHERE id = ?", (item_id,)).fetchone()
    if not row:
        raise HTTPException(404, "Запись не найдена")
    content = '<a href="/" class="text-sm text-emerald-500">← В дневник</a><h1 class="text-2xl font-black mt-5 mb-5">Редактировать запись</h1>'
    content += food_form(f"/edit/{item_id}", row[0], row[1], dict(zip(("p", "f", "c"), row[2:])), max_length=120)
    return render_page("Редактировать запись", content)


@app.post("/edit/{item_id}")
def edit_record(item_id: int, name: str = Form(..., min_length=1, max_length=120),
                cal: int = Form(..., ge=0, le=20000), p: int = Form(0, ge=0, le=2000),
                f: int = Form(0, ge=0, le=2000), c: int = Form(0, ge=0, le=2000)):
    name = food_name(name)
    with db() as conn:
        if not conn.execute("UPDATE history SET name = ?, cal = ?, p = ?, f = ?, c = ? WHERE id = ?",
                            (name, cal, p, f, c, item_id)).rowcount:
            raise HTTPException(404, "Запись не найдена")
    return RedirectResponse("/?edited=1", status_code=303)


@app.get("/favorites/new", response_class=HTMLResponse)
def new_favorite_page():
    content = '<a href="/" class="text-sm text-violet-500">← В дневник</a><h1 class="text-2xl font-black mt-5 mb-2">Новый продукт</h1><p class="text-xs text-slate-500 mb-5">Укажи калории и БЖУ одной привычной порции. Она появится в подборе еды.</p>'
    content += food_form("/add_fav", button="Сохранить в избранное")
    return render_page("Новый продукт", content)


def require_diary_date(value: Optional[str]) -> date:
    if value is None:
        return date.today()
    try:
        selected = date.fromisoformat(value)
    except ValueError:
        raise HTTPException(422, "Выбери корректную дату")
    if selected.isoformat() != value or selected > date.today():
        raise HTTPException(422, "Выбери сегодняшний или прошедший день")
    return selected


def diary_meals(conn, day: str):
    rows = conn.execute("""
        SELECT id, name, cal, p, f, c, time, 0 AS archived FROM history WHERE meal_date = ?
        UNION ALL
        SELECT id, name, cal, p, f, c, time, 1 AS archived FROM archived_meals WHERE meal_date = ?
        ORDER BY time DESC, id DESC, archived
    """, (day, day)).fetchall()
    return [dict(zip(("id", "name", "cal", "p", "f", "c", "time", "archived"), row)) for row in rows]


def diary_calendar(conn, selected: date):
    today = date.today()
    # Семь дней вокруг выбранной даты; сегодняшняя неделя заканчивается сегодня.
    end = min(today.toordinal(), max(7, selected.toordinal() + 3))
    start = max(1, end - 6)
    days = [date.fromordinal(ordinal) for ordinal in range(start, end + 1)]
    logged = {row[0] for row in conn.execute("""
        SELECT date(date) FROM archive WHERE date(date) BETWEEN ? AND ?
        UNION SELECT meal_date FROM history WHERE meal_date BETWEEN ? AND ?
    """, (days[0].isoformat(), days[-1].isoformat()) * 2)}
    weekdays = ("Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс")
    tiles = []
    for day in days:
        active = day == selected
        colors = "bg-emerald-500 text-white shadow-sm" if active else "bg-slate-50 dark:bg-slate-900 text-slate-500 hover:bg-emerald-50 dark:hover:bg-slate-800"
        dot = "bg-white" if active else "bg-emerald-500"
        marker = f'<span class="w-1 h-1 rounded-full {dot}"></span>' if day.isoformat() in logged else '<span class="w-1 h-1"></span>'
        current_attr = 'aria-current="date"' if active else ''
        tiles.append(f'<a href="/?day={day.isoformat()}" aria-label="{day.strftime("%d.%m.%Y")}" {current_attr} class="flex flex-col items-center gap-1 py-2 rounded-xl {colors}"><span class="text-[9px] font-bold">{weekdays[day.weekday()]}</span><span class="text-sm font-black">{day.day}</span>{marker}</a>')
    previous = (f'<a href="/?day={(selected - timedelta(days=1)).isoformat()}" aria-label="Предыдущий день" class="w-10 h-10 grid place-items-center rounded-xl bg-slate-100 dark:bg-slate-800 text-xl">‹</a>'
                if selected > date.min else '<span aria-disabled="true" class="w-10 h-10 grid place-items-center text-slate-300">‹</span>')
    following = (f'<a href="/?day={(selected + timedelta(days=1)).isoformat()}" aria-label="Следующий день" class="w-10 h-10 grid place-items-center rounded-xl bg-slate-100 dark:bg-slate-800 text-xl">›</a>'
                 if selected < today else '<span aria-disabled="true" class="w-10 h-10 grid place-items-center text-slate-300">›</span>')
    return f"""<section aria-label="Календарь питания" class="mb-6 p-4 rounded-3xl border border-slate-100 dark:border-slate-800">
        <div class="flex justify-between items-center gap-2 mb-3">
            <div><p class="text-[10px] font-bold uppercase tracking-widest text-slate-500">Дневник питания</p>
                <p class="text-lg font-black">{selected.strftime('%d.%m.%Y')}{' · Сегодня' if selected == today else ''}</p></div>
            <div class="flex gap-1">{previous}{following}</div>
        </div>
        <div class="grid grid-cols-7 gap-1 mb-3">{''.join(tiles)}</div>
        <form action="/" method="get" class="flex gap-2">
            <input type="date" name="day" aria-label="Дата дневника" value="{selected.isoformat()}" min="0001-01-01" max="{today.isoformat()}" required class="flex-1 min-w-0 h-11 px-2 rounded-xl bg-slate-50 dark:bg-slate-900 text-xs border border-slate-200 dark:border-slate-700">
            <button class="px-3 rounded-xl bg-slate-100 dark:bg-slate-800 text-xs font-bold">Открыть</button>
            {('<a href="/" class="px-3 h-11 grid place-items-center rounded-xl bg-emerald-50 dark:bg-emerald-950 text-emerald-600 dark:text-emerald-400 text-xs font-bold">Сегодня</a>' if selected < today else '')}
        </form>
    </section>"""


def diary_items_html(meals, historical=False):
    items = []
    for item in meals:
        if historical:
            actions = f"""<form action="/diary/repeat/{item['id']}" method="post" class="shrink-0">
                <input type="hidden" name="day" value="{today_str()}">
                <input type="hidden" name="request_id" value="{uuid4()}">
                <button class="px-3 py-2 rounded-xl bg-emerald-50 dark:bg-emerald-950 text-emerald-600 dark:text-emerald-400 text-[10px] font-bold">Повторить<br>сегодня</button>
            </form>"""
        elif item["archived"]:
            actions = '<span class="shrink-0 text-[9px] font-bold text-slate-400">Сохранено</span>'
        else:
            actions = f"""<a href="/edit/{item['id']}" aria-label="Редактировать запись" class="text-slate-400 hover:text-violet-500 p-2 text-lg">✎</a>
                <form action="/delete/{item['id']}" method="post" class="m-0 shrink-0">
                    <button type="submit" aria-label="Удалить запись" class="text-slate-400 hover:text-red-500 w-10 h-10 rounded-full border border-slate-100 dark:border-slate-800 text-xl">×</button>
                </form>"""
        items.append(f"""<li class="flex justify-between items-center gap-2 bg-white dark:bg-slate-800/80 p-4 rounded-2xl border border-slate-100 dark:border-slate-700/50 mb-2 shadow-sm">
            <div class="min-w-0 flex-1">
                <span class="block font-bold text-sm break-words">{escape(item['name'])}</span>
                <div class="flex flex-wrap gap-x-2 gap-y-1 mt-1">
                    <span class="text-xs font-black text-emerald-500">{item['cal']} ккал</span>
                    <span class="text-[10px] font-bold text-slate-500">Б:{item['p'] or 0} Ж:{item['f'] or 0} У:{item['c'] or 0}</span>
                    <span class="text-[10px] text-slate-400">{escape(item['time'] or '')}</span>
                </div>
            </div>{actions}
        </li>""")
    return ''.join(items)


def diary_legacy_notice(conn, day):
    incomplete = conn.execute("SELECT 1 FROM archive WHERE date(date) = ? AND details_complete = 0 LIMIT 1", (day,)).fetchone()
    return ('<p class="mb-4 p-4 rounded-2xl bg-amber-50 dark:bg-amber-950/30 text-amber-700 dark:text-amber-400 text-xs leading-relaxed">'
            'Этот день содержит старые записи: общие итоги сохранены, но часть или все названия блюд недоступны.</p>' if incomplete else '')


def past_diary_page(selected: date):
    day = selected.isoformat()
    with db() as conn:
        meals = diary_meals(conn, day)
        cal, p, f, c, water = day_totals(conn, day)
        saved_goal = conn.execute("SELECT goal FROM archive WHERE date(date) = ? AND goal IS NOT NULL ORDER BY id LIMIT 1", (day,)).fetchone()
        calendar = diary_calendar(conn, selected)
        notice = diary_legacy_notice(conn, day)
    goal_html = f'<span class="text-sm text-slate-400">/ {saved_goal[0]} ккал</span>' if saved_goal else '<span class="text-sm text-slate-400">ккал</span>'
    items = diary_items_html(meals, historical=True)
    empty = '<li class="p-8 text-center text-xs text-slate-500 rounded-2xl bg-slate-50 dark:bg-slate-900">' + ('Подробные записи за этот день недоступны' if notice else 'За этот день нет записей') + '</li>'
    content = f"""<header class="mb-5"><h1 class="text-3xl font-black tracking-tight">История питания<span class="text-emerald-500">.</span></h1>
        <p class="text-xs text-slate-500 mt-2">Прошедшие дни доступны для просмотра. Любое блюдо можно повторить сегодня.</p></header>
        {calendar}
        <section class="p-5 rounded-3xl bg-slate-900 text-white mb-5">
            <p class="text-[10px] font-bold uppercase tracking-widest text-slate-400">Итоги дня</p>
            <p class="text-4xl font-black mt-2">{cal} {goal_html}</p>
            <div class="grid grid-cols-3 gap-2 mt-4 text-center text-xs">
                <div class="p-2 rounded-xl bg-white/5">Белки<strong class="block mt-1 text-blue-400">{p} г</strong></div>
                <div class="p-2 rounded-xl bg-white/5">Жиры<strong class="block mt-1 text-amber-400">{f} г</strong></div>
                <div class="p-2 rounded-xl bg-white/5">Углеводы<strong class="block mt-1 text-emerald-400">{c} г</strong></div>
            </div>
            <p class="text-xs font-bold text-blue-300 mt-4">💧 Вода за день: {water} мл</p>
        </section>
        {notice}
        <h2 class="text-xs font-black uppercase tracking-widest text-slate-500 mb-3">Журнал питания · {len(meals)}</h2>
        <ul>{items or empty}</ul>"""
    return render_page("История питания", content, show_add_button=False)


@app.post("/diary/repeat/{item_id}")
def repeat_meal(item_id: int, day: date = Form(...), request_id: UUID = Form(...)):
    with db() as conn:
        if day.isoformat() != today_str():
            raise HTTPException(409, "Наступил новый день. Открой историю заново.")
        token = str(request_id)
        existing = conn.execute("SELECT meal_id, day FROM meal_repeat_requests WHERE request_id = ?", (token,)).fetchone()
        if existing:
            if existing != (item_id, day.isoformat()):
                raise HTTPException(409, "Запрос уже использован для другого блюда")
        else:
            meal = conn.execute("SELECT name, cal, p, f, c FROM archived_meals WHERE id = ? AND meal_date < ?", (item_id, today_str())).fetchone()
            if not meal:
                raise HTTPException(404, "Блюдо в истории не найдено")
            insert_meal(conn, *meal)
            conn.execute("INSERT INTO meal_repeat_requests (request_id, meal_id, day) VALUES (?, ?, ?)", (token, item_id, today_str()))
    return RedirectResponse("/?repeated=1", status_code=303)


# ================= 3. ГЛОБАЛЬНЫЙ ШАБЛОНИЗАТОР =================
def render_page(title: str, content: str, active_tab: str = "home", scripts: str = "", show_add_button: bool = True):
    home_active = "text-emerald-500" if active_tab == "home" else "text-slate-400 hover:text-emerald-500"
    plan_active = "text-emerald-500" if active_tab == "plan" else "text-slate-400 hover:text-emerald-500"
    stats_active = "text-emerald-500" if active_tab == "stats" else "text-slate-400 hover:text-emerald-500"
    prof_active = "text-emerald-500" if active_tab == "profile" else "text-slate-400 hover:text-emerald-500"

    with db() as conn:
        cursor = conn.execute("SELECT id, name, cal FROM favorites ORDER BY id ASC")
        favs = [{"id": row[0], "name": row[1], "cal": row[2]} for row in cursor.fetchall()]

    favs_html = "".join(
        f"""
        <form action="/add_quick/{fav['id']}" method="post" class="shrink-0 m-0">
            <button type="submit" onclick="vibrateBtn()" class="px-3 py-2 bg-slate-100 dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-xl text-xs font-semibold shadow-sm hover:border-emerald-500 transition active:scale-95 flex items-center gap-1.5 text-slate-700 dark:text-slate-200">
                <span class="truncate max-w-[100px]">{escape(fav['name'])}</span>
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
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>{title}</title>
        <link rel="stylesheet" href="/static/suggestions.css">
        <script src="https://cdn.tailwindcss.com"></script>
        <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.js"></script>
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
            /* Тост уже отцентрован контейнером — анимируем только по вертикали */
            @keyframes slideDown {{ 0% {{ transform: translateY(-100%); opacity: 0; }} 100% {{ transform: translateY(0); opacity: 1; }} }}
            .toast-anim {{ animation: slideDown 0.4s cubic-bezier(0.16, 1, 0.3, 1); }}
            .macro-bar {{ width: 0%; transition: width 1s ease-out; }}
        </style>
    </head>
    <body class="text-slate-800 dark:text-slate-200 transition-colors duration-300 w-full h-[100dvh] flex justify-center">

        <div id="toast-container" class="fixed top-4 left-1/2 -translate-x-1/2 z-[200] flex flex-col gap-2 w-[90%] max-w-sm pointer-events-none"></div>

        <div class="w-full max-w-md bg-white dark:bg-[#0a0a0a] h-full flex flex-col relative shadow-2xl border-x border-slate-100 dark:border-slate-800">

            <div class="flex-1 overflow-y-auto pb-[100px] px-5 pt-6 custom-scrollbar">
                {content}
            </div>

            <!-- Плавающая кнопка (FAB) справа внизу -->
            <div class="absolute bottom-[90px] right-6 z-50" {'hidden' if not show_add_button else ''}>
                <button aria-label="Добавить еду за сегодня" onclick="toggleModal(); vibrateBtn()" class="w-14 h-14 bg-emerald-500 hover:bg-emerald-400 text-white rounded-full shadow-[0_8px_20px_rgba(16,185,129,0.5)] flex items-center justify-center transition-transform active:scale-95">
                    <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round"><line x1="12" y1="5" x2="12" y2="19"></line><line x1="5" y1="12" x2="19" y2="12"></line></svg>
                </button>
            </div>

            <!-- Классическое нижнее меню -->
            <div class="absolute bottom-0 w-full h-[75px] bg-white/95 dark:bg-[#0a0a0a]/95 backdrop-blur-xl border-t border-slate-200 dark:border-slate-800 flex justify-around items-center z-40 pb-safe">
                <a href="/" onclick="vibrateBtn()" aria-label="Главная" class="flex flex-col items-center gap-1 transition-colors {home_active} active:scale-95 flex-1 text-[9px] font-bold"><svg xmlns="http://www.w3.org/2000/svg" width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="18" height="18" rx="2" ry="2"></rect><line x1="3" y1="9" x2="21" y2="9"></line><line x1="9" y1="21" x2="9" y2="9"></line></svg><span>День</span></a>
                <a href="/plan" onclick="vibrateBtn()" aria-label="План питания" class="flex flex-col items-center gap-1 transition-colors {plan_active} active:scale-95 flex-1 text-[9px] font-bold"><svg xmlns="http://www.w3.org/2000/svg" width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="4" width="18" height="17" rx="2"></rect><line x1="16" y1="2" x2="16" y2="6"></line><line x1="8" y1="2" x2="8" y2="6"></line><line x1="3" y1="10" x2="21" y2="10"></line><path d="m9 15 2 2 4-4"></path></svg><span>План</span></a>
                <a href="/stats" onclick="vibrateBtn()" aria-label="Аналитика" class="flex flex-col items-center gap-1 transition-colors {stats_active} active:scale-95 flex-1 text-[9px] font-bold"><svg xmlns="http://www.w3.org/2000/svg" width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="20" x2="18" y2="10"></line><line x1="12" y1="20" x2="12" y2="4"></line><line x1="6" y1="20" x2="6" y2="14"></line></svg><span>Статистика</span></a>
                <a href="/profile" onclick="vibrateBtn()" aria-label="Профиль" class="flex flex-col items-center gap-1 transition-colors {prof_active} active:scale-95 flex-1 text-[9px] font-bold"><svg xmlns="http://www.w3.org/2000/svg" width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"></path><circle cx="12" cy="7" r="4"></circle></svg><span>Профиль</span></a>
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
                                <input type="text" name="food" placeholder="Название блюда" required maxlength="80" class="flex-1 min-w-0 px-4 font-bold bg-slate-50 dark:bg-slate-950 border border-slate-200 dark:border-slate-800 rounded-2xl focus:outline-none focus:border-emerald-500 transition text-slate-900 dark:text-white placeholder-slate-400">
                                <input type="number" name="cal" placeholder="Ккал" required min="0" max="20000" class="w-24 shrink-0 min-w-0 px-2 font-black bg-slate-50 dark:bg-slate-950 border border-slate-200 dark:border-slate-800 rounded-2xl focus:outline-none focus:border-emerald-500 transition text-center text-slate-900 dark:text-white placeholder-slate-400">
                            </div>
                            <details class="group bg-slate-50 dark:bg-slate-950 rounded-2xl border border-slate-200 dark:border-slate-800 overflow-hidden mb-4">
                                <summary class="text-xs font-semibold text-slate-500 cursor-pointer list-none px-4 py-3 flex justify-between items-center outline-none">
                                    <span>Указать БЖУ (Опционально)</span>
                                    <span class="text-emerald-500 group-open:rotate-180 transition-transform">▼</span>
                                </summary>
                                <div class="flex gap-2 px-3 pb-3 h-12">
                                    <input type="number" name="p" value="0" min="0" placeholder="Белки" class="flex-1 min-w-0 px-2 font-bold bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-700 rounded-xl text-center text-blue-500 focus:border-blue-500 outline-none">
                                    <input type="number" name="f" value="0" min="0" placeholder="Жиры" class="flex-1 min-w-0 px-2 font-bold bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-700 rounded-xl text-center text-amber-500 focus:border-amber-500 outline-none">
                                    <input type="number" name="c" value="0" min="0" placeholder="Углев" class="flex-1 min-w-0 px-2 font-bold bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-700 rounded-xl text-center text-emerald-500 focus:border-emerald-500 outline-none">
                                </div>
                            </details>
                            <label class="flex items-center gap-2 mb-4 text-xs font-semibold text-slate-500 cursor-pointer select-none">
                                <input type="checkbox" name="save_fav" value="1" class="w-4 h-4 accent-emerald-500">
                                Сохранить в избранное (вместе с БЖУ)
                            </label>
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
                setTimeout(() => {{ toast.style.transition = 'all 0.3s'; toast.style.opacity = '0'; toast.style.transform = 'translateY(-20px)'; setTimeout(() => toast.remove(), 300); }}, 3000);
            }}

            const urlParams = new URLSearchParams(window.location.search);
            if (urlParams.has('added')) {{ showToast('Запись добавлена', 'success'); }}
            else if (urlParams.has('repeated')) {{ showToast('Блюдо добавлено на сегодня', 'success'); }}
            else if (urlParams.has('water')) {{ showToast('+250 мл воды', 'info'); }}
            else if (urlParams.has('unwater')) {{ showToast('−250 мл воды', 'info'); }}
            else if (urlParams.has('deleted')) {{ showToast('Запись удалена', 'error'); }}
            else if (urlParams.has('archived')) {{ confetti({{ particleCount: 150, spread: 90, origin: {{ y: 0.5 }}, colors: ['#10b981', '#3b82f6', '#f59e0b'] }}); showToast('День завершен!', 'success'); }}
            else if (urlParams.has('edited')) {{ showToast('Запись обновлена', 'success'); }}
            else if (urlParams.has('copied')) {{ showToast('Меню скопировано', 'success'); }}
            else if (urlParams.has('saved')) {{ showToast('Изменения сохранены', 'success'); }}
            else if (urlParams.has('calculated')) {{ confetti({{ particleCount: 150, spread: 90, origin: {{ y: 0.5 }}, colors: ['#10b981', '#fcd34d'] }}); showToast('Норма пересчитана!', 'success'); }}
            ['added', 'repeated', 'water', 'unwater', 'deleted', 'archived', 'saved', 'calculated', 'edited', 'copied'].forEach(key => urlParams.delete(key));
            window.history.replaceState({{}}, document.title, window.location.pathname + (urlParams.size ? '?' + urlParams.toString() : ''));

            VanillaTilt.init(document.querySelectorAll("[data-tilt]"));
        </script>
        {scripts}
        <script src="/static/suggestions.js" defer></script>
    </body>
    </html>
    """


# ================= 4. ГЛАВНАЯ СТРАНИЦА (ДАШБОРД) =================
# Обработчики — обычные def: FastAPI сам запускает их в пуле потоков,
# поэтому блокирующий sqlite не вешает event loop (раньше было async def).
@app.get("/", response_class=HTMLResponse)
def get_index(day: Optional[str] = None):
    selected = require_diary_date(day)
    if selected < date.today():
        return past_diary_page(selected)
    with db() as conn:
        history = diary_meals(conn, selected.isoformat())
        calendar = diary_calendar(conn, selected)
        legacy_notice = diary_legacy_notice(conn, selected.isoformat())

        total_calories, total_p, total_f, total_c, total_water = day_totals(conn)
        daily_goal = conn.execute("SELECT daily_goal FROM settings WHERE id = 1").fetchone()[0]
        w_row = conn.execute("SELECT weight FROM weight_history ORDER BY id DESC LIMIT 1").fetchone()
        current_weight = w_row[0] if w_row else 70.0

    goal_p = int((daily_goal * 0.3) / 4)
    goal_f = int((daily_goal * 0.3) / 9)
    goal_c = int((daily_goal * 0.4) / 4)
    pct_p = min(100, int((total_p / goal_p) * 100)) if goal_p > 0 else 0
    pct_f = min(100, int((total_f / goal_f) * 100)) if goal_f > 0 else 0
    pct_c = min(100, int((total_c / goal_c) * 100)) if goal_c > 0 else 0

    w_goal = water_goal(current_weight)
    pct_w = min(100, int(total_water / w_goal * 100))
    diff = daily_goal - total_calories
    remaining_text = f"осталось {diff}" if diff >= 0 else f"перебор {-diff}"

    water_card = f"""
        <div class="bg-white dark:bg-slate-800 p-5 rounded-3xl mb-8 border border-slate-100 dark:border-slate-700/50 shadow-sm">
            <div class="flex justify-between text-[11px] font-bold mb-2">
                <span class="text-blue-500 uppercase tracking-widest">Вода</span>
                <span class="text-slate-900 dark:text-slate-300">{total_water} / {w_goal} мл</span>
            </div>
            <div class="w-full bg-slate-100 dark:bg-slate-900 rounded-full h-2 overflow-hidden mb-4"><div id="bar-w" class="bg-blue-500 h-2 rounded-full macro-bar"></div></div>
            <div class="flex gap-2">
                <form action="/add_water" method="post" class="flex-1 m-0">
                    <button type="submit" onclick="vibrateBtn()" class="w-full h-11 bg-blue-50 dark:bg-blue-900/20 border border-blue-200 dark:border-blue-800/50 text-blue-600 dark:text-blue-400 text-xs font-bold rounded-xl transition active:scale-95">💧 +{WATER_STEP} мл</button>
                </form>
                <form action="/remove_water" method="post" class="m-0">
                    <button type="submit" onclick="vibrateBtn()" class="h-11 px-4 bg-slate-100 dark:bg-slate-900 border border-slate-200 dark:border-slate-700 text-slate-500 text-xs font-bold rounded-xl transition active:scale-95">−{WATER_STEP}</button>
                </form>
            </div>
        </div>
    """

    items_html = diary_items_html(history)

    color_class = "text-emerald-500" if total_calories <= daily_goal else "text-red-500"

    coach_text = "Идеальный ритм! Идем по графику 🎯"
    coach_color = "text-emerald-500 bg-emerald-50 dark:bg-emerald-900/10 border-emerald-100 dark:border-emerald-900/30"
    if total_calories == 0:
        coach_text = "Двигатель не заведен. Пора поесть! 🔑"; coach_color = "text-slate-500 bg-slate-50 dark:bg-slate-800/50 border-slate-200 dark:border-slate-700"
    elif total_calories > daily_goal:
        coach_text = "ПЕРЕГРЕВ! Лимит превышен 🛑"; coach_color = "text-red-500 bg-red-50 dark:bg-red-900/10 border-red-200 dark:border-red-900/30"
    elif total_water < 750 and total_calories > daily_goal * 0.5:   # было: сравнение ккал с граммами углеводов
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

        {calendar}

        <div class="flex items-center gap-3 p-3 rounded-2xl border {coach_color} mb-6 text-xs font-bold shadow-sm transition-colors uppercase tracking-wide">
            <span class="animate-pulse">●</span> {coach_text}
        </div>

        <section class="meal-discovery p-5 rounded-3xl mb-6 relative overflow-hidden shadow-xl">
            <div class="flex justify-between items-start gap-3">
                <div><p class="discovery-label mb-2">Твоё меню · новый вариант</p>
                    <h2 class="text-2xl font-black tracking-tight">Что бы съесть?</h2>
                    <p class="text-xs text-slate-300 mt-2 leading-relaxed">{'Осталось ' + str(max(0, diff)) + ' ккал. Соберём вкусную порцию?' if diff > 0 else 'Цель достигнута. Все блюда можно добавить через «+».'}</p>
                </div><span class="discovery-orb shrink-0" aria-hidden="true">🍽️</span>
            </div>
            <button id="suggestion-open" type="button" class="w-full mt-5 h-12 rounded-2xl bg-violet-500 hover:bg-violet-400 text-white text-sm font-black transition active:scale-95">✦ Подобрать еду</button>
            <p class="text-[10px] text-slate-400 text-center mt-3">Из твоего избранного · порции на твой вкус</p>
        </section>
        <dialog id="suggestion-dialog" class="suggestion-sheet" aria-labelledby="suggestion-title">
            <div class="p-6">
                <header class="flex justify-between items-center mb-5"><h2 id="suggestion-title" class="text-2xl font-black">Что бы съесть?</h2>
                    <button id="suggestion-close" type="button" aria-label="Закрыть подбор" class="w-10 h-10 rounded-full bg-slate-100 dark:bg-slate-800 text-xl">×</button></header>
                <div id="suggestion-panel"></div>
                <div class="rounded-2xl p-4 bg-violet-50 dark:bg-violet-950/40 mt-4" aria-live="polite">
                    <div class="text-4xl font-black text-violet-500"><span id="suggestion-cal">—</span> <span class="text-sm">ккал</span></div>
                    <p id="suggestion-macros" class="text-xs font-bold mt-2 text-slate-500"></p><p id="suggestion-status" class="text-xs mt-3"></p>
                </div>
                <div class="flex gap-2 mt-5"><button id="suggestion-next" hidden type="button" class="flex-1 h-12 rounded-xl bg-slate-100 dark:bg-slate-800 text-xs font-bold disabled:opacity-50">↻ Другой вариант</button>
                    <button id="suggestion-eat" hidden type="button" class="flex-1 h-12 rounded-xl bg-violet-500 text-white text-xs font-black disabled:opacity-50">Я это съел</button></div>
                <a href="/favorites/new" class="block text-center text-xs font-bold text-violet-500 mt-5">+ Добавить продукт в избранное</a>
            </div>
        </dialog>

        <div class="relative h-[180px] flex justify-center items-end overflow-hidden mb-8">
            <canvas id="calorieChart" class="w-full"></canvas>
            <div class="absolute bottom-2 flex flex-col items-center justify-center pointer-events-none">
                <span class="text-6xl font-black {color_class} tracking-tighter drop-shadow-sm">{total_calories}</span>
                <span class="text-[10px] font-bold mt-1 uppercase tracking-widest text-slate-500 bg-white/50 dark:bg-slate-900/50 px-2 py-0.5 rounded-md">из {daily_goal} ккал · {remaining_text}</span>
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

        {water_card}

        <div class="mb-4">
            <h3 class="text-[11px] font-black text-slate-800 dark:text-slate-200 uppercase tracking-widest mb-4 px-1">Журнал питания</h3>
            {legacy_notice}
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
                document.getElementById('bar-w').style.width = '{pct_w}%';
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


# ================= 5. ПЛАН ПИТАНИЯ =================
def parse_plan_date(value: Optional[str]) -> date:
    """Принимает только календарную дату ISO, не позволяя планировать задним числом."""
    if not value:
        return date.today()
    try:
        parsed = date.fromisoformat(value)
    except (TypeError, ValueError):
        return date.today()
    return parsed if parsed.isoformat() == value and parsed >= date.today() else date.today()


def require_plan_date(value: str) -> date:
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        raise HTTPException(422, "Выбери корректную дату")
    if parsed.isoformat() != value or parsed < date.today():
        raise HTTPException(422, "Выбери сегодняшний или будущий день")
    return parsed


@app.get("/plan", response_class=HTMLResponse)
def get_plan(day: Optional[str] = None):
    selected = parse_plan_date(day)
    plan_date = selected.isoformat()
    with db() as conn:
        daily_goal = conn.execute("SELECT daily_goal FROM settings WHERE id = 1").fetchone()[0]
        rows = conn.execute(
            "SELECT id, meal_type, name, cal, p, f, c FROM meal_plan WHERE plan_date = ? ORDER BY id", (plan_date,)
        ).fetchall()
        favorites = conn.execute("SELECT id, name, cal, p, f, c FROM favorites ORDER BY id").fetchall()

    total_cal = sum(row[3] for row in rows)
    total_p, total_f, total_c = (sum(row[i] for row in rows) for i in (4, 5, 6))
    remaining = daily_goal - total_cal
    plan_items = "".join(f"""
        <li class="bg-white dark:bg-slate-800 p-4 rounded-2xl border border-slate-100 dark:border-slate-700/50 flex justify-between items-center gap-2">
            <div class="min-w-0"><span class="text-[9px] uppercase tracking-wider text-emerald-500 font-black">{escape(row[1])}</span>
            <div class="font-bold text-sm truncate">{escape(row[2])}</div>
            <div class="text-[10px] text-slate-500 mt-1">{row[3]} ккал · Б:{row[4]} Ж:{row[5]} У:{row[6]}</div></div>
            <div class="flex flex-col gap-1 shrink-0">
                {f'<form action="/plan/eat/{row[0]}" method="post"><input type="hidden" name="day" value="{plan_date}"><button class="px-3 py-2 rounded-lg bg-emerald-500 text-white text-[10px] font-bold">В дневник</button></form>' if selected == date.today() else ''}
                <form action="/plan/delete/{row[0]}" method="post"><input type="hidden" name="day" value="{plan_date}"><button class="px-3 py-1 rounded-lg bg-slate-100 dark:bg-slate-900 text-slate-500 text-[10px] font-bold w-full">Удалить</button></form>
            </div>
        </li>""" for row in rows)
    fav_options = "".join(f'<option value="{row[0]}">{escape(row[1])} · {row[2]} ккал</option>' for row in favorites)
    tomorrow = (selected + timedelta(days=1)).isoformat()
    previous = (selected - timedelta(days=1)).isoformat()
    content = f"""
        <header class="mb-6"><h1 class="text-3xl font-black tracking-tight text-slate-900 dark:text-white">План питания<span class="text-emerald-500">.</span></h1>
        <p class="text-xs text-slate-500 mt-1">Запланируй еду заранее — фактический дневник не изменится.</p></header>
        <form action="/plan" method="get" class="flex items-center gap-2 mb-5">
            <a href="/plan?day={previous}" class="w-11 h-11 grid place-items-center rounded-xl bg-slate-100 dark:bg-slate-800 font-bold">‹</a>
            <input type="date" name="day" min="{today_str()}" value="{plan_date}" onchange="this.form.submit()" class="flex-1 min-w-0 h-11 px-3 rounded-xl border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800 text-sm">
            <a href="/plan?day={tomorrow}" class="w-11 h-11 grid place-items-center rounded-xl bg-slate-100 dark:bg-slate-800 font-bold">›</a>
        </form>
        <section class="p-5 rounded-3xl bg-slate-900 text-white mb-5">
            <div class="text-[10px] uppercase tracking-widest text-slate-400">Запланировано</div>
            <div class="text-3xl font-black mt-1">{total_cal} <span class="text-sm text-slate-400">/ {daily_goal} ккал</span></div>
            <div class="text-xs mt-2 {'text-emerald-400' if remaining >= 0 else 'text-red-400'}">{'Остаток' if remaining >= 0 else 'Превышение'}: {abs(remaining)} ккал</div>
            <div class="text-[10px] text-slate-400 mt-2">Б {total_p} г · Ж {total_f} г · У {total_c} г</div>
        </section>
        <section class="bg-white dark:bg-slate-800 p-4 rounded-3xl border border-slate-100 dark:border-slate-700/50 mb-5">
            <h2 class="text-sm font-black mb-3">Добавить в план</h2>
            <form action="/plan/add" method="post" class="flex flex-col gap-2">
                <input type="hidden" name="day" value="{plan_date}">
                <input name="name" required maxlength="80" placeholder="Название блюда" class="h-11 px-3 rounded-xl bg-slate-50 dark:bg-slate-900 border border-slate-200 dark:border-slate-700 text-sm">
                <div class="grid grid-cols-2 gap-2"><select name="meal_type" class="h-11 px-3 rounded-xl bg-slate-50 dark:bg-slate-900 border border-slate-200 dark:border-slate-700 text-xs"><option>Завтрак</option><option>Обед</option><option>Ужин</option><option>Перекус</option></select>
                <input type="number" name="cal" required min="0" max="20000" placeholder="Ккал" class="h-11 px-3 rounded-xl bg-slate-50 dark:bg-slate-900 border border-slate-200 dark:border-slate-700 text-sm"></div>
                <div class="grid grid-cols-3 gap-2"><input type="number" name="p" min="0" max="2000" value="0" placeholder="Белки" class="h-10 px-2 rounded-xl bg-slate-50 dark:bg-slate-900 text-center text-xs"><input type="number" name="f" min="0" max="2000" value="0" placeholder="Жиры" class="h-10 px-2 rounded-xl bg-slate-50 dark:bg-slate-900 text-center text-xs"><input type="number" name="c" min="0" max="2000" value="0" placeholder="Углев." class="h-10 px-2 rounded-xl bg-slate-50 dark:bg-slate-900 text-center text-xs"></div>
                <button class="h-11 bg-emerald-500 text-white font-bold rounded-xl text-sm">Добавить блюдо</button>
            </form>
            {f'<form action="/plan/add_favorite" method="post" class="flex gap-2 mt-3"><input type="hidden" name="day" value="{plan_date}"><select name="fav_id" class="flex-1 min-w-0 h-10 px-2 rounded-xl bg-slate-50 dark:bg-slate-900 text-xs">{fav_options}</select><button class="px-3 rounded-xl bg-emerald-50 text-emerald-600 text-xs font-bold">Из избранного</button></form>' if favorites else ''}
        </section>
        <h2 class="text-xs uppercase tracking-widest font-black text-slate-500 mb-3">Блюда на {plan_date}</h2>
        <ul class="flex flex-col gap-2 mb-6">{plan_items or '<li class="text-center text-xs text-slate-400 py-8">Пока ничего не запланировано</li>'}</ul>
        {f'<section class="p-4 rounded-2xl bg-slate-50 dark:bg-slate-800"><h2 class="font-bold text-sm mb-2">Скопировать меню</h2><p class="text-xs text-slate-500 mb-3">Блюда добавятся к меню выбранного дня.</p><form action="/plan/copy" method="post" class="flex gap-2"><input type="hidden" name="day" value="{plan_date}"><input type="date" name="target_day" value="{tomorrow}" min="{today_str()}" required aria-label="Куда скопировать меню" class="flex-1 min-w-0 px-2 h-11 bg-white dark:bg-slate-900 rounded-xl text-xs"><button class="px-3 bg-emerald-500 text-white font-bold text-xs rounded-xl">Копировать</button></form></section>' if rows else ''}
    """
    return render_page("План питания", content, active_tab="plan")


@app.post("/plan/add")
def add_plan_item(
    day: str = Form(...), name: str = Form(..., min_length=1, max_length=80),
    cal: int = Form(..., ge=0, le=20000), meal_type: str = Form("Приём пищи"),
    p: int = Form(0, ge=0, le=2000), f: int = Form(0, ge=0, le=2000), c: int = Form(0, ge=0, le=2000),
):
    selected = require_plan_date(day)
    allowed_types = {"Завтрак", "Обед", "Ужин", "Перекус", "Приём пищи"}
    meal_type = meal_type if meal_type in allowed_types else "Приём пищи"
    with db() as conn:
        conn.execute("INSERT INTO meal_plan (plan_date, meal_type, name, cal, p, f, c) VALUES (?, ?, ?, ?, ?, ?, ?)",
                     (selected.isoformat(), meal_type, food_name(name), cal, p, f, c))
    return RedirectResponse(url=f"/plan?day={selected.isoformat()}&saved=1", status_code=303)


@app.post("/plan/add_favorite")
def add_plan_favorite(day: str = Form(...), fav_id: int = Form(..., ge=1)):
    selected = require_plan_date(day)
    with db() as conn:
        fav = conn.execute("SELECT name, cal, p, f, c FROM favorites WHERE id = ?", (fav_id,)).fetchone()
        if fav:
            conn.execute("INSERT INTO meal_plan (plan_date, meal_type, name, cal, p, f, c) VALUES (?, 'Перекус', ?, ?, ?, ?, ?)",
                         (selected.isoformat(), *fav))
    return RedirectResponse(url=f"/plan?day={selected.isoformat()}&saved=1", status_code=303)


@app.post("/plan/delete/{item_id}")
def delete_plan_item(item_id: int, day: str = Form(...)):
    selected = require_plan_date(day)
    with db() as conn:
        conn.execute("DELETE FROM meal_plan WHERE id = ? AND plan_date = ?", (item_id, selected.isoformat()))
    return RedirectResponse(url=f"/plan?day={selected.isoformat()}", status_code=303)


@app.post("/plan/eat/{item_id}")
def add_planned_to_diary(item_id: int, day: str = Form(...)):
    selected = require_plan_date(day)
    if selected != date.today():
        return RedirectResponse(url=f"/plan?day={selected.isoformat()}", status_code=303)
    with db() as conn:
        item = conn.execute("SELECT name, cal, p, f, c FROM meal_plan WHERE id = ? AND plan_date = ?",
                            (item_id, selected.isoformat())).fetchone()
        if item:
            insert_meal(conn, *item)
            conn.execute("DELETE FROM meal_plan WHERE id = ?", (item_id,))
    return RedirectResponse(url="/?added=1", status_code=303)


@app.post("/plan/copy")
def copy_plan(day: str = Form(...), target_day: str = Form(...)):
    source, target = require_plan_date(day), require_plan_date(target_day)
    if source == target:
        raise HTTPException(422, "Выбери другой день для копирования")
    with db() as conn:
        conn.execute("INSERT INTO meal_plan (plan_date, meal_type, name, cal, p, f, c) SELECT ?, meal_type, name, cal, p, f, c FROM meal_plan WHERE plan_date = ?",
                     (target.isoformat(), source.isoformat()))
    return RedirectResponse(f"/plan?day={target.isoformat()}&copied=1", status_code=303)


# ================= 6. АНАЛИТИКА И ПРОФИЛЬ =================
@app.get("/stats", response_class=HTMLResponse)
def get_stats():
    with db() as conn:
        daily_goal = conn.execute("SELECT daily_goal FROM settings WHERE id = 1").fetchone()[0]
        archive_data = conn.execute("SELECT date, cal, p, f, c, water, goal FROM archive ORDER BY id DESC LIMIT 14").fetchall()
        weight_data = conn.execute("SELECT date, weight FROM weight_history ORDER BY id DESC LIMIT 14").fetchall()

    week = list(reversed(archive_data[:7]))          # последние 7 дней в хронологическом порядке
    chart_labels = [r[0].split()[0][5:] for r in week]
    chart_data = [r[1] for r in week]
    chart_goals = [r[6] or daily_goal for r in week]  # норма на тот день (у старых записей её нет — берём текущую)
    chart_colors = ["#ef4444" if cal > goal * 1.1 else "#10b981" for cal, goal in zip(chart_data, chart_goals)]

    w_rows = list(reversed(weight_data))
    w_labels = [r[0].split()[0][5:] for r in w_rows]
    w_data = [r[1] for r in w_rows]

    n = len(week)
    avg_cal = round(sum(chart_data) / n) if n else 0
    in_goal = sum(1 for cal, goal in zip(chart_data, chart_goals) if abs(cal - goal) <= goal * 0.1)
    w_text = f"{w_data[-1] - w_data[0]:+.1f} кг" if len(w_data) > 1 else "—"

    def tile(label, value, color):
        return (
            '<div class="bg-white dark:bg-slate-800 p-4 rounded-2xl border border-slate-100 dark:border-slate-700/50 shadow-sm text-center">'
            f'<div class="text-[9px] font-bold text-slate-400 uppercase tracking-widest mb-1">{label}</div>'
            f'<div class="text-base font-black {color}">{value}</div></div>'
        )

    summary_html = (
        '<div class="grid grid-cols-3 gap-3 mb-6">'
        + tile("Среднее", f"{avg_cal} ккал" if n else "—", "text-emerald-500")
        + tile("В норме", f"{in_goal} из {n}" if n else "—", "text-blue-500")
        + tile("Вес", w_text, "text-amber-500")
        + "</div>"
    )

    archive_html = "".join(
        f"""
        <a href="/?day={row[0].split()[0]}" aria-label="Открыть питание за {row[0].split()[0]}" class="block bg-white dark:bg-slate-800 p-5 rounded-3xl border border-slate-100 dark:border-slate-700/50 mb-3 shadow-sm hover:border-emerald-500 transition-colors">
            <div class="flex justify-between items-center mb-3">
                <span class="font-black text-slate-800 dark:text-slate-200 text-sm tracking-wider">{row[0].split()[0]}</span>
                <span class="text-sm font-black text-emerald-500 bg-emerald-50 dark:bg-emerald-900/20 px-2 py-1 rounded-lg">{row[1]} ккал</span>
            </div>
            <div class="flex justify-between text-[10px] font-bold text-slate-500 uppercase tracking-widest">
                <span class="bg-slate-50 dark:bg-slate-900 px-2 py-1 rounded-md border border-slate-100 dark:border-slate-700">Б:{row[2]} Ж:{row[3]} У:{row[4]}</span>
                <span class="text-blue-500 bg-blue-50 dark:bg-blue-900/30 px-2 py-1 rounded-md">💧 {row[5]} мл</span>
            </div>
            <p class="text-[10px] font-bold text-emerald-500 mt-3">Открыть день →</p>
        </a>
        """ for row in archive_data
    )

    content = f"""
        <div class="flex items-center justify-between mb-6">
            <h1 class="text-3xl font-black tracking-tighter text-slate-900 dark:text-white uppercase italic">Аналитика<span class="text-emerald-500">.</span></h1>
        </div>

        {summary_html}

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
                data: {{ labels: {json.dumps(chart_labels)}, datasets: [
                    {{ label: 'Ккал', data: {json.dumps(chart_data)}, backgroundColor: {json.dumps(chart_colors)}, borderRadius: 8, barPercentage: 0.5 }},
                    {{ type: 'line', label: 'Норма', data: {json.dumps(chart_goals)}, borderColor: '#f59e0b', borderDash: [6, 6], borderWidth: 2, pointRadius: 0, fill: false }}
                ] }},
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
def get_profile():
    with db() as conn:
        t_cal, t_p, t_f, t_c, t_water = day_totals(conn)

        settings = conn.execute(
            "SELECT daily_goal, water, streak, age, height, gender, activity, goal_type, xp, last_archive FROM settings WHERE id = 1"
        ).fetchone()
        daily_goal, streak = settings[0], settings[2]
        age, height, gender, activity, goal_type = settings[3], settings[4], settings[5], settings[6], settings[7]
        xp, last_archive = settings[8], settings[9]

        w_row = conn.execute("SELECT weight FROM weight_history ORDER BY id DESC LIMIT 1").fetchone()
        current_weight = w_row[0] if w_row else 70.0

        cursor = conn.execute("SELECT id, name, cal, p, f, c FROM favorites ORDER BY id ASC")
        favorites = [{"id": row[0], "name": row[1], "cal": row[2], "p": row[3], "f": row[4], "c": row[5]} for row in cursor.fetchall()]

    streak = current_streak(streak, last_archive)
    level, current_xp, title = get_user_level(xp)
    achievements = check_achievements(t_cal, t_p, t_f, t_c, t_water, daily_goal, water_goal(current_weight))

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
            <span class="text-sm font-bold text-slate-800 dark:text-slate-200">{escape(fav['name'])} <span class="text-emerald-500 text-[10px] bg-emerald-50 dark:bg-emerald-900/30 px-1.5 py-0.5 rounded-md">{fav['cal']} ккал</span><span class="block text-[10px] text-slate-500 mt-1">Б {fav['p']} · Ж {fav['f']} · У {fav['c']}</span></span>
            <form action="/delete_fav/{fav['id']}" method="post" class="m-0"><button type="submit" class="text-slate-400 hover:text-red-500 px-3 py-1.5 text-[10px] font-black uppercase tracking-widest bg-slate-50 dark:bg-slate-900 rounded-lg">Удалить</button></form>
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
                                <input type="number" name="age" value="{age}" required min="10" max="100" class="w-full h-12 px-4 font-bold bg-slate-50 dark:bg-slate-900 border border-slate-200 dark:border-slate-700 rounded-xl focus:border-emerald-500 text-xs dark:text-white outline-none">
                            </div>
                        </div>
                        <div class="flex gap-3">
                            <div class="flex-1"><label class="text-[9px] font-bold text-slate-400 uppercase tracking-widest mb-1.5 block">Рост (см)</label>
                                <input type="number" name="height" value="{height}" required min="100" max="250" class="w-full h-12 px-4 font-bold bg-slate-50 dark:bg-slate-900 border border-slate-200 dark:border-slate-700 rounded-xl focus:border-emerald-500 text-xs dark:text-white outline-none">
                            </div>
                            <div class="flex-1"><label class="text-[9px] font-bold text-slate-400 uppercase tracking-widest mb-1.5 block">Вес (кг)</label>
                                <input type="number" step="0.1" name="weight" value="{current_weight}" required min="20" max="500" class="w-full h-12 px-4 font-bold bg-slate-50 dark:bg-slate-900 border border-slate-200 dark:border-slate-700 rounded-xl focus:border-emerald-500 text-xs dark:text-white outline-none">
                            </div>
                        </div>
                        <div><label class="text-[9px] font-bold text-slate-400 uppercase tracking-widest mb-1.5 block">Активность</label>
                            <select name="activity" class="w-full h-12 px-3 font-bold bg-slate-50 dark:bg-slate-900 border border-slate-200 dark:border-slate-700 rounded-xl focus:border-emerald-500 text-xs dark:text-white outline-none"><option value="1.2" {a_12}>Минимум (Офис)</option><option value="1.375" {a_13}>Легкая (1-3 тренировки)</option><option value="1.55" {a_15}>Средняя (3-5 тренировок)</option><option value="1.725" {a_17}>Высокая (6-7 тренировок)</option><option value="1.9" {a_19}>Экстрим (Физ. работа)</option></select>
                        </div>
                        <div><label class="text-[9px] font-bold text-slate-400 uppercase tracking-widest mb-1.5 block">Цель</label>
                            <select name="goal_type" class="w-full h-12 px-3 font-bold bg-slate-50 dark:bg-slate-900 border border-slate-200 dark:border-slate-700 rounded-xl focus:border-emerald-500 text-xs dark:text-white outline-none"><option value="lose" {gt_l}>Снижение веса (-500 ккал)</option><option value="maintain" {gt_m}>Поддержание формы</option><option value="gain" {gt_g}>Набор массы (+500 ккал)</option></select>
                        </div>
                        <button type="submit" onclick="vibrateBtn()" class="w-full mt-2 h-14 bg-slate-900 dark:bg-white text-white dark:text-slate-900 font-black uppercase tracking-widest rounded-xl shadow-sm transition active:scale-95 text-xs">Применить параметры</button>
                        <p class="text-[10px] text-slate-400 font-semibold leading-snug">Норма не опускается ниже 1200 ккал (женщины) / 1500 ккал (мужчины).</p>
                    </form>
                </div>
            </details>

            <form action="/update_settings" method="post" class="bg-white dark:bg-slate-800 p-5 rounded-3xl border border-slate-100 dark:border-slate-700/50 shadow-sm flex gap-3">
                <div class="flex-1">
                    <label class="text-[9px] font-bold text-slate-400 uppercase tracking-widest mb-1.5 block">Ручной лимит</label>
                    <input type="number" name="new_goal" value="{daily_goal}" required min="800" max="10000" class="w-full h-12 px-4 font-bold bg-slate-50 dark:bg-slate-900 border border-slate-200 dark:border-slate-700 rounded-xl outline-none text-slate-900 dark:text-white">
                </div>
                <div class="flex-1">
                    <label class="text-[9px] font-bold text-slate-400 uppercase tracking-widest mb-1.5 block">Текущий вес</label>
                    <input type="number" step="0.1" name="new_weight" value="{current_weight}" required min="20" max="500" class="w-full h-12 px-4 font-bold bg-slate-50 dark:bg-slate-900 border border-slate-200 dark:border-slate-700 rounded-xl outline-none text-slate-900 dark:text-white">
                </div>
                <button type="submit" onclick="vibrateBtn()" class="mt-5 px-5 h-12 bg-emerald-500 text-white font-black uppercase rounded-xl transition active:scale-95 text-xs">OK</button>
            </form>
        </div>

        <div class="mb-8">
            <h3 class="text-[10px] font-black text-slate-500 uppercase tracking-widest mb-4 px-1">Менеджер Избранного</h3>
            <div class="bg-white dark:bg-slate-800 p-5 rounded-3xl border border-slate-100 dark:border-slate-700/50 shadow-sm">
                {food_form("/add_fav", button="Добавить в избранное")}
                <ul class="flex flex-col mt-4">{fav_manager if fav_manager else '<span class="text-xs text-slate-400 text-center py-4 font-bold uppercase tracking-widest">Нет сохраненной еды</span>'}</ul>
            </div>
        </div>

        <div class="mb-8">
            <h3 class="text-[10px] font-black text-slate-500 uppercase tracking-widest mb-4 px-1">Достижения (Сегодня)</h3>
            <div class="grid grid-cols-2 gap-3 mb-10">
                {ach_html}
            </div>

            <form action="/archive_day" method="post" onsubmit="return confirm('Сохранить итоги дня? Блюда останутся в истории питания.')">
                <button type="submit" onclick="vibrateBtn()" class="w-full h-16 bg-red-500/10 text-red-500 font-black uppercase tracking-widest rounded-2xl transition active:scale-95 text-xs flex items-center justify-center gap-2 border border-red-500/30 shadow-[0_0_15px_rgba(239,68,68,0.1)] hover:bg-red-500/20">
                    <svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"></path><polyline points="7 10 12 15 17 10"></polyline><line x1="12" y1="15" x2="12" y2="3"></line></svg>
                    Завершить день в архив
                </button>
            </form>
            <p class="text-[10px] text-center text-slate-500 font-bold mt-4 px-4">Блюда и итоги останутся в дневнике. Сохранённые записи доступны для просмотра; новый день начнётся автоматически.</p>

            <a href="/export.csv" class="mt-6 w-full h-12 bg-slate-100 dark:bg-slate-800 border border-slate-200 dark:border-slate-700 text-slate-600 dark:text-slate-300 font-black uppercase tracking-widest rounded-xl transition active:scale-95 text-[10px] flex items-center justify-center">Экспорт архива (CSV)</a>
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


# ================= 6. РОУТЫ (API) =================
# Ограничения в Form(...) отсекают мусор: отрицательные калории, 10-значные числа, пустые имена.
# Пустое числовое поле формы FastAPI трактует как "не передано" и берёт значение по умолчанию.
@app.post("/add")
def add_record(
    food: str = Form(..., min_length=1, max_length=80),
    cal: int = Form(..., ge=0, le=20000),
    p: int = Form(0, ge=0, le=2000),
    f: int = Form(0, ge=0, le=2000),
    c: int = Form(0, ge=0, le=2000),
    save_fav: Optional[str] = Form(None),
):
    name = food_name(food)
    with db() as conn:
        insert_meal(conn, name, cal, p, f, c)
        if save_fav:
            conn.execute("INSERT INTO favorites (name, cal, p, f, c) VALUES (?, ?, ?, ?, ?)", (name, cal, p, f, c))
    return RedirectResponse(url="/?added=1", status_code=303)


@app.post("/add_quick/{fav_id}")
def add_quick(fav_id: int):
    with db() as conn:
        fav = conn.execute("SELECT name, cal, p, f, c FROM favorites WHERE id = ?", (fav_id,)).fetchone()
        if fav:
            insert_meal(conn, *fav)
    return RedirectResponse(url="/?added=1", status_code=303)


@app.post("/add_fav")
def add_fav(name: str = Form(..., min_length=1, max_length=80), cal: int = Form(..., ge=0, le=20000),
            p: int = Form(0, ge=0, le=2000), f: int = Form(0, ge=0, le=2000), c: int = Form(0, ge=0, le=2000)):
    name = food_name(name)
    with db() as conn:
        conn.execute("INSERT INTO favorites (name, cal, p, f, c) VALUES (?, ?, ?, ?, ?)", (name, cal, p, f, c))
    return RedirectResponse(url="/profile?saved=1", status_code=303)


@app.post("/delete_fav/{fav_id}")
def delete_fav(fav_id: int):
    with db() as conn:
        conn.execute("DELETE FROM favorites WHERE id = ?", (fav_id,))
    return RedirectResponse(url="/profile?saved=1", status_code=303)


@app.post("/add_water")
def add_water():
    with db() as conn:
        conn.execute("UPDATE settings SET water = water + ? WHERE id = 1", (WATER_STEP,))
    return RedirectResponse(url="/?water=1", status_code=303)


@app.post("/remove_water")
def remove_water():
    with db() as conn:
        conn.execute("UPDATE settings SET water = MAX(0, water - ?) WHERE id = 1", (WATER_STEP,))
    return RedirectResponse(url="/?unwater=1", status_code=303)


@app.post("/delete/{item_id}")
def delete_record(item_id: int):
    with db() as conn:
        deleted = conn.execute("DELETE FROM history WHERE id = ?", (item_id,)).rowcount
        if deleted:
            add_xp(conn, -XP_PER_MEAL)   # иначе "добавил-удалил" фармит опыт
    return RedirectResponse(url="/?deleted=1", status_code=303)


@app.post("/update_settings")
def update_settings(new_goal: int = Form(..., ge=800, le=10000), new_weight: float = Form(..., ge=20, le=500)):
    with db() as conn:
        conn.execute("UPDATE settings SET daily_goal = ? WHERE id = 1", (new_goal,))
        record_weight(conn, new_weight)
    return RedirectResponse(url="/profile?saved=1", status_code=303)


@app.post("/calculate_goal")
def calculate_goal(
    gender: Literal["male", "female"] = Form(...),
    age: int = Form(..., ge=10, le=100),
    height: float = Form(..., ge=100, le=250),
    weight: float = Form(..., ge=20, le=500),
    activity: float = Form(..., ge=1.0, le=2.5),
    goal_type: Literal["lose", "maintain", "gain"] = Form(...),
):
    # Формула Миффлина — Сан-Жеора
    bmr = (10 * weight) + (6.25 * height) - (5 * age) + (5 if gender == "male" else -161)
    tdee = bmr * activity
    if goal_type == "lose":
        final_goal = int(tdee - 500)
    elif goal_type == "gain":
        final_goal = int(tdee + 500)
    else:
        final_goal = int(tdee)
    # Страховка от слишком жёсткого дефицита
    final_goal = min(10000, max(final_goal, 1200 if gender == "female" else 1500))

    with db() as conn:
        conn.execute(
            "UPDATE settings SET daily_goal = ?, age = ?, height = ?, gender = ?, activity = ?, goal_type = ? WHERE id = 1",
            (final_goal, age, height, gender, activity, goal_type),
        )
        record_weight(conn, weight)
    return RedirectResponse(url="/profile?calculated=1", status_code=303)


@app.post("/archive_day")
def archive_day():
    with db() as conn:
        archive_current_day(conn, today_str())
    return RedirectResponse(url="/profile?archived=1", status_code=303)


@app.get("/export.csv")
def export_csv():
    """Выгрузка архива. BOM в начале — чтобы Excel правильно открыл UTF-8."""
    with db() as conn:
        rows = conn.execute("SELECT date, cal, p, f, c, water, goal FROM archive ORDER BY id").fetchall()
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["date", "kcal", "protein_g", "fat_g", "carbs_g", "water_ml", "goal_kcal"])
    writer.writerows(rows)
    return Response(
        content="\ufeff" + buf.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="calories_archive.csv"'},
    )


if __name__ == "__main__":
    uvicorn.run(app, host=os.getenv("HOST", "127.0.0.1"), port=int(os.getenv("PORT", "8000")))
