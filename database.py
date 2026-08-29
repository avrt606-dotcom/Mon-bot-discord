"""
database.py — Gère la sauvegarde des données du bot (avertissements, Premium,
compteur de messages, owners du bot, logs de modération, sanctions auto,
messages de bienvenue/départ, giveaways) dans une base SQLite locale
(bot_data.db), pour que rien ne soit perdu si le bot redémarre ou plante.

Ce fichier doit rester dans le même dossier que main.py.
"""

import sqlite3
import os
import json
import datetime

# Sur Railway, une fois qu'un Volume est attaché au service, Railway définit
# automatiquement la variable d'environnement RAILWAY_VOLUME_MOUNT_PATH avec
# le chemin du dossier persistant (ex: /data). On l'utilise si elle existe,
# sinon (en local, ou sans volume) on garde le dossier du script comme avant.
_volume_path = os.environ.get("RAILWAY_VOLUME_MOUNT_PATH")
if _volume_path:
    DB_PATH = os.path.join(_volume_path, "bot_data.db")
else:
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
            code TEXT NOT NULL,
            color TEXT NOT NULL DEFAULT 'FFD700'
        )
    """)

    # Migration : ajoute la colonne "color" si la base existait déjà avant cette fonctionnalité.
    try:
        cur.execute("ALTER TABLE premium_servers ADD COLUMN color TEXT NOT NULL DEFAULT 'FFD700'")
    except sqlite3.OperationalError:
        pass  # la colonne existe déjà

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

    cur.execute("""
        CREATE TABLE IF NOT EXISTS bot_owners (
            user_id INTEGER PRIMARY KEY,
            added_by INTEGER NOT NULL,
            added_at TEXT NOT NULL
        )
    """)

    # --- Nouveau : salon de logs de modération par serveur (Premium) ---
    cur.execute("""
        CREATE TABLE IF NOT EXISTS mod_logs_config (
            guild_id INTEGER PRIMARY KEY,
            channel_id INTEGER NOT NULL
        )
    """)

    # --- Nouveau : configuration des sanctions automatiques par serveur (Premium) ---
    # actions_json est un texte JSON du type {"3": "mute:10m", "5": "kick", "7": "ban"}
    # où la clé est le nombre de warnings cumulés et la valeur l'action à déclencher.
    cur.execute("""
        CREATE TABLE IF NOT EXISTS auto_sanctions_config (
            guild_id INTEGER PRIMARY KEY,
            actions_json TEXT NOT NULL
        )
    """)

    # --- Nouveau : message de bienvenue / départ par serveur ---
    # Accessible à tous les serveurs (salon + message par défaut). Les serveurs Premium
    # peuvent en plus personnaliser le texte du message et ajouter une image/bannière
    # (welcome_image_url / leave_image_url).
    cur.execute("""
        CREATE TABLE IF NOT EXISTS welcome_config (
            guild_id INTEGER PRIMARY KEY,
            welcome_channel_id INTEGER,
            welcome_message TEXT,
            welcome_image_url TEXT,
            leave_channel_id INTEGER,
            leave_message TEXT,
            leave_image_url TEXT
        )
    """)

    # Migration : ajoute les colonnes image si la base existait déjà avant cette fonctionnalité.
    for colonne in ("welcome_image_url", "leave_image_url"):
        try:
            cur.execute(f"ALTER TABLE welcome_config ADD COLUMN {colonne} TEXT")
        except sqlite3.OperationalError:
            pass  # la colonne existe déjà

    # --- Nouveau : giveaways ---
    cur.execute("""
        CREATE TABLE IF NOT EXISTS giveaways (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id INTEGER NOT NULL,
            channel_id INTEGER NOT NULL,
            message_id INTEGER,
            prize TEXT NOT NULL,
            game TEXT,
            image_url TEXT,
            host_id INTEGER NOT NULL,
            winners_count INTEGER NOT NULL DEFAULT 1,
            excluded_roles TEXT,
            required_role INTEGER,
            end_time TEXT NOT NULL,
            ended INTEGER NOT NULL DEFAULT 0,
            winners_json TEXT,
            created_at TEXT NOT NULL
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS giveaway_entries (
            giveaway_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            PRIMARY KEY (giveaway_id, user_id)
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
    cur.execute("SELECT guild_id, activated_by, activated_at, code, color FROM premium_servers")
    rows = cur.fetchall()
    conn.close()

    return {
        row["guild_id"]: {
            "activated_by": row["activated_by"],
            "activated_at": datetime.datetime.fromisoformat(row["activated_at"]),
            "code": row["code"],
            "color": row["color"] or "FFD700",
        }
        for row in rows
    }


def add_premium_server(guild_id: int, activated_by: int, activated_at: str, code: str, color: str = "FFD700"):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "INSERT OR REPLACE INTO premium_servers (guild_id, activated_by, activated_at, code, color) VALUES (?, ?, ?, ?, ?)",
        (guild_id, activated_by, activated_at, code, color),
    )
    conn.commit()
    conn.close()


def update_premium_color(guild_id: int, color: str):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("UPDATE premium_servers SET color = ? WHERE guild_id = ?", (color, guild_id))
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


# --- Owners du bot ---
# Des utilisateurs "owner" ajoutés par le propriétaire réel du bot (celui du token/de l'app Discord).
# Un owner ajouté peut utiliser toutes les commandes de modération sur n'importe quel serveur où le
# bot est présent, sans avoir besoin des permissions Discord habituelles — sauf /premium generer,
# qui reste réservée au propriétaire réel uniquement.

def load_bot_owners() -> dict:
    """Retourne {user_id: {"added_by": int, "added_at": str}}"""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT user_id, added_by, added_at FROM bot_owners")
    rows = cur.fetchall()
    conn.close()
    return {
        row["user_id"]: {"added_by": row["added_by"], "added_at": row["added_at"]}
        for row in rows
    }


def add_bot_owner(user_id: int, added_by: int, added_at: str):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "INSERT OR REPLACE INTO bot_owners (user_id, added_by, added_at) VALUES (?, ?, ?)",
        (user_id, added_by, added_at),
    )
    conn.commit()
    conn.close()


