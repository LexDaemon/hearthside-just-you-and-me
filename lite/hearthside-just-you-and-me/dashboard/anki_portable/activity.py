"""Real study activity, retaining timing facts when decks are deleted."""
from datetime import date, timedelta


def initialize(db):
    # No card/deck FK: deleting content must not erase completed study time.
    db.execute("""CREATE TABLE IF NOT EXISTS deleted_deck_review_activity (
        review_id INTEGER PRIMARY KEY,
        reviewed_at INTEGER NOT NULL,
        response_ms INTEGER NOT NULL
    )""")


def archive_card_reviews(db, card_id):
    db.execute("""INSERT OR IGNORE INTO deleted_deck_review_activity
        (review_id, reviewed_at, response_ms)
        SELECT id, reviewed_at, response_ms FROM reviews WHERE card_id=?
    """, (card_id,))


def activity(collection, db, now):
    modifier='-'+str(collection.full_preferences()['rollover_hour'])+' hours'
    today = date.fromisoformat(collection._day(now)[0])
    rows = db.execute("""
        SELECT date(reviewed_at, 'unixepoch', 'localtime', ?) AS day,
               COUNT(*) AS reviews, SUM(MAX(0, COALESCE(response_ms, 0))) AS response_ms
        FROM (
            SELECT id, reviewed_at, response_ms FROM reviews
            UNION ALL
            SELECT a.review_id, a.reviewed_at, a.response_ms
            FROM deleted_deck_review_activity a
            WHERE NOT EXISTS (SELECT 1 FROM reviews r WHERE r.id=a.review_id)
        ) WHERE reviewed_at <= ? GROUP BY day ORDER BY day
    """, (modifier,now)).fetchall()
    days = [{'date':r['day'], 'reviews':r['reviews'], 'response_ms':r['response_ms']}
            for r in rows if r['day'] and r['day'] <= today.isoformat()]
    dates = {date.fromisoformat(d['date']) for d in days}
    longest = run = 0
    previous = None
    for day in sorted(dates):
        run = run+1 if previous is not None and day == previous+timedelta(days=1) else 1
        longest = max(longest, run)
        previous = day
    cursor = today if today in dates else today-timedelta(days=1)
    current = 0
    while cursor in dates:
        current += 1
        cursor -= timedelta(days=1)
    count = len(days)
    total = sum(d['reviews'] for d in days)
    elapsed = (today-min(dates)).days+1 if dates else 0
    today_values = next((d for d in days if d['date'] == today.isoformat()), {})
    forecast_rows = db.execute("""
        SELECT date(due_at, 'unixepoch', 'localtime', ?) AS day, COUNT(*) AS due
        FROM cards WHERE suspended=0 AND reps>0 AND due_at>=?
        GROUP BY day ORDER BY day
    """, (modifier,collection._day(now)[2])).fetchall()
    forecast = [{'date':r['day'], 'due':r['due']} for r in forecast_rows if r['day']]
    return {'history_version':1, 'study_day':today.isoformat(), 'days':days, 'forecast':forecast,
            'today':{'reviews':today_values.get('reviews',0), 'response_ms':today_values.get('response_ms',0)},
            'total_reviews':total, 'active_days':count, 'elapsed_days':elapsed,
            'average_per_active_day':total/count if count else 0,
            'active_percent':100*count/elapsed if elapsed else 0,
            'longest_streak':longest, 'current_streak':current}
