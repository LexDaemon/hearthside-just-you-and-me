"""Selected-note conversion with stable card IDs and explicit loss consent."""
import json
from .errors import CollectionError
from .browser_ops import selection
from .note_types import for_note,PREFIX as TYPE_KEY
from .note_schema import load,active,PREFIX as SCHEMA_KEY
from .note_admin import find

SPECIAL={'cloze','image-occlusion'}


def mapping(previous,current):
    result=[];used=set()
    for i,item in enumerate(current):
        old=next((n for n,f in enumerate(previous) if n not in used and f['name'].casefold()==item['name'].casefold()),None)
        if old is None:old=i if i<len(previous) and i not in used else -1
        result.append(old)
        if old>=0:used.add(old)
    return result


def validate_map(value,length,source_length,unique=True):
    if not isinstance(value,list) or len(value)!=length or any(type(i) is not int or i<-1 or i>=source_length for i in value) or unique and len([i for i in value if i>=0])!=len({i for i in value if i>=0}):raise CollectionError('La asignación de campos o tarjetas no es válida.')
    return value


def plan(collection,db,ids,mode,target,fields=None,templates=None):
    cards=selection(db,ids,mode);nids=sorted({c['note_id'] for c in cards})
    notes=list(db.execute('SELECT * FROM notes WHERE id IN(SELECT value FROM json_each(?)) ORDER BY id',(json.dumps(nids),)))
    sources={for_note(db,n['guid'],n['model_kind'])['id'] for n in notes}
    if len(sources)!=1:raise CollectionError('Seleccioná notas de un único tipo para cambiar su tipo de nota.')
    source=load(db,next(iter(sources)));destination=load(db,find(db,target)['id'])
    fields=validate_map(mapping(source['fields'],destination['fields']) if fields is None else fields,len(destination['fields']),len(source['fields']),unique=False)
    special=source['kind'] in SPECIAL or destination['kind'] in SPECIAL
    templates=[] if special and templates is None else mapping(source['templates'],destination['templates']) if templates is None else templates
    templates=validate_map(templates,0 if special else len(destination['templates']),len(source['templates']))
    all_cards=list(db.execute('SELECT * FROM cards WHERE note_id IN(SELECT value FROM json_each(?)) ORDER BY id',(json.dumps(nids),)))
    keep=lambda c: c['ordinal']<len(destination['templates']) if special and destination['kind'] not in SPECIAL else True if special else c['ordinal'] in templates
    dropped=[c['id'] for c in all_cards if not keep(c)]
    lost_fields=sum(bool(value) for n in notes for i,value in enumerate(json.loads(n['fields_json'])) if i not in fields)
    reviews=db.execute('SELECT COUNT(*) FROM reviews WHERE card_id IN(SELECT value FROM json_each(?))',(json.dumps(dropped),)).fetchone()[0]
    return {'source':source['id'],'target':destination['id'],'source_version':source['version'],'target_version':destination['version'],'fields':fields,'templates':templates,'lost_fields':lost_fields,'lost_cards':len(dropped),'lost_reviews':reviews,'notes':len(notes)}


