"""Note-content batch edits; one caller-owned transaction and undo step."""
import json
from .errors import CollectionError
from .browser_patterns import compile_pattern,replacement_function,substitute


def replace(db,notes,value,now):
    if set(value)-{'find','replace','field','regular','match_case','all_notes','tags'} or not {'find','replace','field','regular','match_case'}<=set(value):raise CollectionError('Los datos de búsqueda y reemplazo no son válidos.')
    if any(type(value.get(key,False)) is not bool for key in ('regular','match_case','all_notes','tags')):raise CollectionError('Las opciones de reemplazo no son válidas.')
    field=value['field']
    if field is not None and (not isinstance(field,str) or not 1<=len(field)<=200):raise CollectionError('El campo de reemplazo no es válido.')
    compiled=compile_pattern(value['find'],value['match_case'],not value['regular'])
    replacement=replacement_function(value['replace'],value['regular'])
    from .note_schema import load
    from .note_types import for_note
    from .generation_edit import reconcile
    changed_fields=changed_notes=replacements=0
    for note in notes:
        definition=for_note(db,note['guid'],note['model_kind']);schema=load(db,definition['id'])
        fields=json.loads(note['fields_json']);changed=0
        if value.get('tags',False):
            if field is not None:raise CollectionError('Elegí etiquetas o un campo, no ambos.')
            old_tags=json.loads(note['tags_json']);new_tags=[];count=0
            for tag in old_tags:
                result,n=substitute(compiled,tag,replacement);count+=n;new_tags.extend(result.split())
            from .browser_ops import tags
            new_tags=tags(new_tags)
            if new_tags==old_tags:continue
            modified=dict(note);modified['tags_json']=json.dumps(new_tags,ensure_ascii=False)
            reconcile(db,modified,fields,now)
            db.execute('UPDATE notes SET tags_json=?,modified_at=? WHERE id=?',(modified['tags_json'],now,note['id']))
            changed_notes+=1;replacements+=count;continue
        for index,spec in enumerate(schema['fields']):
            if field is not None and spec['name']!=field:continue
            result,count=substitute(compiled,fields[index],replacement)
            if result!=fields[index]:fields[index]=result;changed+=1;replacements+=count
        if not changed:continue
        fields=reconcile(db,note,fields,now)
        db.execute('UPDATE notes SET fields_json=?,modified_at=? WHERE id=?',(json.dumps(fields,ensure_ascii=False),now,note['id']))
        changed_fields+=changed;changed_notes+=1
    return {'changed_fields':changed_fields,'changed_notes':changed_notes,'replacements':replacements}


from html.parser import HTMLParser

class DuplicateText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True);self.parts=[];self.hidden=0
    def handle_starttag(self,tag,attrs):
        if tag in ('script','style'):self.hidden+=1
        elif tag=='img' and not self.hidden:self.parts.append(' '+dict(attrs).get('src','')+' ')
    def handle_endtag(self,tag):
        if tag in ('script','style') and self.hidden:self.hidden-=1
    def handle_data(self,value):
        if not self.hidden:self.parts.append(value)


def duplicates(collection,db,field,query,now=None):
    if not isinstance(field,str) or not 1<=len(field)<=200 or not isinstance(query,str) or len(query)>2097152:raise CollectionError('El campo o límite de búsqueda no es válido.')
    from .browser import configure
    from .browser_search import Search,TYPE
    from .note_types import definitions
    from .note_schema import load
    schemas={t['id']:load(db,t['id']) for t in definitions(db)}
    if not any(f['name']==field for schema in schemas.values() for f in schema['fields']):raise CollectionError('No existe ese campo en la colección.')
    configure(db);where,args=Search(collection,db,collection._now(now),None).compile(query)
    rows=db.execute('SELECT DISTINCT n.id,n.fields_json,'+TYPE+' AS ident FROM cards c JOIN notes n ON n.id=c.note_id JOIN decks d ON d.id=c.deck_id WHERE '+where+' ORDER BY n.id',args)
    grouped={}
    for row in rows:
        schema=schemas[row['ident']];index=next((i for i,f in enumerate(schema['fields']) if f['name']==field),None)
        if index is None:continue
        parser=DuplicateText();parser.feed(json.loads(row['fields_json'])[index]);parser.close();text=''.join(parser.parts)
        if text:grouped.setdefault(text,[]).append(row['id'])
    return {'groups':[{'value':text,'note_ids':ids,'query':'nid:'+','.join(map(str,ids))} for text,ids in grouped.items() if len(ids)>1]}
