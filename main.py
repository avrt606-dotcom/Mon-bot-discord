import discord
from discord import app_commands
from discord.ext import commands, tasks
import os
import datetime

import database as db

# Initialise la base SQLite (crée bot_data.db et ses tables si besoin)
fichier_existait_deja = os.path.exists(db.DB_PATH)
db.init_db()
print(f"[DB] Base de données utilisée : {db.DB_PATH}")
print(f"[DB] Le fichier existait déjà avant ce lancement : {fichier_existait_deja}")

# Les "intents" définissent quelles infos le bot peut recevoir de Discord
intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)

@bot.event
async def on_ready():
    print(f"{bot.user} est connecté et en ligne !")
    if not save_message_counts_task.is_running():
        save_message_counts_task.start()
    try:
        synced = await bot.tree.sync()
        print(f"{len(synced)} commande(s) synchronisée(s).")
    except Exception as e:
        print(f"Erreur lors de la synchronisation des commandes : {e}")

# Compteur de messages : {user_id: nombre_de_messages}, chargé depuis la base SQLite
# et sauvegardé automatiquement toutes les 5 minutes (voir save_message_counts_task).
message_count_store = db.load_message_counts()
print(f"[DB] {len(message_count_store)} compteur(s) de messages chargé(s) depuis la base.")


@tasks.loop(minutes=5)
async def save_message_counts_task():
    db.save_message_counts(message_count_store)


@bot.event
async def on_message(message: discord.Message):
    if not message.author.bot:
        message_count_store[message.author.id] = message_count_store.get(message.author.id, 0) + 1
    await bot.process_commands(message)


# --- Système Owners du bot ---
# bot_owners_store : {user_id: {"added_by": int, "added_at": str}}
# Un owner ajouté ici peut utiliser toutes les commandes de modération sur n'importe quel
# serveur où le bot est présent, sans avoir besoin des permissions Discord habituelles.
# Seul le vrai propriétaire du bot (celui du compte développeur Discord) peut gérer
# cette liste et utiliser /premium generer.
bot_owners_store = db.load_bot_owners()
print(f"[DB] {len(bot_owners_store)} owner(s) additionnel(s) chargé(s) depuis la base.")


async def is_added_owner(user_id: int) -> bool:
    return user_id in bot_owners_store


async def is_full_owner(user: discord.abc.User) -> bool:
    """Vrai uniquement pour le propriétaire réel du bot (l'app Discord elle-même)."""
    return await bot.is_owner(user)


async def is_owner_level(user: discord.abc.User) -> bool:
    """Vrai pour le propriétaire réel OU un owner ajouté via /owner add."""
    if await bot.is_owner(user):
        return True
    return await is_added_owner(user.id)


def has_permissions_or_owner(**perms):
    """Comme app_commands.checks.has_permissions, mais laisse passer automatiquement
    le propriétaire réel du bot et les owners ajoutés via /owner add, même sans les
    permissions Discord normalement requises."""

    async def predicate(interaction: discord.Interaction) -> bool:
        if await is_owner_level(interaction.user):
            return True

        resolved = interaction.permissions if interaction.permissions is not None else interaction.user.guild_permissions
        missing = [perm for perm, value in perms.items() if getattr(resolved, perm, None) != value]
        if not missing:
            return True
        raise app_commands.MissingPermissions(missing)

    return app_commands.check(predicate)


# --- Système Premium ---
# premium_servers : {guild_id: {"activated_by": user_id, "activated_at": datetime, "code": str}}
# premium_codes   : {code: {"generated_by", "assigned_to", "used", "used_by", "used_in_guild", "generated_at"}}
# Chargés depuis la base SQLite au démarrage, et écrits dans la base à chaque changement.
premium_servers = db.load_premium_servers()
premium_codes = db.load_premium_codes()
print(f"[DB] {len(premium_servers)} serveur(s) premium chargé(s) depuis la base.")
print(f"[DB] {len(premium_codes)} code(s) premium chargé(s) depuis la base.")


def is_premium(guild_id: int) -> bool:
    return guild_id in premium_servers


def get_premium_color(guild_id: int) -> discord.Color:
    """Retourne la couleur choisie par le serveur premium, ou doré par défaut."""
    data = premium_servers.get(guild_id)
    hex_value = data["color"] if data else "FFD700"
    try:
        return discord.Color(int(hex_value, 16))
    except (ValueError, TypeError):
        return discord.Color.gold()


def parse_hex_color(texte: str):
    """Valide et convertit un texte comme '#FF5733' ou 'ff5733' en code hex propre (6 caractères).
    Retourne None si le format est invalide."""
    texte = texte.strip().lstrip("#").upper()
    if len(texte) != 6:
        return None
    try:
        int(texte, 16)
    except ValueError:
        return None
    return texte


def generate_premium_code() -> str:
    import random
    import string
    return "-".join(
        "".join(random.choices(string.ascii_uppercase + string.digits, k=4))
        for _ in range(3)
    )


def build_mod_embed(emoji: str, title: str, color: discord.Color, cible, moderateur: discord.Member = None, raison: str = None, extra_fields: list = None, guild: discord.Guild = None, owner_badge: bool = False) -> discord.Embed:
    """Embed uniforme utilisé par toutes les commandes de modération (mute, ban, kick, etc.).
    Si le serveur est Premium, l'embed passe en doré avec un badge ✨.
    Si l'action a été faite par un Owner du bot, un badge 👑 apparaît en pied de page."""
    premium = guild is not None and is_premium(guild.id)
    if premium:
        color = get_premium_color(guild.id)

    embed = discord.Embed(
        title=f"{emoji} {title}" + (" ✨" if premium else ""),
        color=color,
        timestamp=datetime.datetime.now(),
    )
    embed.set_author(name=str(cible), icon_url=cible.display_avatar.url)
    embed.set_thumbnail(url=cible.display_avatar.url)

    embed.add_field(name="Membre", value=cible.mention, inline=True)
    if moderateur is not None:
        embed.add_field(name="Modérateur", value=moderateur.mention, inline=True)
    if extra_fields:
        for name, value in extra_fields:
            embed.add_field(name=name, value=value, inline=True)
    if raison is not None:
        embed.add_field(name="Raison", value=raison, inline=False)

    footer = f"ID : {cible.id}"
    if owner_badge:
        footer = f"👑 Action Owner — {footer}"
    if premium:
        footer = f"✨ Serveur Premium — {footer}"
    embed.set_footer(text=footer)
    return embed