def remove_bot_owner(user_id: int):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM bot_owners WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()


# --- Logs de modération (Premium) ---
# Un seul salon de logs par serveur. Toutes les actions de modération (mute, ban,
# kick, warn, clear, lock, unlock, rôles, pseudo...) y sont automatiquement postées.

def load_mod_logs_config() -> dict:
    """Retourne {guild_id: channel_id}"""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT guild_id, channel_id FROM mod_logs_config")
    rows = cur.fetchall()
    conn.close()
    return {row["guild_id"]: row["channel_id"] for row in rows}


def set_mod_logs_channel(guild_id: int, channel_id: int):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "INSERT OR REPLACE INTO mod_logs_config (guild_id, channel_id) VALUES (?, ?)",
        (guild_id, channel_id),
    )
    conn.commit()
    conn.close()


def remove_mod_logs_channel(guild_id: int):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM mod_logs_config WHERE guild_id = ?", (guild_id,))
    conn.commit()
    conn.close()


# --- Sanctions automatiques (Premium) ---
# Configuration par serveur : à partir de X warnings cumulés, une action est déclenchée
# automatiquement (mute d'une durée donnée, kick, ou ban).

def load_auto_sanctions_config() -> dict:
    """Retourne {guild_id: {seuil_int: "action"}}, ex: {123: {3: "mute:10m", 5: "kick"}}"""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT guild_id, actions_json FROM auto_sanctions_config")
    rows = cur.fetchall()
    conn.close()

    result = {}
    for row in rows:
        try:
            raw = json.loads(row["actions_json"])
            result[row["guild_id"]] = {int(k): v for k, v in raw.items()}
        except (ValueError, TypeError):
            result[row["guild_id"]] = {}
    return result


