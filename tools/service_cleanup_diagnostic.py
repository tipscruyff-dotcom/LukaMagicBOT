#!/usr/bin/env python3
# service_cleanup_diagnostic.py
# Tool to diagnose and fix service message cleanup issues

import os
import sys
import logging
from datetime import datetime
import asyncio
from telegram import Bot, Chat

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("service-cleanup-diagnostic")

# Check if we're running from the correct directory
try:
    from db import SessionLocal
    from crud import get_service_cleanup_config, set_service_cleanup_config
    from models import Setting, ServiceMessageSeen
except ImportError:
    logger.error("❌ Failed to import required modules. Make sure to run this script from the project root.")
    sys.exit(1)

async def check_bot_permissions(token, chat_ids):
    """Check if the bot has necessary permissions in the given groups"""
    bot = Bot(token=token)
    results = {}
    
    try:
        bot_info = await bot.get_me()
        logger.info(f"✅ Bot connected: @{bot_info.username} (ID: {bot_info.id})")
    except Exception as e:
        logger.error(f"❌ Failed to connect to bot: {e}")
        return None
    
    for chat_id in chat_ids:
        try:
            chat_id = int(chat_id)
            logger.info(f"🔍 Checking bot permissions for chat ID {chat_id}...")
            
            # Get chat info
            try:
                chat = await bot.get_chat(chat_id)
                chat_name = chat.title or f"Chat {chat_id}"
                chat_type = chat.type
                logger.info(f"✅ Found chat: {chat_name} (type: {chat_type})")
                
                # Check if it's an admin-only group
                is_admin_only = False
                try:
                    permissions = chat.permissions
                    can_send_messages = getattr(permissions, "can_send_messages", None)
                    if can_send_messages is False:
                        is_admin_only = True
                        logger.info(f"⚠️ Special case: {chat_name} is an admin-only chat")
                except Exception as perm_err:
                    logger.warning(f"Could not check if admin-only: {perm_err}")
                
            except Exception as e:
                logger.error(f"❌ Failed to get chat {chat_id}: {e}")
                results[chat_id] = {
                    "found": False,
                    "error": str(e),
                    "permissions": None
                }
                continue
            
            # Check bot permissions
            try:
                bot_member = await bot.get_chat_member(chat_id, bot_info.id)
                status = bot_member.status
                can_delete = getattr(bot_member, "can_delete_messages", False)
                
                results[chat_id] = {
                    "found": True,
                    "name": chat_name,
                    "type": chat_type,
                    "is_admin_only": is_admin_only,
                    "status": status,
                    "is_admin": status in ["administrator", "creator"],
                    "can_delete": can_delete,
                    "permissions": {
                        "can_delete_messages": can_delete,
                        "can_restrict_members": getattr(bot_member, "can_restrict_members", False),
                        "can_invite_users": getattr(bot_member, "can_invite_users", False),
                        "can_pin_messages": getattr(bot_member, "can_pin_messages", False),
                    }
                }
                
                if status in ["administrator", "creator"]:
                    if can_delete:
                        logger.info(f"✅ Bot has admin status ({status}) and can delete messages in {chat_name}")
                    else:
                        logger.error(f"❌ Bot is admin in {chat_name} but CANNOT delete messages!")
                else:
                    logger.error(f"❌ Bot is NOT admin in {chat_name} (status: {status})")
                    
            except Exception as e:
                logger.error(f"❌ Failed to get bot member info for {chat_id}: {e}")
                results[chat_id] = {
                    "found": True,
                    "name": chat_name,
                    "type": chat_type,
                    "error": str(e),
                    "permissions": None
                }
        
        except Exception as e:
            logger.error(f"❌ Error checking chat {chat_id}: {e}")
            results[chat_id] = {
                "found": False,
                "error": str(e),
                "permissions": None
            }
    
    return results

