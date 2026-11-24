"""Bounded process-local collection undo, with optimistic divergence checks."""
import hashlib
import json
import threading
from collections import OrderedDict
from .errors import CollectionError

TABLES=('decks','browser_tags','notes','cards','reviews','browser_manual_events','custom_sessions','custom_session_cards','metadata')
LOCK=threading.RLock()
HISTORY=OrderedDict()


def snapshot(db):
    result={}
    for table in TABLES:
        columns=[r['name'] for r in db.execute('PRAGMA table_info('+table+')')]
        keys=[r['name'] for r in db.execute('PRAGMA table_info('+table+')') if r['pk']]
        if not keys:continue
        rows=[dict(r) for r in db.execute('SELECT * FROM '+table)]
        if table=='metadata':rows=[r for r in rows if r['key'] not in ('browser_ui_v2','browser_ui_v3','browser_search_history_v1')]
        result[table]={'columns':columns,'keys':keys,'rows':{tuple(r[k] for k in keys):r for r in rows}}
    return result


def digest(state):
    data=[(table,sorted(info['rows'].items(),key=lambda pair:repr(pair[0]))) for table,info in state.items()]
    return hashlib.sha256(json.dumps(data,ensure_ascii=False,sort_keys=True).encode()).hexdigest()


def remember(path,label,before,after):
    changes={}
    for table,old in before.items():
        new=after[table];changed={key for key in old['rows'].keys()|new['rows'].keys() if old['rows'].get(key)!=new['rows'].get(key)}
        if changed:changes[table]={'keys':old['keys'],'columns':old['columns'],'before':{k:old['rows'].get(k) for k in changed},'after':{k:new['rows'].get(k) for k in changed}}
    changed_cards=set(changes.get('cards',{}).get('before',{}))
    if changed_cards:
        for table in ('reviews','custom_session_cards','browser_manual_events'):
            if table not in before:continue
            old,new=before[table],after[table]
            keys={key for side in (old,new) for key,row in side['rows'].items() if (row.get('card_id'),) in changed_cards}
            if keys:
                diff=changes.setdefault(table,{'keys':old['keys'],'columns':old['columns'],'before':{},'after':{}})
                for key in keys:diff['before'][key]=old['rows'].get(key);diff['after'][key]=new['rows'].get(key)
    if not changes:return
    path=str(path);before_hash=digest(before);after_hash=digest(after)
    with LOCK:
        history=HISTORY.setdefault(path,{'undo':[],'redo':[]})
        if history['undo'] and history['undo'][-1]['after_hash']!=before_hash:history['undo'].clear()
        history['redo'].clear()
        history['undo'].append(dict(label=label,changes=changes,before_hash=before_hash,after_hash=after_hash))
        history['undo']=history['undo'][-20:]
        while sum(len(repr(v)) for v in history['undo'])>64*1024*1024 and len(history['undo'])>1:history['undo'].pop(0)
        HISTORY.move_to_end(path)
        while len(HISTORY)>8:HISTORY.popitem(last=False)


def operation(path,db,action=None):
    if action not in (None,'undo','redo'):raise CollectionError('La acción de historial no es válida.')
    with LOCK:
        history=HISTORY.setdefault(str(path),{'undo':[],'redo':[]})
        current=digest(snapshot(db))
        for direction in ('undo','redo'):
            stack=history[direction]
            if stack and current!=stack[-1]['after_hash' if direction=='undo' else 'before_hash']:
                history['undo'].clear();history['redo'].clear();break
        if action:
            if not history[action]:raise CollectionError('No hay una operación disponible para '+('deshacer.' if action=='undo' else 'rehacer.'))
            entry=history[action][-1];side='before' if action=='undo' else 'after'
            db.execute('PRAGMA defer_foreign_keys=ON')
            # Delete changed records before reinserting: card ordinal swaps must
            # not collide with UNIQUE(note_id,ordinal), even within one note.
            for table in reversed(TABLES):
                change=entry['changes'].get(table)
                if not change:continue
                predicate=' AND '.join(k+'=?' for k in change['keys'])
                for key in change[side]:db.execute('DELETE FROM '+table+' WHERE '+predicate,key)
            for table in TABLES:
                change=entry['changes'].get(table)
                if not change:continue
                cols=change['columns'];sql='INSERT INTO '+table+'('+','.join(cols)+') VALUES('+','.join('?' for _ in cols)+')'
                for row in change[side].values():
                    if row is not None:db.execute(sql,[row[c] for c in cols])
            target=entry['before_hash' if action=='undo' else 'after_hash']
            if digest(snapshot(db))!=target:raise CollectionError('No se pudo restaurar el estado exacto de la colección.')
            history[action].pop();history['redo' if action=='undo' else 'undo'].append(entry)
        return {'undo':history['undo'][-1]['label'] if history['undo'] else None,'redo':history['redo'][-1]['label'] if history['redo'] else None}
