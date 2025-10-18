import os
import discord
from discord.ext import commands, tasks
from discord import app_commands
import asyncio
import random
from datetime import datetime, timezone, timedelta
from database import Database
from keep_alive import keep_alive

# Start keep-alive server for Replit
keep_alive()

intents = discord.Intents.all()
bot = commands.Bot(command_prefix='!', intents=intents, help_command=None)
db = Database()

# Check for Discord token
DISCORD_TOKEN = os.getenv('DISCORD_TOKEN')
if not DISCORD_TOKEN:
    print("ERROR: DISCORD_TOKEN environment variable not set!")
    print("Please set your Discord bot token in the Secrets tab.")
    exit(1)

# Global error handler to prevent crashes
@bot.event
async def on_error(event, *args, **kwargs):
    print(f"Bot error in {event}: {args}")
    # Don't crash the bot, just log the error

COLORS = {
    'red': 0xFF0000,  # Changed to proper red hex for mod logs
    'green': 0x00FF7F,
    'blue': 0x5865F2,
    'yellow': 0xFFD700,
    'purple': 0x800080,
    'pink': 0xFF69B4,
    'orange': 0xFFAA00
}

EMOJIS = {
    'wave': '👋',
    'trophy': '🏆',
    'party': '🎉',
    'chart': '📊',
    'arrow_up': '⬆️',
    'arrow_down': '⬇️',
    'gift': '🎁',
    'tada': '🎊',
    'ticket': '🎫',
    'check': '✅',
    'cross': '❌',
    'warning': '⚠️'
}

TICKET_CHANNEL_ID = 1428777811165974680  # Your ticket channel ID

# Available commands for permission management
AVAILABLE_COMMANDS = [
    'invites', 'claimcheck', 'addclaims', 'removeclaims', 'leaderboard', 'syncinvites',
    'promote', 'demote', 'setstafflog', 'testwelcome',
    'gcreate', 'glist', 'gend', 'greroll', 'ping',
    'setwelcome', 'setmodlogs', 'addcmdperm', 'removecmdperm', 'listcmdperm'
]

# Invite cache for tracking
invite_cache = {}

# Track processed member joins to prevent duplicates
processed_joins = set()

async def check_command_permission(interaction: discord.Interaction, command_name: str) -> bool:
    """Check if user has permission to use a command based on role permissions"""
    # Server owner always has permission
    if interaction.user == interaction.guild.owner:
        return True

    # Get user's role IDs
    user_role_ids = [role.id for role in interaction.user.roles]

    # Check if any role has permission for this command
    has_permission = await db.check_role_permission(interaction.guild.id, user_role_ids, command_name)

    # If no specific permissions are set, fall back to default Discord permissions
    if not has_permission:
        command_permissions = await db.get_command_permissions(interaction.guild.id, command_name)
        if not command_permissions:  # No permissions set, use defaults
            return True

    return has_permission

async def cache_invites(guild):
    """Cache current invites for a guild"""
    try:
        invites = await guild.invites()
        invite_cache[guild.id] = {invite.code: invite.uses for invite in invites}
        # Also update database
        for invite in invites:
            inviter_id = invite.inviter.id if invite.inviter else None
            await db.upsert_invite_code(invite.code, invite.guild.id, inviter_id, invite.uses, invite.max_uses)
    except Exception as e:
        print(f"Error caching invites for {guild.name}: {e}")
        invite_cache[guild.id] = {}

# --- GIVEAWAY MODAL ---
class GiveawayModal(discord.ui.Modal, title='Create Giveaway'):
    def __init__(self):
        super().__init__()

    duration = discord.ui.TextInput(
        label='Duration',
        placeholder='Ex: 10 minutes',
        max_length=20,
        required=True
    )

    winners = discord.ui.TextInput(
        label='Number Of Winners',
        placeholder='1',
        max_length=2,
        required=True
    )

    prize = discord.ui.TextInput(
        label='Prize',
        placeholder='What are you giving away?',
        max_length=100,
        required=True
    )

    description = discord.ui.TextInput(
        label='Description',
        placeholder='Optional description for the giveaway',
        max_length=500,
        required=False,
        style=discord.TextStyle.paragraph
    )

    async def on_submit(self, interaction: discord.Interaction):
        try:
            # Parse duration - support various formats
            duration_str = self.duration.value.lower().strip()
            end_time = None

            # Try to parse different duration formats
            total_seconds = 0
            
            if 'hour' in duration_str or duration_str.endswith('h'):
                # Extract number from string like "2 hours" or "2h"
                import re
                match = re.search(r'(\d+)', duration_str)
                if match:
                    hours = int(match.group(1))
                    total_seconds = hours * 3600
                    end_time = datetime.now(timezone.utc) + timedelta(hours=hours)
            elif 'minute' in duration_str or duration_str.endswith('m'):
                # Extract number from string like "30 minutes" or "30m"
                import re
                match = re.search(r'(\d+)', duration_str)
                if match:
                    minutes = int(match.group(1))
                    total_seconds = minutes * 60
                    end_time = datetime.now(timezone.utc) + timedelta(minutes=minutes)
            elif 'day' in duration_str or duration_str.endswith('d'):
                # Extract number from string like "2 days" or "2d"
                import re
                match = re.search(r'(\d+)', duration_str)
                if match:
                    days = int(match.group(1))
                    total_seconds = days * 86400
                    end_time = datetime.now(timezone.utc) + timedelta(days=days)
            elif 'second' in duration_str or duration_str.endswith('s'):
                # Extract number from string like "30 seconds" or "30s"
                import re
                match = re.search(r'(\d+)', duration_str)
                if match:
                    seconds = int(match.group(1))
                    total_seconds = seconds
                    end_time = datetime.now(timezone.utc) + timedelta(seconds=seconds)
            else:
                # Try direct numeric parsing with default to minutes
                try:
                    minutes = int(duration_str)
                    total_seconds = minutes * 60
                    end_time = datetime.now(timezone.utc) + timedelta(minutes=minutes)
                except:
                    pass

            if not end_time:
                await interaction.response.send_message("Invalid duration format! Use formats like: 10 minutes, 2 hours, 1 day, 30s, 30m, 2h, 1d", ephemeral=True)
                return

            # Check minimum time limit (5 seconds)
            if total_seconds < 5:
                await interaction.response.send_message("Giveaway duration must be at least 5 seconds!", ephemeral=True)
                return

            # Parse number of winners
            try:
                num_winners = int(self.winners.value)
                if num_winners < 1:
                    await interaction.response.send_message("Number of winners must be at least 1!", ephemeral=True)
                    return
            except ValueError:
                await interaction.response.send_message("Invalid number of winners!", ephemeral=True)
                return

            # Create embed - RED COLOR for giveaway embeds
            embed = discord.Embed(
                title=f"{self.prize.value}",
                color=0xFF0000  # RED color matching your screenshots
            )

            # Format fields to exactly match the image with live countdown
            embed.add_field(name="Hosted by:", value=interaction.user.mention, inline=False)
            embed.add_field(name="Entries:", value="0", inline=True)
            embed.add_field(name="Winners:", value=str(num_winners), inline=True)
            embed.add_field(name="Time:", value=f"<t:{int(end_time.timestamp())}:R>", inline=True)

            # Add description if provided
            if self.description.value and self.description.value.strip():
                embed.description = self.description.value.strip()

            # Create Enter Giveaway button view
            view = EnterGiveawayView()

            # Create the giveaway message with embed and button
            await interaction.response.send_message(embed=embed, view=view)

            # Get the message to store in database
            message = await interaction.original_response()

            # Store in database
            await db.create_giveaway(
                guild_id=interaction.guild.id,
                host_id=interaction.user.id,
                prize=self.prize.value,
                message_id=message.id,
                channel_id=interaction.channel.id,
                winners=num_winners,
                end_time=end_time.isoformat()
            )

            # Ping the host with notification
            await interaction.followup.send(f"{interaction.user.mention} Your giveaway has been successfully created!", ephemeral=True)

        except Exception as e:
            print(f"Giveaway creation error: {e}")
            try:
                await interaction.response.send_message("An error occurred while creating the giveaway!", ephemeral=True)
            except:
                await interaction.followup.send("An error occurred while creating the giveaway!", ephemeral=True)