async def check_service_cleanup_config():
    """Check the current service cleanup configuration"""
    try:
        with SessionLocal() as db:
            cfg = get_service_cleanup_config(db)
            
            logger.info(f"🔍 Current service cleanup configuration:")
            logger.info(f"   - Enabled: {cfg.get('enabled', False)}")
            logger.info(f"   - Remove join messages: {cfg.get('remove_on_join', True)}")
            logger.info(f"   - Remove leave messages: {cfg.get('remove_on_leave', True)}")
            logger.info(f"   - Forward target ID: {cfg.get('forward_target_id') or 'Not set'}")
            
            # Check if we need to enable it
            if not cfg.get('enabled', False):
                response = input("❓ Service cleanup is currently disabled. Enable it? (y/n): ")
                if response.lower() in ["y", "yes"]:
                    set_service_cleanup_config(
                        db,
                        enabled=True,
                        remove_on_join=cfg.get('remove_on_join', True),
                        remove_on_leave=cfg.get('remove_on_leave', True),
                        forward_target_id=cfg.get('forward_target_id')
                    )
                    logger.info("✅ Service cleanup has been enabled!")
                    
                    # Double check
                    new_cfg = get_service_cleanup_config(db)
                    if new_cfg.get('enabled', False):
                        logger.info("✅ Verified: Service cleanup is now enabled")
                    else:
                        logger.error("❌ Failed to enable service cleanup. Check database access.")
            else:
                logger.info("✅ Service cleanup is already enabled")
            
            # Check history data
            try:
                history_count = db.query(ServiceMessageSeen).count()
                logger.info(f"   - Recorded service messages: {history_count}")
                
                # Check deleted vs. not deleted
                deleted_count = db.query(ServiceMessageSeen).filter(ServiceMessageSeen.deleted == True).count()
                failed_count = db.query(ServiceMessageSeen).filter(ServiceMessageSeen.deleted == True, 
                                                                   ServiceMessageSeen.delete_ok == False).count()
                
                logger.info(f"   - Messages processed: {deleted_count}")
                if failed_count > 0:
                    logger.info(f"   - Failed deletions: {failed_count}")
                    
                    # Get most common failure reasons
                    error_query = db.query(ServiceMessageSeen.last_error, 
                                          db.func.count(ServiceMessageSeen.id).label('count'))\
                                    .filter(ServiceMessageSeen.last_error != None)\
                                    .group_by(ServiceMessageSeen.last_error)\
                                    .order_by(db.desc('count'))\
                                    .limit(5)
                    
                    error_stats = error_query.all()
                    if error_stats:
                        logger.info(f"   - Common error reasons:")
                        for error, count in error_stats:
                            logger.info(f"     • {error}: {count} occurrences")
            except Exception as e:
                logger.warning(f"Could not check service message history: {e}")
            
            return cfg
    except Exception as e:
        logger.error(f"❌ Error checking service cleanup configuration: {e}")
        return None

async def try_direct_message_delete(token, chat_id, message_id):
    """Try to directly delete a message for testing purposes"""
    try:
        bot = Bot(token=token)
        logger.info(f"🔍 Attempting to delete message {message_id} in chat {chat_id}...")
        
        # Get chat info first
        try:
            chat = await bot.get_chat(chat_id)
            chat_type = chat.type
            logger.info(f"Chat type: {chat_type}")
            
            # Check if admin-only
            if hasattr(chat, "permissions"):
                permissions = chat.permissions
                can_send = getattr(permissions, "can_send_messages", None)
                logger.info(f"can_send_messages permission: {can_send}")
                if can_send is False:
                    logger.info("⚠️ This is an admin-only group - special handling may be needed")
        except Exception as chat_err:
            logger.warning(f"Could not get chat info: {chat_err}")
        
        # Try to delete
        await bot.delete_message(chat_id=chat_id, message_id=message_id)
        logger.info(f"✅ Successfully deleted message {message_id} in chat {chat_id}")
        return True
    except Exception as e:
        logger.error(f"❌ Failed to delete message: {e}")
        
        # Check error details
        error_msg = str(e).lower()
        if "message to delete not found" in error_msg:
            logger.info("The message might have been already deleted or doesn't exist")
        elif "not enough rights" in error_msg or "forbidden" in error_msg:
            logger.info("⚠️ Permission issue: The bot doesn't have rights to delete messages")
            logger.info("🔍 Check that the bot is an admin with 'Delete messages' permission")
        elif "message can't be deleted" in error_msg:
            logger.info("⚠️ The message cannot be deleted. This sometimes happens with:") 
            logger.info("  - Very old messages")
            logger.info("  - Messages in supergroups with specific restrictions")
            logger.info("  - Messages in channels or forums")
        
        return False

