"""Collection-scoped editable note schemas. Independent of Anki's engine."""
import copy
import hashlib
import json
from .errors import CollectionError
from .note_generation import MODES

PREFIX='authoring_schema_v1:'

def fingerprint(value):
    return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()

def defaults(definition):
    kind=definition['kind']
    if kind=='image-occlusion':
        templates=[{'name':'Tarjeta 1','ord':0,'qfmt':'{{Occlusion}}','afmt':'{{Occlusion}}','bqfmt':'','bafmt':''}]
        css='.card { font-family: Arial; font-size: 20px; text-align: center; }'
    else:
        from .note_models import model
        original=model(1,definition['name'],0,kind)
        templates=original['tmpls'];css=original['css']
    from .note_templates import rename_references
    labels={'Front':'Anverso','Back':'Reverso','Add Reverse':'Añadir reverso','Text':'Texto','Back Extra':'Reverso Extra','Image':'Imagen','Masks':'Oclusión','Header':'Encabezado','Comments':'Comentarios'}
    return {'id':definition['id'],'kind':kind,'fields':[{'id':'f'+str(i),'name':labels.get(name,name),'role':name,'font':'Arial','size':20,'rtl':False,'sticky':False,'plain_text':False,'collapse':False,'exclude_from_search':False,'description':''} for i,name in enumerate(MODES[kind][1])],
            'templates':[{'id':'t'+str(i),'name':'Respuesta anidada' if kind=='cloze' else 'Oclusión de imagen' if kind=='image-occlusion' else 'Tarjeta '+str(i+1),**{key:rename_references(t.get(key,''),labels) for key in ('qfmt','afmt','bqfmt','bafmt')}} for i,t in enumerate(templates)],'css':css,'sort_field':'f0'}

def load(db,ident):
    from .note_admin import find
    definition=find(db,ident)
    row=db.execute('SELECT value FROM metadata WHERE key=?',(PREFIX+ident,)).fetchone()
    value=json.loads(row[0]) if row else defaults(definition)
    return dict(value,name=definition['name'],version=fingerprint(value))

def for_note(db,guid,kind):
    from .note_types import for_note as definition
    return load(db,definition(db,guid,kind)['id'])

def active(db,ident):
    return db.execute('SELECT 1 FROM metadata WHERE key=?',(PREFIX+ident,)).fetchone() is not None

def prepare(db,definition,values,editing=False):
    if not active(db,definition['id']):
        from .note_generation import prepare as legacy
        return legacy(definition['kind'],values)
    from .note_templates import prepare_fields
    return prepare_fields(db,load(db,definition['id']),values,editing=editing)

def generate(db,definition,values,tags=None,deck_name='',editing=False,preserve_ordinals=None):
    if not active(db,definition['id']):
        from .note_generation import generate as legacy
        return legacy(definition['kind'],values)
    from .note_templates import cards
    return cards(db,load(db,definition['id']),values,tags,deck_name,editing=editing,preserve_ordinals=preserve_ordinals)

def _editor_preferences_only(old,value):
    preference_keys={'font','size','rtl','sticky','plain_text','collapse','exclude_from_search','description'}
    if any(old[key]!=value[key] for key in value if key!='fields'):return False
    strip=lambda field:{key:item for key,item in field.items() if key not in preference_keys}
    return [strip(field) for field in old['fields']]==[strip(field) for field in value['fields']]

