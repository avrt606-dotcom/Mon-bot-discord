import discord
from discord import app_commands
from discord.ext import commands
import os
import datetime

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

# Compteur de messages en mémoire : {user_id: nombre_de_messages}
# Attention : Discord ne fournit pas d'historique global des messages via son API,
# donc ce compteur ne comptabilise que les messages envoyés depuis le dernier
# démarrage du bot (il repart à zéro à chaque redémarrage).
message_count_store = {}


@bot.event
async def on_message(message: discord.Message):
    if not message.author.bot:
        message_count_store[message.author.id] = message_count_store.get(message.author.id, 0) + 1
    await bot.process_commands(message)


def build_mod_embed(emoji: str, title: str, color: discord.Color, cible, moderateur: discord.Member = None, raison: str = None, extra_fields: list = None) -> discord.Embed:
    """Embed uniforme utilisé par toutes les commandes de modération (mute, ban, kick, etc.)."""
    embed = discord.Embed(
        title=f"{emoji} {title}",
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

    embed.set_footer(text=f"ID : {cible.id}")
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

    couleur = utilisateur.color if utilisateur.color != discord.Color.default() else discord.Color.blurple()
    embed = discord.Embed(
        title=f"Informations sur {utilisateur.display_name}",
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

    embed.set_footer(text=f"Demandé par {interaction.user}", icon_url=interaction.user.display_avatar.url)

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

    embed = build_mod_embed(
        "🔇", "Membre mute", discord.Color.orange(),
        utilisateur, interaction.user, raison,
        extra_fields=[("Durée", duree_lisible)],
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

    embed = build_mod_embed("🔊", "Membre démute", discord.Color.green(), utilisateur, interaction.user, raison)
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


# Stockage des warns en mémoire : {user_id: [ {moderator, reason, timestamp}, ... ]}
# Attention : ces données sont perdues si le bot redémarre (pas de base de données).
warnings_store = {}


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

    embed = build_mod_embed("🔨", "Membre banni", discord.Color.red(), utilisateur, interaction.user, raison)
    await interaction.response.send_message(embed=embed)


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

    embed = build_mod_embed("♻️", "Membre débanni", discord.Color.green(), user, interaction.user, raison)
    await interaction.response.send_message(embed=embed)


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

    embed = build_mod_embed("👢", "Membre expulsé", discord.Color.orange(), utilisateur, interaction.user, raison)
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="warn", description="Donne un avertissement à un membre")
@app_commands.describe(
    utilisateur="La personne à avertir",
    raison="La raison de l'avertissement",
)
@app_commands.checks.has_permissions(moderate_members=True)
async def warn(interaction: discord.Interaction, utilisateur: discord.Member, raison: str = "Aucune raison fournie"):
    user_warnings = warnings_store.setdefault(utilisateur.id, [])
    user_warnings.append({
        "moderator": interaction.user.mention,
        "reason": raison,
        "timestamp": datetime.datetime.now().strftime("%d/%m/%Y %H:%M"),
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
@app_commands.checks.has_permissions(moderate_members=True)
async def warnings_cmd(interaction: discord.Interaction, utilisateur: discord.Member):
    user_warnings = warnings_store.get(utilisateur.id, [])

    embed = build_warnings_embed(utilisateur, user_warnings)
    view = WarningDeleteView(utilisateur, user_warnings, interaction.user.id) if user_warnings else None

    await interaction.response.send_message(embed=embed, view=view, ephemeral=True)


@bot.tree.command(name="clearwarnings", description="Efface tous les avertissements d'un membre")
@app_commands.describe(utilisateur="La personne dont tu veux effacer les avertissements")
@app_commands.checks.has_permissions(moderate_members=True)
async def clearwarnings(interaction: discord.Interaction, utilisateur: discord.Member):
    warnings_store[utilisateur.id] = []
    embed = build_mod_embed("🧹", "Avertissements effacés", discord.Color.green(), utilisateur, interaction.user)
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

# Le token est lu depuis une variable d'environnement, jamais écrit ici en dur
token = os.environ.get("DISCORD_TOKEN")

if token is None:
    print("ERREUR : la variable d'environnement DISCORD_TOKEN n'est pas définie.")
else:
    bot.run(token)
