"""Bounded rich-note authoring, independent of scheduler and native Anki core."""
import html
import json
import re
import uuid
from html.parser import HTMLParser
from urllib.parse import urlsplit
from .errors import CollectionError

ALLOWED = set('b strong i em u s strike sup sub span font p div br ul ol li blockquote pre code h1 h2 h3 hr table thead tbody tr th td a abbr img'.split())
VOID = {'br', 'hr', 'img', 'input', 'meta', 'link', 'source', 'wbr'}
BLOCKED = set('script style iframe object embed svg math form textarea button audio video template noscript'.split())
STYLE = {'color': r'(?:#[0-9a-fA-F]{3,8}|[a-zA-Z]+|rgba?\([\d.,%\s]+\))',
         'background-color': r'(?:#[0-9a-fA-F]{3,8}|[a-zA-Z]+|rgba?\([\d.,%\s]+\))',
         'text-align': r'(?:left|right|center|justify)', 'font-weight': r'(?:normal|bold|[1-9]00)',
         'font-style': r'(?:normal|italic)', 'text-decoration': r'(?:none|underline|line-through)',
         'font-size': r'(?:[1-9]\d?px|[1-3](?:\.\d)?em)', 'white-space': r'(?:pre|pre-wrap|normal)',
         'margin-left': r'(?:[0-9]|[1-9][0-9]|[12][0-9]{2})px'}


def safe_style(value):
    parts = []
    for declaration in value.split(';'):
        key, sep, value = declaration.partition(':')
        key, value = key.strip().lower(), value.strip()
        if sep and key in STYLE and re.fullmatch(STYLE[key], value):
            parts.append(key+':'+value)
    return ';'.join(parts)


