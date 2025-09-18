#!/usr/bin/env python3
# service_cleanup_stats.py
# Tool to collect and visualize statistics about the service message cleanup system

import os
import sys
import logging
from datetime import datetime, timedelta
import asyncio
import argparse
import json
from tabulate import tabulate

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("service-cleanup-stats")

# Check if we're running from the correct directory
try:
    from db import SessionLocal
    from models import ServiceMessageSeen
except ImportError:
    logger.error("❌ Failed to import required modules. Make sure to run this script from the project root.")
    logger.error("   Try: cd /path/to/project && python tools/service_cleanup_stats.py")
    sys.exit(1)

def format_duration(seconds):
    """Format seconds into a human-readable duration string"""
    if seconds < 60:
        return f"{seconds:.1f} seconds"
    elif seconds < 3600:
        return f"{seconds / 60:.1f} minutes"
    elif seconds < 86400:
        return f"{seconds / 3600:.1f} hours"
    else:
        return f"{seconds / 86400:.1f} days"

async def collect_statistics(days_back=7, export_path=None):
    """Collect statistics about service message cleanup operations"""
    try:
        with SessionLocal() as db:
            # Calculate the start date
            end_date = datetime.utcnow()
            start_date = end_date - timedelta(days=days_back)
            
            # Get total count
            total_count = db.query(ServiceMessageSeen).count()
            
            # Get count within date range
            period_count = db.query(ServiceMessageSeen).filter(
                ServiceMessageSeen.seen_at >= start_date
            ).count()
            
            # Get success rate
            deleted_count = db.query(ServiceMessageSeen).filter(
                ServiceMessageSeen.deleted == True,
                ServiceMessageSeen.delete_ok == True
            ).count()
            
            failed_count = db.query(ServiceMessageSeen).filter(
                ServiceMessageSeen.deleted == True,
                ServiceMessageSeen.delete_ok == False
            ).count()
            
            pending_count = db.query(ServiceMessageSeen).filter(
                ServiceMessageSeen.deleted == False
            ).count()
            
            # Calculate success rate if applicable
            success_rate = 0
            if deleted_count + failed_count > 0:
                success_rate = (deleted_count / (deleted_count + failed_count)) * 100
                
            # Get event type distribution
            join_count = db.query(ServiceMessageSeen).filter(
                ServiceMessageSeen.event_type == "join"
            ).count()
            
            leave_count = db.query(ServiceMessageSeen).filter(
                ServiceMessageSeen.event_type == "leave"
            ).count()
            
            other_count = total_count - join_count - leave_count
            
            # Get error distribution
            error_distribution = {}
            if failed_count > 0:
                error_results = db.query(
                    ServiceMessageSeen.last_error, 
                    db.func.count(ServiceMessageSeen.id)
                ).filter(
                    ServiceMessageSeen.deleted == True,
                    ServiceMessageSeen.delete_ok == False,
                    ServiceMessageSeen.last_error != None
                ).group_by(ServiceMessageSeen.last_error).all()
                
                for error, count in error_results:
                    error_distribution[error or "unknown"] = count
            
            # Get chat distribution (top 10 chats)
            chat_distribution = {}
            chat_results = db.query(
                ServiceMessageSeen.chat_id, 
                db.func.count(ServiceMessageSeen.id)
            ).group_by(ServiceMessageSeen.chat_id).order_by(
                db.func.count(ServiceMessageSeen.id).desc()
            ).limit(10).all()
            
            for chat_id, count in chat_results:
                chat_distribution[str(chat_id)] = count
            
            # Get recent activity
            recent_activity = []
            daily_activity = {}
            
            for days in range(days_back):
                day_date = end_date - timedelta(days=days)
                day_start = day_date.replace(hour=0, minute=0, second=0, microsecond=0)
                day_end = day_date.replace(hour=23, minute=59, second=59, microsecond=999999)
                
                day_str = day_start.strftime("%Y-%m-%d")
                
                day_count = db.query(ServiceMessageSeen).filter(
                    ServiceMessageSeen.seen_at >= day_start,
                    ServiceMessageSeen.seen_at <= day_end
                ).count()
                
                day_success = db.query(ServiceMessageSeen).filter(
                    ServiceMessageSeen.seen_at >= day_start,
                    ServiceMessageSeen.seen_at <= day_end,
                    ServiceMessageSeen.deleted == True,
                    ServiceMessageSeen.delete_ok == True
                ).count()
                
                day_failed = db.query(ServiceMessageSeen).filter(
                    ServiceMessageSeen.seen_at >= day_start,
                    ServiceMessageSeen.seen_at <= day_end,
                    ServiceMessageSeen.deleted == True,
                    ServiceMessageSeen.delete_ok == False
                ).count()
                
                daily_activity[day_str] = {
                    "date": day_str,
                    "total": day_count,
                    "success": day_success,
                    "failed": day_failed
                }
                
                recent_activity.append((day_str, day_count, day_success, day_failed))
            
            # Compile all statistics
            statistics = {
                "collection_time": end_date.isoformat(),
                "period_days": days_back,
                "period_start": start_date.isoformat(),
                "period_end": end_date.isoformat(),
                "total_records": total_count,
                "period_records": period_count,
                "status_summary": {
                    "deleted_successfully": deleted_count,
                    "deletion_failed": failed_count,
                    "pending_deletion": pending_count,
                    "success_rate_percent": round(success_rate, 2)
                },
                "event_types": {
                    "join": join_count,
                    "leave": leave_count,
                    "other": other_count
                },
                "error_distribution": error_distribution,
                "chat_distribution": chat_distribution,
                "daily_activity": daily_activity
            }
            
            # Export to file if requested
            if export_path:
                try:
                    with open(export_path, 'w') as f:
                        json.dump(statistics, f, indent=2)
                    logger.info(f"✅ Statistics exported to {export_path}")
                except Exception as e:
                    logger.error(f"❌ Failed to export statistics: {e}")
            
            return statistics, recent_activity
            
    except Exception as e:
        logger.error(f"❌ Failed to collect statistics: {e}")
        return None, None