def apply(collection,db,ids,mode,notes,value,now):
    keys={'source','target','source_version','target_version','fields','templates','confirm'}
    if set(value)-keys-{'losses'} or not keys<=set(value) or type(value['confirm']) is not bool:raise CollectionError('Los datos de conversión no son válidos.')
    current=plan(collection,db,ids,mode,value['target'],value['fields'],value['templates'])
    if any(value[k]!=current[k] for k in ('source','source_version','target_version')):raise CollectionError('El tipo de nota cambió. Volvé a abrir la conversión.')
    losses={key:current[key] for key in ('lost_fields','lost_cards','lost_reviews')}
    if 'losses' in value and (not isinstance(value['losses'],dict) or value['losses']!=losses or any(type(v) is not int for v in value['losses'].values())):raise CollectionError('Cambió la pérdida prevista. Revisá la conversión de nuevo.')
    if (current['lost_cards'] or current['lost_fields']) and (not value['confirm'] or value.get('losses')!=losses):raise CollectionError('Confirmá la pérdida exacta de los campos o tarjetas sin asignación antes de continuar.')
    source=load(db,current['source']);target=load(db,current['target'])

    if not active(db,target['id']):
        document={k:target[k] for k in ('id','kind','fields','templates','css','sort_field')}
        db.execute('INSERT INTO metadata(key,value) VALUES(?,?)',(SCHEMA_KEY+target['id'],json.dumps(document,ensure_ascii=False)))
    from .note_templates import prepare_fields,cards as generate
    from .latex import Renderer
    created=deleted=0
    for note in notes:
        old_fields=json.loads(note['fields_json']);fields=[old_fields[i] if i>=0 else '' for i in current['fields']]
        fields,images=prepare_fields(db,target,fields,editing=True)
        for name in set(images):
            if not db.execute('SELECT 1 FROM media WHERE name=?',(name,)).fetchone():raise CollectionError('Una imagen de la nota no está en la colección.')
        old_cards=list(db.execute('SELECT * FROM cards WHERE note_id=? ORDER BY id',(note['id'],)))
        special=source['kind'] in SPECIAL or target['kind'] in SPECIAL
        ordinal_map={c['ordinal']:c['ordinal'] for c in old_cards if target['kind'] in SPECIAL or c['ordinal']<len(target['templates'])} if special else {old:new for new,old in enumerate(current['templates']) if old>=0}
        keep={ordinal_map[c['ordinal']]:c for c in old_cards if c['ordinal'] in ordinal_map}
        dropped=[c for c in old_cards if c['ordinal'] not in ordinal_map]
        for card in dropped:
            cid=card['id'];db.execute('DELETE FROM reviews WHERE card_id=?',(cid,));db.execute('DELETE FROM custom_session_cards WHERE card_id=?',(cid,));db.execute('DELETE FROM browser_manual_events WHERE card_id=?',(cid,));db.execute('DELETE FROM metadata WHERE key IN(?,?)',('generation_retired:'+str(cid),'browser_modified_at:'+str(cid)));db.execute('DELETE FROM cards WHERE id=?',(cid,));deleted+=1
        for i,card in enumerate(keep.values()):
            db.execute('UPDATE cards SET ordinal=? WHERE id=?',(1000000+i,card['id']))
            if source['id']!=target['id']:db.execute('DELETE FROM metadata WHERE key=?',('generation_retired:'+str(card['id']),))
        db.execute('UPDATE notes SET model_kind=?,fields_json=?,modified_at=? WHERE id=?',(target['kind'],json.dumps(fields,ensure_ascii=False),now,note['id']))
        db.execute('INSERT INTO metadata(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',(TYPE_KEY+note['guid'],target['id']))
        deck=old_cards[0]['deck_id'];deck_name=db.execute('SELECT name FROM decks WHERE id=?',(deck,)).fetchone()[0]
        generated=Renderer(db,target['id'],persist=True).cards(generate(db,target,fields,json.loads(note['tags_json']),deck_name,editing=True,preserve_ordinals=set(keep)))
        position=db.execute('SELECT COALESCE(MAX(new_position),0) FROM cards').fetchone()[0]
        for rendered in generated:
            ordinal=int(rendered['ordinal']);card=keep.pop(ordinal,None)
            if card:
                db.execute('UPDATE cards SET ordinal=?,front=?,back=? WHERE id=?',(ordinal,rendered['front'],rendered['back'],card['id']))
                db.execute('INSERT INTO metadata(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',('browser_modified_at:'+str(card['id']),str(now)))
            else:
                position+=1;db.execute('INSERT INTO cards(note_id,deck_id,ordinal,front,back,due_at,new_position,added_at) VALUES(?,?,?,?,?,?,?,?)',(note['id'],deck,ordinal,rendered['front'],rendered['back'],now,position,now));created+=1
        if keep:raise CollectionError('La conversión no pudo conservar todas las tarjetas asignadas.')
    return {'changed_notes':len(notes),'created_cards':created,'deleted_cards':deleted,'deleted_reviews':current['lost_reviews']}
