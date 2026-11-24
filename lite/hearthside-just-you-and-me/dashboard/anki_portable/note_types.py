"""Versioned own note definitions. Canonical model kinds and study rows stay unchanged."""
import json
import uuid
from .errors import CollectionError

REGISTRY='authoring_note_types_v1'
PREFIX='authoring_note_type:'
from .note_generation import MODES
BUILTINS=[{'id':kind,'name':spec[0],'kind':kind,'builtin':True} for kind,spec in MODES.items()]


def definitions(db):
    row=db.execute('SELECT value FROM metadata WHERE key=?',(REGISTRY,)).fetchone()
    from .note_admin import load,OVERRIDES,members
    overrides=load(db,OVERRIDES)
    builtin=[dict(t,name=overrides.get(t['id'],{}).get('name',t['name'])) for t in BUILTINS if not overrides.get(t['id'],{}).get('deleted') or members(db,t['id'])]
    return builtin+(json.loads(row[0]) if row else [])


def validate_definition(value):
    if not isinstance(value,dict) or value.get('kind') not in MODES:
        raise CollectionError('El tipo de nota no es compatible.')
    ident,name=value.get('id'),value.get('name')
    if not isinstance(ident,str) or not ident.startswith('nt_') or len(ident)!=35 or any(c not in '0123456789abcdef' for c in ident[3:]):
        raise CollectionError('El identificador de tipo no es válido.')
    if not isinstance(name,str) or not name.strip() or len(name)>100 or any(ord(c)<32 for c in name):
        raise CollectionError('El nombre del tipo debe tener entre 1 y 100 caracteres.')
    return {'id':ident,'name':name.strip(),'kind':value['kind'],'builtin':False}


def register(db,definition):
    value=validate_definition(definition);types=definitions(db)
    existing=next((t for t in types if t['id']==value['id']),None)
    if existing:
        if existing!=value:raise CollectionError('Un tipo importado tiene un identificador en conflicto.')
        return existing
    if any(t['name'].casefold()==value['name'].casefold() for t in types):
        raise CollectionError('Ya existe un tipo de nota con ese nombre.')
    custom=[t for t in types if not t['builtin']]
    if len(custom)>=500:raise CollectionError('La colección admite hasta 500 tipos personalizados.')
    custom.append(value)
    db.execute('INSERT INTO metadata(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
               (REGISTRY,json.dumps(custom,ensure_ascii=False)))
    return value


def for_note(db,guid,kind):
    row=db.execute('SELECT value FROM metadata WHERE key=?',(PREFIX+guid,)).fetchone()
    ident=row[0] if row else kind
    return next((t for t in definitions(db) if t['id']==ident),next(t for t in BUILTINS if t['kind']==kind))


def assign(db,guid,ident):
    db.execute('INSERT INTO metadata(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',(PREFIX+guid,ident))


class NoteTypesMixin:
    def note_schema(self,ident):
        from .note_schema import load
        with self._study_connection() as db:
            value=load(db,ident)
            value['note_count']=db.execute('SELECT COUNT(*) FROM notes n WHERE COALESCE((SELECT value FROM metadata WHERE key=?||n.guid),n.model_kind)=?',(PREFIX,ident)).fetchone()[0]
            return value

    def note_field_schemas(self):
        from .note_schema import load
        with self._study_connection() as db:
            result={}
            for definition in definitions(db):
                schema=load(db,definition['id'])
                from .note_schema import active
                result[definition['id']]={key:schema[key] for key in ('id','kind','fields','sort_field','version')}
                result[definition['id']]['editable']=active(db,definition['id'])
            return result

    def save_note_schema(self,ident,draft,expected_version,confirm=False,now=None):
        from .note_schema import save
        at=self._now(now)
        with self._study_connection() as db:
            db.execute('BEGIN IMMEDIATE')
            return save(self,db,ident,draft,expected_version,at,confirm)

    def preview_note_schema(self,ident,draft,expected_version,fields):
        from .note_schema import validate
        from .note_templates import prepare_fields,cards
        from .latex import Renderer
        with self._study_connection() as db:
            _,value,_,_=validate(db,ident,draft,expected_version,confirm=True)
            clean,_=prepare_fields(db,value,fields,editing=True)
            return {'fields':clean,'cards':Renderer(db,ident).cards(cards(db,value,clean,editing=True))}

    def manage_note_types(self):
        from .note_admin import listing
        with self._study_connection() as db:return listing(db)

    def rename_note_type(self,ident,name):
        from .note_admin import rename
        with self._study_connection() as db:
            db.execute('BEGIN IMMEDIATE');return rename(db,ident,name)

    def note_types(self):
        with self._study_connection() as db:return definitions(db)

    def delete_note_type(self,ident,expected_note_count):
        from .note_admin import delete
        with self._study_connection() as db:
            db.execute('BEGIN IMMEDIATE');return delete(db,ident,expected_note_count)

    def note_latex(self,ident):
        from .note_admin import latex_options
        with self._study_connection() as db:return latex_options(db,ident)

    def set_note_latex(self,ident,options):
        from .note_admin import set_latex
        with self._study_connection() as db:
            db.execute('BEGIN IMMEDIATE');return set_latex(db,ident,options)

    def create_note_type(self,source,name):
        if not isinstance(source,str):raise CollectionError('Elegí un tipo de origen válido.')
        with self._study_connection() as db:
            db.execute('BEGIN IMMEDIATE')
            parent=next((t for t in definitions(db) if t['id']==source),None)
            if parent is None:parent=next((t for t in BUILTINS if t['id']==source),None)
            if parent is None:raise CollectionError('No existe el tipo de origen.')
            value=register(db,{'id':'nt_'+uuid.uuid4().hex,'name':name,'kind':parent['kind']})
            from .note_admin import load,store
            from .latex import SETTINGS,settings
            options=load(db,SETTINGS);options[value['id']]=settings(db,parent['id']);store(db,SETTINGS,options)
            from .note_schema import active,load as schema_load,PREFIX as SCHEMA_PREFIX
            if active(db,parent['id']):
                schema=schema_load(db,parent['id']);schema.pop('version',None);schema.pop('name',None);schema['id']=value['id']
                db.execute('INSERT INTO metadata(key,value) VALUES(?,?)',(SCHEMA_PREFIX+value['id'],json.dumps(schema,ensure_ascii=False)))
            return value
