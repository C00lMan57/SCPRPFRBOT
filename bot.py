import os
import logging
from dotenv import load_dotenv
import discord
from discord.ext import commands
from discord import app_commands
from datetime import datetime, timedelta
from typing import Optional

load_dotenv()

logging.basicConfig(level=logging.INFO)

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)

# En mémoire: dernière session par guild -> (channel_id, message_id, creator_id)
last_sessions: dict[int, tuple[int, int, int]] = {}

# En mémoire: warns par user (guild_id, user_id) -> {warns: int, timeouts: int}
user_infractions: dict[tuple[int, int], dict] = {}


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


moderation = app_commands.Group(name="mod", description="Commandes de modération")


@moderation.command(name="warn")
@app_commands.describe(user="Utilisateur à avertir", reason="Raison de l'avertissement")
async def warn(interaction: discord.Interaction, user: discord.User, reason: str = "Pas de raison"):
	"""Avertit un utilisateur. 3 avertissements = timeout 1 jour."""
	await interaction.response.defer()
	
	# Check: seul manage_guild ou ADMIN_ROLE_ID peuvent warn
	admin_role_id = os.getenv("ADMIN_ROLE_ID")
	member = interaction.user
	if not hasattr(member, "roles") and interaction.guild:
		member = await interaction.guild.fetch_member(interaction.user.id)
	
	allowed = False
	if getattr(member, "guild_permissions", None) and member.guild_permissions.manage_guild:
		allowed = True
	elif admin_role_id and interaction.guild:
		try:
			admin_role_id_int = int(admin_role_id)
			if any(r.id == admin_role_id_int for r in getattr(member, "roles", [])):
				allowed = True
		except ValueError:
			pass
	
	if not allowed:
		await interaction.followup.send("Vous n'êtes pas autorisé à donner des avertissements.")
		return
	
	guild_id = interaction.guild.id if interaction.guild else 0
	user_key = (guild_id, user.id)
	
	if user_key not in user_infractions:
		user_infractions[user_key] = {"warns": 0, "timeouts": 0}
	
	user_infractions[user_key]["warns"] += 1
	warns = user_infractions[user_key]["warns"]
	
	embed = discord.Embed(title="⚠️ Avertissement", color=discord.Color.orange())
	embed.add_field(name="Utilisateur", value=f"{user.mention} ({user.id})", inline=False)
	embed.add_field(name="Raison", value=reason, inline=False)
	embed.add_field(name="Avertissements", value=f"{warns}/3", inline=False)
	embed.set_footer(text=f"Donné par {interaction.user.display_name}")
	await interaction.followup.send(embed=embed)
	
	# Si 3 warns: timeout automatique 1 jour
	if warns >= 3:
		try:
			target_member = await interaction.guild.fetch_member(user.id)
			await target_member.timeout(timedelta(days=1), reason="3 avertissements automatiques")
			user_infractions[user_key]["timeouts"] += 1
			timeouts = user_infractions[user_key]["timeouts"]
			
			embed2 = discord.Embed(title="⏱️ Timeout automatique", color=discord.Color.red())
			embed2.add_field(name="Utilisateur", value=f"{user.mention}", inline=False)
			embed2.add_field(name="Durée", value="1 jour", inline=False)
			embed2.add_field(name="Raison", value="3 avertissements atteints", inline=False)
			embed2.add_field(name="Timeouts", value=f"{timeouts}/2", inline=False)
			await interaction.channel.send(embed=embed2)
			
			# Si 2 timeouts: ban automatique
			if timeouts >= 2:
				await interaction.guild.ban(user, reason="2 timeouts automatiques")
				embed3 = discord.Embed(title="🚫 Ban automatique", color=discord.Color.dark_red())
				embed3.add_field(name="Utilisateur", value=f"{user.mention}", inline=False)
				embed3.add_field(name="Raison", value="2 timeouts atteints", inline=False)
				await interaction.channel.send(embed=embed3)
		except Exception as e:
			await interaction.channel.send(f"Erreur lors du timeout: {e}")


@moderation.command(name="ban")
@app_commands.describe(user="Utilisateur à bannir", reason="Raison du ban")
async def ban(interaction: discord.Interaction, user: discord.User, reason: str = "Pas de raison"):
	"""Bannit un utilisateur du serveur."""
	await interaction.response.defer()
	
	# Check: seul manage_guild ou ADMIN_ROLE_ID peuvent ban
	admin_role_id = os.getenv("ADMIN_ROLE_ID")
	member = interaction.user
	if not hasattr(member, "roles") and interaction.guild:
		member = await interaction.guild.fetch_member(interaction.user.id)
	
	allowed = False
	if getattr(member, "guild_permissions", None) and member.guild_permissions.manage_guild:
		allowed = True
	elif admin_role_id and interaction.guild:
		try:
			admin_role_id_int = int(admin_role_id)
			if any(r.id == admin_role_id_int for r in getattr(member, "roles", [])):
				allowed = True
		except ValueError:
			pass
	
	if not allowed:
		await interaction.followup.send("Vous n'êtes pas autorisé à bannir.")
		return
	
	try:
		await interaction.guild.ban(user, reason=reason)
		embed = discord.Embed(title="🚫 Ban", color=discord.Color.dark_red())
		embed.add_field(name="Utilisateur", value=f"{user.mention} ({user.id})", inline=False)
		embed.add_field(name="Raison", value=reason, inline=False)
		embed.set_footer(text=f"Banni par {interaction.user.display_name}")
		await interaction.followup.send(embed=embed)
		
		# Reset des données
		guild_id = interaction.guild.id if interaction.guild else 0
		user_key = (guild_id, user.id)
		if user_key in user_infractions:
			del user_infractions[user_key]
	except Exception as e:
		await interaction.followup.send(f"Erreur lors du ban: {e}")


