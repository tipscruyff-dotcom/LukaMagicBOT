#!/usr/bin/env python3
# service_cleanup_simulator.py
# Tool for simulating service messages to test cleanup functionality

import os
import sys
import logging
import argparse
import asyncio
import json
import random
from datetime import datetime
from typing import Optional, Dict, Any, List, Tuple
import uuid

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("service-cleanup-simulator")

# Check if we're running from the correct directory
try:
    # Import internal modules
    from db import SessionLocal
    import models
    import crud
    from service_cleanup import (
        _is_join_message,
        _is_leave_message,
        service_cleanup_handle_update
    )
except ImportError:
    logger.error("❌ Failed to import required modules. Make sure to run this script from the project root.")
    logger.error("   Try: cd /path/to/project && python tools/service_cleanup_simulator.py")
    sys.exit(1)

# Try to import python-telegram-bot
try:
    from telegram import Update, Chat as TGChat, Message, User, ChatMember
    from telegram.ext import CallbackContext
    import telegram.error
except ImportError:
    logger.error("❌ Failed to import python-telegram-bot. Please install it with:")
    logger.error("   pip install python-telegram-bot==13.7")  # Using version 13.7 for compatibility
    sys.exit(1)

class ServiceMessageSimulator:
    """Simulates service messages and tests cleanup functionality"""
    
    def __init__(self):
        self.results = {
            "simulations": [],
            "stats": {
                "total": 0,
                "detected_as_service": 0,
                "would_be_deleted": 0,
                "detection_failures": 0
            }
        }
        
    def _create_mock_user(self, user_id: int, is_bot: bool = False, 
                          first_name: str = None, last_name: str = None, 
                          username: str = None) -> User:
        """Create a mock Telegram User object"""
        if not first_name:
            first_name = f"User{user_id}"
        
        return User(
            id=user_id,
            is_bot=is_bot,
            first_name=first_name,
            last_name=last_name,
            username=username,
            language_code="en"
        )
    
    def _create_mock_chat(self, chat_id: int, chat_type: str, 
                          title: str = None) -> TGChat:
        """Create a mock Telegram Chat object"""
        if not title and chat_type in ["group", "supergroup", "channel"]:
            title = f"Test Chat {chat_id}"
            
        return TGChat(
            id=chat_id,
            type=chat_type,
            title=title,
            username=None,
            first_name=None if chat_type != "private" else "User",
            last_name=None
        )
    
    def _create_mock_message(self, message_id: int, chat: TGChat, from_user: User = None,
                             text: str = None, new_chat_members: List[User] = None,
                             left_chat_member: User = None, service_msg_type: str = None) -> Message:
        """Create a mock Telegram Message object"""
        # Set default values
        date = int(datetime.now().timestamp())
        
        # Define message parameters based on service type
        if service_msg_type == "join" and not new_chat_members:
            new_chat_members = [self._create_mock_user(random.randint(10000, 99999))]
            
        if service_msg_type == "leave" and not left_chat_member:
            left_chat_member = self._create_mock_user(random.randint(10000, 99999))
        
        # Create the message
        message = Message(
            message_id=message_id,
            date=date,
            chat=chat,
            from_user=from_user,
            text=text,
            new_chat_members=new_chat_members,
            left_chat_member=left_chat_member
        )
        
        return message
    
    def _create_mock_update(self, update_id: int, message: Message = None) -> Update:
        """Create a mock Telegram Update object"""
        return Update(update_id=update_id, message=message)
    
    def _create_mock_context(self, bot_data: Dict = None) -> CallbackContext:
        """Create a mock CallbackContext object"""
        
        class MockBot:
            def __init__(self):
                self.id = 123456789
                self.username = "test_bot"
                self.first_name = "Test Bot"
                
            async def delete_message(self, chat_id, message_id):
                logger.info(f"🗑️ Would delete message {message_id} from chat {chat_id}")
                return True
                
            async def get_chat_member(self, chat_id, user_id):
                # Simulate different permissions
                status = random.choice(["creator", "administrator", "member"])
                return ChatMember(
                    user=self._create_mock_user(user_id),
                    status=status,
                    is_anonymous=False,
                    can_be_edited=False,
                    can_change_info=status in ["creator", "administrator"],
                    can_delete_messages=status in ["creator", "administrator"],
                    can_invite_users=status in ["creator", "administrator"],
                    can_restrict_members=status in ["creator", "administrator"],
                    can_pin_messages=status in ["creator", "administrator"],
                    can_promote_members=status == "creator"
                )
        
        class MockContext:
            def __init__(self, bot_data=None):
                self.bot = MockBot()
                self.bot_data = bot_data or {}
                self.args = []
                self.chat_data = {}
                self.user_data = {}
                
        return MockContext(bot_data=bot_data)
    
    async def check_message_detection(self, message: Message) -> Tuple[bool, bool]:
        """Test if a message would be detected as a service message"""
        is_join = _is_join_message(message)
        is_leave = _is_leave_message(message)
        return (is_join or is_leave), (is_join, is_leave)
    
    async def run_simulation(self, template: str, chat_type: str = "supergroup", 
                           num_messages: int = 10, chat_id: int = None) -> Dict:
        """Run a simulation with the specified template and parameters"""
        if not chat_id:
            chat_id = random.randint(-1000000, -1000)
            
        logger.info(f"🧪 Running simulation with template: {template}, chat type: {chat_type}")
        
        # Create mock chat
        chat = self._create_mock_chat(chat_id, chat_type)
        
        # Initialize counts
        detected = 0
        would_delete = 0
        
        # Process messages
        for i in range(num_messages):
            # Create a message based on the template
            if template == "join_standard":
                user = self._create_mock_user(random.randint(10000, 99999))
                message = self._create_mock_message(
                    message_id=i+1,
                    chat=chat,
                    new_chat_members=[user],
                    service_msg_type="join"
                )
            elif template == "join_custom_names":
                # Create users with different name formats
                name_formats = [
                    {"first": "John", "last": "Doe"},  # Standard Western name
                    {"first": "李", "last": "小龙"},    # Chinese name
                    {"first": "محمد", "last": "عبدالله"}, # Arabic name
                    {"first": "John-Paul", "last": "O'Malley-Smith"}, # Name with special characters
                    {"first": "💻", "last": "🤖"},     # Emoji name
                    {"first": "x"*32, "last": ""},     # Long name
                    {"first": "A", "last": ""},        # Single letter name
                    {"first": "  John  ", "last": "Doe  "}, # Name with spaces
                ]
                name = random.choice(name_formats)
                user = self._create_mock_user(
                    user_id=random.randint(10000, 99999),
                    first_name=name["first"],
                    last_name=name["last"]
                )
                message = self._create_mock_message(
                    message_id=i+1,
                    chat=chat,
                    new_chat_members=[user],
                    service_msg_type="join"
                )
            elif template == "leave_standard":
                user = self._create_mock_user(random.randint(10000, 99999))
                message = self._create_mock_message(
                    message_id=i+1,
                    chat=chat,
                    left_chat_member=user,
                    service_msg_type="leave"
                )
            elif template == "multiple_joins":
                # Create 2-5 users joining at once
                num_users = random.randint(2, 5)
                users = [self._create_mock_user(random.randint(10000, 99999)) for _ in range(num_users)]
                message = self._create_mock_message(
                    message_id=i+1,
                    chat=chat,
                    new_chat_members=users,
                    service_msg_type="join"
                )
            elif template == "non_service":
                # Create a regular text message
                user = self._create_mock_user(random.randint(10000, 99999))
                message = self._create_mock_message(
                    message_id=i+1,
                    chat=chat,
                    from_user=user,
                    text=random.choice([
                        "Hello everyone!",
                        "How are you doing?",
                        "This is a test message",
                        "Good morning!",
                        "/start",
                        "👋 Hi there!"
                    ])
                )
            else:
                logger.error(f"❌ Unknown template: {template}")
                continue
            
            # Create update from message
            update = self._create_mock_update(update_id=i+1000, message=message)
            
            # Test if it's detected as a service message
            is_service, details = await self.check_message_detection(message)
            
            # Record the result
            result = {
                "update_id": update.update_id,
                "message_id": message.message_id,
                "chat_id": chat.id,
                "chat_type": chat.type,
                "template": template,
                "is_detected_as_service": is_service,
                "is_join": details[0],
                "is_leave": details[1],
                "would_delete": False,  # Will be updated if we run through handler
                "error": None
            }
            
            # Only try running the handler if it was detected as a service message
            if is_service:
                detected += 1
                
                # Create a context
                context = self._create_mock_context()
                
                # Make DB settings return service cleanup enabled
                async def mock_get_chat_setting(*args, **kwargs):
                    return "true"
                
                # Monkey patch crud.get_chat_setting
                original_get_chat_setting = crud.get_chat_setting
                crud.get_chat_setting = mock_get_chat_setting
                
                try:
                    # Call the service cleanup handler
                    handler_result = await service_cleanup_handle_update(update, context)
                    result["would_delete"] = handler_result is not None
                    if result["would_delete"]:
                        would_delete += 1
                except Exception as e:
                    result["error"] = str(e)
                    logger.error(f"❌ Error processing update: {e}")
                finally:
                    # Restore original function
                    crud.get_chat_setting = original_get_chat_setting
            
            # Add the result to our collection
            self.results["simulations"].append(result)
        
        # Update stats
        simulation_stats = {
            "template": template,
            "chat_type": chat_type,
            "chat_id": chat_id,
            "messages_generated": num_messages,
            "detected_as_service": detected,
            "would_be_deleted": would_delete,
            "detection_rate": detected / num_messages if num_messages > 0 else 0,
            "deletion_rate": would_delete / detected if detected > 0 else 0
        }
        
        # Update overall stats
        self.results["stats"]["total"] += num_messages
        self.results["stats"]["detected_as_service"] += detected
        self.results["stats"]["would_be_deleted"] += would_delete
        self.results["stats"]["detection_failures"] += (detected - would_delete)
        
        logger.info(f"✅ Simulation completed: {detected}/{num_messages} detected, {would_delete}/{detected} would be deleted")
        return simulation_stats
    
    async def run_all_simulations(self, num_messages: int = 10, chat_types: List[str] = None):
        """Run simulations with all available templates"""
        if chat_types is None:
            chat_types = ["supergroup", "group"]
            
        templates = [
            "join_standard",
            "join_custom_names",
            "leave_standard",
            "multiple_joins",
            "non_service"
        ]
        
        results = []
        for template in templates:
            for chat_type in chat_types:
                stats = await self.run_simulation(
                    template=template,
                    chat_type=chat_type,
                    num_messages=num_messages
                )
                results.append(stats)
        
        return results
        
    def print_summary(self):
        """Print a summary of the simulation results"""
        stats = self.results["stats"]
        
        # Calculate rates
        detection_rate = stats["detected_as_service"] / stats["total"] if stats["total"] > 0 else 0
        success_rate = stats["would_be_deleted"] / stats["detected_as_service"] if stats["detected_as_service"] > 0 else 0
        
        print("\n" + "="*60)
        print("🧪 SERVICE CLEANUP SIMULATION SUMMARY")
        print("="*60)
        print(f"Total messages simulated: {stats['total']}")
        print(f"Detected as service:      {stats['detected_as_service']} ({detection_rate:.1%})")
        print(f"Would be deleted:         {stats['would_be_deleted']} ({success_rate:.1%})")
        print(f"Detection failures:       {stats['detection_failures']}")
        
        # Group by template
        template_stats = {}
        for result in self.results["simulations"]:
            template = result["template"]
            if template not in template_stats:
                template_stats[template] = {
                    "total": 0,
                    "detected": 0,
                    "would_delete": 0,
                    "errors": 0
                }
            
            template_stats[template]["total"] += 1
            if result["is_detected_as_service"]:
                template_stats[template]["detected"] += 1
            if result["would_delete"]:
                template_stats[template]["would_delete"] += 1
            if result["error"]:
                template_stats[template]["errors"] += 1
        
        print("\n📊 BY TEMPLATE:")
        for template, stats in template_stats.items():
            detection_rate = stats["detected"] / stats["total"] if stats["total"] > 0 else 0
            success_rate = stats["would_delete"] / stats["detected"] if stats["detected"] > 0 else 0
            
            print(f"  {template}:")
            print(f"    Detection: {stats['detected']}/{stats['total']} ({detection_rate:.1%})")
            print(f"    Deletion:  {stats['would_delete']}/{stats['detected']} ({success_rate:.1%})")
            if stats["errors"] > 0:
                print(f"    Errors:    {stats['errors']}")
        
        print("="*60)
    
    def export_results(self, filename):
        """Export simulation results to a JSON file"""
        try:
            # Convert datetime objects to strings
            results_copy = self.results.copy()
            
            with open(filename, 'w') as f:
                json.dump(results_copy, f, indent=2)
                
            logger.info(f"✅ Results exported to {filename}")
            return True
        except Exception as e:
            logger.error(f"❌ Error exporting results: {e}")
            return False

