"""Validate and transport editable schemas without a native Anki runtime."""
import copy
import hashlib
import json
import sqlite3
from .errors import CollectionError
from .note_generation import MODES
from .note_schema import PREFIX,fingerprint

KEY='hearthside_schema_v1'

def document(value):
    if not isinstance(value,dict):raise CollectionError('El esquema de campos del paquete no es válido.')
    raw={k:copy.deepcopy(value.get(k)) for k in ('id','kind','fields','templates','css','sort_field')}
    ident=raw['id'];kind=raw['kind']
    if kind not in MODES or not isinstance(ident,str):raise CollectionError('La identidad del esquema importado no es válida.')
    from .note_types import validate_definition,REGISTRY
    if ident in MODES:
        if ident!=kind:raise CollectionError('El esquema predefinido no coincide con su tipo.')
    else:validate_definition({'id':ident,'kind':kind,'name':'Tipo importado'})
    try:
        with sqlite3.connect(':memory:') as db:
            db.row_factory=sqlite3.Row
            db.executescript('CREATE TABLE metadata(key TEXT PRIMARY KEY,value TEXT); CREATE TABLE notes(id INTEGER,guid TEXT,model_kind TEXT,fields_json TEXT,tags_json TEXT,created_at INTEGER,modified_at INTEGER);')
            if ident not in MODES:db.execute('INSERT INTO metadata VALUES(?,?)',(REGISTRY,json.dumps([{'id':ident,'kind':kind,'name':'Tipo importado','builtin':False}])))
            db.execute('INSERT INTO metadata VALUES(?,?)',(PREFIX+ident,json.dumps(raw,ensure_ascii=False)))
            from .note_schema import validate
            _,clean,_,_=validate(db,ident,raw,fingerprint(raw),confirm=True)
            if kind=='image-occlusion':
                roles=[f.get('role') for f in clean['fields']]
                if any(roles.count(role)!=1 for role in MODES[kind][1]):raise CollectionError('Los campos de la oclusión no son válidos.')
            return clean
    except (KeyError,TypeError,ValueError,AttributeError,IndexError,sqlite3.Error) as error:
        raise CollectionError('El esquema de campos del paquete está dañado.') from error

def apply_model(model,schema,definition,now,cards=None):
    schema=document(schema);kind=schema['kind']
    result=copy.deepcopy(model);base=copy.deepcopy(result['tmpls'][0])
    result.update(name=definition['name'],css=schema['css'],sortf=next(i for i,f in enumerate(schema['fields']) if f['id']==schema['sort_field']),type=1 if kind=='cloze' else 0)
    result['flds']=[{'name':f['name'],'ord':i,'sticky':f['sticky'],'rtl':f['rtl'],'font':f['font'],'size':f['size'],'media':[],'description':f.get('description',''),'plainText':f.get('plain_text',False),'collapsed':f.get('collapse',False),'excludeFromSearch':f.get('exclude_from_search',False)} for i,f in enumerate(schema['fields'])]
    if kind=='image-occlusion':
        by_ordinal={c['ordinal']:c for c in cards or []}
        from .note_models import io_ordinals
        for template,ordinal in zip(result['tmpls'],io_ordinals(result)):
            card=by_ordinal.get(ordinal)
            if card:template.update(qfmt=card['front'],afmt=card['back'])
    else:
        result['tmpls']=[dict(base,ord=i,name=t['name'],qfmt=t['qfmt'],afmt=t['afmt'],bqfmt=t.get('bqfmt',''),bafmt=t.get('bafmt','')) for i,t in enumerate(schema['templates'])]
        result['req']=[]
    if definition['builtin']:result['hearthside_builtin']=definition
    else:result['hearthside_note_type']=definition
    result[KEY]=schema
    return result

def resolve_imports(db,notes):
    """Never replace an unrelated destination type with a different schema."""
    from .note_admin import members
    from .note_schema import load,defaults
    from .note_types import definitions,register,BUILTINS
    incoming_guids={n['guid'] for n in notes};cache={}
    for note in notes:
        raw=note.get('editable_schema')
        if raw is None:continue
        value=document(raw);source_ident=value['id'];key=fingerprint(value)
        if key not in cache:
            current_types=definitions(db);target=next((t for t in current_types if t['id']==source_ident),None)
            source=note.get('authoring_type') or note.get('builtin_definition') or next((t for t in BUILTINS if t['id']==source_ident),None)
            if source is None:raise CollectionError('El esquema importado no tiene una definición de tipo.')
            current=load(db,source_ident) if target else None
            same=current is not None and all(current[k]==value[k] for k in value)
            foreign=target is not None and any(row['guid'] not in incoming_guids for row in members(db,source_ident))
            if foreign and not same:
                clone_id='nt_'+hashlib.sha256((source_ident+key).encode()).hexdigest()[:32]
                target=next((t for t in current_types if t['id']==clone_id),None)
                if target is None:
                    base=source['name']+' (importado)';name=base;number=2
                    while any(t['name'].casefold()==name.casefold() for t in current_types):name=base+' '+str(number);number+=1
                    target=register(db,{'id':clone_id,'name':name,'kind':value['kind']})
                value['id']=clone_id
            elif target is None:
                target=register(db,source)
            value['id']=target['id']
            db.execute('INSERT INTO metadata(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',(PREFIX+target['id'],json.dumps(value,ensure_ascii=False)))
            cache[key]=target
        target=cache[key]
        if target['builtin']:note['builtin_definition']=target;note['authoring_type']=None
        else:note['authoring_type']=target;note['builtin_definition']=None
        note['resolved_type']=target['id']
