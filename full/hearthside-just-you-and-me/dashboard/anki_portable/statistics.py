"""Read-only statistics over selected cards and real persisted review events.

Review-day boundary follows the collection (04:00 local). No queue, quota,
scheduler state or activity is modified by opening or filtering this view.
"""
import math
from collections import Counter, defaultdict
from statistics import median
from datetime import date, datetime, timedelta, timezone
from .browser import configure
from .browser_search import Search
from .errors import CollectionError
from . import scheduler as fsrs

KINDS = ('new', 'learning', 'relearning', 'young', 'mature', 'suspended', 'buried')
RATINGS = ('again', 'hard', 'good', 'easy')


def histogram(values, scope='all', percent=False):
    values = sorted(v for v in values if math.isfinite(v) and v >= 0)
    if not values:
        return []
    if percent:
        bins = Counter(min(95, max(0, math.floor(v/5)*5)) for v in values)
        return [{'value':k,'count':v,'end':k+5} for k,v in sorted(bins.items())]
    maximum = 30 if scope == 'month' else values[max(0, math.ceil(len(values)*(.5 if scope == '50' else .95))-1)] if scope in ('50', '95') else values[-1]
    width = max(1, math.ceil(maximum/40))
    bins = Counter(math.floor(v/width)*width for v in values if v <= maximum)
    return [{'value': k, 'count': v, 'end': k+width} for k,v in sorted(bins.items())]


def ratio(rows):
    count = len(rows)
    correct = sum(r['rating'] != 'again' for r in rows)
    return {'total': count, 'correct': correct, 'percent': 100*correct/count if count else None}