async def main():
    parser = argparse.ArgumentParser(description="Service message cleanup simulation tool")
    parser.add_argument("--messages", type=int, default=10, 
                        help="Number of messages to generate per template (default: 10)")
    parser.add_argument("--template", type=str, 
                        choices=["join_standard", "join_custom_names", "leave_standard", 
                                 "multiple_joins", "non_service", "all"],
                        default="all", help="Message template to use")
    parser.add_argument("--chat-type", type=str, 
                        choices=["group", "supergroup", "all"],
                        default="all", help="Chat type to simulate")
    parser.add_argument("--export", type=str, help="Export results to JSON file")
    
    args = parser.parse_args()
    
    simulator = ServiceMessageSimulator()
    
    # Determine which chat types to use
    chat_types = ["group", "supergroup"] if args.chat_type == "all" else [args.chat_type]
    
    # Run simulations
    if args.template == "all":
        await simulator.run_all_simulations(num_messages=args.messages, chat_types=chat_types)
    else:
        for chat_type in chat_types:
            await simulator.run_simulation(
                template=args.template,
                chat_type=chat_type,
                num_messages=args.messages
            )
    
    # Print summary
    simulator.print_summary()
    
    # Export results if requested
    if args.export:
        simulator.export_results(args.export)

if __name__ == "__main__":
    asyncio.run(main())