"""Persistent filtered queues; preview progress is isolated from review history."""
import json
from .errors import CollectionError
from .custom_study import integer

PREVIEW_DELAYS={'again':60,'hard':300,'good':600,'easy':0}

def preview_delays(row):
    if row['mode'] != 'filtered': return PREVIEW_DELAYS
    config=json.loads(row['config_json'])
    return {'again':config['preview_again'],'hard':config['preview_hard'],'good':config['preview_good'],'easy':0}


def session(db,sid,require_active=True):
    if not isinstance(sid,str) or len(sid)!=32 or any(c not in '0123456789abcdef' for c in sid): raise CollectionError('La sesión no es válida.')
    row=db.execute('SELECT s.*,d.name AS deck_name FROM custom_sessions s JOIN decks d ON d.id=s.deck_id WHERE s.id=?',(sid,)).fetchone()
    if row is None or (require_active and not row['active']): raise CollectionError('La sesión ya no está activa. Volvé al mazo.')
    return row


class CustomSessionMixin:
    def custom_session_next(self,sid,now=None):
        now=self._now(now)
        with self._study_connection() as db:
            db.execute('BEGIN')
            s=session(db,sid)
            rows=db.execute('SELECT c.*,d.name AS deck_name,n.tags_json,n.model_kind,sc.token AS session_token,sc.next_at FROM custom_session_cards sc JOIN cards c ON c.id=sc.card_id JOIN decks d ON d.id=c.deck_id JOIN notes n ON n.id=c.note_id WHERE sc.session_id=? AND sc.done=0 AND c.suspended=0 AND c.buried_until<=? ORDER BY sc.next_at,sc.position',(sid,now)).fetchall()
            total,pending=db.execute('SELECT COUNT(*),COALESCE(SUM(done=0),0) FROM custom_session_cards WHERE session_id=?',(sid,)).fetchone()
            status={'available':sum(r['next_at']<=now for r in rows),'remaining':pending,'completed':total-pending,'total':total,'limited':0,'buried':pending-len(rows),'next_due_at':min((r['next_at'] for r in rows if r['next_at']>now),default=None)}
            from .study_queue import category
            counts={'new_count':0,'learning_count':0,'review_count':0}
            for item in rows:
                if item['next_at']<=now:
                    kind=category(item,{'start':self._day(now)[1]});counts[{'new':'new_count','learning':'learning_count','interday':'learning_count','review':'review_count'}[kind]]+=1
            status.update(counts)
            status['reason']='ready' if status['available'] else 'waiting' if rows else 'blocked' if pending else 'complete'
            card=None;options=None
            if rows and rows[0]['next_at']<=now:
                row=rows[0];card=self._card_dict(row);options=self._options(db,row['deck_id'])
                card['queue_kind']={'new':'new','learning':'learning','interday':'learning','review':'review'}[category(row,{'start':self._day(now)[1]})]
                card.update(session_id=sid,session_token=row['session_token'],custom_preview=bool(s['preview']))
                card['intervals']=({rating:{'seconds':seconds,'due_at':now+seconds,**({'label':'Finalizar'} if rating=='easy' else {})} for rating,seconds in preview_delays(s).items()} if s['preview'] else self._answer_intervals(db,row,now))
            from .review_queue import preferences
            return {'card':card,'review_options':options,'review_preferences':preferences(db),'status':status,'session':{'id':sid,'deck_id':s['deck_id'],'deck_name':s['deck_name'],'mode':s['mode'],'preview':bool(s['preview']),'config':json.loads(s['config_json'])}}

    def custom_session_grade(self,sid,card_id,rating,*,expected_token,expected_reps,now=None,response_ms=0):
        integer(card_id,1,2**63-2,'La tarjeta');integer(expected_token,0,2**31-1,'La versión de la sesión');integer(expected_reps,0,2**31-1,'La versión de la tarjeta')
        if not isinstance(rating,str) or rating not in PREVIEW_DELAYS: raise CollectionError('Calificación no válida.')
        integer(response_ms,0,86400000,'El tiempo de respuesta')
        now=self._now(now)
        with self._study_connection() as db:
            db.execute('BEGIN IMMEDIATE')
            s=session(db,sid)
            member=db.execute('SELECT * FROM custom_session_cards WHERE session_id=? AND card_id=?',(sid,card_id)).fetchone()
            row=db.execute('SELECT * FROM cards WHERE id=?',(card_id,)).fetchone()
            if member is None or row is None or member['done'] or member['token']!=expected_token or row['reps']!=expected_reps or row['suspended'] or row['buried_until']>now or member['next_at']>now:
                raise CollectionError('Esta respuesta ya se guardó o la tarjeta cambió. Actualizá la sesión.')
            if s['preview']:
                delay=preview_delays(s)[rating];done=(delay==0 if s['mode']=='filtered' else rating=='easy');due=now+delay
                result={'card_id':card_id,'rating':rating,'preview':True,'reps':row['reps'],'due_at':row['due_at']}
            else:
                result=self._grade_in_transaction(db,card_id,rating,now,response_ms,expected_reps)
                updated=db.execute('SELECT * FROM cards WHERE id=?',(card_id,)).fetchone()
                done=updated['queue']==2 or bool(updated['suspended']);due=updated['due_at']
            db.execute('UPDATE custom_session_cards SET done=?,next_at=?,token=token+1 WHERE session_id=? AND card_id=?',(int(done),due,sid,card_id))
            return {**result,'session_id':sid,'session_token':expected_token+1,'session_done':done}

    def close_custom_session(self,sid):
        with self._study_connection() as db:
            s=session(db,sid,False)
            db.execute('UPDATE custom_sessions SET active=0 WHERE id=?',(s['id'],))
            db.execute('DELETE FROM custom_session_cards WHERE session_id=?',(s['id'],))
            return {'session_id':sid,'closed':True}
