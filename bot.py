import os
import logging
from dotenv import load_dotenv
import discord
from discord.ext import commands
from discord import app_commands
from datetime import datetime
from typing import Optional

load_dotenv()

logging.basicConfig(level=logging.INFO)

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)

# En mémoire: dernière session par guild -> (channel_id, message_id, creator_id)
last_sessions: dict[int, tuple[int, int, int]] = {}


class SessionVoteView(discord.ui.View):
	def __init__(self):
		super().__init__(timeout=None)
		# mapping user_id -> choice ("yes","no","maybe")
		self.votes: dict[int, str] = {}

	async def update_message(self, interaction: discord.Interaction):
		yes = sum(1 for v in self.votes.values() if v == "yes")
		no = sum(1 for v in self.votes.values() if v == "no")
		maybe = sum(1 for v in self.votes.values() if v == "maybe")
		embed = interaction.message.embeds[0] if interaction.message.embeds else None
		if embed:
			new_embed = embed.copy()
			new_embed.set_field_at(0, name="Réponses", value=f"✅ Oui: {yes}\n❌ Non: {no}\n🤔 Peut-être: {maybe}", inline=False)
			try:
				await interaction.message.edit(embed=new_embed, view=self)
			except Exception:
				pass

	@discord.ui.button(label="✅ Oui", style=discord.ButtonStyle.success, custom_id="session_yes")
	async def yes(self, interaction: discord.Interaction, button: discord.ui.Button):
		self.votes[interaction.user.id] = "yes"
		await interaction.response.defer()
		await self.update_message(interaction)

	@discord.ui.button(label="❌ Non", style=discord.ButtonStyle.danger, custom_id="session_no")
	async def no(self, interaction: discord.Interaction, button: discord.ui.Button):
		self.votes[interaction.user.id] = "no"
		await interaction.response.defer()
		await self.update_message(interaction)

	@discord.ui.button(label="🤔 Peut-être", style=discord.ButtonStyle.secondary, custom_id="session_maybe")
	async def maybe(self, interaction: discord.Interaction, button: discord.ui.Button):
		self.votes[interaction.user.id] = "maybe"
		await interaction.response.defer()
		await self.update_message(interaction)


@bot.event
async def on_ready():
	logging.info(f"Connecté en tant que {bot.user} (ID: {bot.user.id})")
	logging.info("Le bot est prêt.")
	# Sync commands optionally to a test guild for faster updates
	guild_id = os.getenv("GUILD_ID")
	try:
		if guild_id:
			await bot.tree.sync(guild=discord.Object(id=int(guild_id)))
			logging.info(f"Slash commands synchronisées pour le guild {guild_id}.")
		else:
			await bot.tree.sync()
			logging.info("Slash commands synchronisées globalement.")
	except Exception as e:
		logging.warning(f"Échec de sync des slash commands: {e}")


session = app_commands.Group(name="session", description="Gérer les sessions/sondages")


@session.command(name="create")
@app_commands.describe(date="Date de la session au format DD/MM/YY ou DD/MM/YYYY")
async def create(interaction: discord.Interaction, date: str):
	"""Crée un sondage de présence pour une date donnée."""
	await interaction.response.defer()
	# parser la date
	parsed: Optional[datetime] = None
	for fmt in ("%d/%m/%y", "%d/%m/%Y"):
		try:
			parsed = datetime.strptime(date, fmt)
			break
		except ValueError:
			continue
	if not parsed:
		await interaction.followup.send("Format de date invalide. Utilisez DD/MM/YY ou DD/MM/YYYY.")
		return

	embed = discord.Embed(title="Nouvelle session", color=discord.Color.blue())
	embed.add_field(name="Date", value=parsed.strftime("%d/%m/%Y"), inline=False)
	embed.add_field(name="Réponses", value="✅ Oui: 0\n❌ Non: 0\n🤔 Peut-être: 0", inline=False)
	embed.set_footer(text=f"Créé par {interaction.user.display_name}")

	view = SessionVoteView()

	# Option: poster le sondage dans un channel configuré via SESSIONS_CHANNEL_ID
	session_channel_id = os.getenv("SESSIONS_CHANNEL_ID")
	if session_channel_id:
		try:
			target_channel = bot.get_channel(int(session_channel_id)) or await bot.fetch_channel(int(session_channel_id))
		except Exception:
			target_channel = None
	else:
		target_channel = None

	if target_channel:
		message = await target_channel.send(embed=embed, view=view)
		# confirmer à l'utilisateur qui a lancé la commande
		await interaction.followup.send(f"Sondage créé dans {target_channel.mention}.", ephemeral=True)
	else:
		message = await interaction.followup.send(embed=embed, view=view)

	# stocker la dernière session pour cette guild (inclut l'ID du créateur)
	guild_id = interaction.guild.id if interaction.guild else 0
	last_sessions[guild_id] = (message.channel.id, message.id, interaction.user.id)


@session.command(name="cancel")
async def cancel(interaction: discord.Interaction):
	"""Annule la dernière session créée dans ce serveur."""
	await interaction.response.defer()
	guild_id = interaction.guild.id if interaction.guild else 0
	if guild_id not in last_sessions:
		await interaction.followup.send("Aucune session trouvée à annuler.")
		return

	channel_id, message_id, creator_id = last_sessions.pop(guild_id)

	# Vérifier les droits: seul le créateur, les membres avec manage_guild,
	# ou les détenteurs du rôle ADMIN_ROLE_ID peuvent annuler
	admin_role_id = os.getenv("ADMIN_ROLE_ID")
	try:
		member = interaction.user
		if not hasattr(member, "roles") and interaction.guild:
			member = await interaction.guild.fetch_member(interaction.user.id)

		allowed = False
		if interaction.user.id == creator_id:
			allowed = True
		elif getattr(member, "guild_permissions", None) and member.guild_permissions.manage_guild:
			allowed = True
		elif admin_role_id and interaction.guild:
			try:
				admin_role_id_int = int(admin_role_id)
				if any(r.id == admin_role_id_int for r in getattr(member, "roles", [])):
					allowed = True
			except ValueError:
				allowed = False

		if not allowed:
			await interaction.followup.send("Vous n'êtes pas autorisé à annuler cette session.")
			return

		channel = bot.get_channel(channel_id) or await bot.fetch_channel(channel_id)
		msg = await channel.fetch_message(message_id)
		# désactiver la view (boutons) en éditant le message
		try:
			await msg.edit(content=f"~~Session du {msg.embeds[0].fields[0].value if msg.embeds else 'date inconnue'} annulée.~~", view=None)
		except Exception:
			pass
		await interaction.followup.send("La dernière session a été annulée.")
	except Exception as e:
		await interaction.followup.send(f"Impossible d'annuler la session: {e}")


bot.tree.add_command(session)


@bot.command(name="ping")
async def ping(ctx: commands.Context):
	"""Répond 'Pong!' et affiche la latence."""
	latency_ms = int(bot.latency * 1000)
	await ctx.reply(f"Pong! Latence: {latency_ms}ms")


if __name__ == "__main__":
	token = os.getenv("DISCORD_TOKEN")
	if not token:
		raise SystemExit("Veuillez définir la variable d'environnement DISCORD_TOKEN.")
	bot.run(token)