async def check_service_messages(token, group_id):
    """Check recent service messages in a specific group"""
    bot = Bot(token=token)
    
    try:
        chat = await bot.get_chat(group_id)
        logger.info(f"📊 Checking service messages in {chat.title} (ID: {group_id})")
        
        with SessionLocal() as db:
            recent_messages = db.query(ServiceMessageSeen)\
                              .filter(ServiceMessageSeen.chat_id == group_id)\
                              .order_by(ServiceMessageSeen.seen_at.desc())\
                              .limit(10)\
                              .all()
            
            if not recent_messages:
                logger.info("No recorded service messages found for this group")
                return
                
            logger.info(f"Found {len(recent_messages)} recent service messages:")
            
            for msg in recent_messages:
                status = "Deleted" if msg.delete_ok else "Failed" if msg.deleted else "Not processed"
                error = f" (Error: {msg.last_error})" if msg.last_error else ""
                logger.info(f"  • Message {msg.message_id}: {status}{error} - {msg.event_type} event at {msg.seen_at}")
    
    except Exception as e:
        logger.error(f"Failed to check service messages: {e}")

async def perform_handler_test(token, chat_id):
    """Simulate a service message handler test"""
    logger.info("🧪 Performing handler simulation test...")
    
    try:
        # This simulates how the handler would process a service message
        # It checks permissions and configuration, but doesn't actually try to delete anything
        
        bot = Bot(token=token)
        
        # Check bot permissions
        bot_info = await bot.get_me()
        bot_id = bot_info.id
        
        # Get chat info
        chat = await bot.get_chat(chat_id)
        chat_type = chat.type
        logger.info(f"Target chat: {chat.title} (type: {chat_type})")
        
        # Permission check
        bot_member = await bot.get_chat_member(chat_id, bot_id)
        status = bot_member.status
        can_delete = getattr(bot_member, "can_delete_messages", False)
        
        logger.info(f"Bot status in chat: {status}")
        logger.info(f"Can delete messages: {can_delete}")
        
        # Check configuration
        with SessionLocal() as db:
            cfg = get_service_cleanup_config(db)
        
        logger.info(f"Service cleanup enabled: {cfg.get('enabled', False)}")
        
        if not cfg.get('enabled', False):
            logger.error("❌ Service cleanup is disabled - it won't process any messages")
            return False
            
        if status not in ["administrator", "creator"]:
            logger.error("❌ Bot is not an admin in the group - cannot delete messages")
            return False
            
        if not can_delete:
            logger.error("❌ Bot does not have 'Delete messages' permission")
            return False
            
        # All checks passed
        logger.info("✅ Handler simulation passed - the bot should be able to delete service messages")
        return True
            
    except Exception as e:
        logger.error(f"❌ Handler test failed: {e}")
        return False

