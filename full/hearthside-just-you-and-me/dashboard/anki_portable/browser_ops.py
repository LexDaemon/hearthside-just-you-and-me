"""Transactional commands for the browser, separate from read-only queries."""
import json
from .errors import CollectionError
from .browser_history import LOCK,snapshot,remember,operation


def selection(db,ids,mode):
    if mode not in ('cards','notes') or not isinstance(ids,list) or not 1<=len(ids)<=200000 or any(type(i) is not int or not 0<i<2**63-1 for i in ids) or len(ids)!=len(set(ids)):raise CollectionError('La selección de Explorar no es válida.')
    key='c.id' if mode=='cards' else 'c.note_id'
    rows=list(db.execute('SELECT c.* FROM cards c WHERE '+key+' IN(SELECT value FROM json_each(?)) ORDER BY c.id',(json.dumps(ids),)))
    if {r['id'] if mode=='cards' else r['note_id'] for r in rows}!=set(ids):raise CollectionError('La selección cambió. Actualizá los resultados.')
    return rows


def tags(value):
    if not isinstance(value,list) or len(value)>200 or any(not isinstance(t,str) or not t or len(t)>200 or any(c.isspace() for c in t) for t in value):raise CollectionError('Las etiquetas no son válidas.')
    return sorted(set(value))


