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

# Le token est lu depuis une variable d'environnement, jamais écrit ici en dur
token = os.environ.get("DISCORD_TOKEN")

if token is None:
    print("ERREUR : la variable d'environnement DISCORD_TOKEN n'est pas définie.")
else:
    bot.run(token)