# --- Enter Giveaway Button View ---
class EnterGiveawayView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label='🎉 Enter Giveaway', style=discord.ButtonStyle.primary, custom_id='enter_giveaway')
    async def enter_giveaway(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            # Get giveaway from database
            giveaway = await db.get_giveaway_by_message(interaction.message.id)
            if not giveaway:
                await interaction.response.send_message("This giveaway is no longer valid.", ephemeral=True)
                return

            # Check if giveaway is still active
            if giveaway['status'] == 'ended':
                embed = discord.Embed(
                    description=f"{EMOJIS['warning']} This giveaway has already ended.",
                    color=0x2F3136
                )
                await interaction.response.send_message(embed=embed, ephemeral=True)
                return

            # Check if user is already entered
            is_entered = await db.check_giveaway_entry(giveaway['id'], interaction.user.id)
            
            if is_entered:
                # User wants to leave the giveaway
                await db.leave_giveaway(giveaway['id'], interaction.user.id)
                
                embed = discord.Embed(
                    description=f"{EMOJIS['cross']} You have left the giveaway.",
                    color=COLORS['red']
                )
                await interaction.response.send_message(embed=embed, ephemeral=True)
                
                # Update entry count in embed
                entries_count = await db.get_giveaway_entries_count(giveaway['id'])
                
                # Update the original message embed
                original_embed = interaction.message.embeds[0]
                for i, field in enumerate(original_embed.fields):
                    if field.name == "Entries:":
                        original_embed.set_field_at(i, name="Entries:", value=str(entries_count), inline=True)
                        break
                
                await interaction.message.edit(embed=original_embed, view=self)
            else:
                # User wants to enter the giveaway
                success = await db.enter_giveaway(giveaway['id'], interaction.user.id)
                
                if success:
                    embed = discord.Embed(
                        description=f"{EMOJIS['check']} You have entered the giveaway! Good luck!",
                        color=COLORS['green']
                    )
                    await interaction.response.send_message(embed=embed, ephemeral=True)
                    
                    # Update entry count in embed
                    entries_count = await db.get_giveaway_entries_count(giveaway['id'])
                    
                    # Update the original message embed
                    original_embed = interaction.message.embeds[0]
                    for i, field in enumerate(original_embed.fields):
                        if field.name == "Entries:":
                            original_embed.set_field_at(i, name="Entries:", value=str(entries_count), inline=True)
                            break
                    
                    await interaction.message.edit(embed=original_embed, view=self)
                else:
                    await interaction.response.send_message("An error occurred while entering the giveaway.", ephemeral=True)

        except Exception as e:
            print(f"Enter giveaway error: {e}")
            await interaction.response.send_message("An error occurred while processing your request.", ephemeral=True)

# --- BOT EVENTS ---
@bot.event
async def on_ready():
    """Bot startup event"""
    print(f"{bot.user.name}#{bot.user.discriminator} has connected to Discord!")
    print(f"Bot is in {len(bot.guilds)} guilds")
    
    # Initialize database
    await db.create_tables()
    
    # Sync slash commands
    try:
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} command(s)")
    except Exception as e:
        print(f"Failed to sync commands: {e}")
    
    # Cache invites for all guilds
    for guild in bot.guilds:
        await cache_invites(guild)
        print(f"Cached invites for {guild.name}")
    
    # Start the giveaway check loop
    if not check_giveaways.is_running():
        check_giveaways.start()

@bot.event
async def on_guild_join(guild):
    """Cache invites when bot joins a new guild"""
    await cache_invites(guild)

@bot.event
async def on_invite_create(invite):
    """Update cache when new invite is created"""
    if invite.guild.id in invite_cache:
        invite_cache[invite.guild.id][invite.code] = 0
    await db.upsert_invite_code(invite.code, invite.guild.id, invite.inviter.id if invite.inviter else None, 0, invite.max_uses)

@bot.event
async def on_invite_delete(invite):
    """Update cache when invite is deleted"""
    if invite.guild.id in invite_cache and invite.code in invite_cache[invite.guild.id]:
        del invite_cache[invite.guild.id][invite.code]

@bot.event
async def on_member_join(member):
    """Track invite usage when member joins"""
    try:
        guild = member.guild
        
        # Skip if we've already processed this join
        join_key = f"{guild.id}_{member.id}_{int(member.joined_at.timestamp())}"
        if join_key in processed_joins:
            return
        processed_joins.add(join_key)
        
        # Get current invites
        try:
            current_invites = await guild.invites()
        except:
            return
            
        current_uses = {invite.code: invite.uses for invite in current_invites}
        
        # Find which invite was used
        used_invite = None
        if guild.id in invite_cache:
            for code, old_uses in invite_cache[guild.id].items():
                if code in current_uses and current_uses[code] > old_uses:
                    used_invite = code
                    break
        
        # Update cache
        invite_cache[guild.id] = current_uses
        
        # Track the invite in database
        if used_invite:
            invite_info = await db.get_invite_info(used_invite, guild.id)
            if invite_info and invite_info['inviter_id']:
                inviter_id = invite_info['inviter_id']
                
                # Check if account is less than 7 days old (fake account)
                account_age = datetime.now(timezone.utc) - member.created_at
                is_fake = account_age.days < 7
                
                # Check if this user was previously invited by this person
                was_previous = await db.check_previous_invite_relationship(guild.id, inviter_id, member.id)
                
                if is_fake:
                    await db.add_fake_invite(inviter_id, guild.id, member.id)
                else:
                    await db.add_invite(inviter_id, guild.id, member.id)
        
        # Send welcome message if channel is set
        settings = await db.get_guild_settings(guild.id)
        if settings and settings.get('welcome_channel_id'):
            welcome_channel = guild.get_channel(settings['welcome_channel_id'])
            if welcome_channel:
                # Create welcome embed - EXACT format from your screenshots
                inviter_text = "Unknown"
                if used_invite:
                    invite_info = await db.get_invite_info(used_invite, guild.id)
                    if invite_info and invite_info['inviter_id']:
                        inviter = guild.get_member(invite_info['inviter_id'])
                        if inviter:
                            inviter_text = f"@{inviter.display_name}"
                
                # Check if account is less than 7 days old (fake account)
                account_age = datetime.now(timezone.utc) - member.created_at
                is_fake = account_age.days < 7
                
                description = f"Welcome to **{guild.name}**, {member.mention}!\n"
                description += f"Invited by: {inviter_text}"
                
                if is_fake:
                    description += f"\n⚠️ Account is new (< {account_age.days} days old) — counted as a fake invite."
                
                embed = discord.Embed(
                    title="👋 Welcome!",
                    description=description,
                    color=0xFF0000
                )
                embed.add_field(name="", value=f"Member #{guild.member_count}", inline=False)
                embed.set_thumbnail(url=member.display_avatar.url)
                
                await welcome_channel.send(embed=embed)
    
    except Exception as e:
        print(f"Error in on_member_join: {e}")