@bot.tree.command(name="ping", description="Vérifie que le bot répond bien")
async def ping(interaction: discord.Interaction):
    latence_ms = round(bot.latency * 1000)
    embed = discord.Embed(
        title="🏓 Pong !",
        description="Le bot fonctionne parfaitement.",
        color=discord.Color.green(),
        timestamp=datetime.datetime.now(),
    )
    embed.add_field(name="Latence", value=f"{latence_ms} ms", inline=True)
    embed.set_footer(text=f"Demandé par {interaction.user}", icon_url=interaction.user.display_avatar.url)
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="info", description="Affiche toutes les informations d'un membre")
@app_commands.describe(utilisateur="La personne à consulter (toi par défaut)")
async def info(interaction: discord.Interaction, utilisateur: discord.Member = None):
    utilisateur = utilisateur or interaction.user

    couleur = get_premium_color(interaction.guild.id) if is_premium(interaction.guild.id) else (utilisateur.color if utilisateur.color != discord.Color.default() else discord.Color.blurple())
    embed = discord.Embed(
        title=f"Informations sur {utilisateur.display_name}" + (" ✨" if is_premium(interaction.guild.id) else ""),
        color=couleur,
        timestamp=datetime.datetime.now(),
    )
    embed.set_author(name=str(utilisateur), icon_url=utilisateur.display_avatar.url)
    embed.set_thumbnail(url=utilisateur.display_avatar.url)

    embed.add_field(name="👤 Pseudo", value=str(utilisateur), inline=True)
    embed.add_field(name="🆔 ID", value=str(utilisateur.id), inline=True)
    embed.add_field(name="🤖 Bot", value="Oui" if utilisateur.bot else "Non", inline=True)

    embed.add_field(
        name="📅 Compte Discord créé le",
        value=f"{discord.utils.format_dt(utilisateur.created_at, style='F')}\n{discord.utils.format_dt(utilisateur.created_at, style='R')}",
        inline=False,
    )

    if utilisateur.joined_at:
        embed.add_field(
            name="📥 A rejoint le serveur le",
            value=f"{discord.utils.format_dt(utilisateur.joined_at, style='F')}\n{discord.utils.format_dt(utilisateur.joined_at, style='R')}",
            inline=False,
        )

    roles = [role.mention for role in reversed(utilisateur.roles) if role != interaction.guild.default_role]
    roles_text = ", ".join(roles) if roles else "Aucun rôle"
    if len(roles_text) > 1024:
        roles_text = roles_text[:1000] + "…"
    embed.add_field(name=f"🎭 Rôles ({len(roles)})", value=roles_text, inline=False)

    if utilisateur.top_role != interaction.guild.default_role:
        embed.add_field(name="👑 Rôle le plus élevé", value=utilisateur.top_role.mention, inline=True)

    if utilisateur.premium_since:
        embed.add_field(
            name="💎 Booste le serveur depuis",
            value=discord.utils.format_dt(utilisateur.premium_since, style="R"),
            inline=True,
        )

    nb_warnings = len(warnings_store.get(utilisateur.id, []))
    embed.add_field(name="⚠️ Avertissements", value=str(nb_warnings), inline=True)

    nb_messages = message_count_store.get(utilisateur.id, 0)
    embed.add_field(
        name="💬 Messages envoyés",
        value=f"{nb_messages} (comptés depuis le dernier redémarrage du bot)",
        inline=True,
    )

    if await is_owner_level(utilisateur):
        embed.add_field(name="👑 Owner du bot", value="Oui", inline=True)

    footer_text = f"Demandé par {interaction.user}"
    if is_premium(interaction.guild.id):
        footer_text = f"✨ Serveur Premium — {footer_text}"
    embed.set_footer(text=footer_text, icon_url=interaction.user.display_avatar.url)

    await interaction.response.send_message(embed=embed)


def parse_duration(duration_str: str):
    """Convertit un texte comme '10m', '10min', '1h', '1j' en secondes.
    Retourne None si le format n'est pas reconnu."""
    duration_str = duration_str.strip().lower()

    units = {
        "s": 1, "sec": 1, "secs": 1, "second": 1, "seconds": 1,
        "m": 60, "min": 60, "mins": 60, "minute": 60, "minutes": 60,
        "h": 3600, "hr": 3600, "hrs": 3600, "heure": 3600, "heures": 3600, "hour": 3600, "hours": 3600,
        "j": 86400, "d": 86400, "day": 86400, "days": 86400, "jour": 86400, "jours": 86400,
    }

    # Sépare le nombre du texte, ex: "10min" -> "10" et "min"
    number = ""
    unit = ""
    for char in duration_str:
        if char.isdigit():
            number += char
        else:
            unit += char

    if not number or unit not in units:
        return None

    return int(number) * units[unit]


def format_duration(seconds: int) -> str:
    """Convertit un nombre de secondes en texte lisible, ex: 600 -> '10 minutes'."""
    units = [
        (86400, "jour", "jours"),
        (3600, "heure", "heures"),
        (60, "minute", "minutes"),
        (1, "seconde", "secondes"),
    ]

    for unit_seconds, singular, plural in units:
        if seconds % unit_seconds == 0 and seconds // unit_seconds >= 1:
            value = seconds // unit_seconds
            label = singular if value == 1 else plural
            return f"{value} {label}"

    # Cas où la durée ne tombe pas rond sur une seule unité (ex: 90s = 1min30)
    return f"{seconds} secondes"


@bot.tree.command(name="mute", description="Mute un membre du serveur pour une durée donnée")
@app_commands.describe(
    utilisateur="La personne à mute",
    duree="La durée du mute, ex: 10m, 1h, 1j",
    raison="La raison du mute",
)
@has_permissions_or_owner(moderate_members=True)
async def mute(interaction: discord.Interaction, utilisateur: discord.Member, duree: str, raison: str = "Aucune raison fournie"):
    seconds = parse_duration(duree)

    if seconds is None:
        await interaction.response.send_message(
            "Format de durée invalide. Exemples valides : 10s, 10min, 1h, 1j",
            ephemeral=True,
        )
        return

    if seconds > 28 * 24 * 3600:
        await interaction.response.send_message(
            "Discord ne permet pas de mute au-delà de 28 jours.",
            ephemeral=True,
        )
        return

    if utilisateur.is_timed_out():
        embed = discord.Embed(
            title="⛔ Déjà mute",
            description=f"{utilisateur.mention} est déjà mute actuellement.",
            color=discord.Color.red(),
            timestamp=datetime.datetime.now(),
        )
        embed.set_thumbnail(url=utilisateur.display_avatar.url)
        if utilisateur.timed_out_until:
            embed.add_field(
                name="Mute actif jusqu'à",
                value=f"{discord.utils.format_dt(utilisateur.timed_out_until, style='F')}\n{discord.utils.format_dt(utilisateur.timed_out_until, style='R')}",
                inline=False,
            )
        embed.set_footer(text=f"ID : {utilisateur.id} • Utilise /demute pour retirer le mute avant d'en appliquer un nouveau")
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return

    timeout_duration = datetime.timedelta(seconds=seconds)

    try:
        await utilisateur.timeout(timeout_duration, reason=raison)
    except discord.Forbidden:
        await interaction.response.send_message(
            "Je n'ai pas la permission de mute cette personne (vérifie mon rôle et sa position dans la hiérarchie).",
            ephemeral=True,
        )
        return

    duree_lisible = format_duration(seconds)

    embed = build_mod_embed(
        "🔇", "Membre mute", discord.Color.orange(),
        utilisateur, interaction.user, raison,
        extra_fields=[("Durée", duree_lisible)],
        guild=interaction.guild,
        owner_badge=await is_owner_level(interaction.user),
    )
    await interaction.response.send_message(embed=embed)

    # Tentative d'envoi d'un MP à la personne mutée
    try:
        await utilisateur.send(
            f"Tu as été mute par {interaction.user.mention} pour {duree_lisible}. Raison : {raison}"
        )
    except discord.Forbidden:
        # La personne a désactivé les MP, on ignore silencieusement
        pass


