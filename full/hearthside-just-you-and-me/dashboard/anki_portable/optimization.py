"""Local upstream Rust optimizer: small native wheel, no PyTorch runtime."""
import json
import math
from collections import defaultdict
from .errors import CollectionError
from .scheduler import dependencies, scheduler, RATINGS

MIN_REVIEWS = 100
MIN_CARDS = 20
MAX_ITEMS = 10000


def training_data(collection, db, deck_id):
    _, _, _, _, rust = dependencies()
    by_card = defaultdict(list)
    for row in db.execute('SELECT r.card_id,r.reviewed_at,r.rating,r.interval_before FROM reviews r JOIN cards c ON c.id=r.card_id WHERE c.deck_id=? ORDER BY r.card_id,r.reviewed_at,r.id', (deck_id,)):
        by_card[row['card_id']].append(row)
    items, card_ids, cards = [], [], set()
    cutoff = collection._options(db,deck_id)['ignore_before']
    skipped={row['id']:row['review_history_skip'] for row in db.execute('SELECT id,review_history_skip FROM cards WHERE deck_id=?',(deck_id,))}
    for card_id, history in by_card.items():
        history=history[skipped.get(card_id,0):]
        if not history:continue
        # Exclude the card, not just the old events: never turn a truncated
        # sequence into a fabricated first exposure for the optimizer.
        if cutoff and collection._day(history[0]['reviewed_at'])[0] < cutoff:
            continue
        if history[0]['interval_before'] > 0:
            continue  # This optimizer binding cannot accept a starting memory state.
        reviews = []
        last = None
        for row in history[:256]:
            day = collection._day(row['reviewed_at'])[0]
            from datetime import date
            day = date.fromisoformat(day)
            delta = (day-last).days if last else 0
            reviews.append(rust.FSRSReview(RATINGS[row['rating']], max(0,delta)))
            if delta > 0:
                items.append(rust.FSRSItem(list(reviews)))
                card_ids.append(card_id)
                cards.add(card_id)
            last = day
    return items[-MAX_ITEMS:], card_ids[-MAX_ITEMS:], len(cards)


def status(collection, db, deck_id):
    if not collection._options(db, deck_id)['fsrs_enabled']:
        return {'training_reviews':0,'training_cards':0,'minimum_reviews':MIN_REVIEWS,'minimum_cards':MIN_CARDS,'can_optimize':False,'optimized_at':None}
    items, _, cards = training_data(collection,db,deck_id)
    row = db.execute('SELECT optimized_at FROM deck_options WHERE deck_id=?', (deck_id,)).fetchone()
    return {'training_reviews':len(items),'training_cards':cards,'minimum_reviews':MIN_REVIEWS,'minimum_cards':MIN_CARDS,'can_optimize':len(items)>=MIN_REVIEWS and cards>=MIN_CARDS,'optimized_at':row['optimized_at'] if row else None}


def optimize(collection, deck_id, now):
    _, _, _, _, rust = dependencies()
    with collection._study_connection() as db:
        if not collection._options(db,deck_id)['fsrs_enabled']:
            raise CollectionError('Activá FSRS antes de optimizar sus parámetros.')
        info = status(collection,db,deck_id)
        if not info['can_optimize']:
            return dict(info,status='insufficient_history')
        options = collection._options(db,deck_id)
        items, card_ids, _ = training_data(collection,db,deck_id)
        old_parameters = collection._parameters(db,deck_id)
        revision = db.execute('SELECT COUNT(*),MAX(id) FROM reviews').fetchone()
        revision = tuple(revision)
    original = rust.FSRS(old_parameters or [])
    try:
        parameters = original.compute_parameters(items, card_ids=card_ids, num_relearning_steps=len(options['relearning_steps']), training_config=rust.TrainingConfig(num_epochs=5, seed=2023))
        if len(parameters) != 21 or any(not math.isfinite(v) for v in parameters):
            raise ValueError('invalid FSRS parameters')
        scheduler(options,parameters)
        baseline = original.evaluate(items).log_loss
        trained = rust.FSRS(parameters).evaluate(items).log_loss
        improved = math.isfinite(trained) and trained < baseline
        if not improved:
            parameters = list(old_parameters or rust.DEFAULT_PARAMETERS)
    except Exception as exc:
        raise CollectionError('No se pudo optimizar FSRS. Se conservaron los parámetros anteriores.') from exc
    with collection._study_connection() as db:
        db.execute('BEGIN IMMEDIATE')
        if tuple(db.execute('SELECT COUNT(*),MAX(id) FROM reviews').fetchone()) != revision or collection._options(db,deck_id) != options or collection._parameters(db,deck_id) != old_parameters:
            raise CollectionError('La colección cambió durante la optimización. Volvé a intentarlo.')
        stored_options = dict(options)
        from .daily_limits import KEYS
        for key in KEYS: stored_options.pop(key,None)
        db.execute('INSERT INTO deck_options(deck_id,options_json,parameters_json,optimized_at) VALUES(?,?,?,?) ON CONFLICT(deck_id) DO UPDATE SET parameters_json=excluded.parameters_json,optimized_at=excluded.optimized_at', (deck_id,json.dumps(stored_options),json.dumps(parameters),now))
    return dict(info,status='optimized' if improved else 'already_optimal',optimized_at=now)
