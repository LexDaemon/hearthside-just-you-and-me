"""Read-only queue eligibility and persisted daily completion for study progress."""
RATINGS=('again','hard','good','easy')


def read(collection,now=None,deck_id=None,include_children=False,session_id=None):
    at=collection._now(now)
    day,start,end=collection._day(at)
    with collection._study_connection() as db:
        db.execute('PRAGMA query_only=ON');db.execute('BEGIN')
        where='1';args=[];deck=None
        if deck_id is not None:
            from .custom_study import scope
            deck,where,args=scope(db,deck_id,include_children)
        total=db.execute('SELECT COUNT(*) FROM cards c JOIN decks d ON d.id=c.deck_id WHERE '+where,args).fetchone()[0]
        rows=list(db.execute("SELECT r.card_id,r.rating,r.reviewed_at,r.id,c.card_type,c.due_at FROM reviews r JOIN cards c ON c.id=r.card_id JOIN decks d ON d.id=c.deck_id WHERE ("+where+") AND r.reviewed_at>=? AND r.reviewed_at<? AND r.rating IN ('again','hard','good','easy') ORDER BY r.reviewed_at,r.id",[*args,start,min(end,at+1)]))
        unique=len({r['card_id'] for r in rows})
        ratings={key:sum(r['rating']==key for r in rows) for key in RATINGS}
        stages={str(row['card_type']):row['n'] for row in db.execute('SELECT c.card_type,COUNT(*) n FROM cards c JOIN decks d ON d.id=c.deck_id WHERE '+where+' GROUP BY c.card_type',args)}
        latest={row['card_id']:row for row in rows}
        completed=sum(row['rating']!='again' and row['card_type']==2 and row['due_at']>=end for row in latest.values())
        result={'version':1,'study_day':day,'total':total,'completed_unique':unique,'answers':len(rows),
                'remaining':max(0,total-unique),'percent':100*unique/total if total else 0,
                'ratings':ratings,'last_rating':rows[-1]['rating'] if rows else None,
                'new':stages.get('0',0),'learning':stages.get('1',0)+stages.get('3',0),'review':stages.get('2',0)}

        if deck is not None:
            result.update(version=2,deck_id=deck_id,deck_name=deck['name'],include_children=include_children,completed=completed,remaining=max(0,total-completed),percent=100*completed/total if total else 0)
        from . import study_queue
        eligible,_=study_queue.select(collection,db,at,deck_id,include_children)
        available_ids={str(row['id']) for row in eligible}
        completed_ids={str(row['card_id']) for row in latest.values() if row['rating']!='again' and row['card_type']==2 and row['due_at']>=end}
        daily_ids=available_ids|{str(row['card_id']) for row in rows}
        session_ids=available_ids;session_completed=completed_ids
        if session_id is not None:
            from .custom_sessions import session
            custom=session(db,session_id)
            if deck_id is None or custom['deck_id']!=deck_id:
                from .errors import CollectionError
                raise CollectionError('La sesión no pertenece al mazo elegido.')
            members=list(db.execute('SELECT sc.card_id,sc.done FROM custom_session_cards sc JOIN cards c ON c.id=sc.card_id WHERE sc.session_id=?',(session_id,)))
            session_ids={str(row['card_id']) for row in members}
            session_completed={str(row['card_id']) for row in members if row['done']}
            if not custom['preview']:daily_ids|=session_ids
        result.update(progress_version=3,available_ids=sorted(available_ids),completed_ids=sorted(completed_ids),
                      session_id=session_id,session_ids=sorted(session_ids),session_completed_ids=sorted(session_completed),
                      daily_total=len(daily_ids),daily_completed=len(completed_ids&daily_ids),day_ends_at=end)
        # Stable first-encounter order; a repeat replaces only that card's rating.
        answer_index={str(row['card_id']):index for index,row in enumerate(rows,1)}
        ordered_ids=list(dict.fromkeys([*(str(card_id) for card_id in latest),*(str(row['id']) for row in eligible),*sorted(daily_ids)]))
        result.update(rating_progress_version=1,daily_cards=[
            {'id':card_id,'rating':latest[int(card_id)]['rating'] if int(card_id) in latest else None,
             'answer_index':answer_index.get(card_id,0)}
            for card_id in ordered_ids if card_id in daily_ids])
        return result
