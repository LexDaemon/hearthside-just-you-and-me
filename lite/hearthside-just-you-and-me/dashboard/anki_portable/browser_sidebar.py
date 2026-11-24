"""Transactional tag catalog operations for the local browser sidebar."""
import json
from .errors import CollectionError
from .browser_history import LOCK,snapshot,remember,operation


def initialize(db):
    db.execute('CREATE TABLE IF NOT EXISTS browser_tags(name TEXT PRIMARY KEY COLLATE NOCASE)')
    db.execute('INSERT OR IGNORE INTO browser_tags SELECT DISTINCT value FROM notes,json_each(notes.tags_json)')
    for event,suffix in [('INSERT','insert'),('UPDATE OF tags_json','update')]:
        db.execute('CREATE TRIGGER IF NOT EXISTS browser_tags_'+suffix+' AFTER '+event+' ON notes BEGIN INSERT OR IGNORE INTO browser_tags SELECT value FROM json_each(NEW.tags_json); END')


def name(value)->str:
    if not isinstance(value,str) or not value or len(value)>200 or any(c.isspace() or ord(c)<32 for c in value) or any(not part for part in value.split('::')):raise CollectionError('El nombre de etiqueta no es válido.')
    return value


def descendant(tag,parent):
    return tag.casefold()==parent.casefold() or tag.casefold().startswith(parent.casefold()+'::')


def command(collection,action,value,now=None):
    expected={'rename_tag':{'name','new_name'},'remove_tag':{'name'},'add_tag':{'name'},'cleanup_tags':set()}
    if action not in expected or not isinstance(value,dict) or set(value)!=expected[action]:raise CollectionError('La operación de barra lateral no es válida.')
    at=collection._now(now)
    with LOCK:
        with collection._study_connection() as db:
            db.execute('BEGIN IMMEDIATE');before=snapshot(db)
            catalog=[r[0] for r in db.execute('SELECT name FROM browser_tags')]
            label={'rename_tag':'Renombrar etiqueta','remove_tag':'Eliminar etiqueta','add_tag':'Añadir etiqueta','cleanup_tags':'Limpiar etiquetas no utilizadas'}[action]
            changed=0
            if action=='cleanup_tags':
                used={t for row in db.execute('SELECT tags_json FROM notes') for t in json.loads(row[0])}
                unused=[t for t in catalog if not any(descendant(current,t) for current in used)]
                db.executemany('DELETE FROM browser_tags WHERE name=?',[(t,) for t in unused]);changed=len(unused)
            else:
                original=name(value['name'])
                if action=='add_tag':db.execute('INSERT OR IGNORE INTO browser_tags(name) VALUES(?)',(original,));changed=1
                else:
                    affected=[t for t in catalog if descendant(t,original)]
                    if not affected:raise CollectionError('La etiqueta ya no existe.')
                    replacement=name(value['new_name']) if action=='rename_tag' else None
                    renamed={t:name(replacement+t[len(original):]) for t in affected} if replacement is not None else {}
                    db.executemany('DELETE FROM browser_tags WHERE name=?',[(t,) for t in affected])
                    for row in list(db.execute('SELECT id,tags_json FROM notes')):
                        old=json.loads(row['tags_json'])
                        updated=sorted(set(renamed.get(t,t) for t in old)) if replacement is not None else [t for t in old if not descendant(t,original)]
                        if old!=updated:db.execute('UPDATE notes SET tags_json=?,modified_at=? WHERE id=?',(json.dumps(updated,ensure_ascii=False),at,row['id']));changed+=1
                    db.executemany('INSERT OR IGNORE INTO browser_tags(name) VALUES(?)',[(t,) for t in renamed.values()])
            after=snapshot(db)
        remember(collection.path,label,before,after)
        with collection._study_connection() as db:return {'changed':changed,'history':operation(collection.path,db)}
