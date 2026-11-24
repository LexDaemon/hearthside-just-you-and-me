"""Explicit review commands. They never manufacture review events."""
import json
from datetime import datetime, timedelta, timezone
from .errors import CollectionError

def history(db,card_id,now=None):
    marker=db.execute('SELECT review_history_skip FROM cards WHERE id=?',(card_id,)).fetchone()
    skip=marker[0] if marker else 0
    rows=db.execute('SELECT * FROM reviews WHERE card_id=? ORDER BY reviewed_at,id',(card_id,)).fetchall()[skip:]
    return [r for r in rows if now is None or r['reviewed_at']<=now]


def integer(value,minimum,maximum):
    if type(value) is not int or not minimum<=value<=maximum:raise CollectionError('El valor de la acción no es válido.')


class ReviewActionsMixin:
    def review_info(self,card_id,now=None):
        integer(card_id,1,2**63-1);now=self._now(now)
        with self._study_connection() as db:
            db.execute('PRAGMA query_only=ON');db.execute('BEGIN')
            row=db.execute('SELECT c.*,n.model_kind,n.fields_json,n.tags_json,n.created_at,n.modified_at,d.name AS deck,COALESCE(m.value,n.model_kind) AS note_type_id FROM cards c JOIN notes n ON n.id=c.note_id JOIN decks d ON d.id=c.deck_id LEFT JOIN metadata m ON m.key=\'authoring_note_type:\'||n.guid WHERE c.id=?',(card_id,)).fetchone()
            if row is None:raise CollectionError('La tarjeta ya no existe.')
            from .note_types import definitions
            item=dict(row);item['fields']=json.loads(item.pop('fields_json'));item['tags']=json.loads(item.pop('tags_json'))
            item['model_label']=next((t['name'] for t in definitions(db) if t['id']==item['note_type_id']),item['model_kind'])
            item['sibling_count']=db.execute('SELECT COUNT(*) FROM cards WHERE note_id=?',(row['note_id'],)).fetchone()[0]
            from .card_info import build
            return {'card':item,**build(self,db,item,now)}

    def review_action(self,card_id,action,*,expected_reps,value=None,now=None):
        integer(card_id,1,2**63-1);integer(expected_reps,0,2**31-1)
        allowed={'flag','bury','suspend','mark_note','bury_note','suspend_note','forget','set_due','copy_note','delete_note'}
        if not isinstance(action,str) or action not in allowed:raise CollectionError('La acción de repaso no está disponible.')
        now=self._now(now)
        with self._study_connection() as db:
            db.execute('BEGIN IMMEDIATE')
            result=self._review_action_in_transaction(db,card_id,action,expected_reps,value,now)
        return self.add_note(**result) if action=='copy_note' else result

    def _review_action_in_transaction(self,db,card_id,action,expected_reps,value,now):
        row=db.execute('SELECT * FROM cards WHERE id=?',(card_id,)).fetchone()
        if row is None or row['reps']!=expected_reps:raise CollectionError('La tarjeta cambió. Actualizá el repaso.')
        note=db.execute('SELECT * FROM notes WHERE id=?',(row['note_id'],)).fetchone()
        ids=[r[0] for r in db.execute('SELECT id FROM cards WHERE note_id=? ORDER BY id',(row['note_id'],))] if action.endswith('_note') else [card_id]
        placeholders=','.join('?' for _ in ids)
        if action=='copy_note':
            from .note_types import PREFIX
            ident=db.execute('SELECT value FROM metadata WHERE key=?',(PREFIX+note['guid'],)).fetchone()
            copy=dict(deck_id=row['deck_id'],fields=json.loads(note['fields_json']),tags=json.loads(note['tags_json']),note_type=ident[0] if ident else note['model_kind'],allow_duplicate=True,now=now)
        elif action=='flag':
            integer(value,0,7);db.execute('UPDATE cards SET flag=? WHERE id=?',(value,card_id))
        elif action in ('bury','bury_note'):
            if value is not None:raise CollectionError('La acción no admite valores adicionales.')
            db.execute('UPDATE cards SET buried_until=MAX(buried_until,?),manual_buried_until=MAX(manual_buried_until,?) WHERE id IN ('+placeholders+')',[self._day(now)[2],self._day(now)[2]]+ids)
        elif action in ('suspend','suspend_note'):
            if value is not None:raise CollectionError('La acción no admite valores adicionales.')
            db.execute('UPDATE cards SET suspended=1 WHERE id IN ('+placeholders+')',ids)
        elif action=='mark_note':
            if value is not None:raise CollectionError('La acción no admite valores adicionales.')
            tags=json.loads(note['tags_json']);marked=any(t.casefold()=='marked' for t in tags)
            tags=[t for t in tags if t.casefold()!='marked'] if marked else tags+['marked']
            db.execute('UPDATE notes SET tags_json=?,modified_at=? WHERE id=?',(json.dumps(sorted(tags)),now,note['id']))
        elif action=='forget':
            if value!={'confirm':True} or type(value.get('confirm')) is not bool:raise CollectionError('Confirmá el reinicio de la tarjeta.')
            from .scheduler import dependencies
            from .classic_scheduler import Card as ClassicCard
            Card,_,_,_,_=dependencies();due=datetime.fromtimestamp(now,timezone.utc)
            count=db.execute('SELECT COUNT(*) FROM reviews WHERE card_id=?',(card_id,)).fetchone()[0]
            db.execute('UPDATE cards SET review_history_skip=? WHERE id=?',(count,card_id))
            position=db.execute('SELECT COALESCE(MAX(new_position),0)+1 FROM cards').fetchone()[0]
            use_fsrs=self._options(db,row['deck_id'])['fsrs_enabled']
            db.execute('UPDATE cards SET due_at=?,interval_days=0,reps=0,lapses=0,ease_factor=2500,card_type=0,queue=0,buried_until=0,manual_buried_until=0,new_position=?,fsrs_json=?,classic_json=? WHERE id=?',(now,position,Card(card_id=card_id,due=due).to_json() if use_fsrs else None,ClassicCard(card_id,due).to_json() if not use_fsrs else None,card_id))
            db.execute('DELETE FROM custom_session_cards WHERE card_id=?',(card_id,))
        elif action=='set_due':
            if not isinstance(value,dict) or set(value)!={'days','confirm'} or value['confirm'] is not True:raise CollectionError('Confirmá la fecha de revisión.')
            integer(value['days'],0,36500)
            target_day=datetime.fromtimestamp(self._day(now)[1])+timedelta(days=value['days'])
            due=max(now,int(target_day.timestamp()))
            from . import scheduler as fsrs
            options=self._options(db,row['deck_id']);engine=fsrs.scheduler(options,self._parameters(db,row['deck_id']))
            state=fsrs.restore(row,history(db,card_id),engine,options)
            _,_,_,State,rust=fsrs.dependencies()
            if state.stability is None:
                memory=rust.FSRS([]).memory_state_from_sm2(max(1.3,row['ease_factor']/1000),max(1,value['days']),options['historical_retention'])
                state.stability,state.difficulty=memory.stability,memory.difficulty
            state.state=State.Review;state.step=None;state.due=datetime.fromtimestamp(due,timezone.utc)
            from .classic_scheduler import restore,State as ClassicState
            classic=restore(row);classic.state=ClassicState.Review;classic.step=0;classic.due=state.due;classic.interval_days=max(1,value['days'])
            db.execute('UPDATE cards SET due_at=?,card_type=2,queue=2,interval_days=?,buried_until=0,manual_buried_until=0,fsrs_json=?,classic_json=? WHERE id=?',(due,max(1,value['days']),state.to_json() if options['fsrs_enabled'] else None,classic.to_json() if not options['fsrs_enabled'] else None,card_id))
            db.execute('DELETE FROM custom_session_cards WHERE card_id=?',(card_id,))
        elif action=='delete_note':
            if not isinstance(value,dict) or set(value)!={'confirm','expected_card_count'} or value['confirm'] is not True or type(value['expected_card_count']) is not int or value['expected_card_count']!=len(ids):raise CollectionError('Confirmá la eliminación y la cantidad actual de tarjetas.')
            from .generation_edit import RETIRED
            from .note_types import PREFIX
            for cid in ids:
                db.execute('DELETE FROM reviews WHERE card_id=?',(cid,));db.execute('DELETE FROM custom_session_cards WHERE card_id=?',(cid,))
                db.execute('DELETE FROM metadata WHERE key=?',(RETIRED+str(cid),))
            db.execute('DELETE FROM cards WHERE note_id=?',(note['id'],));db.execute('DELETE FROM notes WHERE id=?',(note['id'],));db.execute('DELETE FROM metadata WHERE key=?',(PREFIX+note['guid'],))
            return {'deleted_note':note['id'],'deleted_cards':len(ids)}
        if action!='copy_note':return {'card_id':card_id,'action':action,'updated':len(ids)}
        return copy
