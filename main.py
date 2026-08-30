import discord
from discord import app_commands
from discord.ext import commands, tasks
import os
import random
import datetime

import database as db
from google import genai

# Initialise la base SQLite (crée bot_data.db et ses tables si besoin)
fichier_existait_deja = os.path.exists(db.DB_PATH)
db.init_db()
print(f"[DB] Base de données utilisée : {db.DB_PATH}")
print(f"[DB] Le fichier existait déjà avant ce lancement : {fichier_existait_deja}")

# Les "intents" définissent quelles infos le bot peut recevoir de Discord
intents = discord.Intents.default()
intents.message_content = True
intents.members = True  # nécessaire pour on_member_join / on_member_remove (bienvenue-départ)

bot = commands.Bot(command_prefix="!", intents=intents)

# Client Gemini (gratuit) pour la commande /ia
ia_client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))

@bot.event
async def on_ready():
    print(f"{bot.user} est connecté et en ligne !")
    if not save_message_counts_task.is_running():
        save_message_counts_task.start()
    if not check_giveaways_task.is_running():
        check_giveaways_task.start()

    giveaways_actifs = db.load_active_giveaways()
    for giveaway in giveaways_actifs:
        bot.add_view(GiveawayView(giveaway["id"]))
    print(f"[Giveaway] {len(giveaways_actifs)} giveaway(s) actif(s) rechargé(s), boutons réactivés.")

    try:
        synced = await bot.tree.sync()
        print(f"{len(synced)} commande(s) synchronisée(s).")
    except Exception as e:
        print(f"Erreur lors de la synchronisation des commandes : {e}")

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


bot_owners_store = db.load_bot_owners()
print(f"[DB] {len(bot_owners_store)} owner(s) additionnel(s) chargé(s) depuis la base.")


async def is_added_owner(user_id: int) -> bool:
    return user_id in bot_owners_store


async def is_full_owner(user: discord.abc.User) -> bool:
    return await bot.is_owner(user)


async def is_owner_level(user: discord.abc.User) -> bool:
    if await bot.is_owner(user):
        return True
    return await is_added_owner(user.id)


def has_permissions_or_owner(**perms):
    async def predicate(interaction: discord.Interaction) -> bool:
        if await is_owner_level(interaction.user):
            return True

        resolved = interaction.permissions if interaction.permissions is not None else interaction.user.guild_permissions
        missing = [perm for perm, value in perms.items() if getattr(resolved, perm, None) != value]
        if not missing:
            return True
        raise app_commands.MissingPermissions(missing)

    return app_commands.check(predicate)


premium_servers = db.load_premium_servers()
premium_codes = db.load_premium_codes()
print(f"[DB] {len(premium_servers)} serveur(s) premium chargé(s) depuis la base.")
print(f"[DB] {len(premium_codes)} code(s) premium chargé(s) depuis la base.")


def is_premium(guild_id: int) -> bool:
    return guild_id in premium_servers


def get_premium_color(guild_id: int) -> discord.Color:
    data = premium_servers.get(guild_id)
    hex_value = data["color"] if data else "FFD700"
    try:
        return discord.Color(int(hex_value, 16))
    except (ValueError, TypeError):
        return discord.Color.gold()


def parse_hex_color(texte: str):
    texte = texte.strip().lstrip("#").upper()
    if len(texte) != 6:
        return None
    try:
        int(texte, 16)
    except ValueError:
        return None
    return texte


def generate_premium_code() -> str:
    import string
    return "-".join(
        "".join(random.choices(string.ascii_uppercase + string.digits, k=4))
        for _ in range(3)
    )


def build_mod_embed(emoji: str, title: str, color: discord.Color, cible, moderateur: discord.Member = None, raison: str = None, extra_fields: list = None, guild: discord.Guild = None, owner_badge: bool = False) -> discord.Embed:
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


mod_logs_store = db.load_mod_logs_config()
print(f"[DB] {len(mod_logs_store)} config(s) de logs de modération chargée(s) depuis la base.")


async def send_mod_log(guild: discord.Guild, embed: discord.Embed):
    if guild is None or not is_premium(guild.id):
        return

    channel_id = mod_logs_store.get(guild.id)
    if channel_id is None:
        return

    channel = guild.get_channel(channel_id)
    if channel is None:
        return

    try:
        await channel.send(embed=embed)
    except (discord.Forbidden, discord.HTTPException):
        pass


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


@bot.tree.command(name="ia", description="Pose une question à l'IA et obtiens une réponse")
@app_commands.describe(question="Ta question pour l'IA")
async def ia(interaction: discord.Interaction, question: str):
    await interaction.response.defer()

    try:
        reponse = ia_client.models.generate_content(
            model="gemini-3.7-flash",
            contents=question,
        )
        texte_reponse = reponse.text
    except Exception as e:
        await interaction.followup.send(f"Erreur lors de la génération de la réponse : {e}")
        return

    if len(texte_reponse) > 4000:
        texte_reponse = texte_reponse[:4000] + "…"

    couleur = get_premium_color(interaction.guild.id) if is_premium(interaction.guild.id) else discord.Color.blurple()
    embed = discord.Embed(
        title="🤖 Réponse de l'IA",
        description=texte_reponse,
        color=couleur,
        timestamp=datetime.datetime.now(),
    )
    embed.add_field(name="Question", value=question[:1024], inline=False)
    embed.set_footer(text=f"Demandé par {interaction.user}", icon_url=interaction.user.display_avatar.url)
    await interaction.followup.send(embed=embed)