@bot.tree.command(name="demute", description="Retire le mute d'un membre du serveur")
@app_commands.describe(
    utilisateur="La personne à démute",
    raison="La raison du démute",
)
@has_permissions_or_owner(moderate_members=True)
async def demute(interaction: discord.Interaction, utilisateur: discord.Member, raison: str = "Aucune raison fournie"):
    try:
        await utilisateur.timeout(None, reason=raison)
    except discord.Forbidden:
        await interaction.response.send_message(
            "Je n'ai pas la permission de démute cette personne.",
            ephemeral=True,
        )
        return

    embed = build_mod_embed("🔊", "Membre démute", discord.Color.green(), utilisateur, interaction.user, raison, guild=interaction.guild, owner_badge=await is_owner_level(interaction.user))
    await interaction.response.send_message(embed=embed)

    try:
        await utilisateur.send(f"Tu as été démute par {interaction.user.mention}. Raison : {raison}")
    except discord.Forbidden:
        pass


@mute.error
@demute.error
async def mute_demute_error(interaction: discord.Interaction, error):
    if isinstance(error, app_commands.MissingPermissions):
        await interaction.response.send_message(
            "Tu n'as pas la permission d'utiliser cette commande.",
            ephemeral=True,
        )
    else:
        await interaction.response.send_message(
            f"Une erreur est survenue : {error}",
            ephemeral=True,
        )


# Stockage des avertissements : {user_id: [ {id, moderator, reason, timestamp}, ... ]}
# Chargé depuis la base SQLite au démarrage, et écrit dans la base à chaque changement.
warnings_store = db.load_warnings()
print(f"[DB] {len(warnings_store)} membre(s) avec des avertissements chargé(s) depuis la base.")


def build_warn_notification_embed(utilisateur: discord.Member, moderateur: discord.Member, raison: str, total: int) -> discord.Embed:
    """Embed affiché dans le salon quand quelqu'un reçoit un avertissement."""
    embed = discord.Embed(
        title="⚠️ Avertissement donné",
        description=f"{utilisateur.mention} a reçu un avertissement.",
        color=discord.Color.orange(),
        timestamp=datetime.datetime.now(),
    )
    embed.set_author(name=str(utilisateur), icon_url=utilisateur.display_avatar.url)
    embed.set_thumbnail(url=utilisateur.display_avatar.url)
    embed.add_field(name="Raison", value=raison, inline=False)
    embed.add_field(name="Modérateur", value=moderateur.mention, inline=True)
    embed.add_field(name="Total d'avertissements", value=str(total), inline=True)
    embed.set_footer(text=f"ID : {utilisateur.id}")
    return embed


def build_warnings_embed(utilisateur: discord.Member, user_warnings: list) -> discord.Embed:
    """Embed listant les avertissements d'un membre, utilisé par /warnings."""
    embed = discord.Embed(
        title=f"Avertissements de {utilisateur}",
        color=discord.Color.orange() if user_warnings else discord.Color.green(),
        timestamp=datetime.datetime.now(),
    )
    embed.set_author(name=str(utilisateur), icon_url=utilisateur.display_avatar.url)
    embed.set_thumbnail(url=utilisateur.display_avatar.url)
    embed.set_footer(text=f"ID : {utilisateur.id}")

    if not user_warnings:
        embed.description = "✅ Ce membre n'a aucun avertissement."
        return embed

    embed.description = f"**{len(user_warnings)}** avertissement(s) au total."
    for i, w in enumerate(user_warnings, start=1):
        embed.add_field(
            name=f"#{i} — {w['timestamp']}",
            value=f"**Raison :** {w['reason']}\n**Par :** {w['moderator']}",
            inline=False,
        )
    return embed