@bot.event
async def on_member_remove(member):
    """Track when members leave"""
    try:
        guild = member.guild
        await db.handle_member_leave(guild.id, member.id)
        print(f"Handled leave for {member.name} from {guild.name}")
        
        # Send mod log for member leave
        settings = await db.get_guild_settings(guild.id)
        if settings and settings.get('mod_log_channel_id'):
            mod_log_channel = guild.get_channel(settings['mod_log_channel_id'])
            if mod_log_channel:
                embed = discord.Embed(
                    title="Member Left",
                    description=f"{member.mention} {member.display_name}\n\nID: {member.id} • {datetime.now().strftime('%m/%d/%y, %I:%M %p')}\n{datetime.now().strftime('Today at %I:%M %p')}",
                    color=0xFF0000
                )
                embed.set_author(name=member.display_name, icon_url=member.display_avatar.url)
                await mod_log_channel.send(embed=embed)
    except Exception as e:
        print(f"Error in on_member_remove: {e}")

# Comprehensive audit log tracking events - EXACT format from screenshots
@bot.event
async def on_member_join(member):
    """Enhanced member join tracking with mod logs"""
    try:
        guild = member.guild
        
        # Skip if we've already processed this join
        join_key = f"{guild.id}_{member.id}_{int(member.joined_at.timestamp())}"
        if join_key in processed_joins:
            return
        processed_joins.add(join_key)
        
        # Get current invites
        try:
            current_invites = await guild.invites()
        except:
            return
            
        current_uses = {invite.code: invite.uses for invite in current_invites}
        
        # Find which invite was used
        used_invite = None
        if guild.id in invite_cache:
            for code, old_uses in invite_cache[guild.id].items():
                if code in current_uses and current_uses[code] > old_uses:
                    used_invite = code
                    break
        
        # Update cache
        invite_cache[guild.id] = current_uses
        
        # Track the invite in database
        if used_invite:
            invite_info = await db.get_invite_info(used_invite, guild.id)
            if invite_info and invite_info['inviter_id']:
                inviter_id = invite_info['inviter_id']
                
                # Check if account is less than 7 days old (fake account)
                account_age = datetime.now(timezone.utc) - member.created_at
                is_fake = account_age.days < 7
                
                # Check if this user was previously invited by this person
                was_previous = await db.check_previous_invite_relationship(guild.id, inviter_id, member.id)
                
                if is_fake:
                    await db.add_fake_invite(inviter_id, guild.id, member.id)
                else:
                    await db.add_invite(inviter_id, guild.id, member.id)
        
        # Send mod log for member join - EXACT format from screenshot
        settings = await db.get_guild_settings(guild.id)
        if settings and settings.get('mod_log_channel_id'):
            mod_log_channel = guild.get_channel(settings['mod_log_channel_id'])
            if mod_log_channel:
                account_age = datetime.now(timezone.utc) - member.created_at
                
                embed = discord.Embed(
                    title="Member Joined",
                    description=f"{member.mention} {member.display_name}\n\nAccount Age: {account_age.days} days\nID: {member.id} • {member.joined_at.strftime('%m/%d/%y, %I:%M %p')}\n{datetime.now().strftime('Today at %I:%M %p')}",
                    color=0xFF0000
                )
                embed.set_author(name=member.display_name, icon_url=member.display_avatar.url)
                await mod_log_channel.send(embed=embed)
        
        # Send welcome message if channel is set
        if settings and settings.get('welcome_channel_id'):
            welcome_channel = guild.get_channel(settings['welcome_channel_id'])
            if welcome_channel:
                # Create welcome embed - EXACT format from your screenshots
                inviter_text = "Unknown"
                if used_invite:
                    invite_info = await db.get_invite_info(used_invite, guild.id)
                    if invite_info and invite_info['inviter_id']:
                        inviter = guild.get_member(invite_info['inviter_id'])
                        if inviter:
                            inviter_text = inviter.mention  # Use .mention instead of display_name
                
                # Check if account is less than 7 days old (fake account)
                account_age = datetime.now(timezone.utc) - member.created_at
                is_fake = account_age.days < 7
                
                description = f"Welcome to **{guild.name}**, {member.mention}!\n"
                description += f"Invited by: {inviter_text}"
                
                if is_fake:
                    description += f"\n⚠️ Account is new (< {account_age.days} days old) — counted as a fake invite."
                
                embed = discord.Embed(
                    title="👋 Welcome!",
                    description=description,
                    color=0xFF0000
                )
                embed.add_field(name="", value=f"Member #{guild.member_count}", inline=False)
                embed.set_thumbnail(url=member.display_avatar.url)
                
                await welcome_channel.send(embed=embed)
    
    except Exception as e:
        print(f"Error in on_member_join: {e}")

@bot.event
async def on_message_delete(message):
    """Track message deletions - EXACT format from screenshots"""
    try:
        if message.author.bot:
            return
        
        settings = await db.get_guild_settings(message.guild.id)
        if settings and settings.get('mod_log_channel_id'):
            mod_log_channel = bot.get_channel(settings['mod_log_channel_id'])
            if mod_log_channel:
                # EXACT format from screenshot
                embed = discord.Embed(
                    title="Message Deleted",
                    description=f"{message.author.mention} {message.author.display_name}\n\n**Content:** {message.content[:1000] if message.content else 'No content'}\n\nID: {message.id} • {datetime.now().strftime('%m/%d/%y, %I:%M %p')}\n{datetime.now().strftime('Today at %I:%M %p')}",
                    color=0xFF0000
                )
                embed.set_author(name=message.author.display_name, icon_url=message.author.display_avatar.url)
                await mod_log_channel.send(embed=embed)
    except Exception as e:
        print(f"Error in on_message_delete: {e}")

@bot.event
async def on_message_edit(before, after):
    """Track message edits - EXACT format from screenshots"""
    try:
        if before.author.bot or before.content == after.content:
            return
            
        settings = await db.get_guild_settings(before.guild.id)
        if settings and settings.get('mod_log_channel_id'):
            mod_log_channel = bot.get_channel(settings['mod_log_channel_id'])
            if mod_log_channel:
                # EXACT format from screenshot
                embed = discord.Embed(
                    title=f"{before.author.display_name}",
                    description=f"**Message sent by** {before.author.mention} **Deleted in** {before.channel.mention}\n{before.content[:500] if before.content else 'No content'}\n\nAuthor: {before.author.id} | Message ID: {before.id} • {datetime.now().strftime('Today at %I:%M %p')}",
                    color=0xFF0000
                )
                embed.set_author(name=before.author.display_name, icon_url=before.author.display_avatar.url)
                await mod_log_channel.send(embed=embed)
    except Exception as e:
        print(f"Error in on_message_edit: {e}")

