#!/usr/bin/env python3
# service_cleanup_viewer.py
# Interactive visualization tool for service message cleanup data

import os
import sys
import logging
import argparse
import asyncio
from datetime import datetime, timedelta, date
import json

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("service-cleanup-viewer")

# Try to import visualization libraries
try:
    import pandas as pd
    import matplotlib.pyplot as plt
    import matplotlib.dates as mdates
    from matplotlib.ticker import MaxNLocator
    has_visualization = True
except ImportError:
    has_visualization = False
    logger.warning("⚠️ Visualization libraries not found. Install with: pip install pandas matplotlib")

# Check if we're running from the correct directory
try:
    from db import SessionLocal
    from models import ServiceMessageSeen, Chat
    import sqlalchemy as sa
    from sqlalchemy import func, and_, or_, case
except ImportError:
    logger.error("❌ Failed to import required modules. Make sure to run this script from the project root.")
    logger.error("   Try: cd /path/to/project && python tools/service_cleanup_viewer.py")
    sys.exit(1)

class CleanupDataViewer:
    def __init__(self):
        self.data = None
        self.chat_names = {}
        
    async def load_data(self, days=30, chat_id=None):
        """Load cleanup data from database"""
        try:
            logger.info(f"📊 Loading data for the last {days} days...")
            threshold_date = datetime.utcnow() - timedelta(days=days)
            
            with SessionLocal() as db:
                # Build base query
                query = db.query(
                    ServiceMessageSeen.id,
                    ServiceMessageSeen.chat_id,
                    ServiceMessageSeen.is_join,
                    ServiceMessageSeen.seen_at,
                    ServiceMessageSeen.deleted,
                    ServiceMessageSeen.delete_ok,
                    ServiceMessageSeen.last_error,
                    ServiceMessageSeen.delete_after
                ).filter(ServiceMessageSeen.seen_at >= threshold_date)
                
                # Add chat_id filter if provided
                if chat_id:
                    query = query.filter(ServiceMessageSeen.chat_id == chat_id)
                
                # Execute query and convert to list of dicts
                records = query.order_by(ServiceMessageSeen.seen_at.asc()).all()
                
                if not records:
                    logger.warning(f"No data found for the specified period")
                    return False
                    
                # Load chat names
                chat_ids = set(r.chat_id for r in records)
                chats = db.query(Chat.id, Chat.title).filter(Chat.id.in_(chat_ids)).all()
                for chat_id, title in chats:
                    self.chat_names[chat_id] = title or f"Chat {chat_id}"
                
                # Convert to list of dictionaries
                data = []
                for r in records:
                    chat_name = self.chat_names.get(r.chat_id, f"Chat {r.chat_id}")
                    data.append({
                        "id": r.id,
                        "chat_id": r.chat_id,
                        "chat_name": chat_name,
                        "message_type": "JOIN" if r.is_join else "LEAVE",
                        "seen_at": r.seen_at,
                        "date": r.seen_at.date(),
                        "hour": r.seen_at.hour,
                        "deleted": r.deleted,
                        "delete_ok": r.delete_ok,
                        "status": "success" if r.delete_ok else ("failed" if r.deleted else "pending"),
                        "error": r.last_error,
                        "delete_after": r.delete_after
                    })
                
                self.data = data
                logger.info(f"✅ Loaded {len(data)} records")
                return True
                
        except Exception as e:
            logger.error(f"❌ Error loading data: {e}")
            return False
    
    def export_data(self, filename):
        """Export data to JSON file"""
        if not self.data:
            logger.error("❌ No data to export")
            return False
            
        try:
            # Convert datetime objects to strings for JSON serialization
            export_data = []
            for item in self.data:
                export_item = item.copy()
                export_item["seen_at"] = item["seen_at"].isoformat()
                export_item["date"] = str(item["date"])
                export_data.append(export_item)
                
            with open(filename, 'w') as f:
                json.dump(export_data, f, indent=2)
                
            logger.info(f"✅ Data exported to {filename}")
            return True
        except Exception as e:
            logger.error(f"❌ Error exporting data: {e}")
            return False
    
    def print_summary(self):
        """Print summary statistics of loaded data"""
        if not self.data:
            logger.error("❌ No data loaded")
            return
        
        total = len(self.data)
        deleted = sum(1 for r in self.data if r["deleted"])
        success = sum(1 for r in self.data if r["delete_ok"])
        failed = deleted - success
        pending = total - deleted
        
        if deleted > 0:
            success_rate = (success / deleted) * 100
        else:
            success_rate = 0
            
        # Group by chat
        chat_counts = {}
        for r in self.data:
            chat_name = r["chat_name"]
            if chat_name not in chat_counts:
                chat_counts[chat_name] = {"total": 0, "success": 0, "failed": 0, "pending": 0}
                
            chat_counts[chat_name]["total"] += 1
            if r["deleted"]:
                if r["delete_ok"]:
                    chat_counts[chat_name]["success"] += 1
                else:
                    chat_counts[chat_name]["failed"] += 1
            else:
                chat_counts[chat_name]["pending"] += 1
        
        # Group by message type
        join_count = sum(1 for r in self.data if r["message_type"] == "JOIN")
        leave_count = total - join_count
        
        # Group by error type
        error_counts = {}
        for r in self.data:
            if not r["delete_ok"] and r["deleted"] and r["error"]:
                error = r["error"]
                error_counts[error] = error_counts.get(error, 0) + 1
        
        # Print summary
        print("\n" + "="*60)
        print("📊 SERVICE CLEANUP SUMMARY")
        print("="*60)
        print(f"Total records:        {total}")
        print(f"Successful deletions: {success} ({success_rate:.1f}%)")
        print(f"Failed deletions:     {failed}")
        print(f"Pending deletions:    {pending}")
        print(f"JOIN messages:        {join_count}")
        print(f"LEAVE messages:       {leave_count}")
        
        # Print chat summary
        print("\n🏢 BY CHAT:")
        for chat_name, stats in sorted(chat_counts.items(), key=lambda x: x[1]["total"], reverse=True):
            success_pct = 0
            if stats["success"] + stats["failed"] > 0:
                success_pct = (stats["success"] / (stats["success"] + stats["failed"])) * 100
            print(f"  {chat_name}: {stats['total']} msgs, {success_pct:.1f}% success rate")
        
        # Print error summary if there are errors
        if error_counts:
            print("\n❌ TOP ERRORS:")
            for error, count in sorted(error_counts.items(), key=lambda x: x[1], reverse=True)[:5]:
                print(f"  {error}: {count} occurrences")
                
        print("="*60)
    
    def visualize_data(self):
        """Visualize data with matplotlib"""
        if not self.data or not has_visualization:
            if not self.data:
                logger.error("❌ No data loaded")
            elif not has_visualization:
                logger.error("❌ Visualization libraries not available")
            return False
            
        try:
            # Convert to pandas DataFrame for easier manipulation
            df = pd.DataFrame(self.data)
            
            # Create figure with subplots
            fig = plt.figure(figsize=(14, 10))
            fig.suptitle('Service Cleanup Analysis', fontsize=16)
            
            # 1. Messages per day with status breakdown
            ax1 = plt.subplot(2, 2, 1)
            daily_data = df.groupby(['date', 'status']).size().unstack().fillna(0)
            daily_data.plot(kind='bar', stacked=True, ax=ax1, 
                          color={'success': 'green', 'failed': 'red', 'pending': 'gray'})
            ax1.set_title('Messages by Day and Status')
            ax1.set_ylabel('Count')
            ax1.set_xlabel('Date')
            plt.setp(ax1.xaxis.get_majorticklabels(), rotation=45, ha='right')
            ax1.legend(['Success', 'Failed', 'Pending'])
            
            # 2. Success rate over time
            ax2 = plt.subplot(2, 2, 2)
            daily_success = df[df['deleted'] == True].groupby('date').agg(
                success=('delete_ok', 'sum'),
                total=('delete_ok', 'count')
            )
            daily_success['rate'] = daily_success['success'] / daily_success['total'] * 100
            daily_success['rate'].plot(marker='o', ax=ax2)
            ax2.set_title('Success Rate Over Time')
            ax2.set_ylabel('Success Rate (%)')
            ax2.set_xlabel('Date')
            ax2.set_ylim(0, 105)
            ax2.axhline(y=100, color='green', linestyle='--', alpha=0.5)
            ax2.axhline(y=80, color='orange', linestyle='--', alpha=0.5)
            ax2.grid(True, alpha=0.3)
            
            # 3. Message types distribution
            ax3 = plt.subplot(2, 2, 3)
            type_counts = df['message_type'].value_counts()
            ax3.pie(type_counts, labels=type_counts.index, autopct='%1.1f%%', 
                   colors=['skyblue', 'lightgreen'], startangle=90)
            ax3.set_title('Message Type Distribution')
            
            # 4. Top chats by message count
            ax4 = plt.subplot(2, 2, 4)
            chat_counts = df.groupby('chat_name').size().sort_values(ascending=False).head(10)
            chat_counts.plot(kind='barh', ax=ax4)
            ax4.set_title('Top 10 Chats by Message Count')
            ax4.set_xlabel('Message Count')
            
            plt.tight_layout(rect=[0, 0, 1, 0.95])
            plt.show()
            return True
            
        except Exception as e:
            logger.error(f"❌ Error visualizing data: {e}")
            return False
            
    def generate_advanced_reports(self):
        """Generate advanced reports with additional visualizations"""
        if not self.data or not has_visualization:
            return False
            
        try:
            # Convert to pandas DataFrame
            df = pd.DataFrame(self.data)
            
            # 1. Hourly message distribution
            plt.figure(figsize=(12, 6))
            hourly_data = df.groupby('hour').size()
            hourly_data.plot(kind='bar', color='teal')
            plt.title('Hourly Message Distribution')
            plt.xlabel('Hour of Day (UTC)')
            plt.ylabel('Message Count')
            plt.xticks(range(0, 24))
            plt.grid(axis='y', alpha=0.3)
            plt.tight_layout()
            plt.show()
            
            # 2. Chat success rates
            plt.figure(figsize=(12, 8))
            chat_success = df[df['deleted'] == True].groupby('chat_name').agg(
                success=('delete_ok', 'sum'),
                total=('delete_ok', 'count')
            )
            chat_success['rate'] = chat_success['success'] / chat_success['total'] * 100
            chat_success = chat_success.sort_values('rate')
            
            if len(chat_success) > 0:
                ax = chat_success['rate'].plot(kind='barh', color='skyblue')
                ax.set_title('Success Rate by Chat')
                ax.set_xlabel('Success Rate (%)')
                ax.set_xlim(0, 105)
                ax.axvline(x=100, color='green', linestyle='--', alpha=0.5)
                ax.axvline(x=80, color='orange', linestyle='--', alpha=0.5)
                ax.grid(axis='x', alpha=0.3)
                plt.tight_layout()
                plt.show()
                
            # 3. Error distribution
            error_df = df[df['status'] == 'failed']
            if len(error_df) > 0:
                plt.figure(figsize=(12, 6))
                error_counts = error_df['error'].value_counts().head(10)
                error_counts.plot(kind='barh', color='salmon')
                plt.title('Top 10 Error Types')
                plt.xlabel('Occurrences')
                plt.tight_layout()
                plt.show()
            
            return True
            
        except Exception as e:
            logger.error(f"❌ Error generating advanced reports: {e}")
            return False

async def main():
    parser = argparse.ArgumentParser(description="Service message cleanup data visualization tool")
    parser.add_argument("--days", type=int, default=30, help="Number of days to analyze (default: 30)")
    parser.add_argument("--chat", type=int, help="Filter by chat ID")
    parser.add_argument("--export", type=str, help="Export data to JSON file")
    parser.add_argument("--visualize", action="store_true", help="Generate visualizations")
    parser.add_argument("--advanced", action="store_true", help="Generate advanced reports")
    
    args = parser.parse_args()
    
    if not has_visualization and (args.visualize or args.advanced):
        logger.error("❌ Visualization requires pandas and matplotlib. Install with:")
        logger.error("   pip install pandas matplotlib")
        return 1
    
    viewer = CleanupDataViewer()
    success = await viewer.load_data(days=args.days, chat_id=args.chat)
    
    if not success:
        return 1
    
    viewer.print_summary()
    
    if args.export:
        viewer.export_data(args.export)
        
    if args.visualize:
        viewer.visualize_data()
        
    if args.advanced:
        viewer.generate_advanced_reports()
    
    return 0

if __name__ == "__main__":
    sys.exit(asyncio.run(main()))