class WarningDeleteSelect(discord.ui.Select):
    """Menu déroulant permettant de choisir quel avertissement supprimer."""

    def __init__(self, utilisateur: discord.Member, user_warnings: list, author_id: int):
        self.utilisateur = utilisateur
        self.author_id = author_id

        options = [
            discord.SelectOption(
                label=f"Avertissement #{i + 1}",
                description=(w["reason"][:95] + "…") if len(w["reason"]) > 95 else w["reason"],
                value=str(i),
                emoji="🗑️",
            )
            for i, w in enumerate(user_warnings)
        ]

        super().__init__(
            placeholder="🗑️ Choisis l'avertissement à supprimer...",
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(
                "Seule la personne qui a lancé la commande peut supprimer un avertissement.",
                ephemeral=True,
            )
            return

        index = int(self.values[0])
        user_warnings = warnings_store.get(self.utilisateur.id, [])

        if index >= len(user_warnings):
            await interaction.response.send_message(
                "Cet avertissement n'existe plus (il a peut-être déjà été supprimé).",
                ephemeral=True,
            )
            return

        removed = user_warnings.pop(index)
        warnings_store[self.utilisateur.id] = user_warnings
        db.delete_warning(removed["id"])

        new_embed = build_warnings_embed(self.utilisateur, user_warnings)
        new_view = WarningDeleteView(self.utilisateur, user_warnings, self.author_id) if user_warnings else None

        await interaction.response.edit_message(embed=new_embed, view=new_view)
        await interaction.followup.send(
            f"🗑️ Avertissement supprimé : *{removed['reason']}*",
            ephemeral=True,
        )


class WarningDeleteView(discord.ui.View):
    """Vue contenant le menu de suppression d'avertissements."""

    def __init__(self, utilisateur: discord.Member, user_warnings: list, author_id: int):
        super().__init__(timeout=120)
        if user_warnings:
            self.add_item(WarningDeleteSelect(utilisateur, user_warnings, author_id))


@bot.tree.command(name="ban", description="Bannit un membre du serveur")
@app_commands.describe(
    utilisateur="La personne à bannir",
    raison="La raison du bannissement",
)
@has_permissions_or_owner(ban_members=True)
async def ban(interaction: discord.Interaction, utilisateur: discord.Member, raison: str = "Aucune raison fournie"):
    try:
        await utilisateur.send(
            f"Tu as été banni par {interaction.user.mention} du serveur {interaction.guild.name}. Raison : {raison}"
        )
    except discord.Forbidden:
        pass

    try:
        await utilisateur.ban(reason=raison)
    except discord.Forbidden:
        await interaction.response.send_message(
            "Je n'ai pas la permission de bannir cette personne.",
            ephemeral=True,
        )
        return

    embed = build_mod_embed("🔨", "Membre banni", discord.Color.red(), utilisateur, interaction.user, raison, guild=interaction.guild, owner_badge=await is_owner_level(interaction.user))
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="unban", description="Débannit un membre via son ID")
@app_commands.describe(
    user_id="L'ID de la personne à débannir",
    raison="La raison du débannissement",
)
@has_permissions_or_owner(ban_members=True)
async def unban(interaction: discord.Interaction, user_id: str, raison: str = "Aucune raison fournie"):
    try:
        user_id_int = int(user_id)
    except ValueError:
        await interaction.response.send_message(
            "L'ID fourni n'est pas valide.",
            ephemeral=True,
        )
        return

    try:
        user = await bot.fetch_user(user_id_int)
        await interaction.guild.unban(user, reason=raison)
    except discord.NotFound:
        await interaction.response.send_message(
            "Cette personne n'est pas bannie ou l'ID est incorrect.",
            ephemeral=True,
        )
        return
    except discord.Forbidden:
        await interaction.response.send_message(
            "Je n'ai pas la permission de débannir cette personne.",
            ephemeral=True,
        )
        return

    embed = build_mod_embed("♻️", "Membre débanni", discord.Color.green(), user, interaction.user, raison, guild=interaction.guild, owner_badge=await is_owner_level(interaction.user))
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="kick", description="Expulse un membre du serveur")
@app_commands.describe(
    utilisateur="La personne à expulser",
    raison="La raison de l'expulsion",
)
@has_permissions_or_owner(kick_members=True)
async def kick(interaction: discord.Interaction, utilisateur: discord.Member, raison: str = "Aucune raison fournie"):
    try:
        await utilisateur.send(
            f"Tu as été expulsé par {interaction.user.mention} du serveur {interaction.guild.name}. Raison : {raison}"
        )
    except discord.Forbidden:
        pass

    try:
        await utilisateur.kick(reason=raison)
    except discord.Forbidden:
        await interaction.response.send_message(
            "Je n'ai pas la permission d'expulser cette personne.",
            ephemeral=True,
        )
        return

    embed = build_mod_embed("👢", "Membre expulsé", discord.Color.orange(), utilisateur, interaction.user, raison, guild=interaction.guild, owner_badge=await is_owner_level(interaction.user))
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="warn", description="Donne un avertissement à un membre")
@app_commands.describe(
    utilisateur="La personne à avertir",
    raison="La raison de l'avertissement",
)
@has_permissions_or_owner(moderate_members=True)
async def warn(interaction: discord.Interaction, utilisateur: discord.Member, raison: str = "Aucune raison fournie"):
    timestamp = datetime.datetime.now().strftime("%d/%m/%Y %H:%M")
    warning_id = db.add_warning(utilisateur.id, interaction.user.mention, raison, timestamp)

    user_warnings = warnings_store.setdefault(utilisateur.id, [])
    user_warnings.append({
        "id": warning_id,
        "moderator": interaction.user.mention,
        "reason": raison,
        "timestamp": timestamp,
    })

    embed = build_warn_notification_embed(utilisateur, interaction.user, raison, len(user_warnings))
    await interaction.response.send_message(embed=embed)

    try:
        await utilisateur.send(
            f"Tu as reçu un avertissement de {interaction.user.mention} sur {interaction.guild.name}. Raison : {raison}"
        )
    except discord.Forbidden:
        pass


@bot.tree.command(name="warnings", description="Affiche les avertissements d'un membre")
@app_commands.describe(utilisateur="La personne dont tu veux voir les avertissements")
@has_permissions_or_owner(moderate_members=True)
async def warnings_cmd(interaction: discord.Interaction, utilisateur: discord.Member):
    user_warnings = warnings_store.get(utilisateur.id, [])

    embed = build_warnings_embed(utilisateur, user_warnings)
    view = WarningDeleteView(utilisateur, user_warnings, interaction.user.id) if user_warnings else None

    await interaction.response.send_message(embed=embed, view=view, ephemeral=True)


@bot.tree.command(name="clearwarnings", description="Efface tous les avertissements d'un membre")
@app_commands.describe(utilisateur="La personne dont tu veux effacer les avertissements")
@has_permissions_or_owner(moderate_members=True)
async def clearwarnings(interaction: discord.Interaction, utilisateur: discord.Member):
    warnings_store[utilisateur.id] = []
    db.clear_warnings(utilisateur.id)
    embed = build_mod_embed("🧹", "Avertissements effacés", discord.Color.green(), utilisateur, interaction.user, guild=interaction.guild, owner_badge=await is_owner_level(interaction.user))
    await interaction.response.send_message(embed=embed)


@ban.error
@unban.error
@kick.error
@warn.error
@warnings_cmd.error
@clearwarnings.error
async def moderation_error(interaction: discord.Interaction, error):
    if isinstance(error, app_commands.MissingPermissions):
        await interaction.response.send_message(
            "Tu n'as pas la permission d'utiliser cette commande.",
            ephemeral=True,
        )
    else:
        await interaction.response.send_message(
            f"Une erreur est survenue : {error}",
            ephemeral=True,
        )


def build_action_embed(emoji: str, title: str, color: discord.Color, description: str, guild: discord.Guild = None, extra_fields: list = None, owner_badge: bool = False) -> discord.Embed:
    """Embed uniforme pour les actions qui ne ciblent pas un membre précis
    (verrouillage de salon, mode lent, purge de messages, etc.)."""
    premium = guild is not None and is_premium(guild.id)
    if premium:
        color = get_premium_color(guild.id)

    embed = discord.Embed(
        title=f"{emoji} {title}" + (" ✨" if premium else ""),
        description=description,
        color=color,
        timestamp=datetime.datetime.now(),
    )
    if extra_fields:
        for name, value in extra_fields:
            embed.add_field(name=name, value=value, inline=True)

    footer_parts = []
    if premium:
        footer_parts.append("✨ Serveur Premium")
    if owner_badge:
        footer_parts.append("👑 Action Owner")
    if footer_parts:
        embed.set_footer(text=" — ".join(footer_parts))
    return embed


# --- Commandes utilitaires ---

@bot.tree.command(name="clear", description="Supprime plusieurs messages d'un coup dans le salon")
@app_commands.describe(
    nombre="Nombre de messages à supprimer (entre 1 et 100)",
    utilisateur="Ne supprimer que les messages de cette personne (optionnel)",
)
@has_permissions_or_owner(manage_messages=True)
async def clear(interaction: discord.Interaction, nombre: app_commands.Range[int, 1, 100], utilisateur: discord.Member = None):
    await interaction.response.defer(ephemeral=True)

    def check(m: discord.Message) -> bool:
        return utilisateur is None or m.author.id == utilisateur.id

    deleted = await interaction.channel.purge(limit=nombre, check=check)

    description = f"**{len(deleted)}** message(s) supprimé(s) dans {interaction.channel.mention}."
    if utilisateur is not None:
        description += f"\nFiltré sur les messages de {utilisateur.mention}."

    embed = build_action_embed(
        "🧹", "Messages supprimés", discord.Color.green(), description,
        guild=interaction.guild, owner_badge=await is_owner_level(interaction.user),
    )
    await interaction.followup.send(embed=embed, ephemeral=True)


