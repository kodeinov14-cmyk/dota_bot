import os
import sqlite3
import time
from datetime import datetime, date
from typing import Optional, Dict, Any
import threading

# Путь к БД
DB_PATH = os.path.join(os.path.dirname(__file__), "metrics.db")

# Thread-local storage для соединений
_local = threading.local()


def get_db_connection() -> sqlite3.Connection:
    """Получает соединение с БД (thread-local)"""
    if not hasattr(_local, "conn") or _local.conn is None:
        _local.conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        _local.conn.row_factory = sqlite3.Row
    return _local.conn


def init_db() -> None:
    """Инициализирует таблицы метрик"""
    conn = get_db_connection()
    cursor = conn.cursor()

    # Таблица пользователей
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            last_name TEXT,
            first_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            total_messages INTEGER DEFAULT 0
        )
    """)

    # Таблица запросов к API
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS api_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            api_type TEXT,  -- 'stratz', 'steam', 'opendota'
            endpoint TEXT,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            success BOOLEAN DEFAULT 1
        )
    """)

    # Индексы для быстрых запросов
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_api_requests_user ON api_requests(user_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_api_requests_timestamp ON api_requests(timestamp)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_api_requests_type ON api_requests(api_type)")

    conn.commit()


def log_user_activity(user_id: int, username: Optional[str] = None,
                      first_name: Optional[str] = None, last_name: Optional[str] = None) -> None:
    """Логирует активность пользователя (вызов при каждом сообщении)"""
    conn = get_db_connection()
    cursor = conn.cursor()

    now = datetime.now().isoformat()

    cursor.execute("""
        INSERT INTO users (user_id, username, first_name, last_name, first_seen, last_seen, total_messages)
        VALUES (?, ?, ?, ?, ?, ?, 1)
        ON CONFLICT(user_id) DO UPDATE SET
            username = COALESCE(excluded.username, users.username),
            first_name = COALESCE(excluded.first_name, users.first_name),
            last_name = COALESCE(excluded.last_name, users.last_name),
            last_seen = excluded.last_seen,
            total_messages = users.total_messages + 1
    """, (user_id, username, first_name, last_name, now, now))

    conn.commit()


def log_api_request(user_id: int, api_type: str, endpoint: str, success: bool = True) -> None:
    """Логирует запрос к внешнему API (STRATZ, Steam, OpenDota)"""
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("""
        INSERT INTO api_requests (user_id, api_type, endpoint, success)
        VALUES (?, ?, ?, ?)
    """, (user_id, api_type, endpoint, 1 if success else 0))

    conn.commit()


def get_metrics() -> Dict[str, Any]:
    """Возвращает сводную статистику для /metrics"""
    conn = get_db_connection()
    cursor = conn.cursor()

    # Общее количество уникальных пользователей
    cursor.execute("SELECT COUNT(*) as count FROM users")
    total_users = cursor.fetchone()["count"]

    # Общее количество API запросов
    cursor.execute("SELECT COUNT(*) as count FROM api_requests")
    total_api_requests = cursor.fetchone()["count"]

    # Запросы за сегодня
    today = date.today().isoformat()
    cursor.execute("""
        SELECT COUNT(*) as count FROM api_requests
        WHERE date(timestamp) = ?
    """, (today,))
    api_requests_today = cursor.fetchone()["count"]

    # Статистика по типам API
    cursor.execute("""
        SELECT api_type, COUNT(*) as count
        FROM api_requests
        GROUP BY api_type
    """)
    api_by_type = {row["api_type"]: row["count"] for row in cursor.fetchall()}

    # Запросы за сегодня по типам
    cursor.execute("""
        SELECT api_type, COUNT(*) as count
        FROM api_requests
        WHERE date(timestamp) = ?
        GROUP BY api_type
    """, (today,))
    api_by_type_today = {row["api_type"]: row["count"] for row in cursor.fetchall()}

    # Топ-5 пользователей по активности
    cursor.execute("""
        SELECT user_id, username, first_name, total_messages
        FROM users
        ORDER BY total_messages DESC
        LIMIT 5
    """)
    top_users = [dict(row) for row in cursor.fetchall()]

    return {
        "total_users": total_users,
        "total_api_requests": total_api_requests,
        "api_requests_today": api_requests_today,
        "api_by_type": api_by_type,
        "api_by_type_today": api_by_type_today,
        "top_users": top_users,
        "date_today": today
    }


def format_metrics_text(metrics: Dict[str, Any]) -> str:
    """Форматирует метрики для отправки в Telegram"""
    lines = [
        "📊 <b>МЕТРИКИ БОТА</b>\n",
        f"👥 <b>Уникальных пользователей:</b> {metrics['total_users']}",
        f"🔗 <b>Всего API запросов:</b> {metrics['total_api_requests']}",
        f"📅 <b>API запросов сегодня ({metrics['date_today']}):</b> {metrics['api_requests_today']}\n",
        "<b>По типам API (все время):</b>"
    ]

    for api_type, count in metrics['api_by_type'].items():
        today_count = metrics['api_by_type_today'].get(api_type, 0)
        lines.append(f"  • {api_type}: {count} (сегодня: {today_count})")

    if metrics['top_users']:
        lines.append("\n<b>Топ-5 активных пользователей:</b>")
        for i, user in enumerate(metrics['top_users'], 1):
            name = user['username'] or user['first_name'] or f"ID:{user['user_id']}"
            lines.append(f"  {i}. {name} — {user['total_messages']} сообщений")

    return "\n".join(lines)


# Инициализация при импорте
init_db()