def print_statistics_summary(statistics, recent_activity):
    """Print a summary of the collected statistics"""
    if not statistics:
        logger.error("No statistics available to display")
        return
    
    # Print overall summary
    print("\n" + "="*60)
    print(f"📊 SERVICE CLEANUP STATISTICS")
    print("="*60)
    
    print(f"\n📆 Period: {statistics['period_start']} to {statistics['period_end']} ({statistics['period_days']} days)")
    print(f"📚 Total records: {statistics['total_records']} (in period: {statistics['period_records']})")
    
    # Print status summary
    print("\n" + "-"*60)
    print(f"🔄 STATUS SUMMARY")
    print("-"*60)
    
    status_data = [
        ["Deleted successfully", statistics['status_summary']['deleted_successfully']],
        ["Deletion failed", statistics['status_summary']['deletion_failed']],
        ["Pending deletion", statistics['status_summary']['pending_deletion']],
        ["Success rate", f"{statistics['status_summary']['success_rate_percent']}%"]
    ]
    
    print(tabulate(status_data, headers=["Status", "Count"], tablefmt="simple"))
    
    # Print event type distribution
    print("\n" + "-"*60)
    print(f"🔄 EVENT TYPE DISTRIBUTION")
    print("-"*60)
    
    event_data = [
        ["Join messages", statistics['event_types']['join']],
        ["Leave messages", statistics['event_types']['leave']],
        ["Other", statistics['event_types']['other']]
    ]
    
    print(tabulate(event_data, headers=["Event Type", "Count"], tablefmt="simple"))
    
    # Print error distribution
    if statistics['error_distribution']:
        print("\n" + "-"*60)
        print(f"❌ ERROR DISTRIBUTION")
        print("-"*60)
        
        error_data = [[error, count] for error, count in statistics['error_distribution'].items()]
        print(tabulate(error_data, headers=["Error Type", "Count"], tablefmt="simple"))
    
    # Print chat distribution
    if statistics['chat_distribution']:
        print("\n" + "-"*60)
        print(f"💬 TOP CHATS (by message count)")
        print("-"*60)
        
        chat_data = [[chat_id, count] for chat_id, count in statistics['chat_distribution'].items()]
        print(tabulate(chat_data, headers=["Chat ID", "Message Count"], tablefmt="simple"))
    
    # Print recent activity
    if recent_activity:
        print("\n" + "-"*60)
        print(f"📅 DAILY ACTIVITY")
        print("-"*60)
        
        headers = ["Date", "Total", "Success", "Failed"]
        print(tabulate(recent_activity, headers=headers, tablefmt="simple"))
    
    print("\n" + "="*60)

async def main():
    parser = argparse.ArgumentParser(description="Collect and display statistics about service message cleanup")
    parser.add_argument("--days", type=int, default=7, help="Number of days to analyze (default: 7)")
    parser.add_argument("--export", type=str, help="Export statistics to JSON file (provide filename)")
    
    args = parser.parse_args()
    
    statistics, recent_activity = await collect_statistics(days_back=args.days, export_path=args.export)
    print_statistics_summary(statistics, recent_activity)

if __name__ == "__main__":
    try:
        import tabulate
    except ImportError:
        logger.error("❌ Missing dependency: tabulate")
        logger.error("   Install with: pip install tabulate")
        sys.exit(1)
        
    asyncio.run(main())