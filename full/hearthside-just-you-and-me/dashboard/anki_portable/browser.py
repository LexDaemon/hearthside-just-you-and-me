"""Local collection browser. Reading never advances or reschedules a card."""
import hashlib
import html
import json
import re
from .errors import CollectionError

from .note_generation import MODES
MODEL_LABELS = {kind:spec[0] for kind,spec in MODES.items()}


def plain(value):
    return html.unescape(re.sub(r'<[^>]*>', '', str(value or '')))


def version(row):
    return hashlib.sha256(json.dumps([row[k] for k in ('fields_json','tags_json','model_kind','modified_at')],ensure_ascii=False).encode()).hexdigest()


def configure(db):
    db.create_function('browser_plain', 1, plain, deterministic=True)
    db.create_function('browser_fold', 1, lambda v: str(v or '').casefold(), deterministic=True)
    from .preferences import load
    import unicodedata
    ignore=load(db)['ignore_accents']
    def fold(value):
        text=str(value or '').casefold()
        return ''.join(c for c in unicodedata.normalize('NFD',text) if not unicodedata.combining(c)) if ignore else text
    db.create_function('browser_match_fold',1,fold,deterministic=True)
    from .note_schema import load as schema_load
    from .note_types import definitions
    schemas={t['id']:schema_load(db,t['id']) for t in definitions(db)}
    def searchable(encoded,ident):
        values=json.loads(encoded);specs=schemas.get(ident,{}).get('fields',[])
        return plain(' '.join(value for i,value in enumerate(values) if i>=len(specs) or not specs[i].get('exclude_from_search')))
    def named_field(encoded,ident,name):
        values=json.loads(encoded)
        for i,field in enumerate(schemas.get(ident,{}).get('fields',[])):
            if field['name'].casefold()==name.casefold():return plain(values[i]) if i<len(values) else ''
        return None
    db.create_function('browser_search_text',2,searchable,deterministic=True)
    db.create_function('browser_named_field',3,named_field,deterministic=True)
    db.create_function('browser_type_label',1,lambda ident:schemas.get(ident,{}).get('name',ident),deterministic=True)



def initialize(db):
    from .browser_sidebar import initialize as initialize_tags
    initialize_tags(db)
    db.execute('CREATE TABLE IF NOT EXISTS browser_manual_events (id INTEGER PRIMARY KEY AUTOINCREMENT,card_id INTEGER NOT NULL REFERENCES cards(id) ON DELETE CASCADE,at INTEGER NOT NULL,kind TEXT NOT NULL,payload TEXT NOT NULL)')
    db.execute('CREATE TABLE IF NOT EXISTS browser_saved_searches (name TEXT PRIMARY KEY, query TEXT NOT NULL)')
    if 'flag' not in {row['name'] for row in db.execute('PRAGMA table_info(cards)')}:
        db.execute('ALTER TABLE cards ADD COLUMN flag INTEGER NOT NULL DEFAULT 0 CHECK(flag BETWEEN 0 AND 7)')