async def main():
    try:
        print("\n" + "="*60)
        print("🔧 SERVICE CLEANUP DIAGNOSTIC TOOL v2.0")
        print("="*60 + "\n")
        
        # Check environment variables
        token = os.environ.get("BOT_TOKEN")
        if not token:
            logger.error("❌ BOT_TOKEN not found in environment variables")
            token = input("Please enter your bot token: ").strip()
            if not token:
                logger.error("❌ No token provided. Exiting.")
                return
        
        # Get VIP_GROUP_IDS
        group_ids = os.environ.get("VIP_GROUP_IDS", "")
        if not group_ids:
            logger.warning("⚠️ VIP_GROUP_IDS not found in environment variables")
            group_ids = input("Please enter group IDs (comma-separated): ").strip()
        
        group_id_list = [gid.strip() for gid in group_ids.split(",") if gid.strip()]
        if not group_id_list:
            logger.error("❌ No group IDs provided. Exiting.")
            return
        
        # 1. Check service cleanup configuration
        print("\n" + "-"*60)
        print("📋 CHECKING SERVICE CLEANUP CONFIGURATION")
        print("-"*60)
        await check_service_cleanup_config()
        
        # 2. Check bot permissions in groups
        print("\n" + "-"*60)
        print("📋 CHECKING BOT PERMISSIONS IN GROUPS")
        print("-"*60)
        permissions = await check_bot_permissions(token, group_id_list)
        
        # 3. Check service messages in each group
        print("\n" + "-"*60)
        print("📋 CHECKING SERVICE MESSAGE HISTORY")
        print("-"*60)
        for group_id in group_id_list:
            await check_service_messages(token, group_id)
        
        # 4. Perform handler simulation
        print("\n" + "-"*60)
        print("📋 PERFORMING HANDLER SIMULATION TEST")
        print("-"*60)
        if group_id_list:
            # Test with first group
            test_group = group_id_list[0]
            await perform_handler_test(token, test_group)
        
        # 5. Report findings and provide recommendations
        print("\n" + "-"*60)
        print("📋 DIAGNOSTIC SUMMARY")
        print("-"*60)
        
        if permissions:
            issues_found = False
            admin_only_groups = []
            
            for chat_id, result in permissions.items():
                if not result["found"]:
                    logger.error(f"❌ Chat {chat_id}: Not found or bot not a member")
                    issues_found = True
                    continue
                
                if "error" in result and result["error"]:
                    logger.error(f"❌ Chat {result.get('name', chat_id)}: Error checking permissions: {result['error']}")
                    issues_found = True
                    continue
                
                if not result.get("is_admin", False):
                    logger.error(f"❌ Chat {result.get('name', chat_id)}: Bot is not an admin (status: {result.get('status')})")
                    issues_found = True
                    continue
                
                if not result.get("can_delete", False):
                    logger.error(f"❌ Chat {result.get('name', chat_id)}: Bot cannot delete messages")
                    issues_found = True
                    continue
                
                # Check for admin-only groups
                if result.get("is_admin_only", False) or result.get("type") == "channel":
                    admin_only_groups.append((chat_id, result.get('name', f"Chat {chat_id}")))
                    logger.info(f"⚠️ Chat {result.get('name', chat_id)}: This is an admin-only group or channel")
                else:
                    logger.info(f"✅ Chat {result.get('name', chat_id)}: Bot has required permissions")
                
            if issues_found:
                print("\n" + "-"*60)
                print("🚨 ISSUES DETECTED - RECOMMENDATIONS")
                print("-"*60)
                print("""
To fix permission issues:
1. Open each problematic group in Telegram
2. Go to group info > Administrators > Add Administrator
3. Add your bot as an administrator
4. Make sure 'Delete messages' permission is enabled
5. Save the changes

After fixing permissions, restart the bot.
                """)
            
            if admin_only_groups:
                print("\n" + "-"*60)
                print("⚠️ ADMIN-ONLY GROUPS DETECTED - SPECIAL NOTICE")
                print("-"*60)
                print(f"""
The following groups are admin-only or channels:
{', '.join(name for _, name in admin_only_groups)}

For admin-only groups or channels:
1. Service message cleanup should work normally but requires special handler priority
2. The bot's handler code has been upgraded to support these special cases
3. If service messages are still not being deleted, contact support
                """)
            
            if not issues_found and not admin_only_groups:
                print("\n" + "-"*60)
                print("✅ NO ISSUES DETECTED")
                print("-"*60)
                print("""
All checks passed successfully! Service message cleanup should work properly.
If you're still experiencing issues, check the logs for any specific error messages
or contact support.
                """)
        
        # Offer direct message deletion test
        print("\n" + "-"*60)
        print("🧪 TEST MESSAGE DELETION")
        print("-"*60)
        test = input("Would you like to test message deletion directly? (y/n): ")
        if test.lower() in ["y", "yes"]:
            chat_id = input("Enter the chat ID: ").strip()
            message_id = input("Enter the message ID to delete: ").strip()
            
            try:
                chat_id = int(chat_id)
                message_id = int(message_id)
                await try_direct_message_delete(token, chat_id, message_id)
            except ValueError:
                logger.error("❌ Invalid chat ID or message ID format")
        
        print("\n" + "-"*60)
        print("🔍 SERVICE CLEANUP TROUBLESHOOTING TIPS")
        print("-"*60)
        print("""
If service messages still aren't being deleted after all checks pass:

1. Make sure the bot was an admin with 'Delete messages' permission BEFORE the message was sent
2. Check logs for any errors containing "service_cleanup"
3. If in admin-only groups, try running this diagnostic tool after seeing a new join/leave
4. Some very old messages might not be deletable due to Telegram API limitations
5. For persisting issues, try reinstating the bot as admin or removing and re-adding it
6. Make sure the bot is running properly and not restarting frequently

For more help, see the SERVICE_CLEANUP_SETUP.md documentation file.
        """)
        
        print("\n" + "="*60)
        print("🏁 DIAGNOSTIC COMPLETE")
        print("="*60)
            
    except Exception as e:
        logger.error(f"❌ Unexpected error: {e}")

if __name__ == "__main__":
    asyncio.run(main())