"""Daily budgets, ordering and scope selection; no scheduling formulas."""
import json


def context(collection, db, now):
    day, start, end = collection._day(now)
    decks = {r['id']: r['name'] for r in db.execute('SELECT id,name FROM decks')}
    by_name = {name: did for did, name in decks.items()}
    parents = {did: [by_name['::'.join(name.split('::')[:n])] for n in range(1, len(name.split('::'))+1) if '::'.join(name.split('::')[:n]) in by_name] for did, name in decks.items()}
    options = {did: collection._options(db, did) for did in decks}
    from .daily_limits import apply_today
    apply_today(db,options,day)
    spent = {did: {'new': set(), 'review': set()} for did in decks}
    last_kind, last_review_kind = {}, {}
    # Legacy logs have no study_kind: retain them and infer only the daily quota category.
    logs = db.execute('SELECT r.*,c.deck_id,(SELECT MIN(id) FROM reviews first WHERE first.card_id=r.card_id) AS first_id FROM reviews r JOIN cards c ON c.id=r.card_id WHERE reviewed_at>=? AND reviewed_at<? ORDER BY reviewed_at,id', (start,end))
    for log in logs:
        kind = log['study_kind'] or ('new' if log['id'] == log['first_id'] and log['interval_before'] == 0 else 'review' if log['interval_before'] > 0 else 'learning')
        if kind not in ('new','review','interday'):
            continue
        for ancestor in parents.get(log['deck_id_at_review'] or log['deck_id'], []):
            budget_kind = 'new' if kind == 'new' else 'review'
            spent[ancestor][budget_kind].add(log['card_id'])
            last_kind[ancestor] = budget_kind
            if kind != 'new': last_review_kind[ancestor] = kind
    remaining = {did: {'new': max(0, options[did]['new_per_day']-len(spent[did]['new'])), 'review': max(0, options[did]['reviews_per_day']-len(spent[did]['review'])-len(spent[did]['new']))} for did in decks}
    review_remaining = {did:max(0,options[did]['reviews_per_day']-len(spent[did]['review'])) for did in decks}
    return dict(day=day,start=start,end=end,decks=decks,parents=parents,options=options,remaining=remaining,review_remaining=review_remaining,last_kind=last_kind,last_review_kind=last_review_kind)


def due_rows(db, now, deck_id, include_children, day_end=None):
    return db.execute("SELECT c.*,d.name AS deck_name,n.tags_json,n.model_kind FROM cards c JOIN decks d ON d.id=c.deck_id JOIN notes n ON n.id=c.note_id WHERE c.suspended=0 AND NOT EXISTS (SELECT 1 FROM custom_session_cards sc JOIN custom_sessions s ON s.id=sc.session_id WHERE sc.card_id=c.id AND sc.done=0 AND s.active=1) AND (c.due_at<=? OR (c.queue=1 AND c.due_at<?)) AND (? IS NULL OR c.deck_id=? OR (? AND substr(d.name,1,length((SELECT name FROM decks WHERE id=?))+2)=(SELECT name FROM decks WHERE id=?) || '::')) ORDER BY c.due_at,c.id", (now,day_end or now,deck_id,deck_id,include_children,deck_id,deck_id)).fetchall()


def category(row, ctx):
    if row['card_type'] == 0 and not row['reps']:
        return 'new'
    raw = row['fsrs_json'] or row['classic_json']
    state = json.loads(raw) if raw else None
    if state:
        if state['state'] == 2:
            return 'review'
        if state['last_review']:
            from datetime import datetime
            return 'learning' if datetime.fromisoformat(state['last_review']).timestamp() >= ctx['start'] else 'interday'
    if row['queue'] == 3: return 'interday'
    if row['queue'] == 1: return 'learning' if row['due_at'] >= ctx['start'] else 'interday'
    return 'review'


PRIORITY = {'learning':0,'interday':1,'review':2,'new':3}
BURY_FLAG = {'new':'bury_new','review':'bury_review','interday':'bury_interday'}


def bury_siblings(collection,db,row,now,start,end):
    source_kind = category(row,{'start':start})
    for sibling in db.execute('SELECT * FROM cards WHERE note_id=? AND id<>? AND suspended=0',(row['note_id'],row['id'])).fetchall():
        kind = category(sibling,{'start':start})
        if kind != 'learning' and PRIORITY[source_kind] <= PRIORITY[kind] and collection._options(db,sibling['deck_id'])[BURY_FLAG[kind]]:
            db.execute('UPDATE cards SET buried_until=? WHERE id=?',(end,sibling['id']))


