import os
import asyncio
import random
from datetime import datetime, timedelta, timezone
import discord
from discord.ext import commands, tasks
from aiohttp import web
from motor.motor_asyncio import AsyncIOMotorClient

# --- Dummy Web Server for Render ---
async def handle(request):
    return web.Response(text="Giveaway Bot is active and running!")

app = web.Application()
app.router.add_get("/", handle)

async def start_web_server():
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", 10000))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    print(f"Dummy web server started on port {port}")

# --- MongoDB Setup ---
MONGO_URI = os.getenv("MONGO_URI")
db_client = AsyncIOMotorClient(MONGO_URI)
db = db_client["giveaway_database"]
giveaways_collection = db["giveaways"]

# --- Discord Bot Setup ---
intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True
bot = commands.Bot(command_prefix="!", intents=intents)

# --- Giveaway View (Buttons) ---
class GiveawayView(discord.ui.View):
    def __init__(self, message_id: int):
        super().__init__(timeout=None)
        self.message_id = message_id

    @discord.ui.button(label="🎉 Join Giveaway", style=discord.ButtonStyle.primary, custom_id="join_giveaway_button")
    async def join_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        giveaway = await giveaways_collection.find_one({"message_id": self.message_id})
        
        if not giveaway or not giveaway.get("active", False):
            await interaction.response.send_message("This giveaway has already ended or does not exist.", ephemeral=True)
            return
            
        user_id = interaction.user.id
        if user_id in giveaway.get("participants", []):
            await interaction.response.send_message("You are already participating in this giveaway! Good luck! 🍀", ephemeral=True)
            return

        # Add user to database
        await giveaways_collection.update_one(
            {"message_id": self.message_id}, 
            {"$push": {"participants": user_id}}
        )
        await interaction.response.send_message("You have successfully entered the giveaway! 🎉", ephemeral=True)

# --- Bot Ready Event ---
@bot.event
async def on_ready():
    print(f"Logged in as {bot.user} (ID: {bot.user.id})")
    bot.add_view(GiveawayView(0))
    
    # Sync is completely removed from on_ready to prevent 429 bans on public bots.
    # Use the !sync command below manually in Discord to update slash commands.
    
    if not check_giveaways.is_running():
        check_giveaways.start()

# --- Manual Sync Command for Public Bot (Owner Only) ---
@bot.command()
@commands.is_owner()
async def sync(ctx):
    """Syncs slash commands globally. Run this only when you add/change commands."""
    try:
        synced = await bot.tree.sync()
        await ctx.send(f"Success! Synced {len(synced)} slash commands globally.")
    except Exception as e:
        await ctx.send(f"Failed to sync commands: {e}")

# --- Giveaway Commands ---
@bot.tree.command(name="gstart", description="Start a new giveaway")
async def gstart(interaction: discord.Interaction, prize: str, duration_minutes: int, winners: int = 1):
    end_time = datetime.now(timezone.utc) + timedelta(minutes=duration_minutes)
    
    embed = discord.Embed(
        title="🎉 New Giveaway! 🎉", 
        description=f"**Prize:** {prize}\n**Winners:** {winners}\n**Ends:** <t:{int(end_time.timestamp())}:R>", 
        color=discord.Color.gold()
    )
    embed.set_footer(text="Click the button below to join!")
    
    await interaction.response.send_message("Giveaway is starting...", ephemeral=True)
    msg = await interaction.channel.send(embed=embed)
    
    view = GiveawayView(message_id=msg.id)
    await msg.edit(view=view)

    # Save to MongoDB
    await giveaways_collection.insert_one({
        "message_id": msg.id,
        "channel_id": msg.channel.id,
        "prize": prize,
        "winners_count": winners,
        "end_time": end_time.timestamp(),
        "participants": [],
        "active": True
    })

@bot.tree.command(name="gend", description="Manually end a giveaway")
async def gend(interaction: discord.Interaction, message_id: str):
    try:
        msg_id = int(message_id)
    except ValueError:
        await interaction.response.send_message("Please enter a valid message ID.", ephemeral=True)
        return
    
    giveaway = await giveaways_collection.find_one({"message_id": msg_id, "active": True})
    if not giveaway:
        await interaction.response.send_message("No active giveaway found with this ID.", ephemeral=True)
        return
    
    await end_giveaway(giveaway)
    await interaction.response.send_message("Giveaway ended successfully.", ephemeral=True)

# --- Background Task ---
@tasks.loop(seconds=30)
async def check_giveaways():
    now = datetime.now(timezone.utc).timestamp()
    cursor = giveaways_collection.find({"active": True, "end_time": {"$lte": now}})
    async for giveaway in cursor:
        await end_giveaway(giveaway)

async def end_giveaway(giveaway):
    msg_id = giveaway["message_id"]
    channel_id = giveaway["channel_id"]
    prize = giveaway["prize"]
    winners_count = giveaway["winners_count"]
    participants = giveaway.get("participants", [])

    await giveaways_collection.update_one({"message_id": msg_id}, {"$set": {"active": False}})

    channel = bot.get_channel(channel_id)
    if not channel:
        return

    try:
        msg = await channel.fetch_message(msg_id)
        await msg.edit(view=None) 
    except Exception:
        pass

    if len(participants) == 0:
        await channel.send(f"The giveaway for **{prize}** has ended, but no one participated. 😢")
        return
    
    actual_winners_count = min(winners_count, len(participants))
    winner_ids = random.sample(participants, actual_winners_count)
    winners_mentions = ", ".join([f"<@{uid}>" for uid in winner_ids])

    embed = discord.Embed(
        title="🎉 Giveaway Ended! 🎉", 
        description=f"**Prize:** {prize}\n**Winners:** {winners_mentions}", 
        color=discord.Color.green()
    )
    await channel.send(content=f"Congratulations {winners_mentions}! You won **{prize}**!", embed=embed)

# --- Main Entry Point ---
async def main():
    token = os.getenv("DISCORD_TOKEN")
    if not token:
        print("Error: DISCORD_TOKEN environment variable not set!")
        return

    await start_web_server()

    async with bot:
        await bot.start(token)

if __name__ == "__main__":
    asyncio.run(main())
