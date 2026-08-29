"""
database.py — Gère la sauvegarde des données du bot (avertissements, Premium,
compteur de messages) dans une base SQLite locale (bot_data.db), pour que
rien ne soit perdu si le bot redémarre ou plante.

Ce fichier doit rester dans le même dossier que main.py.
"""

import sqlite3
import os
import datetime

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bot_data.db")


def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """Crée les tables si elles n'existent pas encore. À appeler une fois au démarrage."""
    conn = get_connection()
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS warnings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            moderator TEXT NOT NULL,
            reason TEXT NOT NULL,
            timestamp TEXT NOT NULL
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS premium_servers (
            guild_id INTEGER PRIMARY KEY,
            activated_by INTEGER NOT NULL,
            activated_at TEXT NOT NULL,
            code TEXT NOT NULL
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS premium_codes (
            code TEXT PRIMARY KEY,
            generated_by INTEGER NOT NULL,
            assigned_to INTEGER,
            used INTEGER NOT NULL DEFAULT 0,
            used_by INTEGER,
            used_in_guild INTEGER,
            generated_at TEXT NOT NULL
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS message_counts (
            user_id INTEGER PRIMARY KEY,
            count INTEGER NOT NULL DEFAULT 0
        )
    """)

    conn.commit()
    conn.close()


# --- Avertissements ---

def load_warnings() -> dict:
    """Retourne {user_id: [ {id, moderator, reason, timestamp}, ... ]}"""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT id, user_id, moderator, reason, timestamp FROM warnings ORDER BY id ASC")
    rows = cur.fetchall()
    conn.close()

    warnings_store = {}
    for row in rows:
        warnings_store.setdefault(row["user_id"], []).append({
            "id": row["id"],
            "moderator": row["moderator"],
            "reason": row["reason"],
            "timestamp": row["timestamp"],
        })
    return warnings_store


def add_warning(user_id: int, moderator: str, reason: str, timestamp: str) -> int:
    """Insère un avertissement et retourne son ID (utile pour pouvoir le supprimer plus tard)."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO warnings (user_id, moderator, reason, timestamp) VALUES (?, ?, ?, ?)",
        (user_id, moderator, reason, timestamp),
    )
    conn.commit()
    new_id = cur.lastrowid
    conn.close()
    return new_id


def delete_warning(warning_id: int):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM warnings WHERE id = ?", (warning_id,))
    conn.commit()
    conn.close()


def clear_warnings(user_id: int):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM warnings WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()


# --- Premium ---

def load_premium_servers() -> dict:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT guild_id, activated_by, activated_at, code FROM premium_servers")
    rows = cur.fetchall()
    conn.close()

    return {
        row["guild_id"]: {
            "activated_by": row["activated_by"],
            "activated_at": datetime.datetime.fromisoformat(row["activated_at"]),
            "code": row["code"],
        }
        for row in rows
    }


def add_premium_server(guild_id: int, activated_by: int, activated_at: str, code: str):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "INSERT OR REPLACE INTO premium_servers (guild_id, activated_by, activated_at, code) VALUES (?, ?, ?, ?)",
        (guild_id, activated_by, activated_at, code),
    )
    conn.commit()
    conn.close()


def load_premium_codes() -> dict:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT code, generated_by, assigned_to, used, used_by, used_in_guild, generated_at FROM premium_codes"
    )
    rows = cur.fetchall()
    conn.close()

    return {
        row["code"]: {
            "generated_by": row["generated_by"],
            "assigned_to": row["assigned_to"],
            "used": bool(row["used"]),
            "used_by": row["used_by"],
            "used_in_guild": row["used_in_guild"],
            "generated_at": row["generated_at"],
        }
        for row in rows
    }


def add_premium_code(code: str, generated_by: int, assigned_to: int, generated_at: str):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO premium_codes (code, generated_by, assigned_to, used, generated_at) VALUES (?, ?, ?, 0, ?)",
        (code, generated_by, assigned_to, generated_at),
    )
    conn.commit()
    conn.close()


def mark_premium_code_used(code: str, used_by: int, used_in_guild: int):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "UPDATE premium_codes SET used = 1, used_by = ?, used_in_guild = ? WHERE code = ?",
        (used_by, used_in_guild, code),
    )
    conn.commit()
    conn.close()


# --- Compteur de messages ---

def load_message_counts() -> dict:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT user_id, count FROM message_counts")
    rows = cur.fetchall()
    conn.close()
    return {row["user_id"]: row["count"] for row in rows}


def save_message_counts(message_count_store: dict):
    """Sauvegarde en une fois tout le dictionnaire des compteurs de messages (upsert)."""
    if not message_count_store:
        return

    conn = get_connection()
    cur = conn.cursor()
    cur.executemany(
        "INSERT INTO message_counts (user_id, count) VALUES (?, ?) "
        "ON CONFLICT(user_id) DO UPDATE SET count = excluded.count",
        list(message_count_store.items()),
    )
    conn.commit()
    conn.close()
