"""Explicit browser scheduling commands; no synthetic study on date changes."""
import json
import random
import re
from datetime import datetime,timedelta,timezone
from .errors import CollectionError


def apply(collection,db,rows,action,value,now):
    changed=[]
    if action=='reposition':
        if set(value)!={'start','step','randomize','shift'} or type(value['start']) is not int or not 0<=value['start']<=2**31-1 or type(value['step']) is not int or not 1<=value['step']<=1000000 or any(type(value[k]) is not bool for k in ('randomize','shift')):raise CollectionError('Las posiciones no son válidas.')
        selected=sorted((r for r in rows if r['card_type']==0),key=lambda r:(r['new_position'],r['id']))
        if not selected:raise CollectionError('La selección no contiene tarjetas nuevas.')
        if value['randomize']:random.SystemRandom().shuffle(selected)
        start,step=value['start'],value['step'];ids={r['id'] for r in selected}
        if start+len(selected)*step>2**31-1:raise CollectionError('Las posiciones superan el límite permitido.')
        if value['shift']:
            for row in db.execute('SELECT id,new_position FROM cards WHERE card_type=0 AND new_position>=?',(start,)).fetchall():
                if row['id'] not in ids:
                    db.execute('UPDATE cards SET new_position=new_position+? WHERE id=?',(len(selected)*step,row['id']));changed.append(row['id'])
        for index,row in enumerate(selected):db.execute('UPDATE cards SET new_position=? WHERE id=?',(start+index*step,row['id']));changed.append(row['id'])
        label='Reposicionar tarjetas'
    elif action=='forget':
        if set(value)!={'restore_position','reset_counts'} or any(type(v) is not bool for v in value.values()):raise CollectionError('Las opciones de Olvidar no son válidas.')
        for row in rows:
            collection._review_action_in_transaction(db,row['id'],'forget',row['reps'],{'confirm':True},now)
            if not value['reset_counts']:db.execute('UPDATE cards SET reps=?,lapses=? WHERE id=?',(row['reps'],row['lapses'],row['id']))
            if value['restore_position'] and row['new_position']:db.execute('UPDATE cards SET new_position=? WHERE id=?',(row['new_position'],row['id']))
            changed.append(row['id'])
        label='Olvidar tarjetas'
    elif action=='set_due':
        if set(value)!={'days'} or not isinstance(value['days'],str):raise CollectionError('La fecha de revisión no es válida.')
        match=re.fullmatch(r'(-?\d+)(?:-(-?\d+))?(!)?',value['days'].strip())
        if not match:raise CollectionError('Usá días, un intervalo como 3-7 y, opcionalmente, ! para cambiar el intervalo.')
        low=int(match[1]);high=int(match[2]) if match[2] is not None else low
        if not -36500<=low<=high<=36500:raise CollectionError('El intervalo de fechas no es válido.')
        for row in rows:
            days=random.SystemRandom().randint(low,high)
            collection._review_action_in_transaction(db,row['id'],'set_due',row['reps'],{'days':max(0,days),'confirm':True},now)
            target=datetime.fromtimestamp(collection._day(now)[1])+timedelta(days=days);due=int(target.timestamp())
            actual=db.execute('SELECT * FROM cards WHERE id=?',(row['id'],)).fetchone()
            ivl=max(1,days) if match[3] or row['card_type']==0 else row['interval_days']
            states={}
            for key in ('fsrs_json','classic_json'):
                encoded=actual[key]
                if encoded:
                    state=json.loads(encoded);state['due']=datetime.fromtimestamp(due,timezone.utc).isoformat();states[key]=json.dumps(state)
                else:states[key]=None
            db.execute('UPDATE cards SET due_at=?,interval_days=?,fsrs_json=?,classic_json=? WHERE id=?',(due,ivl,states['fsrs_json'],states['classic_json'],row['id']));changed.append(row['id'])
        label='Establecer fecha de revisión'
    elif action=='grade':
        if set(value)!={'rating'} or value['rating'] not in ('again','hard','good','easy'):raise CollectionError('La calificación no es válida.')
        for row in rows:
            if row['suspended']:db.execute('UPDATE cards SET suspended=0 WHERE id=?',(row['id'],))
            collection._grade_in_transaction(db,row['id'],value['rating'],now,0,row['reps'])
            if row['suspended']:db.execute('UPDATE cards SET suspended=1 WHERE id=?',(row['id'],))
            db.execute('UPDATE custom_session_cards SET done=1 WHERE card_id=?',(row['id'],));changed.append(row['id'])
        label='Ajustar Ahora'
    else:raise CollectionError('La acción manual no es válida.')
    for cid in changed:
        db.execute('INSERT INTO browser_manual_events(card_id,at,kind,payload) VALUES(?,?,?,?)',(cid,now,action,json.dumps(value,ensure_ascii=False)))
        db.execute('INSERT INTO metadata(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',('browser_modified_at:'+str(cid),str(now)))
    return label
