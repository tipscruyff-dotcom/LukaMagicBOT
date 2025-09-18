#!/usr/bin/env python3
# service_cleanup_monitor.py
# Tool for periodic monitoring of service message cleanup system

import os
import sys
import logging
import argparse
import asyncio
import json
import time
from datetime import datetime, timedelta
import smtplib
from email.message import EmailMessage

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("service-cleanup-monitor")

# Check if we're running from the correct directory
try:
    from db import SessionLocal
    from models import ServiceMessageSeen
except ImportError:
    logger.error("❌ Failed to import required modules. Make sure to run this script from the project root.")
    logger.error("   Try: cd /path/to/project && python tools/service_cleanup_monitor.py")
    sys.exit(1)

async def check_message_status(hours=24, alert_threshold=0.8):
    """
    Check the status of service message cleanup in the last X hours
    
    Args:
        hours: Number of hours to look back
        alert_threshold: Success rate threshold below which to trigger an alert (0.0-1.0)
        
    Returns:
        Tuple of (status_ok, details_dict)
    """
    try:
        with SessionLocal() as db:
            # Calculate the time threshold
            threshold_time = datetime.utcnow() - timedelta(hours=hours)
            
            # Get all service messages in the time period
            total_count = db.query(ServiceMessageSeen).filter(
                ServiceMessageSeen.seen_at >= threshold_time
            ).count()
            
            if total_count == 0:
                logger.info(f"No service messages seen in the last {hours} hours")
                return True, {
                    "status": "ok",
                    "message": f"No service messages seen in the last {hours} hours",
                    "period_hours": hours,
                    "records_found": 0
                }
            
            # Get successful deletions
            success_count = db.query(ServiceMessageSeen).filter(
                ServiceMessageSeen.seen_at >= threshold_time,
                ServiceMessageSeen.deleted == True,
                ServiceMessageSeen.delete_ok == True
            ).count()
            
            # Get failed deletions
            failed_count = db.query(ServiceMessageSeen).filter(
                ServiceMessageSeen.seen_at >= threshold_time,
                ServiceMessageSeen.deleted == True,
                ServiceMessageSeen.delete_ok == False
            ).count()
            
            # Get pending deletions
            pending_count = db.query(ServiceMessageSeen).filter(
                ServiceMessageSeen.seen_at >= threshold_time,
                ServiceMessageSeen.deleted == False
            ).count()
            
            # Calculate success rate
            processed_count = success_count + failed_count
            success_rate = 0
            if processed_count > 0:
                success_rate = success_count / processed_count
            
            # Get error details
            error_details = {}
            if failed_count > 0:
                error_results = db.query(
                    ServiceMessageSeen.last_error,
                    db.func.count(ServiceMessageSeen.id)
                ).filter(
                    ServiceMessageSeen.seen_at >= threshold_time,
                    ServiceMessageSeen.deleted == True,
                    ServiceMessageSeen.delete_ok == False
                ).group_by(ServiceMessageSeen.last_error).all()
                
                for error, count in error_results:
                    error_details[error or "unknown"] = count
            
            # Determine status
            status_ok = success_rate >= alert_threshold
            status = "ok" if status_ok else "alert"
            
            details = {
                "status": status,
                "period_hours": hours,
                "records_found": total_count,
                "success_count": success_count,
                "failed_count": failed_count,
                "pending_count": pending_count,
                "success_rate": round(success_rate, 4),
                "threshold": alert_threshold,
                "error_details": error_details,
                "timestamp": datetime.utcnow().isoformat()
            }
            
            if not status_ok:
                message = f"⚠️ Service cleanup success rate ({success_rate:.2%}) below threshold ({alert_threshold:.2%})!"
                details["message"] = message
                logger.warning(message)
            else:
                message = f"✅ Service cleanup success rate ({success_rate:.2%}) above threshold ({alert_threshold:.2%})"
                details["message"] = message
                logger.info(message)
            
            return status_ok, details
            
    except Exception as e:
        logger.error(f"❌ Error checking message status: {e}")
        return False, {
            "status": "error",
            "message": f"Error checking message status: {str(e)}",
            "timestamp": datetime.utcnow().isoformat()
        }