class FieldHTML(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.output, self.text, self.images, self.blocked = [], [], [], []

    def handle_starttag(self, tag, attrs):
        if self.blocked or tag in BLOCKED:
            if tag not in VOID: self.blocked.append(tag)
            return
        if tag not in ALLOWED: return
        clean = []
        for key, value in attrs:
            value = value or ''
            if key == 'style':
                value = safe_style(value)
                if value: clean.append((key, value))
            elif tag == 'a' and key == 'href':
                if urlsplit(value.strip()).scheme.lower() in ('http', 'https', 'mailto') and not any(ord(c)<32 for c in value): clean.append((key, value.strip()))
            elif tag == 'img' and key == 'src':
                from .collection import _safe_media_name
                try: name = _safe_media_name(value)
                except CollectionError: continue
                if re.search(r'\.(?:png|jpe?g|gif|webp)$', name, re.I) and ':' not in name:
                    clean.append((key, name)); self.images.append(name)
            elif tag == 'abbr' and key == 'title': clean.append((key,value[:1000]))
            elif tag == 'img' and key == 'alt': clean.append((key, value[:500]))
            elif tag == 'font' and key == 'color' and re.fullmatch(STYLE['color'],value): clean.append((key,value))
            elif key in ('dir',) and value in ('ltr','rtl'): clean.append((key,value))
        if tag == 'img' and not any(k=='src' for k,_ in clean): return
        values = dict(clean)
        styles = dict(item.split(':',1) for item in values.get('style','').split(';') if ':' in item)
        foreground = styles.get('color') or (values.get('color') if tag=='font' else None)
        background = styles.get('background-color')
        if foreground or background:
            clean = [(k,v) for k,v in clean if k!='style']
            declarations = [k+':'+v for k,v in styles.items()]
            if foreground: declarations.append('--memoria-color:'+foreground)
            if background: declarations.append('--memoria-background:'+background)
            clean.extend([('data-memoria-format','1'),('style',';'.join(declarations))])
        self.output.append('<'+tag+''.join(' '+k+'="'+html.escape(v,quote=True)+'"' for k,v in clean)+'>')

    def handle_endtag(self, tag):
        if self.blocked:
            if tag in self.blocked:
                self.blocked = self.blocked[:len(self.blocked)-1-self.blocked[::-1].index(tag)]
            return
        if tag in ALLOWED and tag not in VOID: self.output.append('</'+tag+'>')

    def handle_data(self, data):
        if not self.blocked:
            self.output.append(html.escape(data,quote=False)); self.text.append(data)


def clean_field(value, allow_empty=False):
    if not isinstance(value,str) or len(value)>100000:
        raise CollectionError('Cada campo debe ser texto de hasta 100000 caracteres.')
    parser = FieldHTML()
    try: parser.feed(value); parser.close()
    except (ValueError, RecursionError) as exc: raise CollectionError('El HTML del campo no es válido.') from exc
    if not allow_empty and not ''.join(parser.text).replace('\u200b','').strip() and not parser.images:
        raise CollectionError('Completá Anverso y Reverso con texto o una imagen.')
    return ''.join(parser.output).strip(), parser.images


def duplicate_key(value):
    value = value if isinstance(value,str) else ''
    text = ' '.join(html.unescape(re.sub(r'<[^>]*>', '', value)).casefold().split())
    return text or '|'.join(re.findall(r'\bsrc=["\']([^"\']+)["\']',value))

class AuthoringMixin:
    def add_image(self, encoded):
        import base64
        import binascii
        import hashlib
        maximum = 8*1024*1024
        if not isinstance(encoded,str) or not encoded or len(encoded)>((maximum+2)//3)*4:
            raise CollectionError('La imagen debe ocupar como máximo 8 MiB.')
        try: raw = base64.b64decode(encoded,validate=True)
        except (ValueError,binascii.Error) as exc: raise CollectionError('La imagen no está codificada correctamente.') from exc
        if len(raw)>maximum: raise CollectionError('La imagen debe ocupar como máximo 8 MiB.')
        extension = None
        if raw.startswith(b'\x89PNG\r\n\x1a\n') and len(raw)>=24: extension='png'
        elif raw.startswith(b'\xff\xd8\xff') and len(raw)>=12 and raw.endswith(b'\xff\xd9'): extension='jpg'
        elif raw.startswith((b'GIF87a',b'GIF89a')) and len(raw)>=13: extension='gif'
        elif raw.startswith(b'RIFF') and raw[8:12]==b'WEBP' and len(raw)>=20: extension='webp'
        if not extension: raise CollectionError('Usá una imagen PNG, JPEG, GIF o WebP.')
        name = hashlib.sha256(raw).hexdigest()+'.'+extension
        self.add_media(name,raw)
        return {'name':name,'mime':'image/jpeg' if extension=='jpg' else 'image/'+extension,'size':len(raw)}

    def preview_note(self, note_type, fields):
        from .note_types import definitions
        from .note_schema import prepare, generate
        with self._study_connection() as db:
            definition=next((t for t in definitions(db) if t['id']==note_type),None)
            if definition is None:raise CollectionError('No existe ese tipo de nota.')
            clean, images=prepare(db,definition,fields)
            for name in set(images):
                if not db.execute('SELECT 1 FROM media WHERE name=?',(name,)).fetchone():raise CollectionError('Una imagen ya no está en la colección.')
            from .latex import Renderer
            return {'fields':clean,'cards':Renderer(db,definition['id']).cards(generate(db,definition,clean))}

    def add_note(self, deck_id, front=None, back=None, tags=None, note_type='basic', allow_duplicate=False, now=None, fields=None):
        if type(deck_id) is not int or deck_id<1 or not isinstance(note_type,str) or type(allow_duplicate) is not bool:
            raise CollectionError('El tipo de nota o el mazo no es válido.')
        if tags is None: tags=[]
        if not isinstance(tags,list) or len(tags)>200 or any(not isinstance(t,str) or not t or len(t)>200 or any(c.isspace() for c in t) for t in tags):
            raise CollectionError('Usá etiquetas de hasta 200 caracteres, separadas por espacios.')
        current = self._now(now)
        with self._study_connection() as db:
            db.execute('BEGIN IMMEDIATE')
            from .note_types import definitions, assign, PREFIX
            from .note_schema import prepare, generate
            definition=next((t for t in definitions(db) if t['id']==note_type),None)
            if definition is None:raise CollectionError('No existe ese tipo de nota.')
            kind=definition['kind']
            values=fields if fields is not None else [front,back]+([''] if kind=='basic-optional-reversed' else [])
            clean, images=prepare(db,definition,values)
            if db.execute('SELECT 1 FROM metadata WHERE key=?', ('filtered_deck:'+str(deck_id),)).fetchone():
                raise CollectionError('Las notas se añaden a un mazo de origen, no a un mazo filtrado.')
            deck_name=db.execute('SELECT name FROM decks WHERE id=?',(deck_id,)).fetchone()
            generated=generate(db,definition,clean,tags=tags,deck_name=deck_name[0] if deck_name else '')
            if not generated:raise CollectionError('La plantilla no produce ninguna tarjeta con los campos actuales.')
            if not db.execute('SELECT 1 FROM decks WHERE id=?',(deck_id,)).fetchone(): raise CollectionError('No existe ese mazo.')
            for name in set(images):
                if not db.execute('SELECT 1 FROM media WHERE name=?',(name,)).fetchone(): raise CollectionError('Una imagen del borrador ya no está en la colección.')
            db.create_function('authoring_key',1,duplicate_key)
            duplicates = [r[0] for r in db.execute("SELECT n.id FROM notes n WHERE COALESCE((SELECT value FROM metadata WHERE key=?||n.guid),n.model_kind)=? AND authoring_key(json_extract(n.fields_json,'$[0]'))=? LIMIT 20",(PREFIX,note_type,duplicate_key(clean[0])))]
            if duplicates and not allow_duplicate: return {'duplicate':True,'note_ids':duplicates}
            from .latex import Renderer
            generated=Renderer(db,note_type,persist=True).cards(generated)
            note_id = db.execute('INSERT INTO notes(guid,model_kind,fields_json,tags_json,created_at,modified_at) VALUES(?,?,?,?,?,?)',
                (uuid.uuid4().hex,kind,json.dumps(clean,ensure_ascii=False),json.dumps(sorted(set(tags)),ensure_ascii=False),current,current)).lastrowid
            guid=db.execute('SELECT guid FROM notes WHERE id=?',(note_id,)).fetchone()[0]
            assign(db,guid,note_type)
            position = db.execute('SELECT COALESCE(MAX(new_position),0) FROM cards').fetchone()[0]
            ids = []
            for index, card in enumerate(generated):
                ids.append(db.execute('INSERT INTO cards(note_id,deck_id,ordinal,front,back,due_at,new_position,added_at) VALUES(?,?,?,?,?,?,?,?)',
                    (note_id,deck_id,card['ordinal'],card['front'],card['back'],current,position+index+1,current)).lastrowid)
            if self._options(db,deck_id)['new_order']=='random':
                from .order_policy import reposition
                reposition(db,deck_id,'random')
            db.execute('INSERT INTO metadata(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',('authoring_last_deck:'+note_type,str(deck_id)))
            return {'duplicate':False,'id':ids[0],'note_id':note_id,'card_ids':ids,'created':len(ids),'fields':clean,'tags':sorted(set(tags))}
