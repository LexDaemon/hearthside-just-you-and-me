"""Reconcile generated content by ordinal; never erase an existing review."""
import json
from .note_generation import prepare, generate
from .errors import CollectionError

RETIRED='generation_retired:'


def reconcile(db,row,fields,now):
    from .note_types import for_note
    from .note_schema import prepare as prepare_type,generate as generate_type
    definition=for_note(db,row['guid'],row['model_kind'])
    clean,images=prepare_type(db,definition,fields,editing=True)
    for name in set(images):
        if not db.execute('SELECT 1 FROM media WHERE name=?',(name,)).fetchone():raise CollectionError('Una imagen ya no está en la colección.')
    from .latex import Renderer
    from .note_types import for_note
    definition=for_note(db,row['guid'],row['model_kind'])
    existing=list(db.execute('SELECT * FROM cards WHERE note_id=? ORDER BY id',(row['id'],)))
    generated={c['ordinal']:c for c in Renderer(db,definition['id'],persist=True).cards(generate_type(db,definition,clean,tags=json.loads(row['tags_json']),editing=True,preserve_ordinals={c['ordinal'] for c in existing}))}
    deck=existing[0]['deck_id'];position=db.execute('SELECT COALESCE(MAX(new_position),0) FROM cards').fetchone()[0]
    for card in existing:
        key=RETIRED+str(card['id']);saved=db.execute('SELECT value FROM metadata WHERE key=?',(key,)).fetchone()
        new=generated.pop(card['ordinal'],None)
        if new is not None:
            db.execute('UPDATE cards SET front=?,back=? WHERE id=?',(new['front'],new['back'],card['id']))
            if saved:
                db.execute('UPDATE cards SET suspended=? WHERE id=?',(json.loads(saved[0])['suspended'],card['id']));db.execute('DELETE FROM metadata WHERE key=?',(key,))
        else:
            if not saved:db.execute('INSERT INTO metadata(key,value) VALUES(?,?)',(key,json.dumps({'fields':json.loads(row['fields_json']),'suspended':card['suspended']})))
            db.execute('UPDATE cards SET suspended=1 WHERE id=?',(card['id'],))
            db.execute('DELETE FROM custom_session_cards WHERE card_id=?',(card['id'],))
    for index,new in enumerate(generated.values(),1):
        db.execute('INSERT INTO cards(note_id,deck_id,ordinal,front,back,due_at,new_position,added_at) VALUES(?,?,?,?,?,?,?,?)',(row['id'],deck,new['ordinal'],new['front'],new['back'],now,position+index,now))
    return clean


def retired_from_package(raw,kind):
    try:data=json.loads(raw or '{}')
    except (TypeError,ValueError):return None
    retired=data.get('hearthside',{}).get('retired_generation') if isinstance(data,dict) and isinstance(data.get('hearthside',{}),dict) else None
    if retired is None:return None
    if not isinstance(retired,dict) or type(retired.get('suspended')) is not int or retired['suspended'] not in (0,1):raise CollectionError('El estado de tarjeta retirada no es válido.')
    fields,_=prepare(kind,retired.get('fields'));generate(kind,fields)
    return {'fields':fields,'suspended':retired['suspended']}