@bot.tree.command(name="help", description="Affiche la liste de toutes les commandes du bot")
async def help_cmd(interaction: discord.Interaction):
    couleur = get_premium_color(interaction.guild.id) if interaction.guild and is_premium(interaction.guild.id) else discord.Color.blurple()

    embed = discord.Embed(
        title="📖 Commandes du bot",
        description="Voici toutes les commandes disponibles, classées par catégorie.",
        color=couleur,
        timestamp=datetime.datetime.now(),
    )
    if bot.user:
        embed.set_thumbnail(url=bot.user.display_avatar.url)

    embed.add_field(
        name="🛠️ Général",
        value=(
            "`/ping` — Vérifie que le bot répond\n"
            "`/help` — Affiche ce message\n"
            "`/ia` — Pose une question à l'IA\n"
            "`/info` — Infos détaillées sur un membre\n"
            "`/avatar` — Affiche l'avatar d'un membre\n"
            "`/servericon` — Affiche l'icône du serveur\n"
            "`/serverinfo` — Statistiques du serveur\n"
            "`/sondage` — Crée un sondage avec réactions"
        ),
        inline=False,
    )

    embed.add_field(
        name="🔨 Modération — Membres",
        value=(
            "`/mute` — Mute un membre pour une durée donnée\n"
            "`/demute` — Retire le mute d'un membre\n"
            "`/kick` — Expulse un membre\n"
            "`/ban` — Bannit un membre\n"
            "`/unban` — Débannit via un ID\n"
            "`/warn` — Donne un avertissement\n"
            "`/warnings` — Affiche les avertissements d'un membre\n"
            "`/clearwarnings` — Efface les avertissements d'un membre\n"
            "`/pseudo` — Change le pseudo d'un membre\n"
            "`/role add` / `/role remove` — Gère les rôles d'un membre"
        ),
        inline=False,
    )

    embed.add_field(
        name="💬 Modération — Salon",
        value=(
            "`/clear` — Supprime plusieurs messages\n"
            "`/slowmode` — Configure le mode lent\n"
            "`/lock` / `/unlock` — Verrouille/déverrouille le salon"
        ),
        inline=False,
    )

    embed.add_field(
        name="👋 Bienvenue / Départ",
        value=(
            "`/bienvenue` — Configure le message d'arrivée des nouveaux membres\n"
            "`/depart` — Configure le message affiché quand un membre part\n"
            "✨ Premium : personnalise le texte et ajoute une image (sinon message par défaut)"
        ),
        inline=False,
    )

    embed.add_field(
        name="🎉 Giveaways",
        value=(
            "`/giveaway creer` — Lance un giveaway (prix, jeu, durée, image, rôles exclus...)\n"
            "`/giveaway liste` — Affiche les giveaways en cours et terminés\n"
            "`/giveaway terminer` — Termine un giveaway immédiatement\n"
            "`/giveaway reroll` — Retire un nouveau gagnant\n"
            "`/giveaway annuler` — Annule un giveaway sans tirer de gagnant"
        ),
        inline=False,
    )

    embed.add_field(
        name="✨ Premium",
        value=(
            "`/premium status` — Vérifie si le serveur est Premium\n"
            "`/premium activer` — Active le Premium avec un code\n"
            "`/premium couleur` — Personnalise la couleur des embeds\n"
            "`/premium logs` — Configure le salon de logs de modération\n"
            "`/premium sanctions` — Configure les sanctions automatiques"
        ),
        inline=False,
    )

    if await is_owner_level(interaction.user):
        if await is_full_owner(interaction.user):
            embed.add_field(
                name="👑 Owner (propriétaire)",
                value=(
                    "`/premium generer` — Génère un code Premium\n"
                    "`/owner add` — Ajoute un owner du bot\n"
                    "`/owner remove` — Retire un owner du bot\n"
                    "`/owner list` — Liste les owners du bot"
                ),
                inline=False,
            )
        else:
            embed.add_field(
                name="👑 Statut Owner",
                value="Tu es owner du bot : tu peux utiliser toutes les commandes de modération sur n'importe quel serveur où le bot est présent, sans avoir besoin des permissions Discord habituelles.",
                inline=False,
            )

    embed.set_footer(text="Les commandes de modération nécessitent les permissions Discord correspondantes (ou d'être Owner du bot).")
    await interaction.response.send_message(embed=embed, ephemeral=True)


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
    duration_str = duration_str.strip().lower()

    units = {
        "s": 1, "sec": 1, "secs": 1, "second": 1, "seconds": 1,
        "m": 60, "min": 60, "mins": 60, "minute": 60, "minutes": 60,
        "h": 3600, "hr": 3600, "hrs": 3600, "heure": 3600, "heures": 3600, "hour": 3600, "hours": 3600,
        "j": 86400, "d": 86400, "day": 86400, "days": 86400, "jour": 86400, "jours": 86400,
    }

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
    await send_mod_log(interaction.guild, embed)

    try:
        await utilisateur.send(
            f"Tu as été mute par {interaction.user.mention} pour {duree_lisible}. Raison : {raison}"
        )
    except discord.Forbidden:
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
    await send_mod_log(interaction.guild, embed)

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


warnings_store = db.load_warnings()
print(f"[DB] {len(warnings_store)} membre(s) avec des avertissements chargé(s) depuis la base.")

auto_sanctions_store = db.load_auto_sanctions_config()
print(f"[DB] {len(auto_sanctions_store)} config(s) de sanctions automatiques chargée(s) depuis la base.")


def parse_sanction_action(texte: str):
    texte = texte.strip().lower()
    if texte == "kick":
        return ("kick", None)
    if texte == "ban":
        return ("ban", None)
    if texte.startswith("mute:"):
        duree = parse_duration(texte.split(":", 1)[1])
        if duree is None or duree > 28 * 24 * 3600:
            return None
        return ("mute", duree)
    return None


def build_sanction_embed(emoji: str, title: str, color: discord.Color, cible, raison: str, guild: discord.Guild) -> discord.Embed:
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
    embed.add_field(name="Déclenchée par", value="🤖 Sanction automatique", inline=True)
    embed.add_field(name="Raison", value=raison, inline=False)
    footer = f"ID : {cible.id}"
    if premium:
        footer = f"✨ Serveur Premium — {footer}"
    embed.set_footer(text=footer)
    return embed