def set_auto_sanctions_config(guild_id: int, actions: dict):
    """actions : {seuil_int: "action"}, ex: {3: "mute:10m", 5: "kick", 7: "ban"}"""
    conn = get_connection()
    cur = conn.cursor()
    actions_json = json.dumps({str(k): v for k, v in actions.items()})
    cur.execute(
        "INSERT OR REPLACE INTO auto_sanctions_config (guild_id, actions_json) VALUES (?, ?)",
        (guild_id, actions_json),
    )
    conn.commit()
    conn.close()


def remove_auto_sanctions_config(guild_id: int):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM auto_sanctions_config WHERE guild_id = ?", (guild_id,))
    conn.commit()
    conn.close()


# --- Message de bienvenue / départ ---
# Disponible pour tous les serveurs (salon + message par défaut, sans image).
# Les serveurs Premium peuvent en plus personnaliser le texte et ajouter une image.

def load_welcome_config() -> dict:
    """Retourne {guild_id: {welcome_channel_id, welcome_message, welcome_image_url,
    leave_channel_id, leave_message, leave_image_url}}"""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT guild_id, welcome_channel_id, welcome_message, welcome_image_url, "
        "leave_channel_id, leave_message, leave_image_url FROM welcome_config"
    )
    rows = cur.fetchall()
    conn.close()

    return {
        row["guild_id"]: {
            "welcome_channel_id": row["welcome_channel_id"],
            "welcome_message": row["welcome_message"],
            "welcome_image_url": row["welcome_image_url"],
            "leave_channel_id": row["leave_channel_id"],
            "leave_message": row["leave_message"],
            "leave_image_url": row["leave_image_url"],
        }
        for row in rows
    }


def _get_or_create_welcome_row(guild_id: int) -> dict:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM welcome_config WHERE guild_id = ?", (guild_id,))
    row = cur.fetchone()
    if row is None:
        cur.execute(
            "INSERT INTO welcome_config (guild_id, welcome_channel_id, welcome_message, welcome_image_url, "
            "leave_channel_id, leave_message, leave_image_url) VALUES (?, NULL, NULL, NULL, NULL, NULL, NULL)",
            (guild_id,),
        )
        conn.commit()
        data = {
            "welcome_channel_id": None, "welcome_message": None, "welcome_image_url": None,
            "leave_channel_id": None, "leave_message": None, "leave_image_url": None,
        }
    else:
        data = dict(row)
    conn.close()
    return data


def set_welcome_config(guild_id: int, channel_id: int, message: str, image_url: str = None):
    _get_or_create_welcome_row(guild_id)
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "UPDATE welcome_config SET welcome_channel_id = ?, welcome_message = ?, welcome_image_url = ? WHERE guild_id = ?",
        (channel_id, message, image_url, guild_id),
    )
    conn.commit()
    conn.close()


def set_leave_config(guild_id: int, channel_id: int, message: str, image_url: str = None):
    _get_or_create_welcome_row(guild_id)
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "UPDATE welcome_config SET leave_channel_id = ?, leave_message = ?, leave_image_url = ? WHERE guild_id = ?",
        (channel_id, message, image_url, guild_id),
    )
    conn.commit()
    conn.close()


def remove_welcome_config(guild_id: int):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM welcome_config WHERE guild_id = ?", (guild_id,))
    conn.commit()
    conn.close()


# --- Giveaways ---
# Un giveaway est identifié par un id auto-incrémenté. Les participants sont
# stockés dans une table séparée (giveaway_entries) pour pouvoir gérer
# facilement l'ajout/retrait via le bouton "Participer".