@bot.tree.command(name="slowmode", description="Configure le mode lent (slowmode) du salon")
@app_commands.describe(secondes="Délai en secondes entre chaque message (0 pour désactiver, max 21600)")
@has_permissions_or_owner(manage_channels=True)
async def slowmode(interaction: discord.Interaction, secondes: app_commands.Range[int, 0, 21600]):
    await interaction.channel.edit(slowmode_delay=secondes)

    if secondes == 0:
        description = f"Le mode lent a été **désactivé** dans {interaction.channel.mention}."
    else:
        description = f"Le mode lent est maintenant de **{format_duration(secondes)}** dans {interaction.channel.mention}."

    embed = build_action_embed(
        "🐌", "Mode lent mis à jour", discord.Color.blurple(), description,
        guild=interaction.guild, owner_badge=await is_owner_level(interaction.user),
    )
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="lock", description="Verrouille le salon (empêche @everyone d'écrire)")
@app_commands.describe(raison="La raison du verrouillage")
@has_permissions_or_owner(manage_channels=True)
async def lock(interaction: discord.Interaction, raison: str = "Aucune raison fournie"):
    overwrite = interaction.channel.overwrites_for(interaction.guild.default_role)
    overwrite.send_messages = False
    await interaction.channel.set_permissions(interaction.guild.default_role, overwrite=overwrite, reason=raison)

    embed = build_action_embed(
        "🔒", "Salon verrouillé", discord.Color.red(),
        f"{interaction.channel.mention} a été verrouillé. Seuls les membres avec des permissions spécifiques peuvent encore écrire.",
        guild=interaction.guild, extra_fields=[("Raison", raison)],
        owner_badge=await is_owner_level(interaction.user),
    )
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="unlock", description="Déverrouille le salon")
@app_commands.describe(raison="La raison du déverrouillage")
@has_permissions_or_owner(manage_channels=True)
async def unlock(interaction: discord.Interaction, raison: str = "Aucune raison fournie"):
    overwrite = interaction.channel.overwrites_for(interaction.guild.default_role)
    overwrite.send_messages = None
    await interaction.channel.set_permissions(interaction.guild.default_role, overwrite=overwrite, reason=raison)

    embed = build_action_embed(
        "🔓", "Salon déverrouillé", discord.Color.green(),
        f"{interaction.channel.mention} a été déverrouillé.",
        guild=interaction.guild, extra_fields=[("Raison", raison)],
        owner_badge=await is_owner_level(interaction.user),
    )
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="pseudo", description="Change le pseudo d'un membre sur le serveur")
@app_commands.describe(
    utilisateur="Le membre concerné",
    nouveau_pseudo="Le nouveau pseudo (laisse vide pour réinitialiser au nom d'origine)",
)
@has_permissions_or_owner(manage_nicknames=True)
async def pseudo(interaction: discord.Interaction, utilisateur: discord.Member, nouveau_pseudo: str = None):
    ancien_pseudo = utilisateur.display_name
    try:
        await utilisateur.edit(nick=nouveau_pseudo, reason=f"Changé par {interaction.user}")
    except discord.Forbidden:
        await interaction.response.send_message(
            "Je n'ai pas la permission de changer le pseudo de cette personne (vérifie mon rôle et sa position dans la hiérarchie).",
            ephemeral=True,
        )
        return

    embed = build_mod_embed(
        "✏️", "Pseudo modifié", discord.Color.blurple(), utilisateur, interaction.user,
        extra_fields=[("Avant", ancien_pseudo), ("Après", nouveau_pseudo or utilisateur.name)],
        guild=interaction.guild, owner_badge=await is_owner_level(interaction.user),
    )
    await interaction.response.send_message(embed=embed)


@clear.error
@slowmode.error
@lock.error
@unlock.error
@pseudo.error
async def utility_moderation_error(interaction: discord.Interaction, error):
    if isinstance(error, app_commands.MissingPermissions):
        await interaction.response.send_message(
            "Tu n'as pas la permission d'utiliser cette commande.",
            ephemeral=True,
        )
    else:
        await interaction.response.send_message(
            f"Une erreur est survenue : {error}",
            ephemeral=True,
        )


@bot.tree.command(name="avatar", description="Affiche l'avatar d'un membre en grand")
@app_commands.describe(utilisateur="La personne dont tu veux voir l'avatar (toi par défaut)")
async def avatar(interaction: discord.Interaction, utilisateur: discord.Member = None):
    utilisateur = utilisateur or interaction.user
    couleur = get_premium_color(interaction.guild.id) if is_premium(interaction.guild.id) else discord.Color.blurple()

    embed = discord.Embed(
        title=f"🖼️ Avatar de {utilisateur.display_name}",
        color=couleur,
        timestamp=datetime.datetime.now(),
    )
    embed.set_image(url=utilisateur.display_avatar.url)
    embed.set_footer(text=f"ID : {utilisateur.id}")
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="servericon", description="Affiche l'icône du serveur en grand")
async def servericon(interaction: discord.Interaction):
    if interaction.guild.icon is None:
        await interaction.response.send_message("Ce serveur n'a pas d'icône.", ephemeral=True)
        return

    couleur = get_premium_color(interaction.guild.id) if is_premium(interaction.guild.id) else discord.Color.blurple()
    embed = discord.Embed(
        title=f"🖼️ Icône de {interaction.guild.name}",
        color=couleur,
        timestamp=datetime.datetime.now(),
    )
    embed.set_image(url=interaction.guild.icon.url)
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="serverinfo", description="Affiche les informations générales du serveur")
async def serverinfo(interaction: discord.Interaction):
    guild = interaction.guild
    couleur = get_premium_color(guild.id) if is_premium(guild.id) else discord.Color.blurple()

    embed = discord.Embed(
        title=f"📊 Informations sur {guild.name}" + (" ✨" if is_premium(guild.id) else ""),
        color=couleur,
        timestamp=datetime.datetime.now(),
    )
    if guild.icon:
        embed.set_thumbnail(url=guild.icon.url)

    embed.add_field(name="🆔 ID", value=str(guild.id), inline=True)
    embed.add_field(name="👑 Propriétaire", value=guild.owner.mention if guild.owner else "Inconnu", inline=True)
    embed.add_field(name="📅 Créé le", value=discord.utils.format_dt(guild.created_at, style="D"), inline=True)
    embed.add_field(name="👥 Membres", value=str(guild.member_count), inline=True)
    embed.add_field(name="💬 Salons textuels", value=str(len(guild.text_channels)), inline=True)
    embed.add_field(name="🔊 Salons vocaux", value=str(len(guild.voice_channels)), inline=True)
    embed.add_field(name="🎭 Rôles", value=str(len(guild.roles)), inline=True)
    embed.add_field(name="😀 Emojis", value=str(len(guild.emojis)), inline=True)
    embed.add_field(
        name="💎 Niveau de boost",
        value=f"Niveau {guild.premium_tier} ({guild.premium_subscription_count} boost(s))",
        inline=True,
    )
    if is_premium(guild.id):
        embed.add_field(name="✨ Premium du bot", value="Actif — utilise `/premium status` pour les détails", inline=False)

    embed.set_footer(text=f"Demandé par {interaction.user}", icon_url=interaction.user.display_avatar.url)
    await interaction.response.send_message(embed=embed)


