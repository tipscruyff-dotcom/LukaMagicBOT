#!/usr/bin/env python3
# service_cleanup_logger.py
# Enhanced logging tool for real-time service message cleanup monitoring

import os
import sys
import time
import json
import logging
import argparse
import asyncio
from datetime import datetime, timedelta
from collections import defaultdict

# Setup colorful logging if available
try:
    import coloredlogs
    coloredlogs.install(level='INFO',
                        fmt='%(asctime)s [%(levelname)s] %(message)s',
                        level_styles={'info': {'color': 'green'},
                                     'warning': {'color': 'yellow'},
                                     'error': {'color': 'red', 'bold': True}})
    has_colored_logs = True
except ImportError:
    has_colored_logs = False
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s [%(levelname)s] %(message)s'
    )

logger = logging.getLogger("service-cleanup-logger")

# Check if we're running from the correct directory
try:
    from db import SessionLocal, engine
    from models import ServiceMessageSeen, Chat
    import sqlalchemy as sa
    from sqlalchemy.exc import SQLAlchemyError
except ImportError:
    logger.error("❌ Failed to import required modules. Make sure to run this script from the project root.")
    logger.error("   Try: cd /path/to/project && python tools/service_cleanup_logger.py")
    sys.exit(1)

class CleanupLogger:
    def __init__(self, tail_mode=False):
        self.tail_mode = tail_mode
        self.stats = {
            "total_seen": 0,
            "total_deleted": 0,
            "success_count": 0,
            "error_count": 0,
            "pending_count": 0,
            "chats": defaultdict(int),
            "errors": defaultdict(int),
            "start_time": datetime.utcnow()
        }
        self.latest_id = self._get_latest_record_id()
        
    def _get_latest_record_id(self):
        """Get the ID of the latest record to use as a starting point for tail mode"""
        try:
            with SessionLocal() as db:
                latest_record = db.query(sa.func.max(ServiceMessageSeen.id)).scalar()
                return latest_record if latest_record else 0
        except SQLAlchemyError as e:
            logger.error(f"❌ Database error getting latest record ID: {e}")
            return 0
    
    def _get_chat_name(self, db, chat_id):
        """Get the name of a chat from its ID"""
        try:
            chat = db.query(Chat).filter(Chat.id == chat_id).first()
            if chat:
                return chat.title or f"Chat {chat_id}"
            return f"Chat {chat_id}"
        except SQLAlchemyError:
            return f"Chat {chat_id}"
    
    def _format_message_info(self, message):
        """Format a message for display"""
        status = "⏳ PENDING"
        if message.deleted:
            status = "✅ DELETED" if message.delete_ok else f"❌ FAILED: {message.last_error}"
        
        message_type = "JOIN" if message.is_join else "LEAVE"
        
        return (
            f"ID: {message.id} | "
            f"Type: {message_type} | "
            f"Status: {status} | "
            f"Chat: {message.chat_id} | "
            f"Seen: {message.seen_at.strftime('%Y-%m-%d %H:%M:%S')}"
        )
    
    async def collect_recent_logs(self, minutes=15):
        """Collect logs from recent activity"""
        try:
            threshold_time = datetime.utcnow() - timedelta(minutes=minutes)
            
            with SessionLocal() as db:
                records = db.query(ServiceMessageSeen).filter(
                    ServiceMessageSeen.seen_at >= threshold_time
                ).order_by(ServiceMessageSeen.seen_at.asc()).all()
                
                if not records:
                    logger.info(f"No service messages in the last {minutes} minutes")
                    return []
                
                logger.info(f"Found {len(records)} service messages in the last {minutes} minutes")
                
                formatted_records = []
                for record in records:
                    chat_name = self._get_chat_name(db, record.chat_id)
                    formatted_records.append({
                        "id": record.id,
                        "chat_id": record.chat_id,
                        "chat_name": chat_name,
                        "is_join": record.is_join,
                        "deleted": record.deleted,
                        "delete_ok": record.delete_ok,
                        "last_error": record.last_error,
                        "seen_at": record.seen_at.isoformat(),
                        "message": self._format_message_info(record)
                    })
                
                return formatted_records
                
        except Exception as e:
            logger.error(f"❌ Error collecting recent logs: {e}")
            return []
    
    async def monitor_new_records(self):
        """Monitor for new records in tail mode"""
        logger.info(f"📝 Starting tail mode from record ID: {self.latest_id}")
        logger.info("Press Ctrl+C to stop monitoring")
        
        try:
            while True:
                with SessionLocal() as db:
                    # Get new records since the last check
                    new_records = db.query(ServiceMessageSeen).filter(
                        ServiceMessageSeen.id > self.latest_id
                    ).order_by(ServiceMessageSeen.id.asc()).all()
                    
                    if new_records:
                        for record in new_records:
                            # Update the latest ID
                            if record.id > self.latest_id:
                                self.latest_id = record.id
                                
                            # Update stats
                            self.stats["total_seen"] += 1
                            self.stats["chats"][record.chat_id] += 1
                            
                            if record.deleted:
                                self.stats["total_deleted"] += 1
                                if record.delete_ok:
                                    self.stats["success_count"] += 1
                                else:
                                    self.stats["error_count"] += 1
                                    error_key = record.last_error or "Unknown error"
                                    self.stats["errors"][error_key] += 1
                            else:
                                self.stats["pending_count"] += 1
                            
                            # Get chat name
                            chat_name = self._get_chat_name(db, record.chat_id)
                            
                            # Format message based on type and status
                            message_type = "JOIN" if record.is_join else "LEAVE"
                            
                            # Log with appropriate level and color
                            if not record.deleted:
                                logger.info(f"⏳ {message_type} message seen in {chat_name} (ID: {record.id})")
                            elif record.delete_ok:
                                logger.info(f"✅ {message_type} message deleted from {chat_name} (ID: {record.id})")
                            else:
                                logger.error(f"❌ Failed to delete {message_type} message from {chat_name}: {record.last_error} (ID: {record.id})")
                
                # Sleep briefly to avoid hammering the database
                await asyncio.sleep(2)
                
        except KeyboardInterrupt:
            self.print_stats()
        except Exception as e:
            logger.error(f"❌ Error in monitoring: {e}")
    
    def print_stats(self):
        """Print statistics from monitoring"""
        runtime = datetime.utcnow() - self.stats["start_time"]
        runtime_str = str(runtime).split('.')[0]  # Remove microseconds
        
        success_rate = 0
        if self.stats["total_deleted"] > 0:
            success_rate = self.stats["success_count"] / self.stats["total_deleted"]
        
        print("\n" + "="*60)
        print(f"📊 SERVICE CLEANUP STATISTICS (Runtime: {runtime_str})")
        print("="*60)
        print(f"Total messages seen:    {self.stats['total_seen']}")
        print(f"Total deletion attempts: {self.stats['total_deleted']}")
        print(f"Successful deletions:   {self.stats['success_count']}")
        print(f"Failed deletions:       {self.stats['error_count']}")
        print(f"Pending deletions:      {self.stats['pending_count']}")
        print(f"Success rate:           {success_rate:.2%}")
        
        if self.stats["chats"]:
            print("\n🏢 CHATS:")
            for chat_id, count in sorted(self.stats["chats"].items(), key=lambda x: x[1], reverse=True):
                with SessionLocal() as db:
                    chat_name = self._get_chat_name(db, chat_id)
                print(f"  {chat_name}: {count} messages")
        
        if self.stats["errors"]:
            print("\n❌ ERRORS:")
            for error, count in sorted(self.stats["errors"].items(), key=lambda x: x[1], reverse=True):
                print(f"  {error}: {count} occurrences")
        
        print("="*60)

async def main():
    parser = argparse.ArgumentParser(description="Service message cleanup logging and monitoring tool")
    parser.add_argument("--tail", action="store_true", help="Monitor database for new messages in real-time")
    parser.add_argument("--minutes", type=int, default=15, help="Show messages from the last N minutes (default: 15)")
    parser.add_argument("--output", type=str, help="Save results to JSON file")
    
    args = parser.parse_args()
    
    logger.info("🚀 Service Cleanup Logger starting")
    
    cleanup_logger = CleanupLogger(tail_mode=args.tail)
    
    if args.tail:
        await cleanup_logger.monitor_new_records()
    else:
        recent_logs = await cleanup_logger.collect_recent_logs(minutes=args.minutes)
        
        # Print logs to console
        if recent_logs:
            for log in recent_logs:
                print(log["message"])
        
        # Save to file if requested
        if args.output and recent_logs:
            try:
                with open(args.output, 'w') as f:
                    json.dump(recent_logs, f, indent=2)
                logger.info(f"✅ Logs saved to {args.output}")
            except Exception as e:
                logger.error(f"❌ Failed to save logs: {e}")

if __name__ == "__main__":
    asyncio.run(main())