@bot.event
async def on_member_update(before, after):
    """Track member role changes - EXACT format from screenshots"""
    try:
        if before.roles != after.roles:
            settings = await db.get_guild_settings(after.guild.id)
            if settings and settings.get('mod_log_channel_id'):
                mod_log_channel = after.guild.get_channel(settings['mod_log_channel_id'])
                if mod_log_channel:
                    # Find role changes
                    added_roles = [role for role in after.roles if role not in before.roles]
                    removed_roles = [role for role in before.roles if role not in after.roles]
                    
                    description = f"{after.mention} {after.display_name}\n\n"
                    
                    if removed_roles:
                        role_mentions = ", ".join([f"@{role.name}" for role in removed_roles])
                        description += f"**Roles Removed:** {role_mentions}\n\n"
                    
                    if added_roles:
                        role_mentions = ", ".join([f"@{role.name}" for role in added_roles])
                        description += f"**Roles Added:** {role_mentions}\n\n"
                    
                    description += f"ID: {after.id} • {datetime.now().strftime('%m/%d/%y, %I:%M %p')}\n{datetime.now().strftime('Today at %I:%M %p')}"
                    
                    embed = discord.Embed(
                        title="Member Updated",
                        description=description,
                        color=0xFF0000
                    )
                    embed.set_author(name=after.display_name, icon_url=after.display_avatar.url)
                    await mod_log_channel.send(embed=embed)
    except Exception as e:
        print(f"Error in on_member_update: {e}")

@bot.event
async def on_guild_channel_delete(channel):
    """Track channel deletions - EXACT format from screenshots"""
    try:
        settings = await db.get_guild_settings(channel.guild.id)
        if settings and settings.get('mod_log_channel_id'):
            mod_log_channel = channel.guild.get_channel(settings['mod_log_channel_id'])
            if mod_log_channel:
                embed = discord.Embed(
                    title="Channel Deleted",
                    description=f"Channel Name: #{channel.name}\nType: {channel.type}\n\nID: {channel.id} • {datetime.now().strftime('%m/%d/%y, %I:%M %p')}\n{datetime.now().strftime('Today at %I:%M %p')}",
                    color=0xFF0000
                )
                await mod_log_channel.send(embed=embed)
    except Exception as e:
        print(f"Error in on_guild_channel_delete: {e}")

@bot.event
async def on_guild_channel_create(channel):
    """Track channel creation"""
    try:
        settings = await db.get_guild_settings(channel.guild.id)
        if settings and settings.get('mod_log_channel_id'):
            mod_log_channel = channel.guild.get_channel(settings['mod_log_channel_id'])
            if mod_log_channel:
                embed = discord.Embed(
                    title="Channel Created",
                    description=f"Channel: {channel.mention}\nName: #{channel.name}\nType: {channel.type}\n\nID: {channel.id} • {datetime.now().strftime('%m/%d/%y, %I:%M %p')}\n{datetime.now().strftime('Today at %I:%M %p')}",
                    color=0xFF0000
                )
                await mod_log_channel.send(embed=embed)
    except Exception as e:
        print(f"Error in on_guild_channel_create: {e}")

@bot.event
async def on_guild_channel_update(before, after):
    """Track channel updates"""
    try:
        settings = await db.get_guild_settings(after.guild.id)
        if settings and settings.get('mod_log_channel_id'):
            mod_log_channel = after.guild.get_channel(settings['mod_log_channel_id'])
            if mod_log_channel:
                changes = []
                if before.name != after.name:
                    changes.append(f"Name changed: #{before.name} -> #{after.name}")
                
                if changes:
                    embed = discord.Embed(
                        title="Channel Updated",
                        description=f"#{after.mention} was changed:\n\n" + "\n".join(changes) + f"\n\nID: {after.id} • {datetime.now().strftime('%m/%d/%y, %I:%M %p')}\n{datetime.now().strftime('Today at %I:%M %p')}",
                        color=0xFF0000
                    )
                    await mod_log_channel.send(embed=embed)
    except Exception as e:
        print(f"Error in on_guild_channel_update: {e}")

@bot.event
async def on_guild_role_create(role):
    """Track role creation"""
    try:
        settings = await db.get_guild_settings(role.guild.id)
        if settings and settings.get('mod_log_channel_id'):
            mod_log_channel = role.guild.get_channel(settings['mod_log_channel_id'])
            if mod_log_channel:
                embed = discord.Embed(
                    title="Role Created",
                    description=f"Role: {role.mention}\nName: {role.name}\n\nID: {role.id} • {datetime.now().strftime('%m/%d/%y, %I:%M %p')}\n{datetime.now().strftime('Today at %I:%M %p')}",
                    color=0xFF0000
                )
                await mod_log_channel.send(embed=embed)
    except Exception as e:
        print(f"Error in on_guild_role_create: {e}")

@bot.event
async def on_guild_role_delete(role):
    """Track role deletion"""
    try:
        settings = await db.get_guild_settings(role.guild.id)
        if settings and settings.get('mod_log_channel_id'):
            mod_log_channel = role.guild.get_channel(settings['mod_log_channel_id'])
            if mod_log_channel:
                embed = discord.Embed(
                    title="Role Deleted",
                    description=f"Role Name: {role.name}\n\nID: {role.id} • {datetime.now().strftime('%m/%d/%y, %I:%M %p')}\n{datetime.now().strftime('Today at %I:%M %p')}",
                    color=0xFF0000
                )
                await mod_log_channel.send(embed=embed)
    except Exception as e:
        print(f"Error in on_guild_role_delete: {e}")

@bot.event
async def on_member_ban(guild, user):
    """Track member bans"""
    try:
        settings = await db.get_guild_settings(guild.id)
        if settings and settings.get('mod_log_channel_id'):
            mod_log_channel = guild.get_channel(settings['mod_log_channel_id'])
            if mod_log_channel:
                embed = discord.Embed(
                    title="Member Banned",
                    description=f"{user.mention} {user.name}\n\nID: {user.id} • {datetime.now().strftime('%m/%d/%y, %I:%M %p')}\n{datetime.now().strftime('Today at %I:%M %p')}",
                    color=0xFF0000
                )
                embed.set_author(name=user.name, icon_url=user.display_avatar.url)
                await mod_log_channel.send(embed=embed)
    except Exception as e:
        print(f"Error in on_member_ban: {e}")

@bot.event
async def on_member_unban(guild, user):
    """Track member unbans"""
    try:
        settings = await db.get_guild_settings(guild.id)
        if settings and settings.get('mod_log_channel_id'):
            mod_log_channel = guild.get_channel(settings['mod_log_channel_id'])
            if mod_log_channel:
                embed = discord.Embed(
                    title="Member Unbanned",
                    description=f"{user.mention} {user.name}\n\nID: {user.id} • {datetime.now().strftime('%m/%d/%y, %I:%M %p')}\n{datetime.now().strftime('Today at %I:%M %p')}",
                    color=0xFF0000
                )
                embed.set_author(name=user.name, icon_url=user.display_avatar.url)
                await mod_log_channel.send(embed=embed)
    except Exception as e:
        print(f"Error in on_member_unban: {e}")

