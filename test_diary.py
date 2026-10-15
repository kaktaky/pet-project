"""Проверки календаря и сохранности дневника: python -m unittest test_diary -v.

Все запросы выполняются через ASGI и используют отдельные временные базы.
"""
import asyncio
from contextlib import closing
from datetime import date, timedelta
from html.parser import HTMLParser
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import urlencode, urlsplit
from uuid import uuid4

_bootstrap = tempfile.TemporaryDirectory()
os.environ["CALORIES_DB"] = str(Path(_bootstrap.name) / "bootstrap.db")
import main


async def asgi_request(method, url, form=None):
    parsed = urlsplit(url)
    messages = []
    delivered = False

    async def receive():
        nonlocal delivered
        if delivered:
            return {"type": "http.disconnect"}
        delivered = True
        return {"type": "http.request", "body": urlencode(form or {}).encode(), "more_body": False}

    async def send(message):
        messages.append(message)

    scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
             "method": method, "scheme": "http", "path": parsed.path,
             "raw_path": parsed.path.encode(), "query_string": parsed.query.encode(),
             "root_path": "", "headers": [(b"content-type", b"application/x-www-form-urlencoded")],
             "client": ("127.0.0.1", 1), "server": ("test", 80)}
    await asyncio.wait_for(main.app(scope, receive, send), timeout=10)
    start = next(message for message in messages if message["type"] == "http.response.start")
    body = b"".join(message.get("body", b"") for message in messages if message["type"] == "http.response.body")
    return start["status"], dict(start["headers"]), body.decode()


class RepeatForms(HTMLParser):
    """Считываем отправляемую пользователем форму, включая защиту от дублей."""
    def __init__(self, page):
        super().__init__()
        self.forms = []
        self.current = None
        self.feed(page)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "form" and attrs.get("action", "").startswith("/diary/repeat/"):
            self.current = (attrs["action"], {})
        elif tag == "input" and self.current and "name" in attrs:
            self.current[1][attrs["name"]] = attrs.get("value", "")

    def handle_endtag(self, tag):
        if tag == "form" and self.current:
            self.forms.append(self.current)
            self.current = None


class DiaryTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.original_db = main.DB_PATH
        self.addCleanup(setattr, main, "DB_PATH", self.original_db)
        main.DB_PATH = str(Path(self.directory.name) / "diary.db")
        main.init_db()
        self.today = date.today().isoformat()
        self.yesterday = (date.today() - timedelta(days=1)).isoformat()

    def request(self, method, url, form=None):
        return asyncio.run(asgi_request(method, url, form))

    def query(self, sql, values=()):
        with closing(sqlite3.connect(main.DB_PATH)) as conn:
            return conn.execute(sql, values).fetchall()

    def execute(self, sql, values=()):
        with closing(sqlite3.connect(main.DB_PATH)) as conn, conn:
            conn.execute(sql, values)

    def add_meal(self, name="Гречка", cal=400, p=20, f=10, c=50):
        response = self.request("POST", "/add", dict(food=name, cal=cal, p=p, f=f, c=c))
        self.assertEqual(response[0], 303)

    def seed_yesterday(self, name="Вчерашний обед", cal=400):
        self.add_meal(name, cal)
        self.execute("UPDATE history SET meal_date=?, time='08:15'", (self.yesterday,))
        self.execute("UPDATE settings SET diary_date=?, water=500", (self.yesterday,))
        response = self.request("GET", f"/?day={self.yesterday}")
        self.assertEqual(response[0], 200)
        return response[2]

    def test_manual_archive_preserves_details_once_and_keeps_today_visible(self):
        self.add_meal()
        self.request("POST", "/add_water")
        for _ in range(2):
            self.assertEqual(self.request("POST", "/archive_day")[0], 303)
        self.assertEqual(self.query("SELECT name,cal,p,f,c,meal_date FROM archived_meals"),
                         [("🍽️ Гречка", 400, 20, 10, 50, self.today)])
        self.assertEqual(self.query("SELECT COUNT(*) FROM history"), [(0,)])
        self.assertEqual(self.query("SELECT cal,water,details_complete FROM archive"), [(400, 250, 1)])
        self.assertEqual(self.query("SELECT xp,streak FROM settings"), [(15, 1)])
        page = self.request("GET", "/")[2]
        self.assertIn("🍽️ Гречка", page)
        self.assertIn("Сохранено", page)
        self.assertNotIn('/edit/1', page)

    def test_multiple_closures_merge_totals_and_keep_each_meal(self):
        self.add_meal(cal=700)
        self.request("POST", "/archive_day")
        self.add_meal("Яблоко", 100, 0, 0, 25)
        with main.db() as conn:
            self.assertEqual(main.day_totals(conn), (800, 20, 10, 75, 0))
            self.assertEqual(len(main.diary_meals(conn, self.today)), 2)
        self.request("POST", "/archive_day")
        self.assertEqual(self.query("SELECT cal FROM archive"), [(800,)])
        self.assertEqual(self.query("SELECT COUNT(*) FROM archived_meals"), [(2,)])
        with main.db() as conn:
            self.assertEqual(main.day_totals(conn)[0], 800)

    def test_auto_rollover_separates_days_water_goals_and_experience(self):
        page = self.seed_yesterday()
        self.add_meal("Сегодняшний ужин", 600)
        self.request("POST", "/update_settings", dict(new_goal=2400, new_weight=70))
        with main.db() as conn:
            self.assertEqual(main.day_totals(conn, self.yesterday), (400, 20, 10, 50, 500))
            self.assertEqual(main.day_totals(conn), (600, 20, 10, 50, 0))
        self.assertIn("Вчерашний обед", page)
        self.assertIn("500 мл", page)
        self.assertIn("2000 ккал", self.request("GET", f"/?day={self.yesterday}")[2])
        today_page = self.request("GET", "/")[2]
        self.assertNotIn("Вчерашний обед", today_page)
        self.assertIn("Сегодняшний ужин", today_page)
        self.assertEqual(self.query("SELECT xp FROM settings"), [(30,)])

    def test_repeat_uses_form_adds_today_once_and_preserves_source(self):
        page = self.seed_yesterday()
        action, form = RepeatForms(page).forms[0]
        source = self.query("SELECT * FROM archived_meals")
        with patch.object(main, "now_hm", return_value="19:45"):
            for _ in range(2):
                status, headers, _ = self.request("POST", action, form)
                self.assertEqual(status, 303)
                self.assertEqual(headers[b"location"], b"/?repeated=1")
        self.assertEqual(self.query("SELECT * FROM archived_meals"), source)
        self.assertEqual(self.query("SELECT name,cal,p,f,c,meal_date,time FROM history"),
                         [("🍽️ Вчерашний обед", 400, 20, 10, 50, self.today, "19:45")])
        self.assertEqual(self.query("SELECT xp FROM settings"), [(30,)])
        form["request_id"] = str(uuid4())
        self.assertEqual(self.request("POST", action, form)[0], 303)
        self.assertEqual(self.query("SELECT COUNT(*) FROM history"), [(2,)])

    def test_stale_and_invalid_repeat_requests_do_not_add_food(self):
        page = self.seed_yesterday()
        action, form = RepeatForms(page).forms[0]
        self.assertEqual(self.request("POST", action, dict(form, day=self.yesterday))[0], 409)
        self.assertEqual(self.request("POST", action, dict(form, request_id="invalid"))[0], 422)
        self.assertEqual(self.request("POST", "/diary/repeat/99999", form)[0], 404)
        self.assertEqual(self.query("SELECT COUNT(*) FROM history"), [(0,)])
        self.assertEqual(self.query("SELECT COUNT(*) FROM meal_repeat_requests"), [(0,)])
        self.assertEqual(self.query("SELECT xp FROM settings"), [(15,)])
        self.assertEqual(self.request("POST", action, form)[0], 303)
        self.assertEqual(self.request("POST", "/diary/repeat/99999", form)[0], 409)
        self.assertEqual(self.query("SELECT COUNT(*) FROM history"), [(1,)])

    def test_legacy_archives_keep_totals_without_inventing_details(self):
        self.execute("INSERT INTO archive (date,cal,p,f,c,water,goal) VALUES (?,900,30,20,100,750,1800)", (self.yesterday,))
        page = self.request("GET", f"/?day={self.yesterday}")[2]
        for text in ("900", "30 г", "750 мл", "1800 ккал", "названия блюд недоступны", "Подробные записи за этот день недоступны"):
            self.assertIn(text, page)
        self.assertEqual(RepeatForms(page).forms, [])
        self.assertEqual(self.query("SELECT COUNT(*) FROM archived_meals"), [(0,)])

    def test_mixed_legacy_and_new_records_keep_partial_history_notice(self):
        self.execute("INSERT INTO archive (date,cal,p,f,c,water,goal) VALUES (?,500,10,5,50,250,2000)", (self.today,))
        self.add_meal(cal=200)
        self.request("POST", "/archive_day")
        self.assertEqual(self.query("SELECT cal,water,details_complete FROM archive"), [(700, 250, 0)])
        with main.db() as conn:
            self.assertEqual(main.day_totals(conn)[0], 700)
        page = self.request("GET", "/")[2]
        self.assertIn("названия блюд недоступны", page)
        self.assertIn("Гречка", page)

    def test_empty_dates_and_strict_calendar_boundaries(self):
        tomorrow = (date.today() + timedelta(days=1)).isoformat()
        for day in ("", "invalid", "2026-02-30", "20261001", tomorrow):
            self.assertEqual(self.request("GET", f"/?day={day}")[0], 422, day)
        for day in (self.yesterday, "0001-01-01"):
            status, _, page = self.request("GET", f"/?day={day}")
            self.assertEqual(status, 200)
            self.assertIn("За этот день нет записей", page)
            self.assertIn('href="/"', page)
            self.assertEqual(RepeatForms(page).forms, [])
        self.assertEqual(self.request("GET", f"/?day={self.today}")[0], 200)

    def test_calendar_and_stats_link_to_day_and_escape_meal_names(self):
        page = self.seed_yesterday('<script>alert("meal")</script>')
        self.assertIn('&lt;script&gt;', page)
        self.assertNotIn('<script>alert("meal")</script>', page)
        self.assertNotIn('/edit/', page)
        self.assertNotIn('/delete/', page)
        self.assertIn(f'max="{self.today}"', page)
        self.assertIn(f'href="/?day={self.yesterday}"', self.request("GET", "/")[2])
        stats = self.request("GET", "/stats")[2]
        self.assertIn(f'href="/?day={self.yesterday}"', stats)
        self.assertIn("Открыть день", stats)

    def test_water_only_and_zero_calorie_days_are_preserved(self):
        self.request("POST", "/add_water")
        self.request("POST", "/archive_day")
        self.add_meal("Белок", 0, 10, 0, 0)
        self.request("POST", "/archive_day")
        self.assertEqual(self.query("SELECT cal,p,water,details_complete FROM archive"), [(0, 10, 250, 1)])
        self.assertEqual(self.query("SELECT cal,p FROM archived_meals"), [(0, 10)])

    def test_archiving_failure_rolls_back_details_totals_and_water(self):
        self.add_meal()
        self.request("POST", "/add_water")
        self.execute("CREATE TRIGGER reject_archive BEFORE INSERT ON archived_meals BEGIN SELECT RAISE(ABORT, 'test'); END")
        with self.assertRaises(sqlite3.IntegrityError):
            self.request("POST", "/archive_day")
        self.assertEqual(self.query("SELECT COUNT(*) FROM history"), [(1,)])
        self.assertEqual(self.query("SELECT COUNT(*) FROM archive"), [(0,)])
        self.assertEqual(self.query("SELECT water,streak FROM settings"), [(250, 0)])

    def test_migration_of_old_schema_is_idempotent(self):
        main.DB_PATH = str(Path(self.directory.name) / "legacy.db")
        with closing(sqlite3.connect(main.DB_PATH)) as conn, conn:
            conn.execute("CREATE TABLE history (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, cal INTEGER NOT NULL)")
            conn.execute("INSERT INTO history (name,cal) VALUES ('Старый завтрак',300)")
            conn.execute("CREATE TABLE settings (id INTEGER PRIMARY KEY, daily_goal INTEGER NOT NULL)")
            conn.execute("INSERT INTO settings VALUES (1,2100)")
            conn.execute("CREATE TABLE archive (id INTEGER PRIMARY KEY, date TEXT, cal INTEGER,p INTEGER,f INTEGER,c INTEGER,water INTEGER)")
            conn.execute("INSERT INTO archive VALUES (1,?,800,20,10,50,1000)", (self.yesterday,))
        for _ in range(2):
            main.init_db()
        self.assertEqual(self.query("SELECT name,cal,meal_date FROM history"), [("Старый завтрак", 300, self.today)])
        self.assertEqual(self.query("SELECT cal,details_complete FROM archive"), [(800, 0)])
        self.assertEqual(self.query("SELECT daily_goal,xp FROM settings"), [(2100, 15)])
        self.request("POST", "/archive_day")
        main.init_db()
        self.assertEqual(self.query("SELECT name FROM archived_meals"), [("Старый завтрак",)])
        self.assertEqual(self.query("SELECT xp FROM settings"), [(15,)])


if __name__ == "__main__":
    unittest.main()
