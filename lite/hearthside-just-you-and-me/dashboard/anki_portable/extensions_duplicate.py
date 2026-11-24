"""Independent deck/subdeck duplication with fresh scheduling, in one transaction."""
import json,uuid
from .errors import CollectionError


def duplicate(collection,payload,now=None):
    if not isinstance(payload,dict) or set(payload)-{'deck_id','name','keep_tags'}:raise CollectionError('Los datos de duplicación no son válidos.')
    identifier=payload.get('deck_id');name=payload.get('name');keep_tags=payload.get('keep_tags',False)
    if type(identifier) is not int or identifier<1 or type(keep_tags) is not bool:raise CollectionError('El mazo o la opción de etiquetas no son válidos.')
    if not isinstance(name,str) or not name.strip() or '::' in name or len(name)>128 or any(ord(ch)<32 for ch in name):raise CollectionError('Usá un nombre simple de 1 a 128 caracteres.')
    at=collection._now(now)
    from .note_types import for_note,assign
    from .note_schema import generate
    from .latex import Renderer
    with collection._study_connection() as db:
        db.execute('BEGIN IMMEDIATE');branch=collection._branch(db,identifier);source=next(r for r in branch if r['id']==identifier)
        parent=source['name'].rsplit('::',1)[0]+'::' if '::' in source['name'] else ''
        root_name=parent+name.strip();destinations={r['id']:root_name+r['name'][len(source['name']):] for r in branch}
        for destination in destinations.values():
            if len(destination)>128 or len(destination.split('::'))>5:raise CollectionError('La copia supera 128 caracteres o los cinco niveles permitidos.')
            if db.execute('SELECT 1 FROM decks WHERE name=?',(destination,)).fetchone():raise CollectionError('Ya existe un mazo en la ruta de la copia. Elegí otro nombre.')
        prefix=source['name']+'::'
        note_ids=[r[0] for r in db.execute('SELECT DISTINCT c.note_id FROM cards c JOIN decks d ON d.id=c.deck_id WHERE d.id=? OR substr(d.name,1,?)=? ORDER BY c.note_id',(identifier,len(prefix),prefix))]
        deck_map={}
        for original in branch:
            new_id=db.execute('INSERT INTO decks(name,description,created_at) VALUES(?,?,?)',(destinations[original['id']],original['description'],at)).lastrowid
            deck_map[original['id']]=new_id
            options=db.execute('SELECT * FROM deck_options WHERE deck_id=?',(original['id'],)).fetchone()
            if options:
                columns=[k for k in options.keys() if k!='deck_id']
                db.execute('INSERT INTO deck_options(deck_id,'+','.join(columns)+') VALUES('+','.join('?' for _ in range(len(columns)+1))+')',[new_id]+[options[k] for k in columns])
        new_root=deck_map[identifier];position=db.execute('SELECT COALESCE(MAX(new_position),0) FROM cards').fetchone()[0];created_cards=0
        for note_id in note_ids:
            note=db.execute('SELECT * FROM notes WHERE id=?',(note_id,)).fetchone();definition=for_note(db,note['guid'],note['model_kind']);fields=json.loads(note['fields_json']);tags=json.loads(note['tags_json']) if keep_tags else []
            guid=uuid.uuid4().hex
            new_note=db.execute('INSERT INTO notes(guid,model_kind,fields_json,tags_json,created_at,modified_at) VALUES(?,?,?,?,?,?)',(guid,note['model_kind'],note['fields_json'],json.dumps(tags,ensure_ascii=False),at,at)).lastrowid
            assign(db,guid,definition['id'])
            locations={r['ordinal']:r['deck_id'] for r in db.execute('SELECT ordinal,deck_id FROM cards WHERE note_id=?',(note_id,))}
            generated=generate(db,definition,fields,tags=tags,deck_name=root_name)
            if not generated:raise CollectionError('Una nota de origen no genera tarjetas; la copia se canceló sin cambios.')
            for base in generated:
                original_deck=locations.get(base['ordinal'],identifier);destination=deck_map.get(original_deck,new_root);deck_name=destinations.get(original_deck,root_name)
                card=base if destination==new_root else next(c for c in generate(db,definition,fields,tags=tags,deck_name=deck_name) if c['ordinal']==base['ordinal'])
                card=Renderer(db,definition['id'],persist=True).cards([card])[0]
                position+=1;created_cards+=1
                db.execute('INSERT INTO cards(note_id,deck_id,ordinal,front,back,due_at,new_position,added_at) VALUES(?,?,?,?,?,?,?,?)',(new_note,destination,card['ordinal'],card['front'],card['back'],at,position,at))
        for destination in deck_map.values():
            if collection._options(db,destination)['new_order']=='random':
                from .order_policy import reposition
                reposition(db,destination,'random')
        db.commit()
    return {'id':new_root,'name':root_name,'decks':len(deck_map),'notes':len(note_ids),'cards':created_cards,'keep_tags':keep_tags}
