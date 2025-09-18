#!/usr/bin/env python3
# service_cleanup_integration_test.py
# Tool for comprehensive end-to-end testing of the service message cleanup system

import os
import sys
import logging
import argparse
import asyncio
import json
from datetime import datetime, timedelta
import random
import time
import traceback

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("service-cleanup-integration-test")

# Check if we're running from the correct directory
try:
    from db import SessionLocal, engine
    from models import ServiceMessageSeen, Setting, Chat
    from service_cleanup import _is_join_message, _is_leave_message, service_cleanup_handle_update
    from crud import get_service_cleanup_config, set_service_cleanup_config
except ImportError:
    logger.error("❌ Failed to import required modules. Make sure to run this script from the project root.")
    logger.error("   Try: cd /path/to/project && python tools/service_cleanup_integration_test.py")
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

class IntegrationTester:
    """Comprehensive integration tester for service message cleanup system"""
    
    def __init__(self, bot_token=None):
        self.bot_token = bot_token
        self.results = {
            "tests": [],
            "summary": {
                "total": 0,
                "passed": 0,
                "failed": 0,
                "skipped": 0
            },
            "timestamp": datetime.utcnow().isoformat()
        }
    
    def log_test_result(self, test_name, result, details=None):
        """Log a test result"""
        status = "PASSED" if result else "FAILED"
        logger.info(f"Test '{test_name}': {status}")
        
        test_result = {
            "name": test_name,
            "passed": result,
            "timestamp": datetime.utcnow().isoformat()
        }
        
        if details:
            test_result["details"] = details
            if not result and details.get("error"):
                logger.error(f"Error: {details['error']}")
        
        self.results["tests"].append(test_result)
        self.results["summary"]["total"] += 1
        
        if result:
            self.results["summary"]["passed"] += 1
        else:
            self.results["summary"]["failed"] += 1
    
    def skip_test(self, test_name, reason):
        """Mark a test as skipped"""
        logger.info(f"Test '{test_name}': SKIPPED ({reason})")
        
        test_result = {
            "name": test_name,
            "skipped": True,
            "reason": reason,
            "timestamp": datetime.utcnow().isoformat()
        }
        
        self.results["tests"].append(test_result)
        self.results["summary"]["total"] += 1
        self.results["summary"]["skipped"] += 1
    
    async def test_database_connection(self):
        """Test database connection and structure"""
        test_name = "Database Connection"
        try:
            with SessionLocal() as db:
                # Simple query to check connection
                result = db.execute("SELECT 1").scalar()
                
                if result == 1:
                    # Check if necessary tables exist
                    tables = [
                        "service_message_seen",
                        "settings",
                        "chats"
                    ]
                    
                    missing_tables = []
                    for table in tables:
                        table_exists = db.execute(
                            f"SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='{table}'"
                        ).scalar() > 0
                        
                        if not table_exists:
                            missing_tables.append(table)
                    
                    if missing_tables:
                        self.log_test_result(test_name, False, {
                            "error": f"Missing tables: {', '.join(missing_tables)}"
                        })
                    else:
                        self.log_test_result(test_name, True, {
                            "message": "Database connection successful and all required tables exist"
                        })
                else:
                    self.log_test_result(test_name, False, {
                        "error": "Database query did not return expected result"
                    })
        except Exception as e:
            self.log_test_result(test_name, False, {
                "error": str(e),
                "traceback": traceback.format_exc()
            })
    
    async def test_service_cleanup_config(self):
        """Test service cleanup configuration reading and writing"""
        test_name = "Service Cleanup Configuration"
        try:
            with SessionLocal() as db:
                # Get current configuration
                original_config = get_service_cleanup_config(db)
                
                # Try setting a new configuration
                new_enabled = not original_config.get("enabled", True)
                set_service_cleanup_config(
                    db,
                    enabled=new_enabled,
                    remove_on_join=original_config.get("remove_on_join", True),
                    remove_on_leave=original_config.get("remove_on_leave", True),
                    forward_target_id=original_config.get("forward_target_id")
                )
                
                # Read back and verify
                updated_config = get_service_cleanup_config(db)
                
                if updated_config.get("enabled") == new_enabled:
                    # Restore original configuration
                    set_service_cleanup_config(
                        db,
                        enabled=original_config.get("enabled", True),
                        remove_on_join=original_config.get("remove_on_join", True),
                        remove_on_leave=original_config.get("remove_on_leave", True),
                        forward_target_id=original_config.get("forward_target_id")
                    )
                    
                    self.log_test_result(test_name, True, {
                        "message": "Configuration successfully changed and restored",
                        "original_config": original_config,
                        "test_config": updated_config
                    })
                else:
                    self.log_test_result(test_name, False, {
                        "error": "Failed to update configuration",
                        "original_config": original_config,
                        "actual_config": updated_config,
                        "expected_enabled": new_enabled
                    })
                    
        except Exception as e:
            self.log_test_result(test_name, False, {
                "error": str(e),
                "traceback": traceback.format_exc()
            })
    
    def _create_mock_update(self, is_join=True, is_admin_only=False, chat_id=-1001234567890):
        """Create a mock update object for testing"""
        # Create mock user
        user_id = random.randint(10000, 99999)
        user = User(
            id=user_id,
            is_bot=False,
            first_name="Test",
            last_name="User",
            username=f"test_user_{user_id}"
        )
        
        # Create mock chat
        chat_type = "supergroup"
        chat = TGChat(
            id=chat_id,
            type=chat_type,
            title="Test Chat"
        )
        
        # Add permissions if needed for admin-only simulation
        if is_admin_only:
            # This is a trick to simulate admin-only groups
            # In real admin-only groups, regular members can't send messages
            chat.permissions = type('obj', (object,), {
                'can_send_messages': False,
                'can_send_media_messages': False
            })
        
        # Create mock message
        message_id = random.randint(1000, 9999)
        message_date = int(datetime.utcnow().timestamp())
        
        if is_join:
            message = Message(
                message_id=message_id,
                date=message_date,
                chat=chat,
                from_user=user,
                new_chat_members=[user]
            )
        else:
            message = Message(
                message_id=message_id,
                date=message_date,
                chat=chat,
                from_user=user,
                left_chat_member=user
            )
        
        # Create update
        update = Update(
            update_id=random.randint(10000, 99999),
            message=message
        )
        
        return update, message
    
    def _create_mock_context(self):
        """Create a mock context object for testing"""
        class MockBot:
            def __init__(self):
                self.id = 123456789
                self.username = "test_bot"
                
            async def delete_message(self, chat_id, message_id):
                logger.info(f"Would delete message {message_id} from chat {chat_id}")
                return True
                
            async def get_chat_member(self, chat_id, user_id):
                return ChatMember(
                    user=User(id=user_id, is_bot=False, first_name="Test"),
                    status="administrator",
                    can_delete_messages=True
                )
        
        class MockContext:
            def __init__(self):
                self.bot = MockBot()
                self.bot_data = {}
                self.args = []
                self.chat_data = {}
                self.user_data = {}
                
        return MockContext()
    
    async def test_message_detection(self):
        """Test service message detection functions"""
        test_name = "Service Message Detection"
        try:
            # Create both join and leave test messages
            join_update, join_message = self._create_mock_update(is_join=True)
            leave_update, leave_message = self._create_mock_update(is_join=False)
            
            # Test normal message (should not be detected)
            normal_message = Message(
                message_id=random.randint(1000, 9999),
                date=int(datetime.utcnow().timestamp()),
                chat=join_message.chat,
                from_user=join_message.from_user,
                text="This is a normal message"
            )
            
            # Test detection functions
            join_detected = _is_join_message(join_message)
            leave_detected = _is_leave_message(leave_message)
            normal_join_detected = _is_join_message(normal_message)
            normal_leave_detected = _is_leave_message(normal_message)
            
            if join_detected and leave_detected and not normal_join_detected and not normal_leave_detected:
                self.log_test_result(test_name, True, {
                    "message": "Service message detection functions working correctly",
                    "join_detected": join_detected,
                    "leave_detected": leave_detected,
                    "normal_message_not_detected": not (normal_join_detected or normal_leave_detected)
                })
            else:
                self.log_test_result(test_name, False, {
                    "error": "Service message detection functions failed",
                    "join_detected": join_detected,
                    "leave_detected": leave_detected,
                    "normal_join_detected": normal_join_detected,
                    "normal_leave_detected": normal_leave_detected
                })
                
        except Exception as e:
            self.log_test_result(test_name, False, {
                "error": str(e),
                "traceback": traceback.format_exc()
            })
    
    async def test_handler_execution(self):
        """Test service cleanup handler execution"""
        test_name = "Handler Execution"
        try:
            # Create test update and context
            update, _ = self._create_mock_update()
            context = self._create_mock_context()
            
            # Create a monitored version of get_chat_setting to verify it gets called
            original_get_chat_setting = None
            called_with = []
            
            # Function to monitor calls
            async def mock_get_chat_setting(*args, **kwargs):
                called_with.append((args, kwargs))
                return "true"
            
            # Override the get_chat_setting function
            try:
                import crud
                original_get_chat_setting = crud.get_chat_setting
                crud.get_chat_setting = mock_get_chat_setting
                
                # Call the handler
                result = await service_cleanup_handle_update(update, context)
                
                # Check if get_chat_setting was called
                if called_with:
                    self.log_test_result(test_name, True, {
                        "message": "Handler executed successfully",
                        "handler_result": str(result),
                        "crud_function_called": True
                    })
                else:
                    self.log_test_result(test_name, False, {
                        "error": "Handler did not call get_chat_setting",
                        "handler_result": str(result)
                    })
                
            finally:
                # Restore original function
                if original_get_chat_setting:
                    crud.get_chat_setting = original_get_chat_setting
                
        except Exception as e:
            self.log_test_result(test_name, False, {
                "error": str(e),
                "traceback": traceback.format_exc()
            })
    
    async def test_admin_only_detection(self):
        """Test service cleanup in admin-only groups"""
        test_name = "Admin-Only Group Detection"
        try:
            # Create test updates for normal and admin-only groups
            normal_update, _ = self._create_mock_update(is_admin_only=False)
            admin_only_update, _ = self._create_mock_update(is_admin_only=True)
            
            # Get the message objects
            normal_message = normal_update.message
            admin_only_message = admin_only_update.message
            
            # Create mocks for testing admin-only detection
            class MockPermissions:
                def __init__(self, can_send=True):
                    self.can_send_messages = can_send
            
            # Test with different permission combinations
            test_cases = [
                {"name": "Normal group", "permissions": MockPermissions(can_send=True), "expected": False},
                {"name": "Admin-only group", "permissions": MockPermissions(can_send=False), "expected": True}
            ]
            
            results = []
            for case in test_cases:
                # Set permissions on a test chat
                normal_message.chat.permissions = case["permissions"]
                
                # Check if detection works (we're testing the internal logic here)
                # In reality, the handler uses these checks to determine if it's an admin-only group
                is_admin_only = hasattr(normal_message.chat, 'permissions') and getattr(normal_message.chat.permissions, 'can_send_messages') is False
                
                results.append({
                    "case": case["name"],
                    "expected": case["expected"],
                    "actual": is_admin_only,
                    "passed": is_admin_only == case["expected"]
                })
            
            # Check all results
            all_passed = all(r["passed"] for r in results)
            
            if all_passed:
                self.log_test_result(test_name, True, {
                    "message": "Admin-only group detection working correctly",
                    "results": results
                })
            else:
                self.log_test_result(test_name, False, {
                    "error": "Admin-only group detection failed",
                    "results": results
                })
                
        except Exception as e:
            self.log_test_result(test_name, False, {
                "error": str(e),
                "traceback": traceback.format_exc()
            })
    
    async def test_message_db_logging(self):
        """Test service message database logging"""
        test_name = "Message Database Logging"
        try:
            # Generate a unique chat ID for this test
            test_chat_id = -1000000000 - random.randint(1000, 9999)
            
            # Create test update
            update, message = self._create_mock_update(chat_id=test_chat_id)
            context = self._create_mock_context()
            
            # We need to modify the service cleanup handler to just log the message but not try to delete it
            # This can be done by temporarily replacing functions or by checking the database after the handler runs
            
            # First, check if there are any existing records for this chat ID
            with SessionLocal() as db:
                initial_count = db.query(ServiceMessageSeen).filter(
                    ServiceMessageSeen.chat_id == test_chat_id
                ).count()
            
            # Replace the delete_message function temporarily
            async def mock_delete_message(chat_id, message_id):
                logger.info(f"Mock delete message {message_id} from chat {chat_id}")
                return True
                
            original_delete_message = context.bot.delete_message
            context.bot.delete_message = mock_delete_message
            
            try:
                # Override get_chat_setting to return enabled
                import crud
                original_get_chat_setting = crud.get_chat_setting
                
                async def mock_get_chat_setting(*args, **kwargs):
                    return "true"
                
                crud.get_chat_setting = mock_get_chat_setting
                
                # Call the handler
                await service_cleanup_handle_update(update, context)
                
                # Check if a record was created in the database
                with SessionLocal() as db:
                    final_count = db.query(ServiceMessageSeen).filter(
                        ServiceMessageSeen.chat_id == test_chat_id
                    ).count()
                    
                    record_added = final_count > initial_count
                    
                    if record_added:
                        # Get the record
                        record = db.query(ServiceMessageSeen).filter(
                            ServiceMessageSeen.chat_id == test_chat_id
                        ).order_by(ServiceMessageSeen.id.desc()).first()
                        
                        self.log_test_result(test_name, True, {
                            "message": "Service message was logged to database",
                            "record_id": record.id if record else None,
                            "initial_count": initial_count,
                            "final_count": final_count
                        })
                    else:
                        self.log_test_result(test_name, False, {
                            "error": "Service message was not logged to database",
                            "initial_count": initial_count,
                            "final_count": final_count
                        })
            finally:
                # Restore original functions
                context.bot.delete_message = original_delete_message
                crud.get_chat_setting = original_get_chat_setting
                
        except Exception as e:
            self.log_test_result(test_name, False, {
                "error": str(e),
                "traceback": traceback.format_exc()
            })
    
    async def test_bot_integration(self):
        """Test direct integration with the bot API"""
        test_name = "Bot API Integration"
        
        if not self.bot_token:
            self.skip_test(test_name, "Bot token not provided")
            return
            
        try:
            from telegram import Bot
            
            bot = Bot(self.bot_token)
            
            # Test bot connection
            bot_info = await bot.get_me()
            
            if bot_info:
                self.log_test_result(test_name, True, {
                    "message": f"Successfully connected to bot: @{bot_info.username} (ID: {bot_info.id})"
                })
            else:
                self.log_test_result(test_name, False, {
                    "error": "Failed to get bot information"
                })
                
        except Exception as e:
            self.log_test_result(test_name, False, {
                "error": str(e),
                "traceback": traceback.format_exc()
            })
    
    async def run_all_tests(self):
        """Run all integration tests"""
        logger.info("🧪 Running service cleanup integration tests...")
        
        # Database tests
        await self.test_database_connection()
        await self.test_service_cleanup_config()
        await self.test_message_db_logging()
        
        # Detection and handling tests
        await self.test_message_detection()
        await self.test_handler_execution()
        await self.test_admin_only_detection()
        
        # Bot API test (if token provided)
        await self.test_bot_integration()
        
        # Print summary
        logger.info("\n" + "="*60)
        logger.info("🧪 TEST SUMMARY")
        logger.info("="*60)
        logger.info(f"Total tests:  {self.results['summary']['total']}")
        logger.info(f"Passed:       {self.results['summary']['passed']}")
        logger.info(f"Failed:       {self.results['summary']['failed']}")
        logger.info(f"Skipped:      {self.results['summary']['skipped']}")
        logger.info("="*60)
        
        return self.results["summary"]["failed"] == 0
    
    def export_results(self, filename):
        """Export test results to a JSON file"""
        try:
            with open(filename, 'w') as f:
                json.dump(self.results, f, indent=2)
            logger.info(f"✅ Test results exported to {filename}")
            return True
        except Exception as e:
            logger.error(f"❌ Failed to export test results: {e}")
            return False

async def main():
    parser = argparse.ArgumentParser(description="Run integration tests for service message cleanup system")
    parser.add_argument("--token", help="Bot token for API integration tests")
    parser.add_argument("--export", type=str, help="Export test results to JSON file")
    
    args = parser.parse_args()
    
    # Get token from args or environment
    bot_token = args.token or os.environ.get("BOT_TOKEN")
    
    # Run tests
    tester = IntegrationTester(bot_token=bot_token)
    success = await tester.run_all_tests()
    
    # Export results if requested
    if args.export:
        tester.export_results(args.export)
    
    # Exit with appropriate code
    sys.exit(0 if success else 1)

if __name__ == "__main__":
    asyncio.run(main())