# Task to check for ended giveaways
@tasks.loop(minutes=1)
async def check_giveaways():
    """Check for ended giveaways and process them"""
    try:
        ended_giveaways = await db.get_ended_giveaways()
        for giveaway in ended_giveaways:
            try:
                # Get guild and channel
                guild = bot.get_guild(giveaway['guild_id'])
                if not guild:
                    continue
                    
                channel = guild.get_channel(giveaway['channel_id'])
                if not channel:
                    continue

                # Get entries
                entries = await db.get_giveaway_entries(giveaway['id'])
                
                # Get original message
                try:
                    original_message = await channel.fetch_message(giveaway['message_id'])
                except:
                    original_message = None

                if not entries:
                    # No entries
                    embed = discord.Embed(
                        title="🎉 Congratulations!",
                        description=f"**Prize:** {giveaway['prize']}\n\nNo one entered this giveaway.",
                        color=0xFF0000
                    )
                else:
                    # Select winners
                    num_winners = min(giveaway['winners'], len(entries))
                    winners = random.sample(entries, num_winners)
                    
                    winner_mentions = []
                    for winner_data in winners:
                        user = guild.get_member(winner_data['user_id'])
                        if user:
                            winner_mentions.append(user.mention)
                    
                    # Send winner pings first
                    if winner_mentions:
                        winner_pings = " ".join(winner_mentions)
                        await channel.send(f"🎉 {winner_pings}")
                    
                    # Create winner embed
                    embed = discord.Embed(
                        title="🎉 Congratulations!",
                        color=0xFF0000
                    )
                    
                    description = f"**Prize:** {giveaway['prize']}\n"
                    if winner_mentions:
                        description += f"**Winner(s):** {', '.join(winner_mentions)}\n"
                    
                    host = guild.get_member(giveaway['host_id'])
                    if host:
                        description += f"**Hosted by:** {host.mention}\n\n"
                    
                    description += "────────────────────────\n\n"
                    description += f"- Open a ticket in <#{TICKET_CHANNEL_ID}>\n"
                    description += "- Please take a screenshot of this message and send it in your claim ticket!"
                    
                    embed.description = description

                # Send results
                await channel.send(embed=embed)

                # Update original message
                if original_message:
                    try:
                        original_embed = original_message.embeds[0]
                        original_embed.title = f"🎉 {giveaway['prize']} (ENDED)"
                        original_embed.color = 0x808080  # Gray for ended
                        
                        # Update time field
                        for i, field in enumerate(original_embed.fields):
                            if field.name == "Time:":
                                original_embed.set_field_at(i, name="Time:", value="Ended", inline=True)
                                break
                        
                        await original_message.edit(embed=original_embed, view=None)
                    except:
                        pass

                # Mark as ended
                await db.end_giveaway(giveaway['id'])

            except Exception as e:
                print(f"Error processing giveaway {giveaway['id']}: {e}")
                
    except Exception as e:
        print(f"Error in check_giveaways: {e}")

# --- SLASH COMMANDS ---

@bot.tree.command(name="ping", description="Select a role to ping for giveaways")
@app_commands.describe(role="The role to ping for the giveaway")
async def ping(interaction: discord.Interaction, role: discord.Role):
    if not await check_command_permission(interaction, 'ping'):
        await interaction.response.send_message("You don't have permission to use this command.", ephemeral=True)
        return
    
    # EXACT format from your screenshot with party emoji and role ping
    message = f"{role.mention} Make sure to join! W {interaction.user.mention} for hosting a giveaway! 🎉"
    await interaction.response.send_message(message)

@bot.tree.command(name="invites", description="Check invite count for a user")
@app_commands.describe(user="The user to check invites for (optional)")
async def invites(interaction: discord.Interaction, user: discord.Member = None):
    if not await check_command_permission(interaction, 'invites'):
        await interaction.response.send_message("You don't have permission to use this command.", ephemeral=True)
        return
    
    target = user or interaction.user
    invites_data = await db.get_user_invites(target.id, interaction.guild.id)
    
    embed = discord.Embed(
        title=f"Invite Stats for {target.display_name}",
        color=0xFF0000  # Red color matching your screenshot
    )

    # Format EXACTLY like your screenshot using description instead of fields
    description = f"**Joined:** {invites_data['total']}\n"
    description += f"**Left:** {invites_data['left']}\n"
    description += f"**Fake Invites (accounts < 7 days):** {invites_data['fake']}\n"
    description += f"**Net Invites:** {invites_data['net']}"

    embed.description = description
    
    embed.set_thumbnail(url=target.display_avatar.url)
    
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="leaderboard", description="Show invite leaderboard")
@app_commands.describe(limit="Number of users to show (default: 10)")
async def leaderboard(interaction: discord.Interaction, limit: int = 10):
    if not await check_command_permission(interaction, 'leaderboard'):
        await interaction.response.send_message("You don't have permission to use this command.", ephemeral=True)
        return
    
    if limit < 1 or limit > 25:
        await interaction.response.send_message("Limit must be between 1 and 25.", ephemeral=True)
        return
    
    leaderboard_data = await db.get_invite_leaderboard(interaction.guild.id, limit)
    
    if not leaderboard_data:
        embed = discord.Embed(
            title=f"{EMOJIS['trophy']} Invite Leaderboard",
            description="No invite data found for this server.",
            color=COLORS['yellow']
        )
        await interaction.response.send_message(embed=embed)
        return
    
    embed = discord.Embed(
        title=f"🏆 Invite Leaderboard",
        color=0xFFD700  # Gold color like your screenshot
    )
    
    description = ""
    for i, entry in enumerate(leaderboard_data, 1):
        user = interaction.guild.get_member(entry['user_id'])
        username = user.mention if user else f"<@{entry['user_id']}>"
        
        description += f"**{i}.** {username} → **{entry['net']}** (joined: {entry['total']}, left: {entry['left']})\n"
    
    embed.description = description
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="addclaims", description="Add claims to a user (Staff only)")
@app_commands.describe(user="The user to add claims to", amount="Number of claims to add")
async def addclaims(interaction: discord.Interaction, user: discord.Member, amount: int):
    if not await check_command_permission(interaction, 'addclaims'):
        await interaction.response.send_message("You don't have permission to use this command.", ephemeral=True)
        return
    
    if amount <= 0:
        await interaction.response.send_message("Amount must be positive.", ephemeral=True)
        return
    
    await db.add_claims(user.id, interaction.guild.id, amount)
    
    embed = discord.Embed(
        title=f"{EMOJIS['check']} Claims Added",
        description=f"Added {amount} claims to {user.mention}",
        color=COLORS['green']
    )
    
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="removeclaims", description="Remove claims from a user (Staff only)")
@app_commands.describe(user="The user to remove claims from", amount="Number of claims to remove")
async def removeclaims(interaction: discord.Interaction, user: discord.Member, amount: int):
    if not await check_command_permission(interaction, 'removeclaims'):
        await interaction.response.send_message("You don't have permission to use this command.", ephemeral=True)
        return
    
    if amount <= 0:
        await interaction.response.send_message("Amount must be positive.", ephemeral=True)
        return
    
    await db.remove_claims(user.id, interaction.guild.id, amount)
    
    embed = discord.Embed(
        title=f"{EMOJIS['check']} Claims Removed",
        description=f"Removed {amount} claims from {user.mention}",
        color=COLORS['red']
    )
    
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="claimcheck", description="Check how many claims a user has")
@app_commands.describe(user="The user to check claims for (optional)")
async def claimcheck(interaction: discord.Interaction, user: discord.Member = None):
    if not await check_command_permission(interaction, 'claimcheck'):
        await interaction.response.send_message("You don't have permission to use this command.", ephemeral=True)
        return
    
    target = user or interaction.user
    invites_data = await db.get_user_invites(target.id, interaction.guild.id)
    
    embed = discord.Embed(
        title=f"📊 Claim Check",
        description=f"{target.mention} has claimed {invites_data['claimed']} invites.",
        color=0x5865F2  # Blue color matching your screenshot
    )
    
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="syncinvites", description="Sync historical invite data (Admin only)")
async def syncinvites(interaction: discord.Interaction):
    if not await check_command_permission(interaction, 'syncinvites'):
        await interaction.response.send_message("You don't have permission to use this command.", ephemeral=True)
        return
    
    await interaction.response.defer()
    
    try:
        # Get all current invites
        invites = await interaction.guild.invites()
        invite_data = {}
        
        for invite in invites:
            if invite.inviter:
                invite_data[invite.code] = {
                    'inviter_id': invite.inviter.id,
                    'uses': invite.uses
                }
        
        # Sync with database
        synced_count = await db.sync_historical_invites(interaction.guild.id, invite_data)
        
        embed = discord.Embed(
            title=f"{EMOJIS['check']} Invites Synced",
            description=f"Successfully synced historical invite data for {synced_count} users.",
            color=COLORS['green']
        )
        
        await interaction.followup.send(embed=embed)
        
        # Update invite cache
        await cache_invites(interaction.guild)
        
    except Exception as e:
        print(f"Error syncing invites: {e}")
        embed = discord.Embed(
            title=f"{EMOJIS['cross']} Sync Failed",
            description="An error occurred while syncing invite data.",
            color=COLORS['red']
        )
        await interaction.followup.send(embed=embed)

