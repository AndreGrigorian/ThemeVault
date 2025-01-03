import os
import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv
from pymongo.mongo_client import MongoClient
from pymongo.server_api import ServerApi
import json
import aiohttp


# mongodb setup
load_dotenv()
URI = os.getenv('MONGO_URI')
# client = MongoClient(URI, server_api=ServerApi('1'))
client = MongoClient(URI)
db = client["discord_themes"]
themes_collection = db["themes"]

try:
    client.admin.command('ping')
    print("Pinged your deployment. You successfully connected to MongoDB!")
except Exception as e:
    print(e)


TOKEN = os.getenv('DISCORD_TOKEN')
intents = discord.Intents.default()
intents.guilds = True
intents.guild_messages = True

# bot = discord.Client(intents=intents)
bot = commands.Bot(command_prefix="!", intents=intents)
MAX_THEME_AMOUNT = 25


@bot.event
async def on_ready():
    print(f"Bot is online as {bot.user}")
    try:
        synced = await bot.tree.sync()  # Force re-sync slash commands
        print(f"Synced {len(synced)} slash commands.")
    except Exception as e:
        print(f"Failed to sync commands: {e}")

    try:
        client = MongoClient(URI, server_api=ServerApi('1'))
        client.admin.command('ping')  # Test connection
        print("MongoDB connection successful!")
    except Exception as e:
        print(f"MongoDB connection failed: {e}")


@bot.tree.command(name="save_theme", description="Save the current server state as a theme.")
async def save_theme(interaction: discord.Interaction, theme_name: str):
    # Acknowledge interaction to handle longer processing times using interaction.followup.sen
    await interaction.response.defer()
    # Save the current server state.
    guild = interaction.guild
    if guild is None:
        await interaction.followup.send("This command can only be used in a server.", ephemeral=True)
        return

    # Check if the theme name already exists
    server_data = themes_collection.find_one({"server_id": guild.id})
    if server_data:
        existing_theme_names = [theme["name"]
                                for theme in server_data.get("themes", [])]
        if len(existing_theme_names) == MAX_THEME_AMOUNT:
            await interaction.followup.send(f"A server is allowed only up to {MAX_THEME_AMOUNT} saved themes. Consider removing unneeded themes using /remove_theme.")
            return
        if theme_name in existing_theme_names:
            await interaction.followup.send(f"A theme with the name '{theme_name}' already exists. Please choose a different name.", ephemeral=True)
            return

    # Gather server data
    server_data = {
        "server_name": guild.name,
        "server_id": guild.id,
        "server_icon": str(guild.icon.url) if guild.icon else None,
        "server_banner": str(guild.banner.url) if guild.banner else None,
        "channels": [
            {
                "id": channel.id,
                "name": channel.name,
                "type": str(channel.type),
                "category": channel.category.name if channel.category else None
            }
            for channel in guild.channels
        ],
        "categories": [
            {
                "name": category.name,
                "position": category.position
            }
            for category in guild.categories
        ]
    }

    # Store theme in MongoDB
    try:
        themes_collection.update_one(
            {"server_id": guild.id},  # Query to find the server
            {
                # Ensure server ID is always present
                "$set": {"server_id": guild.id},
                "$push": {
                    "themes": {
                        "name": theme_name,
                        "data": server_data
                    }
                }
            },
            upsert=True  # Create a new document if no matching document is found
        )

        await interaction.followup.send(f"Theme '{theme_name}' saved successfully!")
    except Exception as e:
        print(f"Failed to save theme: {e}")
        await interaction.followup.send("Failed to save the theme. Please try again later.", ephemeral=True)

    # print(json.dumps(server_data, indent=4))