@moderation.command(name="timeout")
@app_commands.describe(user="Utilisateur à mettre en timeout", duration_hours="Durée en heures", reason="Raison")
async def timeout(interaction: discord.Interaction, user: discord.User, duration_hours: int, reason: str = "Pas de raison"):
	"""Met un utilisateur en timeout pour une durée spécifiée (en heures)."""
	await interaction.response.defer()
	
	# Check: seul manage_guild ou ADMIN_ROLE_ID peuvent timeout
	admin_role_id = os.getenv("ADMIN_ROLE_ID")
	member = interaction.user
	if not hasattr(member, "roles") and interaction.guild:
		member = await interaction.guild.fetch_member(interaction.user.id)
	
	allowed = False
	if getattr(member, "guild_permissions", None) and member.guild_permissions.manage_guild:
		allowed = True
	elif admin_role_id and interaction.guild:
		try:
			admin_role_id_int = int(admin_role_id)
			if any(r.id == admin_role_id_int for r in getattr(member, "roles", [])):
				allowed = True
		except ValueError:
			pass
	
	if not allowed:
		await interaction.followup.send("Vous n'êtes pas autorisé à mettre en timeout.")
		return
	
	if duration_hours <= 0 or duration_hours > 40320:  # max 28 jours Discord
		await interaction.followup.send("Durée invalide. Maximum 28 jours (40320 heures).")
		return
	
	try:
		target_member = await interaction.guild.fetch_member(user.id)
		await target_member.timeout(timedelta(hours=duration_hours), reason=reason)
		
		guild_id = interaction.guild.id if interaction.guild else 0
		user_key = (guild_id, user.id)
		if user_key not in user_infractions:
			user_infractions[user_key] = {"warns": 0, "timeouts": 0}
		user_infractions[user_key]["timeouts"] += 1
		timeouts = user_infractions[user_key]["timeouts"]
		
		embed = discord.Embed(title="⏱️ Timeout", color=discord.Color.red())
		embed.add_field(name="Utilisateur", value=f"{user.mention} ({user.id})", inline=False)
		embed.add_field(name="Durée", value=f"{duration_hours}h", inline=False)
		embed.add_field(name="Raison", value=reason, inline=False)
		embed.add_field(name="Timeouts totaux", value=f"{timeouts}/2", inline=False)
		embed.set_footer(text=f"Timeout par {interaction.user.display_name}")
		await interaction.followup.send(embed=embed)
		
		# Si 2 timeouts: ban automatique
		if timeouts >= 2:
			await interaction.guild.ban(user, reason="2 timeouts automatiques")
			embed2 = discord.Embed(title="🚫 Ban automatique", color=discord.Color.dark_red())
			embed2.add_field(name="Utilisateur", value=f"{user.mention}", inline=False)
			embed2.add_field(name="Raison", value="2 timeouts atteints", inline=False)
			await interaction.channel.send(embed=embed2)
	except Exception as e:
		await interaction.followup.send(f"Erreur lors du timeout: {e}")


@moderation.command(name="warns")
@app_commands.describe(user="Utilisateur à vérifier")
async def warns(interaction: discord.Interaction, user: discord.User):
	"""Affiche les avertissements et timeouts d'un utilisateur."""
	await interaction.response.defer()
	
	guild_id = interaction.guild.id if interaction.guild else 0
	user_key = (guild_id, user.id)
	
	if user_key not in user_infractions:
		embed = discord.Embed(title="📋 Infractions", color=discord.Color.green())
		embed.add_field(name="Utilisateur", value=f"{user.mention}", inline=False)
		embed.add_field(name="Avertissements", value="0/3", inline=False)
		embed.add_field(name="Timeouts", value="0/2", inline=False)
		await interaction.followup.send(embed=embed)
	else:
		data = user_infractions[user_key]
		embed = discord.Embed(title="📋 Infractions", color=discord.Color.orange())
		embed.add_field(name="Utilisateur", value=f"{user.mention}", inline=False)
		embed.add_field(name="Avertissements", value=f"{data['warns']}/3", inline=False)
		embed.add_field(name="Timeouts", value=f"{data['timeouts']}/2", inline=False)
		await interaction.followup.send(embed=embed)


bot.tree.add_command(moderation)


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