class StatisticsMixin:
    def statistics(self, query='', current_deck_id=None, now=None, history='year', year=None,
                   separate=True, forecast_days=30, review_days=30, interval_range='95',
                   stability_range='95', hourly_days=365, button_days=365, added_days=30):
        if not isinstance(query, str) or len(query) > 4096:
            raise CollectionError('La búsqueda supera el límite permitido.')
        if current_deck_id is not None and (type(current_deck_id) is not int or not 0 < current_deck_id < 2**63-1):
            raise CollectionError('El mazo actual no es válido.')
        if history not in ('year', 'all') or type(separate) is not bool:
            raise CollectionError('El alcance estadístico no es válido.')
        if year is not None and (type(year) is not int or not 1 <= year <= 9998):
            raise CollectionError('El año no es válido.')
        for value in (forecast_days, review_days, hourly_days, button_days, added_days):
            if type(value) is not int or value not in (0, 30, 90, 365):
                raise CollectionError('El período estadístico no es válido.')
        if interval_range not in ('month', '50', '95', 'all') or stability_range not in ('month', '50', '95', 'all'):
            raise CollectionError('El intervalo estadístico no es válido.')
        now = self._now(now)
        today, start, end = self._day(now)
        today_date = date.fromisoformat(today)
        year = year or today_date.year
        history_start = int((datetime.fromtimestamp(start)-timedelta(days=364)).timestamp()) if history == 'year' else -2**62
        def since(days):
            return max(history_start, int((datetime.fromtimestamp(start)-timedelta(days=days)).timestamp())) if days else history_start
        with self._study_connection() as db:
            db.execute('PRAGMA query_only=ON')
            db.execute('BEGIN')
            configure(db)
            where, args = Search(self, db, now, current_deck_id).compile(query)
            base = ' FROM cards c JOIN notes n ON n.id=c.note_id JOIN decks d ON d.id=c.deck_id WHERE '+where
            cards = db.execute('SELECT c.*'+base, args).fetchall()
            # Join through the matching cards, never a giant IN list or user SQL.
            logs = db.execute('SELECT r.* FROM reviews r JOIN ('+'SELECT c.id'+base+') chosen ON chosen.id=r.card_id WHERE r.reviewed_at<=? ORDER BY r.reviewed_at,r.id', args+[now]).fetchall()
            counts = dict.fromkeys(KINDS, 0)
            forecast = Counter()
            intervals, stability, difficulty, retrievability = [], [], [], []
            engines = {}
            note_retrievability = defaultdict(list)
            daily_load = 0
            for card in cards:
                if separate and card['suspended']:
                    kind = 'suspended'
                elif separate and (card['buried_until'] > now or card['queue'] in (-2, -3)):
                    kind = 'buried'
                elif card['card_type'] == 0:
                    kind = 'new'
                elif card['card_type'] == 3 or card['card_type'] == 2 and card['queue'] in (1, 3):
                    kind = 'relearning'
                elif card['card_type'] == 1 or card['queue'] in (1, 3):
                    kind = 'learning'
                else:
                    kind = 'mature' if card['interval_days'] >= 21 else 'young'
                counts[kind] += 1
                if card['suspended'] or card['queue'] < 0:
                    continue
                if card['card_type'] != 0:
                    daily_load += 1/max(1, card['interval_days'])
                # Current scheduled due dates only; no invented future repetitions.
                if card['card_type'] != 0 and card['due_at'] >= start:
                    due_day = date.fromisoformat(self._day(card['due_at'])[0])
                    offset = (due_day-today_date).days
                    if forecast_days == 0 or offset <= forecast_days:
                        forecast[due_day.isoformat()] += 1
                if card['card_type'] == 2 and card['queue'] == 2:
                    intervals.append(card['interval_days'])
                if not card['reps'] or card['added_at'] > now:
                    continue
                did = card['deck_id']
                if did not in engines:
                    options = self._options(db, did)
                    engines[did] = (options, fsrs.scheduler(options, self._parameters(db,did)) if options['fsrs_enabled'] else None)
                options, engine = engines[did]
                if engine is None:
                    continue
                from .review_actions import history as scheduler_history
                history_rows = [] if card['fsrs_json'] else scheduler_history(db,card['id'],now)
                memory = fsrs.restore(card, history_rows, engine, options)
                if memory.last_review and memory.last_review.timestamp() > now:
                    continue
                if memory.stability is not None:
                    stability.append(memory.stability)
                if memory.difficulty is not None:
                    difficulty.append(max(0, min(100, (memory.difficulty-1)*100/9)))
                if memory.stability is not None and memory.last_review is not None:
                    probability = engine.get_card_retrievability(memory, datetime.fromtimestamp(now, timezone.utc))*100
                    retrievability.append(probability)
                    note_retrievability[card['note_id']].append(probability)
            counts['total'] = len(cards)
            # The quota category 'learning' also contains relearning. Recover
            # that distinction from preceding real lapse/graduation records,
            # without changing stored history or the scheduler's quota policy.
            review_types = {}
            relearning = set()
            for log in logs:
                raw = log['study_kind']
                if raw == 'filtered':
                    kind = 'filtered'
                elif raw == 'review' or raw not in ('new','learning','interday','relearning') and log['interval_before'] >= 1:
                    kind = 'mature' if log['interval_before'] >= 21 else 'young'
                elif raw == 'relearning' or log['card_id'] in relearning:
                    kind = 'relearning'
                else:
                    kind = 'learning'
                review_types[log['id']] = kind
                if kind in ('young','mature') and log['rating'] == 'again' and log['interval_after'] < 1:
                    relearning.add(log['card_id'])
                elif log['interval_after'] >= 1:
                    relearning.discard(log['card_id'])
            today_logs = [r for r in logs if r['reviewed_at'] >= start]
            daily = {}
            calendar = Counter()
            hours = defaultdict(list)
            buttons = defaultdict(lambda: Counter())
            first = {}
            for log in logs:
                day = self._day(log['reviewed_at'])[0]
                key = (log['card_id'], day)
                if key not in first:
                    first[key] = log
                if log['reviewed_at'] < history_start:
                    continue
                if day.startswith(str(year)+'-'):
                    calendar[day] += 1
                if log['reviewed_at'] >= since(review_days):
                    item = daily.setdefault(day, {'date':day,'count':0,'response_ms':0,'counts':dict.fromkeys(('learning','relearning','young','mature','filtered'),0),'times':dict.fromkeys(('learning','relearning','young','mature','filtered'),0)})
                    kind = review_types[log['id']]
                    item['count'] += 1
                    item['response_ms'] += max(0,log['response_ms'])
                    item['counts'][kind] += 1
                    item['times'][kind] += max(0,log['response_ms'])
                if log['reviewed_at'] >= since(hourly_days):
                    hours[datetime.fromtimestamp(log['reviewed_at']).hour].append(log)
                if log['reviewed_at'] >= since(button_days):
                    recorded_kind = review_types[log['id']]
                    kind = 'learning' if recorded_kind in ('learning','relearning') else 'mature' if log['interval_before'] >= 21 else 'young'
                    if log['rating'] in RATINGS:
                        buttons[kind][log['rating']] += 1
            retention = []
            for days, label in ((1,'Hoy'),(2,'Ayer'),(7,'La semana pasada'),(30,'El mes pasado'),(365,'El año pasado')):
                lower = int((datetime.fromtimestamp(start)-timedelta(days=(1 if days == 2 else days-1))).timestamp())
                upper = start-1 if days == 2 else now
                selected = [r for r in first.values() if r['interval_before'] >= 1 and max(lower,history_start) <= r['reviewed_at'] <= upper]
                retention.append({'label':label, 'young':ratio([r for r in selected if r['interval_before'] < 21]),'mature':ratio([r for r in selected if r['interval_before'] >= 21]),'all':ratio(selected)})
            added = Counter(self._day(c['added_at'])[0] for c in cards if c['added_at'] is not None and since(added_days) <= c['added_at'] <= now)
            def duration(days, event_days):
                if days:
                    first_day = date.fromisoformat(self._day(since(days))[0])
                elif history == 'year':
                    first_day = date.fromisoformat(self._day(history_start)[0])
                else:
                    first_day = date.fromisoformat(min(event_days, default=today))
                return max(1, (today_date-first_day).days+1)
            review_period = duration(review_days, daily)
            added_period = duration(added_days, added)
            review_total = sum(v['count'] for v in daily.values())
            review_time = sum(v['response_ms'] for v in daily.values())
            active_days = len(daily)
            forecast_period = forecast_days+1 if forecast_days else max(((date.fromisoformat(day)-today_date).days+1 for day in forecast),default=1)
            today_kinds = dict.fromkeys(('learning','review','relearning','filtered'),0)
            for log in today_logs:
                kind = review_types[log['id']]
                category = 'review' if kind in ('young','mature') else kind
                today_kinds[category] += 1
            again_count = sum(r['rating']=='again' for r in today_logs)
            summary = {
                'today': {'kinds':today_kinds, 'again_percent':100*again_count/len(today_logs) if today_logs else 0,
                          'mature':ratio([r for r in today_logs if r['interval_before'] >= 21])},
                'forecast': {'total':sum(forecast.values()), 'days':forecast_period,
                             'average':sum(forecast.values())/forecast_period,
                             'tomorrow':forecast[(today_date+timedelta(days=1)).isoformat()], 'daily_load':daily_load},
                'reviews': {'total':review_total, 'response_ms':review_time, 'active_days':active_days, 'days':review_period,
                            'active_percent':100*active_days/review_period, 'average':review_total/review_period,
                            'average_active':review_total/active_days if active_days else 0,
                            'time_average_ms':review_time/review_period,
                            'time_average_active_ms':review_time/active_days if active_days else 0},
                'added': {'total':sum(added.values()),'days':added_period,'average':sum(added.values())/added_period},
                'interval_median':median(intervals) if intervals else None,
                'stability_median':median(stability) if stability else None,
                'difficulty_median':median(difficulty) if difficulty else None,
                'retrievability_mean':sum(retrievability)/len(retrievability) if retrievability else None,
                'knowledge_cards':sum(retrievability)/100,
                'knowledge_notes':sum(sum(values)/len(values) for values in note_retrievability.values())/100,
            }
            return {'summary':summary,'study_day': today, 'year': year, 'counts': counts,
                    'today': {'reviews':len(today_logs), 'response_ms':sum(max(0,r['response_ms']) for r in today_logs), 'again':sum(r['rating']=='again' for r in today_logs)},
                    'forecast':[{'date':k,'count':v} for k,v in sorted(forecast.items())],
                    'calendar':[{'date':k,'count':v} for k,v in sorted(calendar.items())],
                    'reviews':list(daily.values()), 'intervals':histogram(intervals,interval_range),
                    'stability':histogram(stability,stability_range), 'difficulty':histogram(difficulty,percent=True),
                    'retrievability':histogram(retrievability,percent=True), 'retention':retention,
                    'hourly':[dict(hour=k,**ratio(v)) for k,v in sorted(hours.items())],
                    'buttons':[{'kind':k,'counts':{rating:v[rating] for rating in RATINGS}} for k,v in sorted(buttons.items())],
                    'added':[{'date':k,'count':v} for k,v in sorted(added.items())]}
