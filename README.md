# SCPRPFRBOT

Ce dépôt contient un exemple minimal de bot Discord en Python.

Prérequis
- Python 3.10+ installé
- Un token de bot Discord (Créez une application sur le portail Discord Developer)

Installation
1. Copier `.env.example` en `.env` et mettre votre token:

```bash
cp .env.example .env
# puis editez .env et remplacez la valeur
```

2. Installer les dépendances:

```bash
python -m pip install -r requirements.txt
```

Exécution

```bash
python bot.py
```

Notes
- Activez l'intent "Message Content Intent" dans le portail Discord Developer si vous souhaitez lire le contenu des messages.
- Le préfixe par défaut est `!`. La commande `!ping` répondra avec la latence.

Variables de configuration importantes
- `DISCORD_TOKEN`: token du bot (obligatoire)
- `GUILD_ID`: (optionnel) ID d'un serveur de test pour synchroniser rapidement les slash commands
- `ADMIN_ROLE_ID`: (optionnel) ID d'un rôle dont les membres peuvent annuler n'importe quelle session
- `SESSIONS_CHANNEL_ID`: (optionnel) ID d'un channel où le bot postera automatiquement les sondages

Que modifier dans le code / .env
- Mettez votre token dans `.env` (`DISCORD_TOKEN`).
- Pour le développement, définissez `GUILD_ID` dans `.env` pour avoir les slash commands disponibles instantanément dans ce guild.
- Si vous voulez que seuls certains rôles puissent annuler des sessions, mettez `ADMIN_ROLE_ID` (ID numérique du rôle) dans `.env`.
- Si vous souhaitez centraliser les sondages dans un channel spécifique, mettez `SESSIONS_CHANNEL_ID` (ID numérique du channel) dans `.env`.

Exemples rapides
- Poster les sondages dans un channel et autoriser un rôle admin:

```
DISCORD_TOKEN=xxx
GUILD_ID=123456789012345678
ADMIN_ROLE_ID=987654321098765432
SESSIONS_CHANNEL_ID=234567890123456789
```

Sécurité
- Ne committez jamais votre `DISCORD_TOKEN`. Utilisez `.env` localement et configurez des variables d'environnement sur votre hébergeur.

Besoin d'aide pour ajouter persistence, cogs ou hébergement ? Dites-moi ce que vous préférez.