"""Collection review preferences and transient learning presentation order.

Independent local policy, not imported Anki engine code. Reading never writes.
"""
import json
from .errors import CollectionError

KEY='review_preferences_v1'
DEFAULT={'learn_ahead_seconds':1200,'show_remaining':True}


def preferences(db):
    from .preferences import load
    value=load(db)
    return {k:value[k] for k in DEFAULT}


def save_preferences(db,value):
    from .preferences import load,save
    if not isinstance(value,dict) or set(value)!=set(DEFAULT):raise CollectionError('Las preferencias no son válidas.')
    save(db,dict(load(db),**value))
    return dict(value)


def initialize(db):
    db.execute('CREATE TABLE IF NOT EXISTS review_learning_order (card_id INTEGER PRIMARY KEY REFERENCES cards(id) ON DELETE CASCADE,reps INTEGER NOT NULL,actual_due INTEGER NOT NULL,position_due INTEGER NOT NULL,study_day TEXT NOT NULL)')


def positions(db,day):
    return {r['card_id']:r['position_due'] for r in db.execute('SELECT q.* FROM review_learning_order q JOIN cards c ON c.id=q.card_id WHERE q.study_day=? AND q.reps=c.reps AND q.actual_due=c.due_at',(day,))}


def requeue(collection,db,card_id,now,deck_id=None,include_children=False):
    """Avoid an immediate repeated card when learning is collapsed.

    Only a real grade writes this disposable queue hint; stored due dates and
    logs keep their actual scheduling values. Imports need not carry the hint.
    """
    from .study_queue import select,category
    db.execute('DELETE FROM review_learning_order WHERE card_id=?',(card_id,))
    row=db.execute('SELECT * FROM cards WHERE id=?',(card_id,)).fetchone()
    day,start,end=collection._day(now);cutoff=now+preferences(db)['learn_ahead_seconds']
    if row['queue']!=1 or row['due_at']>=end or row['due_at']>cutoff:return
    eligible,_=select(collection,db,now,deck_id,include_children)
    if any(category(r,{'start':start})!='learning' for r in eligible):return
    others=[r for r in eligible if r['id']!=card_id]
    if not others:return
    ordering=positions(db,day);next_due=ordering.get(others[0]['id'],others[0]['due_at'])
    if row['due_at']<=next_due and next_due+1<cutoff:
        db.execute('INSERT INTO review_learning_order(card_id,reps,actual_due,position_due,study_day) VALUES(?,?,?,?,?)',(card_id,row['reps'],row['due_at'],next_due+1,day))