class BrowserMixin:
    def browser_copy(self,ids,mode):
        from .browser_ops import selection
        with self._study_connection() as db:
            db.execute('PRAGMA query_only=ON');db.execute('BEGIN')
            rows=selection(db,ids,mode);nids={r['note_id'] for r in rows}
            if len(nids)!=1:raise CollectionError('Seleccioná una única nota para crear una copia.')
            nid=next(iter(nids));deck=rows[0]['deck_id']
        result=self.browser_table(query='nid:'+str(nid),mode='notes')
        if not result['rows']:raise CollectionError('La nota ya no existe.')
        row=result['rows'][0]
        return {'note':dict(id=nid,**{key:row[key] for key in ('note_id','note_type_id','model_kind','fields','tags','version')}),'deck_id':deck}

    def browser_sidebar(self,action,value,now=None):
        from .browser_sidebar import command
        return command(self,action,value,now)

    def browser_change_plan(self,ids,mode,target,fields=None,templates=None):
        from .browser_conversion import plan
        with self._study_connection() as db:
            db.execute('PRAGMA query_only=ON');db.execute('BEGIN')
            return plan(self,db,ids,mode,target,fields,templates)

    def browser_duplicates(self,field,query='',now=None):
        from .browser_notes import duplicates
        with self._study_connection() as db:
            db.execute('PRAGMA query_only=ON');db.execute('BEGIN')
            return duplicates(self,db,field,query,now)

    def browser_preview(self,card_id):
        if type(card_id) is not int or not 0<card_id<2**63-1:raise CollectionError('La tarjeta no es válida.')
        card=self.card(card_id)
        back=card.get('full_back')
        if back is None:back=card['back'] if card.get('generation_kind')=='image-occlusion' else card['front']+'<hr id="answer">'+card['back']
        return {'front':card['front'],'back':back,'card_id':card_id}

    def browser_command(self,ids,mode,action,value,now=None):
        from .browser_ops import command
        return command(self,ids,mode,action,value,now)

    def browser_search_history(self, query=None, current_deck_id=None):
        key = 'browser_search_history_v1'
        with self._study_connection() as db:
            if query is not None:
                if not isinstance(query, str) or len(query) > 2097152:
                    raise CollectionError('La búsqueda no es válida.')
                from .browser_search import Search
                Search(self, db, self._now(None), current_deck_id).compile(query)
                db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT value FROM metadata WHERE key=?', (key,)).fetchone()
            queries = json.loads(row[0]) if row else []
            if query is not None:
                queries = [query] + [previous for previous in queries if previous != query]
                queries = queries[:30]
                db.execute('INSERT INTO metadata(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value', (key, json.dumps(queries, ensure_ascii=False)))
            return {'queries': queries}

    def browser_history(self,action=None):
        from .browser_history import operation
        with self._study_connection() as db:
            db.execute('BEGIN IMMEDIATE')
            return operation(self.path,db,action)

    def browser_config(self,state=None):
        from .browser_table import configuration
        with self._study_connection() as db:return configuration(db,state)

    def browser_table(self,**kwargs):
        from .browser_table import table
        with self._study_connection() as db:
            db.execute('PRAGMA query_only=ON');db.execute('BEGIN')
            return table(self,db,**kwargs)

    def browser_facets(self):
        from .note_schema import load as schema_load
        with self._study_connection() as db:
            from .note_types import definitions, PREFIX
            used={r[0] for r in db.execute('SELECT DISTINCT COALESCE(m.value,n.model_kind) FROM notes n LEFT JOIN metadata m ON m.key=?||n.guid',(PREFIX,))}
            return {'decks': [dict(r,filtered=bool(db.execute('SELECT 1 FROM metadata WHERE key=?',('filtered_deck:'+str(r['id']),)).fetchone())) for r in db.execute('SELECT id,name FROM decks ORDER BY name COLLATE NOCASE')],
                    'models': [{'id':t['id'],'label':t['name']} for t in definitions(db) if t['id'] in used],
                    'native_models': [dict(id=t['id'],label=t['name'],**{k:schema_load(db,t['id'])[k] for k in ('kind','fields','templates','sort_field')}) for t in definitions(db)],
                    'tags': [r[0] for r in db.execute('SELECT name FROM browser_tags ORDER BY name COLLATE NOCASE')],
                    'saved_searches': [dict(r) for r in db.execute('SELECT name,query FROM browser_saved_searches ORDER BY name COLLATE NOCASE')]}

    def browse(self, query='', mode='cards', sort='field', descending=False, limit=50, offset=0, current_deck_id=None, now=None):
        if mode not in ('cards', 'notes') or sort not in ('field', 'type', 'due', 'deck'):
            raise CollectionError('El modo o el orden de Explorar no es válido.')
        if type(descending) is not bool or (current_deck_id is not None and (type(current_deck_id) is not int or not 0<current_deck_id<2**63-1)):
            raise CollectionError('La dirección o el mazo actual no es válido.')
        if type(limit) is not int or not 1 <= limit <= 200 or type(offset) is not int or offset < 0:
            raise CollectionError('La página de resultados no es válida.')
        if not isinstance(query, str) or len(query)>2097152:
            raise CollectionError('La búsqueda supera el límite permitido.')
        with self._study_connection() as db:
            configure(db)
            from .note_types import definitions
            type_names={t["id"]:t["name"] for t in definitions(db)}
            db.create_function("authoring_type_label",1,lambda ident:type_names.get(ident,ident),deterministic=True)
            from .browser_search import Search
            where,args = Search(self,db,self._now(now),current_deck_id).compile(query)
            base = " FROM cards c JOIN notes n ON n.id=c.note_id JOIN decks d ON d.id=c.deck_id LEFT JOIN metadata nt ON nt.key='authoring_note_type:'||n.guid WHERE "+where
            total = db.execute('SELECT COUNT('+('DISTINCT n.id' if mode=='notes' else '*')+')'+base, args).fetchone()[0]
            if mode=='notes':
                base += ' AND c.id IN (SELECT MIN(c.id)'+base+' GROUP BY n.id)'
                args = args+args
            ordering = {'field':"browser_fold(browser_plain(json_extract(n.fields_json,'$[0]')))", 'type':'browser_fold(authoring_type_label(COALESCE(nt.value,n.model_kind)))', 'due':'c.due_at', 'deck':'browser_fold(d.name)'}[sort]
            rows = db.execute('SELECT c.*,n.guid AS note_guid,COALESCE(nt.value,n.model_kind) AS note_type_id,n.model_kind,n.fields_json,n.tags_json,n.modified_at,d.name AS deck,(SELECT COUNT(*) FROM cards nc WHERE nc.note_id=c.note_id) AS card_count'+base+' ORDER BY '+ordering+(' DESC' if descending else ' ASC')+',c.id LIMIT ? OFFSET ?',args+[limit,offset]).fetchall()
            result=[]
            for row in rows:
                item=dict(row)
                item['version']=version(row)
                siblings=db.execute('SELECT id,ordinal FROM cards WHERE note_id=? ORDER BY id',(row['note_id'],)).fetchall()
                item['card_ids']=[r['id'] for r in siblings];item['ordinals']=[r['ordinal'] for r in siblings]
                item.update(fields=json.loads(item.pop('fields_json')), tags=json.loads(item.pop('tags_json')))
                item['sort_field']=plain(item['fields'][0])
                item['model_label']=type_names.get(item['note_type_id'],MODEL_LABELS.get(item['model_kind'],item['model_kind']))
                result.append(item)
            return {'rows':result,'total':total,'offset':offset,'limit':limit,'mode':mode}


    def browser_edit(self, note_id, fields, tags, expected_version, now=None):
        if type(note_id) is not int or note_id<1 or not isinstance(fields,list) or not 1<=len(fields)<=128 or any(not isinstance(f,str) or len(f)>100000 for f in fields):
            raise CollectionError('La pregunta y la respuesta deben tener contenido y no superar 100000 caracteres.')
        if not isinstance(tags,list) or len(tags)>200 or any(not isinstance(t,str) or not t or len(t)>200 or any(c.isspace() for c in t) for t in tags):
            raise CollectionError('Las etiquetas no son válidas; separalas por espacios.')
        with self._study_connection() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT * FROM notes WHERE id=?',(note_id,)).fetchone()
            if row is None: raise CollectionError('No existe esa nota.')
            if not isinstance(expected_version,str) or expected_version!=version(row):
                raise CollectionError('La nota cambió desde que la abriste. Volvé a seleccionarla antes de guardar.')
            if row['model_kind'] not in MODEL_LABELS: raise CollectionError('Este tipo de nota todavía no se puede editar.')
            from .generation_edit import reconcile
            clean=reconcile(db,row,fields,self._now(now))
            db.execute('UPDATE notes SET fields_json=?,tags_json=?,modified_at=? WHERE id=?',
                       (json.dumps(clean,ensure_ascii=False),json.dumps(sorted(set(tags)),ensure_ascii=False),self._now(now),note_id))
            actual=db.execute('SELECT * FROM notes WHERE id=?',(note_id,)).fetchone()
            return {'note_id':note_id,'fields':json.loads(actual['fields_json']),'tags':json.loads(actual['tags_json']),'version':version(actual)}

    def browser_action(self, card_ids, action, value, now=None):
        if not isinstance(card_ids,list) or not 1<=len(card_ids)<=500 or any(type(i) is not int or not 0<i<2**63-1 for i in card_ids) or len(set(card_ids))!=len(card_ids):
            raise CollectionError('Seleccioná entre 1 y 500 tarjetas distintas.')
        if action=='flag':
            if type(value) is not int or not 0<=value<=7: raise CollectionError('El marcador debe ser un número del 0 al 7.')
            assignment='flag=?'; args=[value]
        elif action in ('suspend','bury'):
            if type(value) is not bool: raise CollectionError('La acción debe indicar activar o desactivar.')
            if action=='suspend':
                assignment='suspended=?'+(", queue=CASE WHEN queue<0 THEN CASE card_type WHEN 0 THEN 0 WHEN 2 THEN 2 ELSE 1 END ELSE queue END" if not value else '')
                args=[int(value)]
            else:
                assignment='buried_until=?,manual_buried_until=?';until=self._day(self._now(now))[2] if value else 0;args=[until,until]
        elif action=='move':
            if type(value) is not int or value<1: raise CollectionError('Elegí un mazo de destino válido.')
            assignment='deck_id=?'; args=[value]
        else: raise CollectionError('La acción de Explorar no está disponible.')
        placeholders=','.join('?' for _ in card_ids)
        with self._study_connection() as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute('SELECT COUNT(*) FROM cards WHERE id IN ('+placeholders+')',card_ids).fetchone()[0]!=len(card_ids):
                raise CollectionError('Una tarjeta ya no existe. Actualizá los resultados.')
            if action=='move' and not db.execute('SELECT 1 FROM decks WHERE id=?',(value,)).fetchone(): raise CollectionError('No existe el mazo de destino.')
            db.execute('UPDATE cards SET '+assignment+' WHERE id IN ('+placeholders+')',args+card_ids)
            if action=='move': db.execute('DELETE FROM custom_session_cards WHERE card_id IN ('+placeholders+')',card_ids)
            actual=[dict(r) for r in db.execute('SELECT id,flag,suspended,buried_until,deck_id FROM cards WHERE id IN ('+placeholders+') ORDER BY id',card_ids)]
            return {'cards':actual,'updated':len(actual)}

    def browser_manage_saved_search(self, action, name, new_name=None):
        if action not in ('rename', 'delete') or not isinstance(name, str) or not name.strip():
            raise CollectionError('La búsqueda guardada no es válida.')
        if action == 'rename' and (not isinstance(new_name, str) or not new_name.strip() or len(new_name) > 80):
            raise CollectionError('La búsqueda guardada necesita un nombre de hasta 80 caracteres.')
        with self._study_connection() as db:
            db.execute('BEGIN IMMEDIATE')
            if not db.execute('SELECT 1 FROM browser_saved_searches WHERE name=?', (name,)).fetchone():
                raise CollectionError('La búsqueda guardada cambió. Actualizá la barra lateral.')
            if action == 'delete':
                db.execute('DELETE FROM browser_saved_searches WHERE name=?', (name,))
            else:
                new_name = new_name.strip()
                if new_name != name and db.execute('SELECT 1 FROM browser_saved_searches WHERE name=?', (new_name,)).fetchone():
                    raise CollectionError('Ya existe una búsqueda con ese nombre.')
                db.execute('UPDATE browser_saved_searches SET name=? WHERE name=?', (new_name, name))
            return {'saved_searches': [dict(row) for row in db.execute('SELECT name,query FROM browser_saved_searches ORDER BY name COLLATE NOCASE')]}

    def browser_save_search(self, name, query, now=None):
        if not isinstance(name,str) or not name.strip() or len(name)>80:
            raise CollectionError('La búsqueda guardada necesita un nombre de hasta 80 caracteres.')
        if query is not None and (not isinstance(query,str) or len(query)>4096): raise CollectionError('La búsqueda no es válida.')
        with self._study_connection() as db:
            if query is None:
                db.execute('DELETE FROM browser_saved_searches WHERE name=?',(name.strip(),))
            else:
                from .browser_search import Search
                Search(self,db,self._now(now),None).compile(query)
                db.execute('INSERT INTO browser_saved_searches(name,query) VALUES(?,?) ON CONFLICT(name) DO UPDATE SET query=excluded.query',(name.strip(),query))
            return {'saved_searches':[dict(r) for r in db.execute('SELECT name,query FROM browser_saved_searches ORDER BY name COLLATE NOCASE')]}