def select(collection, db, now, deck_id=None, include_children=False, ctx=None, rows=None):
    ctx = ctx or context(collection, db, now)
    if deck_id is not None and deck_id not in ctx['decks']:
        from .errors import CollectionError
        raise CollectionError('No existe ese mazo.')
    rows = due_rows(db,now,deck_id,include_children,ctx['end']) if rows is None else rows
    from .review_queue import preferences,positions
    cutoff=now+preferences(db)['learn_ahead_seconds'];ordering=positions(db,ctx['day'])
    ahead,all_learning=[],[]
    urgent, reviews, new, interday = [], [], [], []
    buried = 0
    for row in rows:
        kind = category(row,ctx)
        if row['due_at']>now and kind!='learning':continue
        if row['manual_buried_until']>now or (row['buried_until'] > now and kind in BURY_FLAG and ctx['options'][row['deck_id']][BURY_FLAG[kind]]):
            buried += 1
            continue
        if kind=='learning':
            all_learning.append(row);due=ordering.get(row['id'],row['due_at'])
            if due<=now:urgent.append(row)
            elif due<=cutoff:ahead.append(row)
        else:{'new':new,'review':reviews,'interday':interday}[kind].append(row)
    urgent.sort(key=lambda r:(r['reps']==0,ordering.get(r['id'],r['due_at']),r['id']))
    ahead.sort(key=lambda r:(r['reps']==0,ordering.get(r['id'],r['due_at']),r['id']))
    selected_options = ctx['options'][deck_id if deck_id is not None else min(ctx['decks'])]
    from . import order_policy
    new = order_policy.gather(new,selected_options['new_gather'],ctx['day'])
    reviews = order_policy.reviews(collection,db,reviews,selected_options['review_sort'],ctx['day'],now)
    interday.sort(key=lambda r:(r['due_at'],r['id']))
    budget = {did: dict(values) for did,values in ctx['remaining'].items()}
    ignore_review_limit = selected_options['new_ignore_review_limit']
    if ignore_review_limit:
        for did in budget: budget[did]['review'] = ctx['review_remaining'][did]
    admitted = {'interday':[],'review':[],'new':[]}
    chosen_notes = {r['note_id']:0 for r in all_learning}
    limited = 0
    # Reserve siblings and capacity in category priority, independently of
    # presentation order. A new-first display must not defeat review burial.
    for row in interday+reviews+new:
        kind = category(row,ctx)
        if row['note_id'] in chosen_notes and ctx['options'][row['deck_id']][BURY_FLAG[kind]]:
            buried += 1
            continue
        ancestors = ctx['parents'][row['deck_id']]
        if deck_id is not None and not selected_options['limits_start_top']:
            ancestors = ancestors[ancestors.index(deck_id):]
        consumes_review = kind != 'new' or not ignore_review_limit
        if any((consumes_review and budget[did]['review'] <= 0) or (kind == 'new' and budget[did]['new'] <= 0) for did in ancestors):
            limited += 1
            continue
        admitted[kind].append(row)
        chosen_notes[row['note_id']] = PRIORITY[kind]
        for did in ancestors:
            if consumes_review: budget[did]['review'] -= 1
            if kind == 'new': budget[did]['new'] -= 1
    display_new = order_policy.classify(admitted['new'],selected_options['new_sort'],ctx['day'])
    display_reviews = order_policy.mix(admitted['review'],admitted['interday'],selected_options['interday_position'],ctx['last_review_kind'].get(deck_id or 1) != 'review')
    eligible = urgent + order_policy.mix(display_reviews,display_new,selected_options['new_position'],ctx['last_kind'].get(deck_id or 1) != 'review') + ahead
    return eligible, {'new_count':len(admitted['new']),'learning_count':len(urgent)+len(ahead)+len(admitted['interday']),'review_count':len(admitted['review']),'available':len(eligible),'limited':limited,'buried':buried,'reason':'ready' if eligible else 'daily_limit' if limited else 'buried' if buried else 'complete','day':ctx['day'],'resets_at':ctx['end']}