def validate(db,ident,draft,expected_version,confirm=False):
    import re
    from .note_templates import validate_markup,validate_css,rename_references,TOKEN
    from .note_admin import members
    old=load(db,ident)
    if expected_version!=old['version']:raise CollectionError('El tipo de nota cambió. Volvé a abrir sus opciones.')
    if not isinstance(draft,dict):raise CollectionError('El tipo de nota no es válido.')
    value={key:copy.deepcopy(draft.get(key)) for key in ('id','kind','fields','templates','css','sort_field')}
    if value['id']!=ident or value['kind']!=old['kind']:raise CollectionError('No se puede cambiar la identidad del tipo de nota.')
    old_fields={f['id']:f for f in old['fields']};fields=value['fields'];templates=value['templates']
    if not isinstance(fields,list) or not 1<=len(fields)<=128:raise CollectionError('El tipo debe tener entre 1 y 128 campos.')
    reserved={'FrontSide','Tags','Type','Deck','Subdeck','Card','CardFlag','Occlusion'}
    field_ids=set();field_names=set()
    for field in fields:
        if not isinstance(field,dict):raise CollectionError('El campo no es válido.')
        fid=field.get('id');name=field.get('name')
        if not isinstance(fid,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}',fid) or fid in field_ids:raise CollectionError('El identificador del campo no es válido.')
        if not isinstance(name,str) or not name.strip() or len(name)>100 or any(c in name for c in '{}:#<>') or any(ord(c)<32 for c in name) or name in reserved or name.casefold() in field_names:raise CollectionError('El nombre del campo está vacío, repetido o reservado.')
        field['name']=name.strip();field['role']=old_fields.get(fid,{}).get('role')
        field['font']=field.get('font','Arial');field['size']=field.get('size',20)
        if not isinstance(field['font'],str) or len(field['font'])>100 or type(field['size']) is not int or not 6<=field['size']<=200:raise CollectionError('La tipografía del campo no es válida.')
        for key in ('rtl','sticky','plain_text','collapse','exclude_from_search'):
            if type(field.get(key,False)) is not bool:raise CollectionError('La opción del campo no es válida.')
            field[key]=field.get(key,False)
        description=field.get('description','')
        if not isinstance(description,str) or len(description)>2000:raise CollectionError('La descripción del campo es demasiado larga.')
        field['description']=description;field_ids.add(fid);field_names.add(name.casefold())
    if value['sort_field'] not in field_ids:raise CollectionError('Elegí un campo para ordenar.')
    if old['kind']=='image-occlusion' and any(f.get('role') in ('Image','Masks') and f['id'] not in field_ids for f in old['fields']):raise CollectionError('No se pueden eliminar los campos de imagen y máscaras de una oclusión.')
    if not isinstance(templates,list) or not 1<=len(templates)<=128:raise CollectionError('El tipo debe tener entre 1 y 128 plantillas.')
    if old['kind'] in ('cloze','image-occlusion') and len(templates)!=1:raise CollectionError('Este tipo utiliza una sola plantilla.')
    template_ids=set();template_names=set()
    mapping={f['name']:next(x['name'] for x in fields if x['id']==f['id']) for f in old['fields'] if f['id'] in field_ids}
    known={f['name'] for f in fields}|reserved
    for template in templates:
        if not isinstance(template,dict):raise CollectionError('La plantilla no es válida.')
        tid=template.get('id');name=template.get('name')
        if not isinstance(tid,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}',tid) or tid in template_ids:raise CollectionError('El identificador de la plantilla no es válido.')
        if not isinstance(name,str) or not name.strip() or len(name)>100 or name.casefold() in template_names:raise CollectionError('El nombre de la plantilla está vacío o repetido.')
        for key in ('qfmt','afmt','bqfmt','bafmt'):
            markup=template.get(key,'');markup=rename_references(markup,mapping) if isinstance(markup,str) else markup
            template[key]=validate_markup(markup)
            for token in TOKEN.findall(markup):
                parts=token.strip().lstrip('#^/').split(':')
                if any(op not in {'text','cloze','type','hint','edit'} for op in parts[:-1]):raise CollectionError('La plantilla usa un filtro no implementado.')
                term=parts[-1]
                if term not in known:raise CollectionError('La plantilla usa un campo que no existe: '+term)
        template_ids.add(tid);template_names.add(name.casefold())
    value['css']=validate_css(value['css'])
    if _editor_preferences_only(old,value):return old,value,[],[]
    lost_fields=[(i,f) for i,f in enumerate(old['fields']) if f['id'] not in field_ids]
    note_rows=list(members(db,ident))
    lost_values=sum(any(json.loads(row['fields_json'])[i] for i,_ in lost_fields) for row in note_rows)
    lost_templates=[i for i,t in enumerate(old['templates']) if t['id'] not in template_ids]
    dropped_cards=[]
    if lost_templates and old['kind'] not in ('cloze','image-occlusion'):
        for row in note_rows:
            dropped_cards.extend(c[0] for c in db.execute('SELECT id FROM cards WHERE note_id=?',(row['id'],)) if db.execute('SELECT ordinal FROM cards WHERE id=?',(c[0],)).fetchone()[0] in lost_templates)
    if (lost_values or dropped_cards) and confirm is not True:raise CollectionError('Confirmá la eliminación de los datos de campos o tarjetas afectados.')
    return old,value,note_rows,dropped_cards

def save(collection,db,ident,draft,expected_version,now,confirm=False):
    old,value,rows,dropped_cards=validate(db,ident,draft,expected_version,confirm)
    current={key:old[key] for key in value}
    if current==value:return dict(old,affected_notes=0,safety_backup=None)
    # The caller holds BEGIN IMMEDIATE; a separate read connection backs up the
    # committed pre-change state while other collection writers are excluded.
    preference_only=_editor_preferences_only(old,value)
    safety=None if preference_only else collection.create_backup(force=True,safety=True,now=now)
    db.execute('INSERT INTO metadata(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',(PREFIX+ident,json.dumps(value,ensure_ascii=False)))
    if preference_only:return dict(load(db,ident),affected_notes=0,safety_backup=None)
    indices={field['id']:i for i,field in enumerate(old['fields'])}
    old_templates={template['id']:i for i,template in enumerate(old['templates'])}
    order={old_templates[t['id']]:i for i,t in enumerate(value['templates']) if t['id'] in old_templates}
    for row in rows:
        before=json.loads(row['fields_json']);fields=[before[indices[f['id']]] if f['id'] in indices else '' for f in value['fields']]
        if old['kind'] not in ('cloze','image-occlusion') and any(a!=b for a,b in order.items()):
            cards=list(db.execute('SELECT id,ordinal FROM cards WHERE note_id=?',(row['id'],)))
            for card in cards:db.execute('UPDATE cards SET ordinal=? WHERE id=?',(1000000+card['ordinal'],card['id']))
            for card in cards:
                target=order.get(card['ordinal'])
                if target is not None:db.execute('UPDATE cards SET ordinal=? WHERE id=?',(target,card['id']))
        for cid in dropped_cards:
            db.execute('DELETE FROM reviews WHERE card_id=?',(cid,));db.execute('DELETE FROM custom_session_cards WHERE card_id=?',(cid,));db.execute('DELETE FROM metadata WHERE key=?',('generation_retired:'+str(cid),));db.execute('DELETE FROM cards WHERE id=?',(cid,))
        from .generation_edit import reconcile
        clean=reconcile(db,row,fields,now)
        db.execute('UPDATE notes SET fields_json=?,modified_at=? WHERE id=?',(json.dumps(clean,ensure_ascii=False),now,row['id']))
    return dict(load(db,ident),affected_notes=len(rows),safety_backup=safety['name'])