# --- Gestion des rôles ---
role_group = app_commands.Group(name="role", description="Gestion des rôles des membres")
bot.tree.add_command(role_group)


@role_group.command(name="add", description="Ajoute un rôle à un membre")
@app_commands.describe(utilisateur="Le membre concerné", role="Le rôle à ajouter")
@has_permissions_or_owner(manage_roles=True)
async def role_add(interaction: discord.Interaction, utilisateur: discord.Member, role: discord.Role):
    if role >= interaction.guild.me.top_role:
        await interaction.response.send_message(
            "Je ne peux pas gérer ce rôle : il est plus haut que mon propre rôle dans la hiérarchie.",
            ephemeral=True,
        )
        return
    if role in utilisateur.roles:
        await interaction.response.send_message(f"{utilisateur.mention} a déjà le rôle {role.mention}.", ephemeral=True)
        return

    await utilisateur.add_roles(role, reason=f"Ajouté par {interaction.user}")
    embed = build_mod_embed(
        "➕", "Rôle ajouté", discord.Color.green(), utilisateur, interaction.user,
        extra_fields=[("Rôle", role.mention)], guild=interaction.guild,
        owner_badge=await is_owner_level(interaction.user),
    )
    await interaction.response.send_message(embed=embed)


@role_group.command(name="remove", description="Retire un rôle à un membre")
@app_commands.describe(utilisateur="Le membre concerné", role="Le rôle à retirer")
@has_permissions_or_owner(manage_roles=True)
async def role_remove(interaction: discord.Interaction, utilisateur: discord.Member, role: discord.Role):
    if role >= interaction.guild.me.top_role:
        await interaction.response.send_message(
            "Je ne peux pas gérer ce rôle : il est plus haut que mon propre rôle dans la hiérarchie.",
            ephemeral=True,
        )
        return
    if role not in utilisateur.roles:
        await interaction.response.send_message(f"{utilisateur.mention} n'a pas le rôle {role.mention}.", ephemeral=True)
        return

    await utilisateur.remove_roles(role, reason=f"Retiré par {interaction.user}")
    embed = build_mod_embed(
        "➖", "Rôle retiré", discord.Color.orange(), utilisateur, interaction.user,
        extra_fields=[("Rôle", role.mention)], guild=interaction.guild,
        owner_badge=await is_owner_level(interaction.user),
    )
    await interaction.response.send_message(embed=embed)


@role_add.error
@role_remove.error
async def role_error(interaction: discord.Interaction, error):
    if isinstance(error, app_commands.MissingPermissions):
        await interaction.response.send_message(
            "Tu n'as pas la permission d'utiliser cette commande.",
            ephemeral=True,
        )
    else:
        await interaction.response.send_message(
            f"Une erreur est survenue : {error}",
            ephemeral=True,
        )


@bot.tree.command(name="sondage", description="Crée un sondage rapide avec réactions")
@app_commands.describe(
    question="La question du sondage",
    options="Les choix séparés par des virgules (max 9). Laisse vide pour un simple 👍 / 👎",
)
async def sondage(interaction: discord.Interaction, question: str, options: str = None):
    numeros = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣"]
    couleur = get_premium_color(interaction.guild.id) if is_premium(interaction.guild.id) else discord.Color.blurple()

    embed = discord.Embed(
        title="📊 Sondage",
        description=f"**{question}**",
        color=couleur,
        timestamp=datetime.datetime.now(),
    )
    embed.set_footer(text=f"Sondage lancé par {interaction.user}", icon_url=interaction.user.display_avatar.url)

    if options:
        choix = [c.strip() for c in options.split(",") if c.strip()][:9]
        if len(choix) < 2:
            await interaction.response.send_message(
                "Donne au moins 2 choix séparés par des virgules, ex : `Pizza, Sushi, Burger`.",
                ephemeral=True,
            )
            return
        texte = "\n".join(f"{numeros[i]} {c}" for i, c in enumerate(choix))
        embed.add_field(name="Choix", value=texte, inline=False)
        emojis = numeros[:len(choix)]
    else:
        emojis = ["👍", "👎"]

    await interaction.response.send_message(embed=embed)
    message = await interaction.original_response()
    for e in emojis:
        await message.add_reaction(e)


# Couleurs prédéfinies proposées pour /premium couleur : (nom affiché, code hex, emoji)
PRESET_COLORS = [
    ("Rose", "FF69B4", "🩷"),
    ("Rouge", "E74C3C", "🔴"),
    ("Orange", "E67E22", "🟠"),
    ("Jaune", "F1C40F", "🟡"),
    ("Vert", "2ECC71", "🟢"),
    ("Turquoise", "1ABC9C", "🔷"),
    ("Bleu", "3498DB", "🔵"),
    ("Violet", "9B59B6", "🟣"),
    ("Marron", "8B4513", "🟤"),
    ("Noir", "23272A", "⚫"),
    ("Blanc", "FFFFFF", "⚪"),
    ("Doré (défaut)", "FFD700", "🟨"),
]