async def check_auto_sanctions(guild: discord.Guild, utilisateur: discord.Member, total_warnings: int):
    if guild is None or not is_premium(guild.id):
        return

    config = auto_sanctions_store.get(guild.id)
    if not config or total_warnings not in config:
        return

    parsed = parse_sanction_action(config[total_warnings])
    if parsed is None:
        return

    action_type, duree = parsed
    raison = f"Sanction automatique : {total_warnings} avertissement(s) cumulé(s)"

    try:
        if action_type == "mute":
            if utilisateur.is_timed_out():
                return
            await utilisateur.timeout(datetime.timedelta(seconds=duree), reason=raison)
            embed = build_sanction_embed("🔇", "Mute automatique", discord.Color.orange(), utilisateur, raison, guild)
            embed.add_field(name="Durée", value=format_duration(duree), inline=True)
        elif action_type == "kick":
            await utilisateur.kick(reason=raison)
            embed = build_sanction_embed("👢", "Expulsion automatique", discord.Color.orange(), utilisateur, raison, guild)
        elif action_type == "ban":
            await utilisateur.ban(reason=raison)
            embed = build_sanction_embed("🔨", "Bannissement automatique", discord.Color.red(), utilisateur, raison, guild)
        else:
            return
    except discord.Forbidden:
        return

    await send_mod_log(guild, embed)

    try:
        await utilisateur.send(
            f"Une sanction automatique a été appliquée sur {guild.name} suite à tes avertissements : {raison}"
        )
    except discord.Forbidden:
        pass


def build_warn_notification_embed(utilisateur: discord.Member, moderateur: discord.Member, raison: str, total: int) -> discord.Embed:
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
    await send_mod_log(interaction.guild, embed)


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
    await send_mod_log(interaction.guild, embed)


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
    await send_mod_log(interaction.guild, embed)


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
    await send_mod_log(interaction.guild, embed)

    try:
        await utilisateur.send(
            f"Tu as reçu un avertissement de {interaction.user.mention} sur {interaction.guild.name}. Raison : {raison}"
        )
    except discord.Forbidden:
        pass

    await check_auto_sanctions(interaction.guild, utilisateur, len(user_warnings))


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
    await send_mod_log(interaction.guild, embed)


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
    await send_mod_log(interaction.guild, embed)


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
    await send_mod_log(interaction.guild, embed)


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
    await send_mod_log(interaction.guild, embed)


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
    await send_mod_log(interaction.guild, embed)


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
    await send_mod_log(interaction.guild, embed)


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
    await send_mod_log(interaction.guild, embed)


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
    await send_mod_log(interaction.guild, embed)


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
    def __init__(self, guild_id: int, author_id: int):
        super().__init__(timeout=120)
        self.add_item(PremiumColorSelect(guild_id, author_id))


premium_group = app_commands.Group(name="premium", description="Gestion du service Premium du bot")
bot.tree.add_command(premium_group)


@premium_group.command(name="generer", description="[Propriétaire uniquement] Génère un code Premium et l'envoie en MP")
@app_commands.describe(utilisateur="La personne à qui envoyer le code Premium")
async def premium_generer(interaction: discord.Interaction, utilisateur: discord.User):
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
            "• Salon de logs de modération avec `/premium logs`\n"
            "• Sanctions automatiques avec `/premium sanctions`\n"
            "• Texte personnalisé + image sur `/bienvenue` et `/depart`\n"
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


