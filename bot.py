import discord
from discord import app_commands
import os
import asyncio
import random
from datetime import datetime, timezone, timedelta
import motor.motor_asyncio
from aiohttp import web

# משיכת משתני הסביבה (Secrets) מ-Render
MONGO_URI = os.getenv("MONGO_URI")
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")

# התחברות למסד הנתונים
cluster = motor.motor_asyncio.AsyncIOMotorClient(MONGO_URI)
db = cluster["giveaway_bot"]
giveaways_col = db["giveaways"]

class GiveawayClient(discord.Client):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)

    async def setup_hook(self):
        # הוספת כפתור ההגרלה התמידי כדי שיעבוד גם אחרי ריסטארט
        self.add_view(GiveawayView())
        # סנכרון פקודות הסלאש
        await self.tree.sync()
        # הפעלת שרת האינטרנט הפנימי בשביל Render (פורט 8080)
        self.loop.create_task(start_dummy_server())
        # הפעלת לולאת הרקע שבודקת מתי הגרלות מסתיימות
        self.loop.create_task(check_giveaways_loop())

client = GiveawayClient()

# --- כפתור ההשתתפות הקבוע ---
class GiveawayView(discord.ui.View):
    def __init__(self):
        # timeout=None קריטי כדי שהכפתור לא יפסיק לעבוד אף פעם
        super().__init__(timeout=None)

    @discord.ui.button(label="🎉 Join Giveaway", style=discord.ButtonStyle.green, custom_id="join_giveaway_button")
    async def join_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        msg_id = str(interaction.message.id)
        user_id = interaction.user.id
        
        # חיפוש ההגרלה במונגו כדי לוודא שהיא פעילה
        giveaway = await giveaways_col.find_one({"_id": msg_id, "status": "active"})
        if not giveaway:
            return await interaction.response.send_message("הגרלה זו הסתיימה או שהיא לא קיימת יותר.", ephemeral=True)
            
        # בדיקה אם המשתמש כבר משתתף
        if user_id in giveaway.get("participants", []):
            return await interaction.response.send_message("אתה כבר משתתף בהגרלה הזו! 🎉", ephemeral=True)
            
        # הוספת המשתמש למסד הנתונים
        await giveaways_col.update_one({"_id": msg_id}, {"$push": {"participants": user_id}})
        await interaction.response.send_message("נכנסת להגרלה בהצלחה! בהצלחה! 🎁", ephemeral=True)

# --- פקודת התחלת הגרלה ---
@client.tree.command(name="giveaway", description="Start a new giveaway")
@app_commands.describe(prize="What is the prize?", duration_minutes="How many minutes will the giveaway run?", winners="How many winners?")
async def giveaway(interaction: discord.Interaction, prize: str, duration_minutes: int, winners: int = 1):
    # וידוא שיש למפעיל אישור לנהל אירועים
    if not interaction.user.guild_permissions.manage_events:
        return await interaction.response.send_message("You do not have permission to start giveaways.", ephemeral=True)

    end_time = datetime.now(timezone.utc) + timedelta(minutes=duration_minutes)
    unix_time = int(end_time.timestamp())
    
    embed = discord.Embed(
        title="🎉 GIVEAWAY STARTED 🎉", 
        description=f"**Prize:** {prize}\n**Winners:** {winners}\n**Ends:** <t:{unix_time}:R>\n\nClick the button below to enter!", 
        color=discord.Color.blue()
    )
    
    await interaction.response.send_message("Giveaway is starting...", ephemeral=True)
    message = await interaction.channel.send(embed=embed, view=GiveawayView())
    
    # שמירת נתוני ההגרלה במונגו
    giveaway_data = {
        "_id": str(message.id),
        "channel_id": str(interaction.channel.id),
        "prize": prize,
        "winners_count": winners,
        "end_time": end_time,
        "status": "active",
        "participants": []
    }
    await giveaways_col.insert_one(giveaway_data)

# --- פקודת סיום הגרלה ידני (בכוח) ---
@client.tree.command(name="force_end", description="Force end an active giveaway immediately")
@app_commands.describe(message_id="The Message ID of the giveaway")
async def force_end(interaction: discord.Interaction, message_id: str):
    if not interaction.user.guild_permissions.manage_events:
        return await interaction.response.send_message("You do not have permission to use this.", ephemeral=True)
        
    giveaway = await giveaways_col.find_one({"_id": message_id, "status": "active"})
    if not giveaway:
        return await interaction.response.send_message("Giveaway not found or it has already ended.", ephemeral=True)
        
    await interaction.response.send_message(f"Ending giveaway {message_id} immediately...", ephemeral=True)
    await end_giveaway(giveaway)

# --- הפעולה שמסיימת הגרלה ובוחרת מנצחים ---
async def end_giveaway(giveaway):
    msg_id = giveaway["_id"]
    channel_id = int(giveaway["channel_id"])
    participants = giveaway.get("participants", [])
    winners_count = giveaway["winners_count"]
    prize = giveaway["prize"]
    
    # עדכון סטטוס במונגו כדי שלא תיבחר פעמיים
    await giveaways_col.update_one({"_id": msg_id}, {"$set": {"status": "ended"}})
    
    channel = client.get_channel(channel_id)
    if not channel:
        try:
            channel = await client.fetch_channel(channel_id)
        except:
            return
            
    try:
        message = await channel.fetch_message(int(msg_id))
        
        # בחירת מנצחים
        if len(participants) == 0:
            await channel.send(f"The giveaway for **{prize}** has ended, but nobody participated! 😢")
        else:
            actual_winners_count = min(winners_count, len(participants))
            winners = random.sample(participants, actual_winners_count)
            winners_mentions = ", ".join([f"<@{w}>" for w in winners])
            
            await channel.send(f"🎉 Congratulations {winners_mentions}! You won **{prize}**! 🎉\n[Jump to Giveaway]({message.jump_url})")
        
        # עדכון ההודעה המקורית (כיבוי כפתור וצבע אדום)
        embed = message.embeds[0]
        embed.title = "🎉 GIVEAWAY ENDED 🎉"
        embed.color = discord.Color.red()
        await message.edit(embed=embed, view=None) 
        
    except discord.NotFound:
        pass

# --- לולאת רקע אוטומטית שרצה כל הזמן ומחפשת הגרלות שנגמרו ---
async def check_giveaways_loop():
    await client.wait_until_ready()
    while not client.is_closed():
        now = datetime.now(timezone.utc)
        # חיפוש הגרלות פעילות שהזמן שלהן קטן או שווה לעכשיו
        cursor = giveaways_col.find({"status": "active", "end_time": {"$lte": now}})
        async for giveaway in cursor:
            await end_giveaway(giveaway)
        await asyncio.sleep(15) # המתנה של 15 שניות בין בדיקה לבדיקה

# --- פונקציות השרת המדומה כדי שרנדר יחשוב שזה אתר פעיל ---
async def dummy_handler(request):
    return web.Response(text="GiveawaySomething is Online!")

async def start_dummy_server():
    app = web.Application()
    app.router.add_get('/', dummy_handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', 8080)
    await site.start()

if __name__ == "__main__":
    if not DISCORD_TOKEN or not MONGO_URI:
        print("CRITICAL ERROR: MISSING DISCORD_TOKEN OR MONGO_URI IN ENVIRONMENT VARIABLES")
    else:
        client.run(DISCORD_TOKEN)
