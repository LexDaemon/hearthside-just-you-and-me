"""Adapter around pinned upstream FSRS, never a replacement scheduling formula."""
from datetime import datetime, timedelta, timezone
from typing import Any
from .errors import MissingScheduler

RATINGS = {'again': 1, 'hard': 2, 'good': 3, 'easy': 4}


def dependencies():
    try:
        from fsrs import Card, Rating, Scheduler, State
        import fsrs_rs_python as rust
    except ImportError as exc:
        raise MissingScheduler('Faltan las dependencias FSRS declaradas del plugin. No se usó un motor alternativo.') from exc
    return Card, Rating, Scheduler, State, rust


def scheduler(options, parameters=None):
    _, _, Scheduler, _, _ = dependencies()
    kwargs: dict[str, Any] = dict(desired_retention=options['desired_retention'],
                  learning_steps=[timedelta(seconds=s) for s in options['learning_steps']],
                  relearning_steps=[timedelta(seconds=s) for s in options['relearning_steps']],
                  maximum_interval=options['maximum_interval'], enable_fuzzing=False)
    if parameters:
        kwargs['parameters'] = parameters
    return Scheduler(**kwargs)


def restore(row, history, engine, options=None):
    Card, Rating, _, State, rust = dependencies()
    if row['fsrs_json']:
        return Card.from_json(row['fsrs_json'])
    due = datetime.fromtimestamp(row['due_at'], timezone.utc)
    card = Card(card_id=row['id'], due=due)
    retention = (options or {}).get('historical_retention',.9)
    if history and history[0]['interval_before'] > 0:
        first = history[0]
        memory = rust.FSRS([]).memory_state_from_sm2(max(1.3,first['ease_factor']/1000),first['interval_before'],retention)
        card.stability,card.difficulty = memory.stability,memory.difficulty
        card.state = State.Review
        card.step = None
        card.last_review = datetime.fromtimestamp(first['reviewed_at'],timezone.utc)-timedelta(days=first['interval_before'])
    # Replay real recorded ratings. Existing dates are not changed on migration/read.
    for review in history:
        at = datetime.fromtimestamp(review['reviewed_at'], timezone.utc)
        card, _ = engine.review_card(card, Rating(RATINGS[review['rating']]), at)
    if (row['reps'] or row['card_type'] or row['queue'] in (1,2,3)) and not history:
        memory = rust.FSRS([]).memory_state_from_sm2(max(1.3, row['ease_factor'] / 1000), max(1, row['interval_days']), retention)
        card.stability, card.difficulty = memory.stability, memory.difficulty
        card.last_review = due - timedelta(days=max(1, row['interval_days']))
    if row['reps'] or row['card_type']:
        card.state = State.Relearning if row['queue'] == 1 and row['card_type'] == 2 else State.Learning if row['queue'] in (1, 3) else State.Review
        if card.state != State.Review:
            card.step = card.step or 0
    card.due = due
    return card


def evaluate(row, history, options, parameters, rating, now, response_ms=0):
    _, Rating, _, _, _ = dependencies()
    engine = scheduler(options, parameters)
    card = restore(row, history, engine, options)
    updated, log = engine.review_card(card, Rating(RATINGS[rating]), datetime.fromtimestamp(now, timezone.utc), response_ms)
    return card, updated, log