# --- GIVEAWAY COMMANDS ---

@bot.tree.command(name="gcreate", description="Create a giveaway")
async def gcreate(interaction: discord.Interaction):
    if not await check_command_permission(interaction, 'gcreate'):
        await interaction.response.send_message("You don't have permission to use this command.", ephemeral=True)
        return
    
    # Show the giveaway creation modal
    modal = GiveawayModal()
    await interaction.response.send_modal(modal)

@bot.tree.command(name="glist", description="List active giveaways")
async def glist(interaction: discord.Interaction):
    try:
        if not await check_command_permission(interaction, 'glist'):
            embed = discord.Embed(
                description="❌ You don't have permission to use this command!",
                color=COLORS['red']
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        giveaways = await db.get_active_giveaways(interaction.guild.id)
        
        if not giveaways:
            embed = discord.Embed(
                title="🎉 Active Giveaways",
                description="No active giveaways found.",
                color=COLORS['blue']
            )
            await interaction.response.send_message(embed=embed)
            return
        
        embed = discord.Embed(
            title="🎉 Active Giveaways",
            color=COLORS['blue']
        )
        
        for giveaway in giveaways[:10]:  # Limit to 10 to avoid embed limits
            try:
                host = bot.get_user(giveaway['host_id'])
                host_name = host.name if host else f"User {giveaway['host_id']}"
                
                end_time = datetime.fromisoformat(giveaway['end_time'])
                timestamp = int(end_time.timestamp())
                
                entries_count = await db.get_giveaway_entries_count(giveaway['id'])
                
                embed.add_field(
                    name=f"🎁 {giveaway['prize']}",
                    value=f"**Host:** {host_name}\n**Entries:** {entries_count}\n**Winners:** {giveaway['winners']}\n**Ends:** <t:{timestamp}:R>",
                    inline=True
                )
            except Exception as e:
                print(f"Error processing giveaway {giveaway['id']}: {e}")
        
        await interaction.response.send_message(embed=embed)
    except Exception as e:
        print(f"Error in glist command: {e}")
        await interaction.response.send_message("An error occurred while fetching giveaways.", ephemeral=True)

@bot.tree.command(name="gend", description="End a giveaway early")
@app_commands.describe(message_id="The message ID of the giveaway to end")
async def gend_command(interaction: discord.Interaction, message_id: str):
    """End a giveaway early"""
    try:
        if not await check_command_permission(interaction, 'gend'):
            embed = discord.Embed(
                description="❌ You don't have permission to use this command!",
                color=COLORS['red']
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        try:
            message_id = int(message_id)
        except ValueError:
            await interaction.response.send_message("Invalid message ID!", ephemeral=True)
            return
        
        giveaway = await db.get_giveaway_by_message(message_id)
        if not giveaway:
            await interaction.response.send_message("Giveaway not found!", ephemeral=True)
            return
        
        if giveaway['status'] == 'ended':
            await interaction.response.send_message("This giveaway has already ended!", ephemeral=True)
            return
        
        # Check if user is the host or has admin permissions
        if giveaway['host_id'] != interaction.user.id and not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("You can only end giveaways you created!", ephemeral=True)
            return
        
        # End the giveaway immediately
        await db.end_giveaway(giveaway['id'])
        
        # Process the giveaway ending
        try:
            # Get entries
            entries = await db.get_giveaway_entries(giveaway['id'])
            
            if not entries:
                # No entries
                embed = discord.Embed(
                    title="🎉 Congratulations!",
                    description=f"**Prize:** {giveaway['prize']}\n\nNo one entered this giveaway.",
                    color=0xFF0000
                )
            else:
                # Select winners
                num_winners = min(giveaway['winners'], len(entries))
                winners = random.sample(entries, num_winners)
                
                winner_mentions = []
                for winner_data in winners:
                    user = interaction.guild.get_member(winner_data['user_id'])
                    if user:
                        winner_mentions.append(user.mention)
                
                # Send winner announcement with ticket channel reference
                ticket_channel_mention = f"<#{TICKET_CHANNEL_ID}>"
                announcement_embed = discord.Embed(
                    title="🎉 Congratulations!",
                    description=f"**Prize:** {giveaway['prize']}\n**Winner(s):** {', '.join(winner_mentions)}\n**Hosted by:** <@{giveaway['host_id']}>\n\n" +
                              f"────────────────────────\n\n" +
                              f"- Open a ticket in {ticket_channel_mention}\n" +
                              f"- Please take a screenshot of this message and send it in your claim ticket!",
                    color=0xFF0000
                )
                embed = announcement_embed
                
            # Try to update original message
            try:
                channel = interaction.guild.get_channel(giveaway['channel_id'])
                if channel:
                    original_message = await channel.fetch_message(giveaway['message_id'])
                    if original_message:
                        original_embed = original_message.embeds[0]
                        original_embed.title = f"🎉 {giveaway['prize']} (ENDED)"
                        original_embed.color = 0x808080
                        
                        for i, field in enumerate(original_embed.fields):
                            if field.name == "Time:":
                                original_embed.set_field_at(i, name="Time:", value="Ended", inline=True)
                                break
                        
                        await original_message.edit(embed=original_embed, view=None)
                        
                        # Send results to same channel
                        await channel.send(embed=embed)
            except Exception as e:
                print(f"Error updating message: {e}")
            
            await interaction.response.send_message("✅ Giveaway ended successfully!", ephemeral=True)
            
        except Exception as e:
            print(f"Error processing giveaway: {e}")
            await interaction.response.send_message("Giveaway ended but there was an error processing results.", ephemeral=True)
            
    except Exception as e:
        print(f"Error in gend command: {e}")
        await interaction.response.send_message("An error occurred while ending the giveaway.", ephemeral=True)

@bot.tree.command(name="greroll", description="Reroll a giveaway")
@app_commands.describe(message_id="The message ID of the giveaway to reroll")
async def greroll(interaction: discord.Interaction, message_id: str):
    if not await check_command_permission(interaction, 'greroll'):
        embed = discord.Embed(
            description="❌ You don't have permission to use this command!",
            color=COLORS['red']
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return

    try:
        message_id = int(message_id)
    except ValueError:
        await interaction.response.send_message("Invalid message ID!", ephemeral=True)
        return
    
    giveaway = await db.get_giveaway_by_message(message_id)
    if not giveaway:
        await interaction.response.send_message("Giveaway not found!", ephemeral=True)
        return
    
    # Check if user is the host or has admin permissions
    if giveaway['host_id'] != interaction.user.id and not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("You can only reroll giveaways you created!", ephemeral=True)
        return
    
    # Get entries
    entries = await db.get_giveaway_entries(giveaway['id'])
    
    if not entries:
        await interaction.response.send_message("No entries found for this giveaway.", ephemeral=True)
        return
    
    # Select new winners
    num_winners = min(giveaway['winners'], len(entries))
    winners = random.sample(entries, num_winners)
    
    winner_mentions = []
    for winner_data in winners:
        user = interaction.guild.get_member(winner_data['user_id'])
        if user:
            winner_mentions.append(user.mention)
    
    # EXACT format from your screenshot
    embed = discord.Embed(
        title="🎉 Congratulations!",
        color=0xFF0000  # RED color matching your screenshot
    )
    
    # Build description exactly like your screenshot
    description = f"**Prize:** {giveaway['prize']}\n"
    if winner_mentions:
        description += f"**Winner(s):** {', '.join(winner_mentions)}\n"
    
    host = interaction.guild.get_member(giveaway['host_id'])
    if host:
        description += f"**Hosted by:** {host.mention}\n\n"
    
    description += "────────────────────────\n\n"
    description += f"- Open a ticket in <#{TICKET_CHANNEL_ID}>\n"
    description += "- Please take a screenshot of this message and send it in your claim ticket!"
    
    embed.description = description
    
    await interaction.response.send_message(embed=embed)

# --- STAFF MANAGEMENT COMMANDS ---

@bot.tree.command(name="promote", description="Promote a user and log to staff channel")
@app_commands.describe(user="The user to promote", role="The role to give", reason="Reason for promotion")
async def promote(interaction: discord.Interaction, user: discord.Member, role: discord.Role, reason: str = "No reason provided"):
    # Check command permission
    if not await check_command_permission(interaction, 'promote'):
        await interaction.response.send_message("You don't have permission to use this command!", ephemeral=True)
        return

    try:
        # Add role to user
        await user.add_roles(role)

        # EXACT format from your screenshots - simple green embed
        embed = discord.Embed(
            title="⬆️ User Promoted",
            description=f"{user.mention} has been promoted to {role.mention}",
            color=0x00FF00  # Green color
        )
        await interaction.response.send_message(embed=embed)

        # Log to staff channel if set
        settings = await db.get_guild_settings(interaction.guild.id)
        if settings and settings.get('staff_log_channel_id'):
            staff_channel = interaction.guild.get_channel(settings['staff_log_channel_id'])
            if staff_channel:
                # EXACT format from your staff promotion screenshot
                log_embed = discord.Embed(
                    title="⬆️ Staff Promotion ⬆️",
                    description=f"{user.mention} Has Been **PROMOTED** to {role.mention}\n\nPromoted By: {interaction.user.mention}",
                    color=0x00FF00,  # Green like your screenshot
                    timestamp=datetime.now(timezone.utc)
                )
                log_embed.set_thumbnail(url=user.display_avatar.url)  # User photo on right
                log_embed.set_footer(text=f"Updated by {interaction.user.display_name} • {datetime.now().strftime('%m/%d/%y, %I:%M %p')}", icon_url=interaction.user.display_avatar.url)

                await staff_channel.send(embed=log_embed)

    except Exception as e:
        print(f"Promote error: {e}")
        await interaction.response.send_message("An error occurred during promotion.", ephemeral=True)

@bot.tree.command(name="demote", description="Demote a user and log to staff channel")
@app_commands.describe(user="The user to demote", role="The role to demote to", reason="Reason for demotion")
async def demote(interaction: discord.Interaction, user: discord.Member, role: discord.Role, reason: str = "No reason provided"):
    # Check command permission
    if not await check_command_permission(interaction, 'demote'):
        await interaction.response.send_message("You don't have permission to use this command!", ephemeral=True)
        return

    try:
        # Get user's current roles
        user_roles = [r for r in user.roles if not r.is_default()]  # Exclude @everyone
        
        # Remove all roles above the target role (demote to specified role)
        roles_to_remove = []
        target_role_position = role.position
        
        for user_role in user_roles:
            if user_role.position > target_role_position:
                roles_to_remove.append(user_role)
        
        # Remove higher roles and ensure they have the target role
        if roles_to_remove:
            await user.remove_roles(*roles_to_remove)
        
        # Add the target role if they don't have it
        if role not in user.roles:
            await user.add_roles(role)

        # Simple confirmation embed
        embed = discord.Embed(
            title="⬇️ User Demoted",
            description=f"{user.mention} has been demoted to {role.mention}",
            color=0xFF0000  # Red color
        )
        await interaction.response.send_message(embed=embed)

        # Log to staff channel if set
        settings = await db.get_guild_settings(interaction.guild.id)
        if settings and settings.get('staff_log_channel_id'):
            staff_channel = interaction.guild.get_channel(settings['staff_log_channel_id'])
            if staff_channel:
                # EXACT format from your staff demotion screenshot
                log_embed = discord.Embed(
                    title="⬇️ Staff Demotion ⬇️",
                    description=f"{user.mention} Has Been **DEMOTED** to {role.mention}\n\nDemoted By: {interaction.user.mention}",
                    color=0xFF0000,  # Red like your screenshot
                    timestamp=datetime.now(timezone.utc)
                )
                log_embed.set_thumbnail(url=user.display_avatar.url)  # User photo on right
                log_embed.set_footer(text=f"Updated by {interaction.user.display_name} • {datetime.now().strftime('%m/%d/%y, %I:%M %p')}", icon_url=interaction.user.display_avatar.url)

                await staff_channel.send(embed=log_embed)

    except Exception as e:
        print(f"Demote error: {e}")
        await interaction.response.send_message("An error occurred during demotion.", ephemeral=True)

# --- SERVER CONFIGURATION COMMANDS ---

@bot.tree.command(name="setwelcome", description="Set the welcome channel (Admin only)")
@app_commands.describe(channel="The channel to send welcome messages")
async def setwelcome(interaction: discord.Interaction, channel: discord.TextChannel):
    if not await check_command_permission(interaction, 'setwelcome'):
        await interaction.response.send_message("You don't have permission to use this command.", ephemeral=True)
        return
    
    await db.set_welcome_channel(interaction.guild.id, channel.id)
    
    embed = discord.Embed(
        title=f"{EMOJIS['check']} Welcome Channel Set",
        description=f"Welcome messages will now be sent to {channel.mention}",
        color=COLORS['green']
    )
    
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="setstafflog", description="Set the staff log channel (Admin only)")
@app_commands.describe(channel="The channel to send staff logs")
async def setstafflog(interaction: discord.Interaction, channel: discord.TextChannel):
    if not await check_command_permission(interaction, 'setstafflog'):
        await interaction.response.send_message("You don't have permission to use this command.", ephemeral=True)
        return
    
    await db.set_staff_log_channel(interaction.guild.id, channel.id)
    
    embed = discord.Embed(
        title=f"{EMOJIS['check']} Staff Log Channel Set",
        description=f"Staff logs will now be sent to {channel.mention}",
        color=COLORS['green']
    )
    
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="setmodlogs", description="Set the mod log channel (Admin only)")
@app_commands.describe(channel="The channel to send mod logs")
async def setmodlogs(interaction: discord.Interaction, channel: discord.TextChannel):
    if not await check_command_permission(interaction, 'setmodlogs'):
        await interaction.response.send_message("You don't have permission to use this command.", ephemeral=True)
        return
    
    await db.set_mod_log_channel(interaction.guild.id, channel.id)
    
    embed = discord.Embed(
        title=f"{EMOJIS['check']} Mod Log Channel Set",
        description=f"Mod logs will now be sent to {channel.mention}",
        color=COLORS['green']
    )
    
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="testwelcome", description="Test the welcome message (Admin only)")
async def testwelcome(interaction: discord.Interaction):
    if not await check_command_permission(interaction, 'testwelcome'):
        await interaction.response.send_message("You don't have permission to use this command.", ephemeral=True)
        return
    
    settings = await db.get_guild_settings(interaction.guild.id)
    if not settings or not settings.get('welcome_channel_id'):
        await interaction.response.send_message("No welcome channel set. Use `/setwelcome` first.", ephemeral=True)
        return
    
    welcome_channel = interaction.guild.get_channel(settings['welcome_channel_id'])
    if not welcome_channel:
        await interaction.response.send_message("Welcome channel not found. Please set a new one.", ephemeral=True)
        return
    
    # Create test welcome embed - EXACT format from your screenshots
    description = f"Welcome to **{interaction.guild.name}**, {interaction.user.mention}!"
    description += f"\nInvited by: Test User (This is a test)"
    
    embed = discord.Embed(
        title="👋 Welcome!",
        description=description,
        color=0xFF0000  # Red color exactly like your screenshots
    )
    embed.add_field(name="", value=f"Member #{interaction.guild.member_count}", inline=False)
    embed.set_thumbnail(url=interaction.user.display_avatar.url)
    
    try:
        await welcome_channel.send(embed=embed)
        await interaction.response.send_message(f"Test welcome message sent to {welcome_channel.mention}!", ephemeral=True)
    except Exception as e:
        await interaction.response.send_message(f"Error sending test message: {e}", ephemeral=True)

