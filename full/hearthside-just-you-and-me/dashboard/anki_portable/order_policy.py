"""Stable ordering policies. Gathering precedes quota selection and display sorting."""
import hashlib

CHOICES = {
    'new_gather': ('deck','deck_random_notes','position_asc','position_desc','random_notes','random_cards'),
    'new_sort': ('template','gathered','template_random','note_random_template','random'),
    'review_sort': ('due_random','due_deck','deck_due','interval_asc','interval_desc','easy','hard','retrievability_asc','retrievability_desc','random','added','added_desc'),
}


def random_key(seed, value):
    return hashlib.sha256(f'{seed}:{value}'.encode()).digest()


def position(row):
    return row['new_position'] if row['new_position'] is not None else row['id']


def gather(rows, mode, seed):
    def key(r):
        pos = position(r)
        if mode == 'deck': return (r['deck_name'],pos,r['ordinal'],r['id'])
        if mode == 'deck_random_notes': return (r['deck_name'],random_key(seed,r['note_id']),r['ordinal'],r['id'])
        if mode == 'position_desc': return (-pos,r['ordinal'],r['id'])
        if mode == 'random_notes': return (random_key(seed,r['note_id']),r['ordinal'],r['id'])
        if mode == 'random_cards': return (random_key(seed,r['id']),r['id'])
        return (pos,r['ordinal'],r['id'])
    return sorted(rows,key=key)


def classify(rows, mode, seed):
    indexed = list(enumerate(rows))
    def key(pair):
        i,r = pair
        if mode == 'template': return (r['ordinal'],i)
        if mode == 'template_random': return (r['ordinal'],random_key(seed,r['id']),i)
        if mode == 'note_random_template': return (random_key(seed,r['note_id']),r['ordinal'],i)
        if mode == 'random': return (random_key(seed,r['id']),i)
        return (i,)
    return [r for _,r in sorted(indexed,key=key)]


def reposition(db, deck_id, mode):
    rows = db.execute('SELECT id,note_id FROM cards WHERE deck_id=? AND reps=0 AND card_type=0 ORDER BY id',(deck_id,)).fetchall()
    if mode == 'random': rows = sorted(rows,key=lambda r:(random_key(f'insertion:{deck_id}',r['note_id']),r['id']))
    offset = db.execute('SELECT COALESCE(MAX(new_position),0) FROM cards').fetchone()[0]
    positions = {}
    for row in rows:
        if row['note_id'] not in positions: positions[row['note_id']] = offset+len(positions)+1
        db.execute('UPDATE cards SET new_position=? WHERE id=?',(positions[row['note_id']],row['id']))


def reviews(collection, db, rows, mode, seed, now):
    from . import scheduler as fsrs
    from datetime import datetime,timezone
    engines, deck_options = {}, {}
    memory = {}
    if mode in ('easy','hard','retrievability_asc','retrievability_desc'):
        for row in rows:
            did = row['deck_id']
            if did not in engines:
                options = collection._options(db,did)
                deck_options[did] = options
                engines[did] = fsrs.scheduler(options,collection._parameters(db,did))
            from .review_actions import history as scheduler_history
            history = scheduler_history(db,row['id']) if not row['fsrs_json'] else []
            card = fsrs.restore(row,history,engines[did],deck_options[did])
            memory[row['id']] = (card.difficulty,engines[did].get_card_retrievability(card,datetime.fromtimestamp(now,timezone.utc)))
    def key(r):
        due = collection._day(r['due_at'])[0]
        added = r['added_at'] if r['added_at'] is not None else r['id']
        if mode == 'due_random': return (due,random_key(seed,r['id']),r['id'])
        if mode == 'due_deck': return (due,r['deck_name'],r['id'])
        if mode == 'deck_due': return (r['deck_name'],due,r['id'])
        if mode == 'interval_asc': return (r['interval_days'],r['id'])
        if mode == 'interval_desc': return (-r['interval_days'],r['id'])
        if mode == 'easy': return (memory[r['id']][0],r['id'])
        if mode == 'hard': return (-memory[r['id']][0],r['id'])
        if mode == 'retrievability_asc': return (memory[r['id']][1],r['id'])
        if mode == 'retrievability_desc': return (-memory[r['id']][1],r['id'])
        if mode == 'random': return (random_key(seed,r['id']),r['id'])
        return ((-added if mode == 'added_desc' else added),r['id'])
    return sorted(rows,key=key)


def mix(first, second, position, next_first=True):
    if position == 'before': return second+first
    if position == 'after': return first+second
    a,b = (first,second) if next_first else (second,first)
    return [r for i in range(max(len(a),len(b))) for r in ([a[i]] if i<len(a) else [])+([b[i]] if i<len(b) else [])]