def create_giveaway(guild_id: int, channel_id: int, prize: str, game: str, image_url: str,
                     host_id: int, winners_count: int, excluded_roles: list,
                     required_role: int, end_time: str, created_at: str) -> int:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO giveaways (guild_id, channel_id, prize, game, image_url, host_id,
            winners_count, excluded_roles, required_role, end_time, ended, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?)
    """, (guild_id, channel_id, prize, game, image_url, host_id, winners_count,
          json.dumps(excluded_roles), required_role, end_time, created_at))
    conn.commit()
    new_id = cur.lastrowid
    conn.close()
    return new_id


def set_giveaway_message(giveaway_id: int, message_id: int):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("UPDATE giveaways SET message_id = ? WHERE id = ?", (message_id, giveaway_id))
    conn.commit()
    conn.close()


def _row_to_giveaway(row) -> dict:
    return {
        "id": row["id"],
        "guild_id": row["guild_id"],
        "channel_id": row["channel_id"],
        "message_id": row["message_id"],
        "prize": row["prize"],
        "game": row["game"],
        "image_url": row["image_url"],
        "host_id": row["host_id"],
        "winners_count": row["winners_count"],
        "excluded_roles": json.loads(row["excluded_roles"]) if row["excluded_roles"] else [],
        "required_role": row["required_role"],
        "end_time": datetime.datetime.fromisoformat(row["end_time"]),
        "ended": bool(row["ended"]),
        "winners": json.loads(row["winners_json"]) if row["winners_json"] else [],
        "created_at": row["created_at"],
    }


def get_giveaway(giveaway_id: int):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM giveaways WHERE id = ?", (giveaway_id,))
    row = cur.fetchone()
    conn.close()
    return _row_to_giveaway(row) if row else None


def load_active_giveaways() -> list:
    """Retourne tous les giveaways non terminés, tous serveurs confondus
    (utilisé au démarrage du bot et par la tâche de fond qui vérifie les fins)."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM giveaways WHERE ended = 0")
    rows = cur.fetchall()
    conn.close()
    return [_row_to_giveaway(r) for r in rows]


def load_guild_giveaways(guild_id: int) -> list:
    """Retourne les giveaways d'un serveur (25 derniers), du plus récent au plus ancien."""
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM giveaways WHERE guild_id = ? ORDER BY id DESC LIMIT 25", (guild_id,))
    rows = cur.fetchall()
    conn.close()
    return [_row_to_giveaway(r) for r in rows]


def mark_giveaway_ended(giveaway_id: int, winners: list):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "UPDATE giveaways SET ended = 1, winners_json = ? WHERE id = ?",
        (json.dumps(winners), giveaway_id),
    )
    conn.commit()
    conn.close()


# --- Participants d'un giveaway ---

def add_giveaway_entry(giveaway_id: int, user_id: int) -> bool:
    """Retourne True si l'entrée a bien été ajoutée, False si la personne participait déjà."""
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute("INSERT INTO giveaway_entries (giveaway_id, user_id) VALUES (?, ?)", (giveaway_id, user_id))
        conn.commit()
        added = True
    except sqlite3.IntegrityError:
        added = False
    conn.close()
    return added


def remove_giveaway_entry(giveaway_id: int, user_id: int) -> bool:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM giveaway_entries WHERE giveaway_id = ? AND user_id = ?", (giveaway_id, user_id))
    removed = cur.rowcount > 0
    conn.commit()
    conn.close()
    return removed


def is_giveaway_participant(giveaway_id: int, user_id: int) -> bool:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM giveaway_entries WHERE giveaway_id = ? AND user_id = ?", (giveaway_id, user_id))
    found = cur.fetchone() is not None
    conn.close()
    return found


def get_giveaway_entries(giveaway_id: int) -> list:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT user_id FROM giveaway_entries WHERE giveaway_id = ?", (giveaway_id,))
    rows = cur.fetchall()
    conn.close()
    return [r["user_id"] for r in rows]


def count_giveaway_entries(giveaway_id: int) -> int:
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) AS c FROM giveaway_entries WHERE giveaway_id = ?", (giveaway_id,))
    count = cur.fetchone()["c"]
    conn.close()
    return count