@premium_group.command(name="logs", description="[Premium] Configure le salon de logs de modération")
@app_commands.describe(
    salon="Le salon où poster les logs (laisse vide pour désactiver les logs)",
)
@has_permissions_or_owner(manage_guild=True)
async def premium_logs(interaction: discord.Interaction, salon: discord.TextChannel = None):
    if not is_premium(interaction.guild.id):
        embed = discord.Embed(
            title="✨ Fonctionnalité Premium",
            description="Les logs de modération sont réservés aux serveurs Premium.\nUtilise `/premium activer` avec un code pour débloquer cette option.",
            color=discord.Color.greyple(),
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return

    if salon is None:
        if interaction.guild.id in mod_logs_store:
            del mod_logs_store[interaction.guild.id]
            db.remove_mod_logs_channel(interaction.guild.id)
        embed = discord.Embed(
            title="🔕 Logs de modération désactivés",
            description="Les actions de modération ne seront plus loguées automatiquement.",
            color=discord.Color.greyple(),
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return

    mod_logs_store[interaction.guild.id] = salon.id
    db.set_mod_logs_channel(interaction.guild.id, salon.id)

    embed = discord.Embed(
        title="📋 Logs de modération activés",
        description=f"Toutes les actions de modération (mute, ban, kick, warn, clear, lock, rôle...) seront désormais postées dans {salon.mention}.",
        color=get_premium_color(interaction.guild.id),
        timestamp=datetime.datetime.now(),
    )
    embed.set_footer(text=f"Configuré par {interaction.user}")
    await interaction.response.send_message(embed=embed, ephemeral=True)

    try:
        await salon.send(embed=discord.Embed(
            title="📋 Salon de logs configuré",
            description="Ce salon recevra désormais toutes les actions de modération du bot.",
            color=get_premium_color(interaction.guild.id),
        ))
    except discord.Forbidden:
        pass


@premium_group.command(name="sanctions", description="[Premium] Configure une sanction automatique à un certain nombre d'avertissements")
@app_commands.describe(
    seuil="Nombre d'avertissements cumulés qui déclenche la sanction",
    action="L'action à appliquer : 'kick', 'ban', ou 'mute:DUREE' (ex: mute:1h)",
)
@has_permissions_or_owner(manage_guild=True)
async def premium_sanctions(interaction: discord.Interaction, seuil: app_commands.Range[int, 1, 50], action: str):
    if not is_premium(interaction.guild.id):
        embed = discord.Embed(
            title="✨ Fonctionnalité Premium",
            description="Les sanctions automatiques sont réservées aux serveurs Premium.\nUtilise `/premium activer` avec un code pour débloquer cette option.",
            color=discord.Color.greyple(),
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return

    parsed = parse_sanction_action(action)
    if parsed is None:
        await interaction.response.send_message(
            "Action invalide. Utilise `kick`, `ban`, ou `mute:DUREE` (ex : `mute:10m`, `mute:1h`, `mute:1j`, max 28 jours).",
            ephemeral=True,
        )
        return

    config = auto_sanctions_store.setdefault(interaction.guild.id, {})
    config[seuil] = action.strip().lower()
    db.set_auto_sanctions_config(interaction.guild.id, config)

    lignes = "\n".join(f"• **{s}** avertissement(s) → `{a}`" for s, a in sorted(config.items()))
    embed = discord.Embed(
        title="🤖 Sanctions automatiques mises à jour",
        description=f"Configuration actuelle des sanctions automatiques sur ce serveur :\n\n{lignes}",
        color=get_premium_color(interaction.guild.id),
        timestamp=datetime.datetime.now(),
    )
    embed.set_footer(text=f"Configuré par {interaction.user} • Utilise à nouveau la commande avec le même seuil pour le remplacer")
    await interaction.response.send_message(embed=embed, ephemeral=True)


@premium_group.command(name="sanctions_retirer", description="[Premium] Retire un seuil de sanction automatique")
@app_commands.describe(seuil="Le seuil d'avertissements à retirer")
@has_permissions_or_owner(manage_guild=True)
async def premium_sanctions_retirer(interaction: discord.Interaction, seuil: app_commands.Range[int, 1, 50]):
    config = auto_sanctions_store.get(interaction.guild.id, {})
    if seuil not in config:
        await interaction.response.send_message(f"Aucune sanction automatique n'est configurée pour le seuil {seuil}.", ephemeral=True)
        return

    del config[seuil]
    if config:
        db.set_auto_sanctions_config(interaction.guild.id, config)
    else:
        db.remove_auto_sanctions_config(interaction.guild.id)

    await interaction.response.send_message(f"✅ Sanction automatique retirée pour le seuil {seuil}.", ephemeral=True)


DEFAULT_WELCOME_MESSAGE = "Bienvenue {membre} sur **{serveur}** ! Nous sommes maintenant {nombre_membres} membres 🎉"
DEFAULT_LEAVE_MESSAGE = "**{membre}** a quitté **{serveur}**. Nous sommes maintenant {nombre_membres} membres."


@bot.tree.command(name="bienvenue", description="Configure le message de bienvenue des nouveaux membres")
@app_commands.describe(
    salon="Le salon où poster le message de bienvenue (laisse vide pour désactiver)",
    message="[Premium] Personnalise le texte. Variables : {membre}, {serveur}, {nombre_membres}",
    image="[Premium] URL d'une image à afficher dans le message de bienvenue",
)
@has_permissions_or_owner(manage_guild=True)
async def bienvenue(interaction: discord.Interaction, salon: discord.TextChannel = None, message: str = None, image: str = None):
    premium = is_premium(interaction.guild.id)

    if salon is None:
        welcome_store[interaction.guild.id] = {
            **welcome_store.get(interaction.guild.id, {}),
            "welcome_channel_id": None,
            "welcome_message": None,
            "welcome_image_url": None,
        }
        db.set_welcome_config(interaction.guild.id, None, None, None)
        await interaction.response.send_message("🔕 Message de bienvenue désactivé.", ephemeral=True)
        return

    avertissement = None
    if (message is not None or image is not None) and not premium:
        avertissement = (
            "✨ La personnalisation du message et l'ajout d'une image sont réservés aux serveurs Premium. "
            "Le message par défaut a été utilisé à la place. Utilise `/premium activer` pour débloquer ces options."
        )
        message = None
        image = None

    final_message = message or DEFAULT_WELCOME_MESSAGE
    final_image = image if premium else None

    welcome_store[interaction.guild.id] = {
        **welcome_store.get(interaction.guild.id, {}),
        "welcome_channel_id": salon.id,
        "welcome_message": final_message,
        "welcome_image_url": final_image,
    }
    db.set_welcome_config(interaction.guild.id, salon.id, final_message, final_image)

    apercu = final_message.format(membre=interaction.user.mention, serveur=interaction.guild.name, nombre_membres=interaction.guild.member_count)
    embed = discord.Embed(
        title="👋 Message de bienvenue configuré" + (" ✨" if premium else ""),
        description=f"Les nouveaux membres seront accueillis dans {salon.mention} avec ce message :\n\n{apercu}",
        color=get_premium_color(interaction.guild.id) if premium else discord.Color.blurple(),
        timestamp=datetime.datetime.now(),
    )
    if final_image:
        embed.set_image(url=final_image)
    if not premium:
        embed.add_field(
            name="✨ Envie de plus ?",
            value="Passe Premium pour personnaliser le texte du message et ajouter une image avec `/bienvenue message:... image:...`.",
            inline=False,
        )
    if avertissement:
        embed.add_field(name="⚠️ Info", value=avertissement, inline=False)
    embed.set_footer(text="Variables disponibles : {membre}, {serveur}, {nombre_membres}")
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="depart", description="Configure le message affiché quand un membre quitte le serveur")
@app_commands.describe(
    salon="Le salon où poster le message de départ (laisse vide pour désactiver)",
    message="[Premium] Personnalise le texte. Variables : {membre}, {serveur}, {nombre_membres}",
    image="[Premium] URL d'une image à afficher dans le message de départ",
)
@has_permissions_or_owner(manage_guild=True)
async def depart(interaction: discord.Interaction, salon: discord.TextChannel = None, message: str = None, image: str = None):
    premium = is_premium(interaction.guild.id)

    if salon is None:
        welcome_store[interaction.guild.id] = {
            **welcome_store.get(interaction.guild.id, {}),
            "leave_channel_id": None,
            "leave_message": None,
            "leave_image_url": None,
        }
        db.set_leave_config(interaction.guild.id, None, None, None)
        await interaction.response.send_message("🔕 Message de départ désactivé.", ephemeral=True)
        return

    avertissement = None
    if (message is not None or image is not None) and not premium:
        avertissement = (
            "✨ La personnalisation du message et l'ajout d'une image sont réservés aux serveurs Premium. "
            "Le message par défaut a été utilisé à la place. Utilise `/premium activer` pour débloquer ces options."
        )
        message = None
        image = None

    final_message = message or DEFAULT_LEAVE_MESSAGE
    final_image = image if premium else None

    welcome_store[interaction.guild.id] = {
        **welcome_store.get(interaction.guild.id, {}),
        "leave_channel_id": salon.id,
        "leave_message": final_message,
        "leave_image_url": final_image,
    }
    db.set_leave_config(interaction.guild.id, salon.id, final_message, final_image)

    apercu = final_message.format(membre=str(interaction.user), serveur=interaction.guild.name, nombre_membres=interaction.guild.member_count)
    embed = discord.Embed(
        title="👋 Message de départ configuré" + (" ✨" if premium else ""),
        description=f"Les départs seront annoncés dans {salon.mention} avec ce message :\n\n{apercu}",
        color=get_premium_color(interaction.guild.id) if premium else discord.Color.blurple(),
        timestamp=datetime.datetime.now(),
    )
    if final_image:
        embed.set_image(url=final_image)
    if not premium:
        embed.add_field(
            name="✨ Envie de plus ?",
            value="Passe Premium pour personnaliser le texte du message et ajouter une image avec `/depart message:... image:...`.",
            inline=False,
        )
    if avertissement:
        embed.add_field(name="⚠️ Info", value=avertissement, inline=False)
    embed.set_footer(text="Variables disponibles : {membre}, {serveur}, {nombre_membres}")
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bienvenue.error
@depart.error
async def welcome_config_error(interaction: discord.Interaction, error):
    if isinstance(error, app_commands.MissingPermissions):
        embed = discord.Embed(
            title="⛔ Permission manquante",
            description="Il faut la permission **Gérer le serveur** pour configurer les messages de bienvenue/départ.",
            color=discord.Color.red(),
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
    else:
        await interaction.response.send_message(f"Une erreur est survenue : {error}", ephemeral=True)


@premium_logs.error
@premium_sanctions.error
@premium_sanctions_retirer.error
async def premium_config_error(interaction: discord.Interaction, error):
    if isinstance(error, app_commands.MissingPermissions):
        embed = discord.Embed(
            title="⛔ Permission manquante",
            description="Il faut la permission **Gérer le serveur** pour configurer les fonctionnalités Premium.",
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

        logs_channel_id = mod_logs_store.get(interaction.guild.id)
        logs_channel = interaction.guild.get_channel(logs_channel_id) if logs_channel_id else None
        embed.add_field(name="Logs de modération", value=logs_channel.mention if logs_channel else "Non configurés", inline=True)

        sanctions_config = auto_sanctions_store.get(interaction.guild.id, {})
        embed.add_field(name="Sanctions automatiques", value=f"{len(sanctions_config)} configurée(s)" if sanctions_config else "Aucune", inline=True)

        w_config = welcome_store.get(interaction.guild.id, {})
        bienvenue_active = bool(w_config.get("welcome_channel_id"))
        depart_active = bool(w_config.get("leave_channel_id"))
        embed.add_field(
            name="Bienvenue / Départ",
            value=f"{'✅' if bienvenue_active else '❌'} Bienvenue — {'✅' if depart_active else '❌'} Départ\n(images et texte personnalisé débloqués)",
            inline=True,
        )

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


welcome_store = db.load_welcome_config()
print(f"[DB] {len(welcome_store)} config(s) de bienvenue/départ chargée(s) depuis la base.")


@bot.event
async def on_member_join(member: discord.Member):
    config = welcome_store.get(member.guild.id)
    if not config or not config.get("welcome_channel_id") or not config.get("welcome_message"):
        return

    channel = member.guild.get_channel(config["welcome_channel_id"])
    if channel is None:
        return

    try:
        texte = config["welcome_message"].format(
            membre=member.mention,
            serveur=member.guild.name,
            nombre_membres=member.guild.member_count,
        )
    except (KeyError, IndexError):
        texte = config["welcome_message"]

    premium = is_premium(member.guild.id)
    embed = discord.Embed(
        title="👋 Nouveau membre !" + (" ✨" if premium else ""),
        description=texte,
        color=get_premium_color(member.guild.id) if premium else discord.Color.blurple(),
        timestamp=datetime.datetime.now(),
    )
    embed.set_thumbnail(url=member.display_avatar.url)

    image_url = config.get("welcome_image_url")
    if premium and image_url:
        embed.set_image(url=image_url)

    footer = f"{member.guild.member_count} membre(s)"
    if premium:
        footer = f"✨ Serveur Premium — {footer}"
    embed.set_footer(text=footer)

    try:
        await channel.send(embed=embed)
    except (discord.Forbidden, discord.HTTPException):
        pass


@bot.event
async def on_member_remove(member: discord.Member):
    config = welcome_store.get(member.guild.id)
    if not config or not config.get("leave_channel_id") or not config.get("leave_message"):
        return

    channel = member.guild.get_channel(config["leave_channel_id"])
    if channel is None:
        return

    try:
        texte = config["leave_message"].format(
            membre=str(member),
            serveur=member.guild.name,
            nombre_membres=member.guild.member_count,
        )
    except (KeyError, IndexError):
        texte = config["leave_message"]

    premium = is_premium(member.guild.id)
    embed = discord.Embed(
        title="👋 Départ d'un membre" + (" ✨" if premium else ""),
        description=texte,
        color=discord.Color.greyple(),
        timestamp=datetime.datetime.now(),
    )
    embed.set_thumbnail(url=member.display_avatar.url)

    image_url = config.get("leave_image_url")
    if premium and image_url:
        embed.set_image(url=image_url)

    footer = f"{member.guild.member_count} membre(s)"
    if premium:
        footer = f"✨ Serveur Premium — {footer}"
    embed.set_footer(text=footer)

    try:
        await channel.send(embed=embed)
    except (discord.Forbidden, discord.HTTPException):
        pass


def build_giveaway_embed(guild: discord.Guild, giveaway: dict, ended: bool = False, winners: list = None) -> discord.Embed:
    premium = is_premium(guild.id)

    if ended:
        couleur = get_premium_color(guild.id) if (premium and winners) else (
            discord.Color.gold() if winners else discord.Color.dark_grey()
        )
        titre = "🎉 GIVEAWAY TERMINÉ 🎉"
    else:
        couleur = get_premium_color(guild.id) if premium else discord.Color.fuchsia()
        titre = "🎉 GIVEAWAY EN COURS 🎉"

    embed = discord.Embed(title=titre, color=couleur, timestamp=datetime.datetime.now())

    host = guild.get_member(giveaway["host_id"])
    host_mention = host.mention if host else f"<@{giveaway['host_id']}>"

    lignes = [f"### 🏆 {giveaway['prize']}"]
    if giveaway.get("game"):
        lignes.append(f"🎮 **Jeu concerné :** {giveaway['game']}")
    lignes.append(f"🎙️ **Organisé par :** {host_mention}")

    if ended:
        if winners:
            mentions = ", ".join(f"<@{w}>" for w in winners)
            emoji_gagnant = "🏅" if len(winners) == 1 else "🏅🏅"
            lignes.append(f"\n{emoji_gagnant} **Gagnant(s) :** {mentions}")
        else:
            lignes.append("\n😢 **Personne n'a participé, aucun gagnant désigné.**")
    else:
        lignes.append(f"⏳ **Fin :** {discord.utils.format_dt(giveaway['end_time'], style='F')} ({discord.utils.format_dt(giveaway['end_time'], style='R')})")
        lignes.append(f"🎁 **Nombre de gagnants :** {giveaway['winners_count']}")

        if giveaway["excluded_roles"]:
            roles_txt = ", ".join(f"<@&{rid}>" for rid in giveaway["excluded_roles"])
            lignes.append(f"🚫 **Rôles exclus :** {roles_txt}")
        if giveaway["required_role"]:
            lignes.append(f"🔑 **Rôle requis :** <@&{giveaway['required_role']}>")

        nb_participants = db.count_giveaway_entries(giveaway["id"])
        lignes.append(f"\n👥 **Participants actuels :** {nb_participants}")
        lignes.append("Clique sur **🎉 Participer** ci-dessous pour tenter ta chance !")

    embed.description = "\n".join(lignes)

    if giveaway.get("image_url"):
        embed.set_image(url=giveaway["image_url"])

    footer = f"Giveaway #{giveaway['id']}"
    if premium:
        footer = f"✨ Serveur Premium — {footer}"
    embed.set_footer(text=footer)

    return embed


async def refresh_giveaway_message(guild: discord.Guild, giveaway: dict):
    if guild is None or giveaway.get("message_id") is None:
        return
    channel = guild.get_channel(giveaway["channel_id"])
    if channel is None:
        return
    try:
        message = await channel.fetch_message(giveaway["message_id"])
        await message.edit(embed=build_giveaway_embed(guild, giveaway))
    except (discord.NotFound, discord.Forbidden, discord.HTTPException):
        pass


async def end_giveaway(guild: discord.Guild, giveaway: dict, reroll: bool = False) -> list:
    entries = db.get_giveaway_entries(giveaway["id"])
    nb_winners = min(giveaway["winners_count"], len(entries))
    winners = random.sample(entries, nb_winners) if nb_winners > 0 else []

    db.mark_giveaway_ended(giveaway["id"], winners)
    giveaway["ended"] = True
    giveaway["winners"] = winners

    channel = guild.get_channel(giveaway["channel_id"])
    embed = build_giveaway_embed(guild, giveaway, ended=True, winners=winners)

    if channel is not None and giveaway.get("message_id"):
        try:
            message = await channel.fetch_message(giveaway["message_id"])
            await message.edit(embed=embed, view=None)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            pass

    if channel is not None:
        if winners:
            mentions = ", ".join(f"<@{w}>" for w in winners)
            verbe = "Félicitations" if not reroll else "Nouveau tirage : félicitations"
            annonce = f"🎉 {verbe} {mentions} ! Tu remportes **{giveaway['prize']}** !"
        else:
            annonce = f"😢 Aucun gagnant pour le giveaway **{giveaway['prize']}** (personne n'a participé)."
        try:
            await channel.send(annonce)
        except (discord.Forbidden, discord.HTTPException):
            pass

    return winners


class GiveawayView(discord.ui.View):
    def __init__(self, giveaway_id: int):
        super().__init__(timeout=None)
        self.giveaway_id = giveaway_id
        self.enter_button.custom_id = f"giveaway_enter:{giveaway_id}"

    @discord.ui.button(label="Participer", emoji="🎉", style=discord.ButtonStyle.green)
    async def enter_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        giveaway = db.get_giveaway(self.giveaway_id)
        if giveaway is None or giveaway["ended"]:
            await interaction.response.send_message("Ce giveaway est terminé ou n'existe plus.", ephemeral=True)
            return

        member = interaction.user
        member_role_ids = {r.id for r in member.roles} if isinstance(member, discord.Member) else set()

        roles_bloquants = member_role_ids & set(giveaway["excluded_roles"])
        if roles_bloquants:
            roles_txt = ", ".join(f"<@&{rid}>" for rid in roles_bloquants)
            await interaction.response.send_message(
                f"⛔ Tu ne peux pas participer à ce giveaway à cause de ton rôle : {roles_txt}",
                ephemeral=True,
            )
            return

        if giveaway["required_role"] and giveaway["required_role"] not in member_role_ids:
            await interaction.response.send_message(
                f"🔑 Il faut avoir le rôle <@&{giveaway['required_role']}> pour participer à ce giveaway.",
                ephemeral=True,
            )
            return

        if db.is_giveaway_participant(self.giveaway_id, member.id):
            db.remove_giveaway_entry(self.giveaway_id, member.id)
            await interaction.response.send_message("❌ Tu as retiré ta participation au giveaway.", ephemeral=True)
        else:
            db.add_giveaway_entry(self.giveaway_id, member.id)
            await interaction.response.send_message("🎉 Participation enregistrée, bonne chance !", ephemeral=True)

        await refresh_giveaway_message(interaction.guild, giveaway)


@tasks.loop(seconds=20)
async def check_giveaways_task():
    now = datetime.datetime.now()
    for giveaway in db.load_active_giveaways():
        if giveaway["end_time"] <= now:
            guild = bot.get_guild(giveaway["guild_id"])
            if guild is None:
                db.mark_giveaway_ended(giveaway["id"], [])
                continue
            await end_giveaway(guild, giveaway)


def _build_giveaway_choices(guild_id: int, current: str, ended: bool = None):
    giveaways = db.load_guild_giveaways(guild_id)
    choices = []
    for g in giveaways:
        if ended is not None and g["ended"] != ended:
            continue
        label = f"#{g['id']} — {g['prize']}"
        if current.lower() in label.lower():
            choices.append(app_commands.Choice(name=label[:100], value=g["id"]))
    return choices[:25]


async def active_giveaway_autocomplete(interaction: discord.Interaction, current: str):
    return _build_giveaway_choices(interaction.guild.id, current, ended=False)


async def ended_giveaway_autocomplete(interaction: discord.Interaction, current: str):
    return _build_giveaway_choices(interaction.guild.id, current, ended=True)


giveaway_group = app_commands.Group(name="giveaway", description="Gestion des giveaways")
bot.tree.add_command(giveaway_group)


@giveaway_group.command(name="creer", description="Crée un nouveau giveaway")
@app_commands.describe(
    prix="Le prix à faire gagner",
    duree="Durée du giveaway, ex: 10m, 1h, 1j",
    jeu="Le jeu concerné par le giveaway (optionnel)",
    gagnants="Nombre de gagnants (défaut : 1)",
    salon="Le salon où poster le giveaway (défaut : ce salon)",
    host="Qui héberge le giveaway (défaut : toi)",
    image="URL de l'image du prix (optionnel)",
    role_requis="Rôle obligatoire pour participer (optionnel)",
    role_exclu_1="Un rôle qui ne peut pas participer (optionnel)",
    role_exclu_2="Un autre rôle qui ne peut pas participer (optionnel)",
    role_exclu_3="Un autre rôle qui ne peut pas participer (optionnel)",
)
@has_permissions_or_owner(manage_guild=True)
async def giveaway_creer(
    interaction: discord.Interaction,
    prix: str,
    duree: str,
    jeu: str = None,
    gagnants: app_commands.Range[int, 1, 20] = 1,
    salon: discord.TextChannel = None,
    host: discord.Member = None,
    image: str = None,
    role_requis: discord.Role = None,
    role_exclu_1: discord.Role = None,
    role_exclu_2: discord.Role = None,
    role_exclu_3: discord.Role = None,
):
    seconds = parse_duration(duree)
    if seconds is None:
        await interaction.response.send_message(
            "Format de durée invalide. Exemples valides : 10m, 1h, 1j",
            ephemeral=True,
        )
        return
    if seconds < 10:
        await interaction.response.send_message("La durée minimum est de 10 secondes.", ephemeral=True)
        return

    if image is not None and not (image.startswith("http://") or image.startswith("https://")):
        await interaction.response.send_message("L'URL de l'image doit commencer par http:// ou https://", ephemeral=True)
        return

    salon = salon or interaction.channel
    host = host or interaction.user
    excluded_roles = [r.id for r in (role_exclu_1, role_exclu_2, role_exclu_3) if r is not None]

    end_time = datetime.datetime.now() + datetime.timedelta(seconds=seconds)

    giveaway_id = db.create_giveaway(
        guild_id=interaction.guild.id,
        channel_id=salon.id,
        prize=prix,
        game=jeu,
        image_url=image,
        host_id=host.id,
        winners_count=gagnants,
        excluded_roles=excluded_roles,
        required_role=role_requis.id if role_requis else None,
        end_time=end_time.isoformat(),
        created_at=datetime.datetime.now().isoformat(),
    )

    giveaway = db.get_giveaway(giveaway_id)
    embed = build_giveaway_embed(interaction.guild, giveaway)
    view = GiveawayView(giveaway_id)

    try:
        message = await salon.send(embed=embed, view=view)
    except discord.Forbidden:
        await interaction.response.send_message(
            f"Je n'ai pas la permission d'envoyer de message dans {salon.mention}.",
            ephemeral=True,
        )
        return

    db.set_giveaway_message(giveaway_id, message.id)

    confirm_embed = discord.Embed(
        title="✅ Giveaway lancé !",
        description=f"Le giveaway **#{giveaway_id}** pour **{prix}** a été lancé dans {salon.mention}.",
        color=discord.Color.green(),
    )
    await interaction.response.send_message(embed=confirm_embed, ephemeral=True)


@giveaway_group.command(name="terminer", description="Termine un giveaway immédiatement et tire les gagnants")
@app_commands.describe(giveaway="Le giveaway à terminer")
@app_commands.rename(giveaway="giveaway")
@app_commands.autocomplete(giveaway=active_giveaway_autocomplete)
@has_permissions_or_owner(manage_guild=True)
async def giveaway_terminer(interaction: discord.Interaction, giveaway: int):
    data = db.get_giveaway(giveaway)
    if data is None or data["guild_id"] != interaction.guild.id or data["ended"]:
        await interaction.response.send_message("Giveaway introuvable ou déjà terminé sur ce serveur.", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)
    winners = await end_giveaway(interaction.guild, data)
    if winners:
        await interaction.followup.send(f"✅ Giveaway terminé, {len(winners)} gagnant(s) tiré(s) au sort.", ephemeral=True)
    else:
        await interaction.followup.send("✅ Giveaway terminé, mais personne n'y participait.", ephemeral=True)


@giveaway_group.command(name="reroll", description="Retire de nouveaux gagnants pour un giveaway déjà terminé")
@app_commands.describe(giveaway="Le giveaway concerné")
@app_commands.rename(giveaway="giveaway")
@app_commands.autocomplete(giveaway=ended_giveaway_autocomplete)
@has_permissions_or_owner(manage_guild=True)
async def giveaway_reroll(interaction: discord.Interaction, giveaway: int):
    data = db.get_giveaway(giveaway)
    if data is None or data["guild_id"] != interaction.guild.id or not data["ended"]:
        await interaction.response.send_message("Giveaway introuvable ou pas encore terminé.", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)
    winners = await end_giveaway(interaction.guild, data, reroll=True)
    if winners:
        mentions = ", ".join(f"<@{w}>" for w in winners)
        await interaction.followup.send(f"🔁 Nouveau(x) gagnant(s) tiré(s) : {mentions}", ephemeral=True)
    else:
        await interaction.followup.send("Aucun participant disponible pour un reroll.", ephemeral=True)


@giveaway_group.command(name="annuler", description="Annule un giveaway en cours sans tirer de gagnant")
@app_commands.describe(giveaway="Le giveaway à annuler")
@app_commands.rename(giveaway="giveaway")
@app_commands.autocomplete(giveaway=active_giveaway_autocomplete)
@has_permissions_or_owner(manage_guild=True)
async def giveaway_annuler(interaction: discord.Interaction, giveaway: int):
    data = db.get_giveaway(giveaway)
    if data is None or data["guild_id"] != interaction.guild.id or data["ended"]:
        await interaction.response.send_message("Giveaway introuvable ou déjà terminé.", ephemeral=True)
        return

    db.mark_giveaway_ended(giveaway, [])

    channel = interaction.guild.get_channel(data["channel_id"])
    if channel is not None and data.get("message_id"):
        try:
            message = await channel.fetch_message(data["message_id"])
            embed = discord.Embed(
                title="🚫 Giveaway annulé",
                description=f"Le giveaway pour **{data['prize']}** a été annulé par un modérateur.",
                color=discord.Color.red(),
                timestamp=datetime.datetime.now(),
            )
            embed.set_footer(text=f"Annulé par {interaction.user}")
            await message.edit(embed=embed, view=None)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            pass

    await interaction.response.send_message("✅ Giveaway annulé.", ephemeral=True)


@giveaway_group.command(name="liste", description="Affiche les giveaways du serveur")
async def giveaway_liste(interaction: discord.Interaction):
    giveaways = db.load_guild_giveaways(interaction.guild.id)
    couleur = get_premium_color(interaction.guild.id) if is_premium(interaction.guild.id) else discord.Color.fuchsia()

    embed = discord.Embed(title="🎉 Giveaways du serveur", color=couleur, timestamp=datetime.datetime.now())

    if not giveaways:
        embed.description = "Aucun giveaway n'a jamais été créé sur ce serveur."
    else:
        actifs = [g for g in giveaways if not g["ended"]]
        termines = [g for g in giveaways if g["ended"]][:5]

        if actifs:
            lignes = []
            for g in actifs:
                lignes.append(
                    f"**#{g['id']} — {g['prize']}**\n"
                    f"⏳ Fin {discord.utils.format_dt(g['end_time'], style='R')} • "
                    f"👥 {db.count_giveaway_entries(g['id'])} participant(s)"
                )
            embed.add_field(name="🟢 En cours", value="\n\n".join(lignes), inline=False)
        else:
            embed.add_field(name="🟢 En cours", value="Aucun giveaway en cours.", inline=False)

        if termines:
            lignes = []
            for g in termines:
                gagnants_txt = ", ".join(f"<@{w}>" for w in g["winners"]) if g["winners"] else "Aucun participant"
                lignes.append(f"**#{g['id']} — {g['prize']}**\n🏅 {gagnants_txt}")
            embed.add_field(name="⚪ Terminés récemment", value="\n\n".join(lignes), inline=False)

    embed.set_footer(text=f"Demandé par {interaction.user}")
    await interaction.response.send_message(embed=embed, ephemeral=True)


@giveaway_creer.error
@giveaway_terminer.error
@giveaway_reroll.error
@giveaway_annuler.error
async def giveaway_error(interaction: discord.Interaction, error):
    if isinstance(error, app_commands.MissingPermissions):
        embed = discord.Embed(
            title="⛔ Permission manquante",
            description="Il faut la permission **Gérer le serveur** pour gérer les giveaways.",
            color=discord.Color.red(),
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
    else:
        await interaction.response.send_message(f"Une erreur est survenue : {error}", ephemeral=True)


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


token = os.environ.get("DISCORD_TOKEN")

if token is None:
    print("ERREUR : la variable d'environnement DISCORD_TOKEN n'est pas définie.")
else:
    bot.run(token)