def command(collection,ids,mode,action,value,now=None):
    if action not in ('add_tags','remove_tags','mark','flag','suspend','bury','move','delete_notes','set_due','forget','grade','reposition','find_replace','change_note_type'):raise CollectionError('La acción de Explorar no está disponible.')
    if not isinstance(value,dict):raise CollectionError('Los datos de la acción no son válidos.')
    now=collection._now(now)
    with LOCK:
        with collection._study_connection() as db:
            db.execute('BEGIN IMMEDIATE')
            if action=='find_replace' and value.get('all_notes') is True:
                if mode not in ('cards','notes') or not isinstance(ids,list) or (ids and not selection(db,ids,mode)):raise CollectionError('La selección no es válida.')
                rows=list(db.execute('SELECT * FROM cards ORDER BY id'))
            else:rows=selection(db,ids,mode)
            before=snapshot(db);details={}
            nids=sorted({r['note_id'] for r in rows})
            notes=list(db.execute('SELECT * FROM notes WHERE id IN(SELECT value FROM json_each(?))',(json.dumps(nids),)))
            if action in ('add_tags','remove_tags'):
                if set(value)!={'tags'}:raise CollectionError('Los datos de etiquetas no son válidos.')
                requested=tags(value['tags'])
                label='Añadir etiquetas' if action=='add_tags' else 'Eliminar etiquetas'
                for note in notes:
                    old=json.loads(note['tags_json'])
                    new=sorted(set(old+requested)) if action=='add_tags' else [t for t in old if t.casefold() not in {v.casefold() for v in requested}]
                    if old!=new:db.execute('UPDATE notes SET tags_json=?,modified_at=? WHERE id=?',(json.dumps(new,ensure_ascii=False),now,note['id']))
            elif action=='mark':
                if value:raise CollectionError('Marcar no admite valores adicionales.')
                current=next(r for r in rows if r['id' if mode=='cards' else 'note_id']==ids[-1]);current_note=next(n for n in notes if n['id']==current['note_id']);remove=any(t.casefold()=='marked' for t in json.loads(current_note['tags_json']));label='Marcar nota'
                for note in notes:
                    old=json.loads(note['tags_json']);new=[t for t in old if t.casefold()!='marked'] if remove else sorted(set(old+['marked']))
                    db.execute('UPDATE notes SET tags_json=?,modified_at=? WHERE id=?',(json.dumps(new,ensure_ascii=False),now,note['id']))
            elif action in ('flag','suspend','bury','move'):
                cids=[r['id'] for r in rows];encoded=json.dumps(cids)
                if action=='flag':
                    if set(value)-{'flag','toggle'} or 'flag' not in value or type(value['flag']) is not int or not 0<=value['flag']<=7 or ('toggle' in value and type(value['toggle']) is not bool):raise CollectionError('El marcador no es válido.')
                    current=next(r for r in rows if r['id' if mode=='cards' else 'note_id']==ids[-1]);flag=0 if value.get('toggle') and current['flag']==value['flag'] else value['flag']
                    label='Elegir marcador';db.execute('UPDATE cards SET flag=? WHERE id IN(SELECT value FROM json_each(?))',(flag,encoded))
                elif action in ('suspend','bury'):
                    if set(value)-{'enabled'} or ('enabled' in value and type(value['enabled']) is not bool):raise CollectionError('La acción no es válida.')
                    if action=='suspend':
                        enabled=value.get('enabled',not all(r['suspended'] for r in rows));label='Suspender tarjetas' if enabled else 'Reactivar tarjetas'
                        db.execute('UPDATE cards SET suspended=? WHERE id IN(SELECT value FROM json_each(?))',(int(enabled),encoded))
                    else:
                        enabled=value.get('enabled',not any(r['buried_until']>now for r in rows));until=collection._day(now)[2] if enabled else 0;label='Enterrar tarjetas' if enabled else 'Desenterrar tarjetas'
                        db.execute('UPDATE cards SET buried_until=?,manual_buried_until=? WHERE id IN(SELECT value FROM json_each(?))',(until,until,encoded))
                else:
                    if set(value)!={'deck_id'} or type(value['deck_id']) is not int or not db.execute('SELECT 1 FROM decks WHERE id=?',(value['deck_id'],)).fetchone():raise CollectionError('No existe el mazo de destino.')
                    if db.execute('SELECT 1 FROM metadata WHERE key=?', ('filtered_deck:'+str(value['deck_id']),)).fetchone():raise CollectionError('Usá Construir para añadir tarjetas a un mazo filtrado.')
                    label='Cambiar mazo';db.execute('UPDATE cards SET deck_id=? WHERE id IN(SELECT value FROM json_each(?))',(value['deck_id'],encoded));db.execute('DELETE FROM custom_session_cards WHERE card_id IN(SELECT value FROM json_each(?))',(encoded,))
                for cid in cids:db.execute('INSERT INTO metadata(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',('browser_modified_at:'+str(cid),str(now)))
            elif action=='delete_notes':
                if value!={'confirm':True} or type(value.get('confirm')) is not bool:raise CollectionError('Confirmá la eliminación de las notas y sus tarjetas.')
                label='Eliminar notas'
                all_cards=list(db.execute('SELECT id FROM cards WHERE note_id IN(SELECT value FROM json_each(?))',(json.dumps(nids),)))
                for card in all_cards:
                    cid=card['id'];db.execute('DELETE FROM browser_manual_events WHERE card_id=?',(cid,));db.execute('DELETE FROM reviews WHERE card_id=?',(cid,));db.execute('DELETE FROM custom_session_cards WHERE card_id=?',(cid,));db.execute('DELETE FROM metadata WHERE key IN(?,?)',('generation_retired:'+str(cid),'browser_modified_at:'+str(cid)))
                db.execute('DELETE FROM cards WHERE note_id IN(SELECT value FROM json_each(?))',(json.dumps(nids),))
                for note in notes:db.execute('DELETE FROM metadata WHERE key=?',('authoring_note_type:'+note['guid'],))
                db.execute('DELETE FROM notes WHERE id IN(SELECT value FROM json_each(?))',(json.dumps(nids),))
            elif action=='change_note_type':
                from .browser_conversion import apply
                label='Cambiar tipo de nota';details=apply(collection,db,ids,mode,notes,value,now)
            elif action=='find_replace':
                from .browser_notes import replace
                label='Buscar y reemplazar';details=replace(db,notes,value,now)
            elif action in ('set_due','forget','grade','reposition'):
                from .browser_manual import apply
                label=apply(collection,db,rows,action,value,now)
            after=snapshot(db)
        remember(collection.path,label,before,after)
        return {'updated_cards':len(rows),'updated_notes':len(notes),'history':{'undo':label,'redo':None},**details}
