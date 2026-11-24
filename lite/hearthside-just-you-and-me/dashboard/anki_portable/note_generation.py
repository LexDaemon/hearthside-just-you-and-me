"""Own, bounded note generation. No scheduler state or native Anki dependency."""
import html
import json
import re
import unicodedata
from .errors import CollectionError

MODES = {
    'basic': ('Básico', ['Front', 'Back']),
    'basic-optional-reversed': ('Básico (tarjeta invertida opcional)', ['Front', 'Back', 'Add Reverse']),
    'basic-typing': ('Básico (teclear la respuesta)', ['Front', 'Back']),
    'basic-reversed': ('Básico (y tarjeta invertida)', ['Front', 'Back']),
    'image-occlusion': ('Oclusión de imagen', ['Image', 'Back Extra', 'Masks', 'Header', 'Comments']),
    'cloze': ('Respuesta anidada', ['Text', 'Back Extra']),
}


def prepare(kind, values):
    from .authoring import clean_field
    if kind not in MODES or not isinstance(values, list) or len(values) != len(MODES[kind][1]):
        raise CollectionError('Los campos no coinciden con el tipo de nota.')
    fields, images = [], []
    for index, value in enumerate(values):
        if kind=='image-occlusion' and index==2:
            from .occlusion import masks
            fields.append(json.dumps(masks(value),ensure_ascii=False,separators=(',',':')));continue
        clean, media = clean_field(value, allow_empty=index > 1 or (kind in ('cloze','image-occlusion') and index==1))
        fields.append(clean); images.extend(media)
    if kind=='image-occlusion' and len(re.findall(r'<img\b',fields[0]))!=1:
        raise CollectionError('La oclusión necesita exactamente una imagen.')
    if kind=='basic-typing' and not 1<=len(answer_text(fields[1]))<=512:
        raise CollectionError('La respuesta escrita debe tener entre 1 y 512 caracteres de texto.')
    return fields, images


def generate(kind, fields):
    if kind=='image-occlusion':
        from .occlusion import generate as generate_occlusion
        return generate_occlusion(fields)
    if kind=='cloze':
        nodes, numbers=parse_clozes(fields[0])
        return [{'ordinal':n-1,'front':cloze_html(nodes,n,False),'back':cloze_html(nodes,n,True)+('<hr>'+fields[1] if fields[1] else '')} for n in sorted(numbers)]
    front, back = fields[:2]
    cards = [{'ordinal': 0, 'front': front, 'back': back}]
    if kind == 'basic-reversed' or (kind == 'basic-optional-reversed' and (answer_text(fields[2]) or '<img ' in fields[2])):
        cards.append({'ordinal': 1, 'front': back, 'back': front})
    if kind=='basic-typing':cards[0]['answer_text']=answer_text(back)
    return cards


def answer_text(value):
    from html.parser import HTMLParser
    class Text(HTMLParser):
        def __init__(self):super().__init__(convert_charrefs=True);self.parts=[]
        def handle_data(self,data):self.parts.append(data)
        def handle_starttag(self,tag,attrs):
            if tag in ('br','p','div','li','tr','hr','blockquote'):self.parts.append(' ')
        def handle_endtag(self,tag):self.handle_starttag(tag,[])
    parser=Text();parser.feed(value);parser.close()
    return unicodedata.normalize('NFC',' '.join(''.join(parser.parts).split()))

def parse_clozes(value):
    if any('{{c' in tag for tag in re.findall(r'<[^>]*>', value)):
        raise CollectionError('La eliminación debe estar en el texto, no en un atributo HTML.')
    numbers=set();count=0
    def parse(pos, depth):
        nonlocal count
        if depth>8:raise CollectionError('La respuesta anidada supera 8 niveles.')
        body=[];hint=[];target=body
        while pos<len(value):
            if value.startswith('}}',pos) and depth:return body,hint,pos+2
            if value.startswith('::',pos) and depth:target=hint;pos+=2;continue
            if value.startswith('{{c',pos):
                match=re.match(r'\{\{c([0-9]+(?:,[0-9]+)*)::',value[pos:])
                if not match or any(len(n)>3 or not 1<=int(n)<=500 for n in match[1].split(',')):raise CollectionError('Usá eliminaciones {{c1::texto}} con números del 1 al 500.')
                count+=1
                if count>1000:raise CollectionError('La nota supera 1000 eliminaciones.')
                number=tuple(int(n) for n in match[1].split(','));numbers.update(number)
                child,child_hint,pos=parse(pos+len(match[0]),depth+1)
                if not answer_text(cloze_html(child,-1,True)) and '<img ' not in cloze_html(child,-1,True):raise CollectionError('Una eliminación no puede estar vacía.')
                target.append((number,child,child_hint))
            else:
                end=pos+1
                while end<len(value) and not value.startswith(('{{c','}}','::'),end):end+=1
                target.append(value[pos:end]);pos=end
        if depth:raise CollectionError('Falta cerrar una eliminación con }}.')
        return body,hint,pos
    nodes,_,_=parse(0,0)
    if not numbers:raise CollectionError('Marcá al menos una eliminación {{c1::texto}}.')
    return nodes,numbers


def cloze_html(nodes, active, answer):
    out=[]
    for node in nodes:
        if isinstance(node,str):out.append(node);continue
        number,body,hint=node
        if active in number:
            content=cloze_html(body,active,True) if answer else '['+(html.escape(answer_text(cloze_html(hint,-1,True))) if hint else '...')+']'
            out.append('<span class="memoria-cloze" style="font-weight:700">'+content+'</span>')
        else:out.append(cloze_html(body,active,answer))
    return ''.join(out)

