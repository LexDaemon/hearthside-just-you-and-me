"""Own note-type administration, with explicit destructive-version confirmation."""
import json
from .errors import CollectionError
from .note_types import definitions,REGISTRY,PREFIX

OVERRIDES='authoring_builtin_overrides_v1'


def load(db,key):
    row=db.execute('SELECT value FROM metadata WHERE key=?',(key,)).fetchone()
    return json.loads(row[0]) if row else {}


def store(db,key,value):
    db.execute('INSERT INTO metadata(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',(key,json.dumps(value,ensure_ascii=False)))


def members(db,ident):
    return list(db.execute('SELECT n.* FROM notes n WHERE COALESCE((SELECT value FROM metadata WHERE key=?||n.guid),n.model_kind)=? ORDER BY n.id',(PREFIX,ident)))


def listing(db):
    result=[]
    for value in definitions(db):
        notes=members(db,value['id']);cards=sum(db.execute('SELECT COUNT(*) FROM cards WHERE note_id=?',(n['id'],)).fetchone()[0] for n in notes)
        result.append(dict(value,note_count=len(notes),card_count=cards))
    return result


def find(db,ident):
    if not isinstance(ident,str):raise CollectionError('El identificador del tipo no es válido.')
    value=next((t for t in definitions(db) if t['id']==ident),None)
    if value is None:raise CollectionError('No existe ese tipo de nota.')
    return value


def rename(db,ident,name):
    current=find(db,ident)
    if not isinstance(name,str) or not name.strip() or len(name)>100 or any(ord(c)<32 for c in name):raise CollectionError('El nombre debe tener entre 1 y 100 caracteres.')
    name=name.strip()
    if any(t['id']!=ident and t['name'].casefold()==name.casefold() for t in definitions(db)):raise CollectionError('Ya existe un tipo con ese nombre.')
    if current['builtin']:
        data=load(db,OVERRIDES);data.setdefault(ident,{})['name']=name;store(db,OVERRIDES,data)
    else:
        data=[dict(t,name=name) if t['id']==ident else t for t in definitions(db) if not t['builtin']];store(db,REGISTRY,data)
    return dict(current,name=name)


def delete(db,ident,expected):
    from .generation_edit import RETIRED
    from .latex import SETTINGS
    current=find(db,ident);notes=members(db,ident)
    if type(expected) is not int or expected<0 or len(notes)!=expected:raise CollectionError('Cambió la cantidad de notas. Volvé a revisar antes de eliminar.')
    if len(definitions(db))<=1:raise CollectionError('La colección debe conservar al menos un tipo de nota.')
    count=0
    for note in notes:
        cards=list(db.execute('SELECT id FROM cards WHERE note_id=?',(note['id'],)))
        for card in cards:
            db.execute('DELETE FROM reviews WHERE card_id=?',(card['id'],));db.execute('DELETE FROM custom_session_cards WHERE card_id=?',(card['id'],));db.execute('DELETE FROM metadata WHERE key=?',(RETIRED+str(card['id']),))
        count+=len(cards);db.execute('DELETE FROM cards WHERE note_id=?',(note['id'],));db.execute('DELETE FROM notes WHERE id=?',(note['id'],));db.execute('DELETE FROM metadata WHERE key=?',(PREFIX+note['guid'],))
    if current['builtin']:
        overrides=load(db,OVERRIDES);overrides.setdefault(ident,{})['deleted']=True;store(db,OVERRIDES,overrides)
    else:store(db,REGISTRY,[t for t in definitions(db) if not t['builtin'] and t['id']!=ident])
    options=load(db,SETTINGS);options.pop(ident,None);store(db,SETTINGS,options)
    return {'deleted_notes':len(notes),'deleted_cards':count}


def latex_options(db,ident):
    from .latex import settings,status
    find(db,ident);return {'options':settings(db,ident),'runtime':status()}


def set_latex(db,ident,value):
    from .latex import validate,compile_tex,Renderer,SETTINGS
    from .note_generation import generate
    from .generation_edit import RETIRED
    current=find(db,ident);value=validate(value);compile_tex(r'\(1\)',value)
    options=load(db,SETTINGS);options[ident]=value;store(db,SETTINGS,options)
    renderer=Renderer(db,ident,persist=True,options=value)
    for note in members(db,ident):
        active={c['ordinal']:c for c in generate(current['kind'],json.loads(note['fields_json']))}
        for card in db.execute('SELECT * FROM cards WHERE note_id=?',(note['id'],)).fetchall():
            rendered=active.get(card['ordinal'])
            if rendered is None:
                old=db.execute('SELECT value FROM metadata WHERE key=?',(RETIRED+str(card['id']),)).fetchone()
                if old:rendered=next((c for c in generate(current['kind'],json.loads(old[0])['fields']) if c['ordinal']==card['ordinal']),None)
            if rendered:
                rendered=renderer.cards([rendered])[0];db.execute('UPDATE cards SET front=?,back=? WHERE id=?',(rendered['front'],rendered['back'],card['id']))
    return {'options':value}
