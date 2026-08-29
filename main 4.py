import discord
from discord import app_commands
from discord.ext import commands
import os

# Les "intents" définissent quelles infos le bot peut recevoir de Discord
intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)

@bot.event
async def on_ready():
    print(f"{bot.user} est connecté et en ligne !")
    try:
        synced = await bot.tree.sync()
        print(f"{len(synced)} commande(s) synchronisée(s).")
    except Exception as e:
        print(f"Erreur lors de la synchronisation des commandes : {e}")

@bot.tree.command(name="ping", description="Vérifie que le bot répond bien")
async def ping(interaction: discord.Interaction):
    await interaction.response.send_message("Pong ! Le bot fonctionne 🎉")


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
@app_commands.checks.has_permissions(moderate_members=True)
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

    import datetime
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

    await interaction.response.send_message(
        f"{utilisateur.mention} a été mute par {interaction.user.mention} pour {duree_lisible}. Raison : {raison}"
    )

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
@app_commands.checks.has_permissions(moderate_members=True)
async def demute(interaction: discord.Interaction, utilisateur: discord.Member, raison: str = "Aucune raison fournie"):
    try:
        await utilisateur.timeout(None, reason=raison)
    except discord.Forbidden:
        await interaction.response.send_message(
            "Je n'ai pas la permission de démute cette personne.",
            ephemeral=True,
        )
        return

    await interaction.response.send_message(f"{utilisateur.mention} a été démute !")

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


# Stockage des warns en mémoire : {user_id: [ {moderator, reason, timestamp}, ... ]}
# Attention : ces données sont perdues si le bot redémarre (pas de base de données).
warnings_store = {}


@bot.tree.command(name="ban", description="Bannit un membre du serveur")
@app_commands.describe(
    utilisateur="La personne à bannir",
    raison="La raison du bannissement",
)
@app_commands.checks.has_permissions(ban_members=True)
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

    await interaction.response.send_message(
        f"{utilisateur.mention} a été banni par {interaction.user.mention}. Raison : {raison}"
    )


@bot.tree.command(name="unban", description="Débannit un membre via son ID")
@app_commands.describe(
    user_id="L'ID de la personne à débannir",
    raison="La raison du débannissement",
)
@app_commands.checks.has_permissions(ban_members=True)
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

    await interaction.response.send_message(
        f"{user.mention} a été débanni par {interaction.user.mention}. Raison : {raison}"
    )


@bot.tree.command(name="kick", description="Expulse un membre du serveur")
@app_commands.describe(
    utilisateur="La personne à expulser",
    raison="La raison de l'expulsion",
)
@app_commands.checks.has_permissions(kick_members=True)
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

    await interaction.response.send_message(
        f"{utilisateur.mention} a été expulsé par {interaction.user.mention}. Raison : {raison}"
    )


@bot.tree.command(name="warn", description="Donne un avertissement à un membre")
@app_commands.describe(
    utilisateur="La personne à avertir",
    raison="La raison de l'avertissement",
)
@app_commands.checks.has_permissions(moderate_members=True)
async def warn(interaction: discord.Interaction, utilisateur: discord.Member, raison: str = "Aucune raison fournie"):
    import datetime

    user_warnings = warnings_store.setdefault(utilisateur.id, [])
    user_warnings.append({
        "moderator": interaction.user.mention,
        "reason": raison,
        "timestamp": datetime.datetime.now().strftime("%d/%m/%Y %H:%M"),
    })

    await interaction.response.send_message(
        f"{utilisateur.mention} a reçu un avertissement de {interaction.user.mention}. "
        f"Raison : {raison} (Total : {len(user_warnings)} avertissement(s))"
    )

    try:
        await utilisateur.send(
            f"Tu as reçu un avertissement de {interaction.user.mention} sur {interaction.guild.name}. Raison : {raison}"
        )
    except discord.Forbidden:
        pass


@bot.tree.command(name="warnings", description="Affiche les avertissements d'un membre")
@app_commands.describe(utilisateur="La personne dont tu veux voir les avertissements")
@app_commands.checks.has_permissions(moderate_members=True)
async def warnings_cmd(interaction: discord.Interaction, utilisateur: discord.Member):
    user_warnings = warnings_store.get(utilisateur.id, [])

    if not user_warnings:
        await interaction.response.send_message(
            f"{utilisateur.mention} n'a aucun avertissement.",
            ephemeral=True,
        )
        return

    lines = [f"Avertissements de {utilisateur.mention} ({len(user_warnings)}) :"]
    for i, w in enumerate(user_warnings, start=1):
        lines.append(f"**{i}.** {w['reason']} — par {w['moderator']} le {w['timestamp']}")

    await interaction.response.send_message("\n".join(lines), ephemeral=True)


@bot.tree.command(name="clearwarnings", description="Efface tous les avertissements d'un membre")
@app_commands.describe(utilisateur="La personne dont tu veux effacer les avertissements")
@app_commands.checks.has_permissions(moderate_members=True)
async def clearwarnings(interaction: discord.Interaction, utilisateur: discord.Member):
    warnings_store[utilisateur.id] = []
    await interaction.response.send_message(
        f"Tous les avertissements de {utilisateur.mention} ont été effacés."
    )


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

# Le token est lu depuis une variable d'environnement, jamais écrit ici en dur
token = os.environ.get("DISCORD_TOKEN")

if token is None:
    print("ERREUR : la variable d'environnement DISCORD_TOKEN n'est pas définie.")
else:
    bot.run(token)