class PremiumColorSelect(discord.ui.Select):
    """Menu déroulant listant les couleurs prédéfinies pour /premium couleur."""

    def __init__(self, guild_id: int, author_id: int):
        self.guild_id = guild_id
        self.author_id = author_id

        options = [
            discord.SelectOption(label=nom, value=hex_code, emoji=emoji, description=f"#{hex_code}")
            for nom, hex_code, emoji in PRESET_COLORS
        ]

        super().__init__(
            placeholder="🎨 Choisis une couleur prédéfinie...",
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(
                "Seule la personne qui a lancé la commande peut choisir la couleur.",
                ephemeral=True,
            )
            return

        hex_color = self.values[0]
        premium_servers[self.guild_id]["color"] = hex_color
        db.update_premium_color(self.guild_id, hex_color)

        embed = discord.Embed(
            title="🎨 Couleur mise à jour !",
            description="Tous les embeds du bot sur ce serveur utiliseront désormais cette couleur.",
            color=discord.Color(int(hex_color, 16)),
            timestamp=datetime.datetime.now(),
        )
        embed.add_field(name="Nouvelle couleur", value=f"`#{hex_color}`", inline=True)
        embed.set_footer(text=f"Changé par {interaction.user}")
        await interaction.response.edit_message(embed=embed, view=None)


class PremiumColorView(discord.ui.View):
    """Vue contenant le menu de sélection de couleur prédéfinie."""

    def __init__(self, guild_id: int, author_id: int):
        super().__init__(timeout=120)
        self.add_item(PremiumColorSelect(guild_id, author_id))


# --- Commandes Premium ---
premium_group = app_commands.Group(name="premium", description="Gestion du service Premium du bot")
bot.tree.add_command(premium_group)


@premium_group.command(name="generer", description="[Propriétaire uniquement] Génère un code Premium et l'envoie en MP")
@app_commands.describe(utilisateur="La personne à qui envoyer le code Premium")
async def premium_generer(interaction: discord.Interaction, utilisateur: discord.User):
    # Volontairement limité au SEUL vrai propriétaire du bot : ni les owners ajoutés
    # via /owner add, ni personne d'autre, ne peuvent générer de codes Premium.
    if not await is_full_owner(interaction.user):
        embed = discord.Embed(
            title="⛔ Accès refusé",
            description="Seul le propriétaire du bot peut générer des codes Premium.",
            color=discord.Color.red(),
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return

    code = generate_premium_code()
    generated_at = datetime.datetime.now()
    premium_codes[code] = {
        "generated_by": interaction.user.id,
        "assigned_to": utilisateur.id,
        "used": False,
        "used_by": None,
        "used_in_guild": None,
        "generated_at": generated_at,
    }
    db.add_premium_code(code, interaction.user.id, utilisateur.id, generated_at.isoformat())

    dm_embed = discord.Embed(
        title="✨ Ton code Premium",
        description="Un code Premium vient de t'être offert ! Utilise `/premium activer` sur le serveur que tu veux booster pour l'activer.",
        color=discord.Color.gold(),
        timestamp=datetime.datetime.now(),
    )
    dm_embed.add_field(name="Code", value=f"`{code}`", inline=False)
    dm_embed.set_footer(text="Ce code est à usage unique, garde-le secret.")

    try:
        await utilisateur.send(embed=dm_embed)
        envoye = True
    except discord.Forbidden:
        envoye = False

    confirm_embed = discord.Embed(
        title="✅ Code Premium généré",
        color=discord.Color.gold(),
        timestamp=datetime.datetime.now(),
    )
    confirm_embed.set_thumbnail(url=utilisateur.display_avatar.url)
    confirm_embed.add_field(name="Code", value=f"`{code}`", inline=False)
    confirm_embed.add_field(name="Destinataire", value=utilisateur.mention, inline=True)
    confirm_embed.add_field(name="Envoyé en MP", value="✅ Oui" if envoye else "❌ Non (MP fermés, transmets-le à la main)", inline=True)
    await interaction.response.send_message(embed=confirm_embed, ephemeral=True)


@premium_group.command(name="activer", description="Active le Premium sur ce serveur avec un code reçu en MP")
@app_commands.describe(code="Le code Premium que tu as reçu")
async def premium_activer(interaction: discord.Interaction, code: str):
    code = code.strip().upper()
    data = premium_codes.get(code)

    if is_premium(interaction.guild.id):
        embed = discord.Embed(
            title="✨ Déjà Premium",
            description="Ce serveur profite déjà du Premium !",
            color=discord.Color.gold(),
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return

    if data is None or data["used"]:
        embed = discord.Embed(
            title="❌ Code invalide",
            description="Ce code n'existe pas ou a déjà été utilisé.",
            color=discord.Color.red(),
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return

    data["used"] = True
    data["used_by"] = interaction.user.id
    data["used_in_guild"] = interaction.guild.id
    db.mark_premium_code_used(code, interaction.user.id, interaction.guild.id)

    activated_at = datetime.datetime.now()
    premium_servers[interaction.guild.id] = {
        "activated_by": interaction.user.id,
        "activated_at": activated_at,
        "code": code,
        "color": "FFD700",
    }
    db.add_premium_server(interaction.guild.id, interaction.user.id, activated_at.isoformat(), code, "FFD700")

    embed = discord.Embed(
        title="🎉 Serveur passé Premium !",
        description=f"**{interaction.guild.name}** est maintenant Premium grâce à {interaction.user.mention} !",
        color=discord.Color.gold(),
        timestamp=datetime.datetime.now(),
    )
    embed.add_field(
        name="✨ Bonus débloqués",
        value=(
            "• Embeds colorés sur toutes les commandes de modération\n"
            "• Badge ✨ Premium sur `/info` et `/warnings`\n"
            "• Couleur personnalisable avec `/premium couleur`\n"
            "• Support prioritaire"
        ),
        inline=False,
    )
    if interaction.guild.icon:
        embed.set_thumbnail(url=interaction.guild.icon.url)
    embed.set_footer(text=f"Activé par {interaction.user}")
    await interaction.response.send_message(embed=embed)


@premium_group.command(name="couleur", description="[Premium] Choisis la couleur des embeds du bot sur ce serveur")
@app_commands.describe(couleur="Optionnel : code hex personnalisé (ex: #FF5733). Laisse vide pour choisir une couleur prédéfinie.")
@has_permissions_or_owner(manage_guild=True)
async def premium_couleur(interaction: discord.Interaction, couleur: str = None):
    if not is_premium(interaction.guild.id):
        embed = discord.Embed(
            title="✨ Fonctionnalité Premium",
            description="La personnalisation de couleur est réservée aux serveurs Premium.\nUtilise `/premium activer` avec un code pour débloquer cette option.",
            color=discord.Color.greyple(),
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return

    # Aucun code hex fourni : on propose le menu de couleurs prédéfinies
    if couleur is None:
        embed = discord.Embed(
            title="🎨 Choisis une couleur",
            description=(
                "Sélectionne une couleur prédéfinie dans le menu ci-dessous 👇\n\n"
                "Tu préfères une couleur précise ? Relance la commande avec "
                "`/premium couleur couleur:#RRGGBB` pour un code hex personnalisé."
            ),
            color=get_premium_color(interaction.guild.id),
        )
        # Aperçu visuel des couleurs disponibles
        apercu = "  ".join(f"{emoji} {nom}" for nom, _, emoji in PRESET_COLORS)
        embed.add_field(name="Couleurs disponibles", value=apercu, inline=False)
        view = PremiumColorView(interaction.guild.id, interaction.user.id)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
        return

    hex_color = parse_hex_color(couleur)
    if hex_color is None:
        embed = discord.Embed(
            title="❌ Couleur invalide",
            description="Utilise un code hexadécimal valide, par exemple `#FF5733`, `5865F2` ou `00FF00`.",
            color=discord.Color.red(),
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return

    premium_servers[interaction.guild.id]["color"] = hex_color
    db.update_premium_color(interaction.guild.id, hex_color)

    embed = discord.Embed(
        title="🎨 Couleur mise à jour !",
        description="Tous les embeds du bot sur ce serveur utiliseront désormais cette couleur.",
        color=discord.Color(int(hex_color, 16)),
        timestamp=datetime.datetime.now(),
    )
    embed.add_field(name="Nouvelle couleur", value=f"`#{hex_color}`", inline=True)
    embed.set_footer(text=f"Changé par {interaction.user}")
    await interaction.response.send_message(embed=embed)


@premium_couleur.error
async def premium_couleur_error(interaction: discord.Interaction, error):
    if isinstance(error, app_commands.MissingPermissions):
        embed = discord.Embed(
            title="⛔ Permission manquante",
            description="Il faut la permission **Gérer le serveur** pour changer la couleur Premium.",
            color=discord.Color.red(),
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
    else:
        await interaction.response.send_message(f"Une erreur est survenue : {error}", ephemeral=True)


@premium_group.command(name="status", description="Vérifie si ce serveur profite du Premium")
async def premium_status(interaction: discord.Interaction):
    if is_premium(interaction.guild.id):
        data = premium_servers[interaction.guild.id]
        embed = discord.Embed(
            title="✨ Serveur Premium",
            description=f"**{interaction.guild.name}** profite du service Premium !",
            color=get_premium_color(interaction.guild.id),
            timestamp=datetime.datetime.now(),
        )
        activateur = interaction.guild.get_member(data["activated_by"])
        embed.add_field(
            name="Activé le",
            value=f"{discord.utils.format_dt(data['activated_at'], style='F')}\n{discord.utils.format_dt(data['activated_at'], style='R')}",
            inline=False,
        )
        embed.add_field(name="Activé par", value=activateur.mention if activateur else "Inconnu", inline=False)
        embed.add_field(name="Couleur actuelle", value=f"`#{data.get('color', 'FFD700')}`", inline=False)
        embed.set_footer(text="Change-la avec /premium couleur")
        if interaction.guild.icon:
            embed.set_thumbnail(url=interaction.guild.icon.url)
    else:
        embed = discord.Embed(
            title="Serveur non-Premium",
            description="Ce serveur n'a pas encore le Premium.\nDemande un code au propriétaire du bot, puis utilise `/premium activer`.",
            color=discord.Color.greyple(),
        )

    await interaction.response.send_message(embed=embed, ephemeral=True)


# --- Commandes Owner ---
# Réservées au SEUL vrai propriétaire du bot : lui seul peut ajouter, retirer ou lister
# les owners. Un owner ajouté ne peut pas gérer d'autres owners, ni générer de Premium.
owner_group = app_commands.Group(name="owner", description="Gestion des owners du bot (propriétaire uniquement)")
bot.tree.add_command(owner_group)


def build_access_denied_embed() -> discord.Embed:
    return discord.Embed(
        title="⛔ Accès refusé",
        description="Seul le propriétaire du bot peut gérer les owners.",
        color=discord.Color.red(),
    )


@owner_group.command(name="add", description="[Propriétaire uniquement] Ajoute un owner du bot")
@app_commands.describe(utilisateur="La personne à promouvoir owner du bot")
async def owner_add(interaction: discord.Interaction, utilisateur: discord.User):
    if not await is_full_owner(interaction.user):
        await interaction.response.send_message(embed=build_access_denied_embed(), ephemeral=True)
        return

    if await bot.is_owner(utilisateur):
        embed = discord.Embed(
            title="✨ Déjà propriétaire",
            description=f"{utilisateur.mention} est déjà le propriétaire du bot.",
            color=discord.Color.greyple(),
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return

    if utilisateur.id in bot_owners_store:
        embed = discord.Embed(
            title="👑 Déjà owner",
            description=f"{utilisateur.mention} est déjà un owner du bot.",
            color=discord.Color.greyple(),
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return

    added_at = datetime.datetime.now()
    bot_owners_store[utilisateur.id] = {"added_by": interaction.user.id, "added_at": added_at.isoformat()}
    db.add_bot_owner(utilisateur.id, interaction.user.id, added_at.isoformat())

    embed = discord.Embed(
        title="👑 Nouvel owner ajouté !",
        description=f"{utilisateur.mention} est désormais **owner du bot**.",
        color=discord.Color.gold(),
        timestamp=datetime.datetime.now(),
    )
    embed.set_thumbnail(url=utilisateur.display_avatar.url)
    embed.add_field(
        name="✅ Peut faire",
        value="Toutes les commandes de modération (`/mute`, `/ban`, `/kick`, `/warn`, ...) sur **n'importe quel serveur** où le bot est présent, sans avoir besoin des permissions Discord habituelles.",
        inline=False,
    )
    embed.add_field(
        name="❌ Ne peut pas faire",
        value="`/premium generer` et `/owner add|remove` restent réservées au propriétaire réel du bot.",
        inline=False,
    )
    embed.set_footer(text=f"Ajouté par {interaction.user}")
    await interaction.response.send_message(embed=embed)

    try:
        dm_embed = discord.Embed(
            title="👑 Tu es maintenant owner du bot !",
            description="Tu peux désormais utiliser toutes les commandes de modération sur n'importe quel serveur où le bot est présent.",
            color=discord.Color.gold(),
        )
        await utilisateur.send(embed=dm_embed)
    except discord.Forbidden:
        pass


@owner_group.command(name="remove", description="[Propriétaire uniquement] Retire un owner du bot")
@app_commands.describe(utilisateur="La personne à retirer des owners du bot")
async def owner_remove(interaction: discord.Interaction, utilisateur: discord.User):
    if not await is_full_owner(interaction.user):
        await interaction.response.send_message(embed=build_access_denied_embed(), ephemeral=True)
        return

    if utilisateur.id not in bot_owners_store:
        embed = discord.Embed(
            title="❌ Pas owner",
            description=f"{utilisateur.mention} n'est pas owner du bot.",
            color=discord.Color.red(),
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return

    del bot_owners_store[utilisateur.id]
    db.remove_bot_owner(utilisateur.id)

    embed = discord.Embed(
        title="🗑️ Owner retiré",
        description=f"{utilisateur.mention} n'est plus owner du bot.",
        color=discord.Color.orange(),
        timestamp=datetime.datetime.now(),
    )
    embed.set_thumbnail(url=utilisateur.display_avatar.url)
    embed.set_footer(text=f"Retiré par {interaction.user}")
    await interaction.response.send_message(embed=embed)


@owner_group.command(name="list", description="[Propriétaire uniquement] Liste tous les owners du bot")
async def owner_list(interaction: discord.Interaction):
    if not await is_full_owner(interaction.user):
        await interaction.response.send_message(embed=build_access_denied_embed(), ephemeral=True)
        return

    embed = discord.Embed(
        title="👑 Owners du bot",
        color=discord.Color.gold(),
        timestamp=datetime.datetime.now(),
    )

    if not bot_owners_store:
        embed.description = "Aucun owner additionnel n'a été ajouté pour le moment."
    else:
        lignes = []
        for user_id, data in bot_owners_store.items():
            ajoute_le = datetime.datetime.fromisoformat(data["added_at"])
            lignes.append(f"<@{user_id}> — ajouté {discord.utils.format_dt(ajoute_le, style='R')}")
        embed.description = "\n".join(lignes)

    embed.set_footer(text=f"{len(bot_owners_store)} owner(s) additionnel(s)")
    await interaction.response.send_message(embed=embed, ephemeral=True)


# Le token est lu depuis une variable d'environnement, jamais écrit ici en dur
token = os.environ.get("DISCORD_TOKEN")

if token is None:
    print("ERREUR : la variable d'environnement DISCORD_TOKEN n'est pas définie.")
else:
    bot.run(token)
