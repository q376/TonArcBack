from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger
import requests
import os

ADMIN_SECRET = os.getenv("ADMIN_SECRET")
API_URL = "http://localhost:8000"  # Change in production

def update_statuses():
    try:
        response = requests.get(
            f"{API_URL}/admin/tasks/update-statuses",
            headers={"X-Admin-Secret": ADMIN_SECRET}
        )
        print(f"Status update: {response.json()}")
    except Exception as e:
        print(f"Failed to update statuses: {e}")

def start_scheduler():
    scheduler = BackgroundScheduler()
    scheduler.add_job(
        update_statuses,
        trigger=IntervalTrigger(minutes=1),
        id='update_tournament_statuses',
        name='Update tournament statuses every minute',
        replace_existing=True
    )
    scheduler.start()
    print("✅ Scheduler started")

if __name__ == "__main__":
    start_scheduler()
    # Keep script running
    import time
    while True:
        time.sleep(60)
