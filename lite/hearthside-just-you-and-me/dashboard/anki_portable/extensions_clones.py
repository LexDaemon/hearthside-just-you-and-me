"""Exact-field clone cleanup in a named deck branch, with preview and stale guards."""
import hashlib,json,re
from .errors import CollectionError
from .browser_history import LOCK,snapshot,remember


def _deck(payload,keys):
    if not isinstance(payload,dict) or set(payload)!=set(keys) or type(payload.get('deck_id')) is not int or payload['deck_id']<1:raise CollectionError('El alcance de la limpieza no es válido.')
    return payload['deck_id']


def _plan(collection,db,deck_id):
    branch=collection._branch(db,deck_id);deck=next(d for d in branch if d['id']==deck_id);dids=json.dumps([d['id'] for d in branch])
    notes=list(db.execute('SELECT n.* FROM notes n WHERE n.id IN(SELECT note_id FROM cards WHERE deck_id IN(SELECT value FROM json_each(?))) ORDER BY n.created_at,n.id LIMIT 200001',(dids,)))
    if len(notes)>200000:raise CollectionError('La limpieza supera 200000 notas; elegí un submazo.')
    groups={};byte_count=0
    for note in notes:
        byte_count+=len(note['fields_json'].encode())
        if byte_count>64*1024*1024:raise CollectionError('Los campos del alcance superan 64 MiB; elegí un submazo.')
        key=tuple(json.loads(note['fields_json']));groups.setdefault(key,[]).append(note['id'])
    clones=[nid for ids in groups.values() for nid in ids[1:]];nids=json.dumps([n['id'] for n in notes])
    cards=list(db.execute('SELECT * FROM cards WHERE note_id IN(SELECT value FROM json_each(?)) ORDER BY id',(nids,)))
    clone_ids=set(clones);deleted_cards=[c['id'] for c in cards if c['note_id'] in clone_ids]
    hasher=hashlib.sha256(json.dumps([[dict(d) for d in branch],[dict(n) for n in notes],[dict(c) for c in cards]],ensure_ascii=False,sort_keys=True,separators=(',',':')).encode())
    for review in db.execute('SELECT r.* FROM reviews r JOIN cards c ON c.id=r.card_id WHERE c.note_id IN(SELECT value FROM json_each(?)) ORDER BY r.id',(nids,)):hasher.update(json.dumps(dict(review),sort_keys=True,separators=(',',':')).encode())
    digest=hasher.hexdigest()
    count=db.execute('SELECT COUNT(*) FROM reviews WHERE card_id IN(SELECT value FROM json_each(?))',(json.dumps(deleted_cards),)).fetchone()[0]
    return {'deck_id':deck_id,'deck_name':deck['name'],'groups':sum(len(ids)>1 for ids in groups.values()),'clones':len(clones),'cards':len(deleted_cards),'reviews':count,'token':digest,'_note_ids':clones}


def plan(collection,payload):
    deck_id=_deck(payload,{'deck_id'})
    with collection._study_connection() as db:
        db.execute('PRAGMA query_only=ON');db.execute('BEGIN');result=_plan(collection,db,deck_id)
    result.pop('_note_ids');return result


def remove(collection,payload):
    deck_id=_deck(payload,{'deck_id','expected_token','confirm'})
    if payload['confirm'] is not True or not isinstance(payload['expected_token'],str) or not re.fullmatch('[a-f0-9]{64}',payload['expected_token']):raise CollectionError('Confirmá la eliminación de clones con una vista previa actual.')
    with LOCK:
        with collection._study_connection() as db:
            db.execute('BEGIN IMMEDIATE');result=_plan(collection,db,deck_id)
            if result['token']!=payload['expected_token']:raise CollectionError('Las notas o tarjetas cambiaron. Actualizá la vista previa antes de eliminar.')
            ids=result.pop('_note_ids');before=snapshot(db);encoded=json.dumps(ids)
            cards=list(db.execute('SELECT id FROM cards WHERE note_id IN(SELECT value FROM json_each(?))',(encoded,)))
            for row in cards:
                cid=row['id'];db.execute('DELETE FROM browser_manual_events WHERE card_id=?',(cid,));db.execute('DELETE FROM reviews WHERE card_id=?',(cid,));db.execute('DELETE FROM custom_session_cards WHERE card_id=?',(cid,));db.execute('DELETE FROM metadata WHERE key IN(?,?)',('generation_retired:'+str(cid),'browser_modified_at:'+str(cid)))
            for row in db.execute('SELECT guid FROM notes WHERE id IN(SELECT value FROM json_each(?))',(encoded,)).fetchall():db.execute('DELETE FROM metadata WHERE key=?',('authoring_note_type:'+row['guid'],))
            db.execute('DELETE FROM cards WHERE note_id IN(SELECT value FROM json_each(?))',(encoded,));db.execute('DELETE FROM notes WHERE id IN(SELECT value FROM json_each(?))',(encoded,));after=snapshot(db)
        remember(collection.path,'Eliminar clones de notas',before,after)
    return {'deleted_notes':len(ids),'deleted_cards':len(cards),'history':{'undo':'Eliminar clones de notas','redo':None}}