def send_alert_email(smtp_config, details):
    """Send an alert email with monitoring results"""
    try:
        host = smtp_config.get("host")
        port = smtp_config.get("port", 587)
        username = smtp_config.get("username")
        password = smtp_config.get("password")
        from_email = smtp_config.get("from_email")
        to_emails = smtp_config.get("to_emails", [])
        
        if not all([host, username, password, from_email, to_emails]):
            logger.error("❌ Incomplete SMTP configuration")
            return False
        
        # Create message
        msg = EmailMessage()
        msg['Subject'] = f"⚠️ Service Cleanup Alert: {details['message']}"
        msg['From'] = from_email
        msg['To'] = ", ".join(to_emails)
        
        # Create email body
        body = f"""
Service Cleanup Monitoring Alert

Status: {details['status'].upper()}
Message: {details['message']}
Time: {details['timestamp']}

Period: Last {details['period_hours']} hours
Success rate: {details['success_rate'] * 100:.2f}% (Threshold: {details['threshold'] * 100:.2f}%)

Stats:
- Total records: {details['records_found']}
- Successful: {details['success_count']}
- Failed: {details['failed_count']}
- Pending: {details['pending_count']}

"""
        
        # Add error details if available
        if details.get('error_details'):
            body += "Error breakdown:\n"
            for error, count in details['error_details'].items():
                body += f"- {error}: {count}\n"
        
        msg.set_content(body)
        
        # Send email
        with smtplib.SMTP(host, port) as server:
            server.starttls()
            server.login(username, password)
            server.send_message(msg)
            
        logger.info(f"✅ Alert email sent to {', '.join(to_emails)}")
        return True
        
    except Exception as e:
        logger.error(f"❌ Failed to send alert email: {e}")
        return False

async def run_monitor(args):
    """Run the monitor with the specified arguments"""
    status_ok, details = await check_message_status(
        hours=args.hours, 
        alert_threshold=args.threshold
    )
    
    # Save results to file if requested
    if args.output:
        try:
            with open(args.output, 'w') as f:
                json.dump(details, f, indent=2)
            logger.info(f"✅ Results saved to {args.output}")
        except Exception as e:
            logger.error(f"❌ Failed to save results: {e}")
    
    # Send email alert if configured and status is not OK
    if not status_ok and args.alert_email:
        smtp_config = {
            "host": os.environ.get("SMTP_HOST"),
            "port": int(os.environ.get("SMTP_PORT", "587")),
            "username": os.environ.get("SMTP_USERNAME"),
            "password": os.environ.get("SMTP_PASSWORD"),
            "from_email": os.environ.get("SMTP_FROM"),
            "to_emails": args.email_to.split(",") if args.email_to else []
        }
        
        send_alert_email(smtp_config, details)
    
    # Exit with appropriate status code
    return 0 if status_ok else 1

async def continuous_monitoring(args):
    """Run monitoring in a continuous loop"""
    logger.info(f"📊 Starting continuous monitoring (interval: {args.interval} seconds)")
    
    while True:
        try:
            start_time = time.time()
            await run_monitor(args)
            
            # Calculate sleep time (to maintain consistent interval)
            elapsed = time.time() - start_time
            sleep_time = max(1, args.interval - elapsed)
            
            logger.info(f"💤 Next check in {sleep_time:.1f} seconds")
            await asyncio.sleep(sleep_time)
            
        except KeyboardInterrupt:
            logger.info("👋 Monitoring stopped by user")
            break
        except Exception as e:
            logger.error(f"❌ Error in monitoring loop: {e}")
            await asyncio.sleep(args.interval)

async def main():
    parser = argparse.ArgumentParser(description="Monitor service message cleanup system")
    parser.add_argument("--hours", type=int, default=24, help="Hours to look back for messages (default: 24)")
    parser.add_argument("--threshold", type=float, default=0.8, help="Success rate threshold (0.0-1.0, default: 0.8)")
    parser.add_argument("--output", type=str, help="Save results to JSON file")
    parser.add_argument("--continuous", action="store_true", help="Run in continuous monitoring mode")
    parser.add_argument("--interval", type=int, default=3600, help="Monitoring interval in seconds (default: 3600)")
    parser.add_argument("--alert-email", action="store_true", help="Send email alerts when threshold is breached")
    parser.add_argument("--email-to", type=str, help="Comma-separated list of email recipients for alerts")
    
    args = parser.parse_args()
    
    if args.continuous:
        await continuous_monitoring(args)
    else:
        status = await run_monitor(args)
        sys.exit(status)

if __name__ == "__main__":
    asyncio.run(main())