# --- ROLE PERMISSION MANAGEMENT COMMANDS ---

@bot.tree.command(name="addcmdperm", description="Add permission for a role to use a command (Admin only)")
@app_commands.describe(role="The role to give permission to", command="The command name to allow access to")
@app_commands.choices(command=[
    app_commands.Choice(name=cmd, value=cmd) for cmd in AVAILABLE_COMMANDS
])
async def addcmdperm(interaction: discord.Interaction, role: discord.Role, command: str):
    # Only server owner or administrators can manage permissions
    if not (interaction.user == interaction.guild.owner or interaction.user.guild_permissions.administrator):
        await interaction.response.send_message("Only server administrators can manage command permissions.", ephemeral=True)
        return
    
    if command not in AVAILABLE_COMMANDS:
        await interaction.response.send_message("Invalid command name.", ephemeral=True)
        return
    
    success = await db.add_role_permission(interaction.guild.id, role.id, command)
    
    if success:
        embed = discord.Embed(
            title=f"{EMOJIS['check']} Permission Added",
            description=f"Role {role.mention} can now use `/{command}`",
            color=COLORS['green']
        )
    else:
        embed = discord.Embed(
            title=f"{EMOJIS['warning']} Permission Exists",
            description=f"Role {role.mention} already has permission to use `/{command}`",
            color=COLORS['yellow']
        )
    
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="removecmdperm", description="Remove permission for a role to use a command (Admin only)")
@app_commands.describe(role="The role to remove permission from", command="The command name to revoke access to")
@app_commands.choices(command=[
    app_commands.Choice(name=cmd, value=cmd) for cmd in AVAILABLE_COMMANDS
])
async def removecmdperm(interaction: discord.Interaction, role: discord.Role, command: str):
    # Only server owner or administrators can manage permissions
    if not (interaction.user == interaction.guild.owner or interaction.user.guild_permissions.administrator):
        await interaction.response.send_message("Only server administrators can manage command permissions.", ephemeral=True)
        return
    
    if command not in AVAILABLE_COMMANDS:
        await interaction.response.send_message("Invalid command name.", ephemeral=True)
        return
    
    removed = await db.remove_role_permission(interaction.guild.id, role.id, command)
    
    if removed:
        embed = discord.Embed(
            title=f"{EMOJIS['check']} Permission Removed",
            description=f"Role {role.mention} can no longer use `/{command}`",
            color=COLORS['red']
        )
    else:
        embed = discord.Embed(
            title=f"{EMOJIS['warning']} Permission Not Found",
            description=f"Role {role.mention} didn't have permission to use `/{command}`",
            color=COLORS['yellow']
        )
    
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="listcmdperm", description="List all command permissions for this server (Admin only)")
async def listcmdperm(interaction: discord.Interaction):
    # Only server owner or administrators can view permissions
    if not (interaction.user == interaction.guild.owner or interaction.user.guild_permissions.administrator):
        await interaction.response.send_message("Only server administrators can view command permissions.", ephemeral=True)
        return
    
    # Get all permissions from database
    permissions = []
    for command in AVAILABLE_COMMANDS:
        role_ids = await db.get_command_permissions(interaction.guild.id, command)
        for role_id in role_ids:
            permissions.append({'command': command, 'role_id': role_id})
    
    if not permissions:
        embed = discord.Embed(
            title="🔒 Command Permissions",
            description="No command permissions have been set for this server.\n\nWhen no permissions are set, all members can use all commands by default.",
            color=COLORS['blue']
        )
        await interaction.response.send_message(embed=embed)
        return
    
    # Group permissions by command
    commands_dict = {}
    for perm in permissions:
        cmd = perm['command']
        role_id = perm['role_id']
        role = interaction.guild.get_role(role_id)
        
        if cmd not in commands_dict:
            commands_dict[cmd] = []
        
        if role:
            commands_dict[cmd].append(role.mention)
        else:
            commands_dict[cmd].append(f"<@&{role_id}> (deleted role)")
    
    embed = discord.Embed(
        title="🔒 Command Permissions",
        color=COLORS['blue']
    )
    
    description = ""
    for cmd, roles in commands_dict.items():
        description += f"**/{cmd}:** {', '.join(roles)}\n"
    
    if len(description) > 4096:  # Discord embed description limit
        description = description[:4090] + "..."
    
    embed.description = description
    
    await interaction.response.send_message(embed=embed)

# Run the bot
if __name__ == "__main__":
    bot.run(DISCORD_TOKEN)