@bot.tree.command(name="load_theme", description="Load a saved theme for the server.")
async def load_theme(interaction: discord.Interaction, theme_name: str):
    await interaction.response.defer()

    guild = interaction.guild
    if guild is None:
        await interaction.followup.send("This command can only be used in a server.", ephemeral=True)
        return

    # Fetch the theme data from MongoDB by server id
    server_data = themes_collection.find_one({"server_id": guild.id})
    if not server_data:
        await interaction.followup.send("No themes saved for this server.", ephemeral=True)
        return

    # Find the specific theme
    saved_theme = next(
        (theme for theme in server_data.get(
            "themes", []) if theme["name"] == theme_name),
        None
    )
    if not saved_theme:
        await interaction.followup.send(f"Theme '{theme_name}' not found.", ephemeral=True)
        return

    theme_data = saved_theme["data"]

    # Update server name, icon, and badge
    try:
        if theme_data.get("server_name") and guild.name != theme_data["server_name"]:
            await guild.edit(name=theme_data["server_name"])
        if theme_data.get("server_icon"):
            async with aiohttp.ClientSession() as session:
                async with session.get(theme_data["server_icon"]) as resp:
                    if resp.status == 200:
                        icon_bytes = await resp.read()
                        await guild.edit(icon=icon_bytes)
        if theme_data.get("server_banner"):
            async with aiohttp.ClientSession() as session:
                async with session.get(theme_data["server_banner"]) as resp:
                    if resp.status == 200:
                        banner_bytes = await resp.read()
                        await guild.edit(banner=banner_bytes)

    except Exception as e:
        await interaction.followup.send("Failed to update server name, icon, or banner.")
        print(f"Failed to update server name, icon, or banner: {e}")

    # Synchronize categories
    existing_categories = {
        category.name: category for category in guild.categories}
    # Maps saved category names to their created/updated category objects
    category_mapping = {}

    for saved_category in sorted(theme_data.get("categories", []), key=lambda c: c["position"]):
        category_name = saved_category.get("name")
        category_position = saved_category.get("position")

        if category_name in existing_categories:
            category = existing_categories[category_name]
            if category.position != category_position:
                await category.edit(position=category_position)
        else:
            # Create a new category if it doesn't exist
            category = await guild.create_category(name=category_name, position=category_position)

        # Store the mapping of saved category name to the category object
        category_mapping[category_name] = category

    # Synchronize channels
    existing_channels = {channel.id: channel for channel in guild.channels}

    for saved_channel in theme_data.get("channels", []):
        channel_id = saved_channel.get("id")
        channel_name = saved_channel.get("name")
        channel_type = saved_channel.get("type")
        parent_category_name = saved_channel.get("category")

        parent_category = category_mapping.get(parent_category_name)

        if channel_id in existing_channels:
            channel = existing_channels[channel_id]
            if channel.name != channel_name or channel.category != parent_category:
                await channel.edit(name=channel_name, category=parent_category)
        else:
            # Create the channel if it doesn't exist
            if channel_type == "text":
                await guild.create_text_channel(name=channel_name, category=parent_category)
            elif channel_type == "voice":
                await guild.create_voice_channel(name=channel_name, category=parent_category)

    await interaction.followup.send(f"Theme '{theme_name}' loaded successfully!")


@bot.tree.command(name="remove_theme", description="Remove a saved theme from the server.")
async def remove_theme(interaction: discord.Interaction, theme_name: str):
    await interaction.response.defer()

    guild = interaction.guild
    if guild is None:
        await interaction.followup.send("This command can only be used in a server.", ephemeral=True)
        return

    # Fetch the server's saved themes
    server_data = themes_collection.find_one({"server_id": guild.id})
    if not server_data or not server_data.get("themes"):
        await interaction.followup.send("No themes are saved for this server.", ephemeral=True)
        return

    # Check if the theme exists
    existing_theme_names = [theme["name"] for theme in server_data["themes"]]
    if theme_name not in existing_theme_names:
        await interaction.followup.send(f"No theme named '{theme_name}' found.", ephemeral=True)
        return

    # Remove the theme from MongoDB
    try:
        themes_collection.update_one(
            {"server_id": guild.id},
            {"$pull": {"themes": {"name": theme_name}}}
        )
        await interaction.followup.send(f"Theme '{theme_name}' has been removed successfully.")
    except Exception as e:
        print(f"Failed to remove theme: {e}")
        await interaction.followup.send("An error occurred while removing the theme. Please try again later.", ephemeral=True)


@bot.tree.command(name="list_themes", description="List all saved themes for the server.")
async def list_themes(interaction: discord.Interaction):
    # Defer interaction to handle longer processing times
    await interaction.response.defer()

    guild = interaction.guild
    if guild is None:
        await interaction.followup.send("This command can only be used in a server.", ephemeral=True)
        return

    # Fetch the server's saved themes from MongoDB
    server_data = themes_collection.find_one({"server_id": guild.id})
    if not server_data or not server_data.get("themes"):
        await interaction.followup.send("No themes saved for this server.", ephemeral=True)
        return

    # Extract theme names
    theme_names = [theme["name"] for theme in server_data["themes"]]

    # Format the response
    theme_list = "\n".join(f"- {name}" for name in theme_names)
    response_message = f"**Saved Themes for {guild.name}:**\n{theme_list}"

    await interaction.followup.send(response_message)


@bot.tree.command(name="help", description="List all commands")
async def help_command(interaction: discord.Interaction):
    commands = [
        {"name": "/save_theme <theme_name>",
            "description": f"Save the current server state as a theme. Max {MAX_THEME_AMOUNT} themes."},
        {"name": "/load_theme <theme_name>",
            "description": "Load a previously saved theme for the server."},
        {"name": "/list_themes", "description": "List all saved themes for the server."},
        {"name": "/remove_theme <theme_name>",
            "description": "Remove a saved theme from the server."},
        {"name": "/help", "description": "Display a list of available commands and their descriptions."},
    ]

    help_message = "**Available Commands:**\n\n"
    for cmd in commands:
        help_message += f"**{cmd['name']}**\n> {cmd['description']}\n\n"

    await interaction.response.send_message(help_message, ephemeral=True)

bot.run(TOKEN)
