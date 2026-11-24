"""Bounded search grammar compiled to parameterized SQLite predicates."""
from datetime import datetime, timedelta
import re
import json
from .errors import CollectionError

TYPE = "COALESCE((SELECT value FROM metadata WHERE key='authoring_note_type:'||n.guid),n.model_kind)"
TEXT = 'browser_search_text(n.fields_json,'+TYPE+')'


def tokens(query):
    result=[]
    i=0
    while i<len(query):
        if query[i].isspace(): i+=1; continue
        if query[i] in '()-':
            result.append((query[i],query[i])); i+=1; continue
        value=''; quoted=False;regex_depth=0;in_class=False
        while i<len(query) and not query[i].isspace():
            regular=value.casefold().startswith('re:') or ':re:' in value.casefold()
            if query[i] in '()':
                if not regular:break
                if not in_class:
                    if query[i]=='(':regex_depth+=1
                    elif regex_depth:regex_depth-=1
                    else:break
            if regular and query[i]=='[':in_class=True
            elif regular and query[i]==']':in_class=False
            if query[i]=='"':
                quoted=True; i+=1
                while i<len(query) and query[i]!='"':
                    if query[i]=='\\':
                        if i+1>=len(query): raise CollectionError('La búsqueda termina con un escape incompleto.')
                        value+=query[i:i+2]; i+=2
                    else: value+=query[i]; i+=1
                if i>=len(query): raise CollectionError('Cerrá las comillas de la búsqueda.')
                i+=1
            elif query[i]=='\\':
                if i+1>=len(query): raise CollectionError('La búsqueda termina con un escape incompleto.')
                value+=query[i:i+2]; i+=2
            else: value+=query[i]; i+=1
        kind=value.lower() if not quoted and value.lower() in ('and','or') else 'term'
        result.append((kind,value))
        if len(result)>128: raise CollectionError('La búsqueda tiene demasiadas condiciones.')
    return result


def pattern(value, substring=False):
    # Only * and _ are wildcards. Quotes, %, SQL text and backslashes stay data.
    result=''; i=0
    while i<len(value):
        char=value[i]
        if char=='\\' and i+1<len(value):
            i+=1; char=value[i]
            result+=('\\'+char) if char in '%_\\' else char
        elif char=='*': result+='%'
        elif char in '%\\': result+='\\'+char
        else: result+=char
        i+=1
    return ('%'+result+'%') if substring else result


class Search:
    def __init__(self, collection, db, now, current_deck_id):
        self.collection=collection; self.db=db; self.now=now; self.current_deck_id=current_deck_id
        self.args=[]; self.pos=0;self.schemas={}
        self.day,self.start,self.end=collection._day(now)
        from .browser_patterns import search
        self.db.create_function('browser_regex',2,search)
        self.db.create_function('browser_search_field',2,self.searchable_field)
        self.db.create_function('browser_named_raw',3,self.named_raw)

    def schema(self,ident):
        if ident not in self.schemas:
            from .note_schema import load
            self.schemas[ident]=load(self.db,ident)
        return self.schemas[ident]

    def searchable_field(self,ident,index):
        fields=self.schema(ident)['fields'];index=int(index)
        return int(0<=index<len(fields) and not fields[index]['exclude_from_search'])

    def named_raw(self,values,ident,name):
        import json
        fields=json.loads(values)
        return next((fields[i] for i,f in enumerate(self.schema(ident)['fields']) if f['name'].casefold()==name and i<len(fields)),'')

    def regular(self,expression,value):
        from .browser_patterns import compile_pattern
        compile_pattern(value)
        return 'browser_regex('+self.parameter(value)+','+expression+')'


    def parameter(self, value):
        self.args.append(value); return '?'

    def like(self, expression, value, substring=False):
        return f"browser_match_fold({expression}) LIKE browser_match_fold({self.parameter(pattern(value.casefold(),substring))}) ESCAPE '\\'"

    def compile(self, query):
        self.parts=tokens(query)
        if not self.parts: return '1', []
        result=self.either(0)
        if self.pos!=len(self.parts): raise CollectionError('La búsqueda tiene paréntesis u operadores fuera de lugar.')
        return result,self.args

    def kind(self):
        return self.parts[self.pos][0] if self.pos<len(self.parts) else None

    def either(self, depth):
        expressions=[self.both(depth)]
        while self.kind()=='or':
            self.pos+=1; expressions.append(self.both(depth))
        return '('+' OR '.join(expressions)+')'

    def both(self, depth):
        expressions=[self.unary(depth)]
        while self.kind() not in (None,')','or'):
            if self.kind()=='and': self.pos+=1
            expressions.append(self.unary(depth))
        return '('+' AND '.join(expressions)+')'

    def unary(self, depth):
        if depth>12: raise CollectionError('La búsqueda tiene demasiados niveles.')
        kind=self.kind()
        if kind=='-':
            self.pos+=1; return 'NOT ('+self.unary(depth+1)+')'
        if kind=='(':
            self.pos+=1; value=self.either(depth+1)
            if self.kind()!=')': raise CollectionError('Cerrá los paréntesis de la búsqueda.')
            self.pos+=1; return value
        if kind!='term': raise CollectionError('Falta un término en la búsqueda.')
        value=self.parts[self.pos][1]; self.pos+=1
        return self.term(value)

    def term(self, value):
        split=None; escaped=False
        for index,char in enumerate(value):
            if escaped: escaped=False; continue
            if char=='\\': escaped=True; continue
            if char==':': split=index; break
        if split is None:
            return self.like(TEXT,value,True)
        key,value=value[:split].casefold(),value[split+1:]
        if key=='mazo':key='deck'
        if key=='deck' and value.casefold()=='actual':value='current'
        if not value: raise CollectionError('El filtro necesita un valor.')
        if key=='re':
            return 'EXISTS(SELECT 1 FROM json_each(n.fields_json) f WHERE browser_search_field('+TYPE+',f.key) AND '+self.regular('f.value',value)+')'
        if key=='deck':
            if value.casefold()=='current':
                if self.current_deck_id is None: return '0'
                row=self.db.execute('SELECT name FROM decks WHERE id=?',(self.current_deck_id,)).fetchone()
                if row is None: raise CollectionError('No existe el mazo actual.')
                value=row[0]
            home='('+self.like('d.name',value)+' OR '+self.like('d.name',value+'::*')+')'
            borrowed="EXISTS(SELECT 1 FROM custom_session_cards sc JOIN custom_sessions s ON s.id=sc.session_id JOIN decks fd ON fd.id=s.deck_id WHERE sc.card_id=c.id AND sc.done=0 AND s.active=1 AND s.mode='filtered' AND ("+self.like('fd.name',value)+' OR '+self.like('fd.name',value+'::*')+'))'
            return '('+home+' OR '+borrowed+')'
        if key=='tag':
            if value.casefold()=='none': return 'json_array_length(n.tags_json)=0'
            return "EXISTS(SELECT 1 FROM json_each(n.tags_json) t WHERE "+self.like('t.value',value)+' OR '+self.like('t.value',value+'::*')+')'
        if key=='note':
            labels={'básico':'basic','basic':'basic','básico (y tarjeta invertida)':'basic-reversed','basic (and reversed card)':'basic-reversed'}
            from .note_types import definitions, PREFIX
            types=definitions(self.db)
            name=labels.get(value.casefold(),value)
            selected=next((t for t in types if t['id']==name or t['name'].casefold()==value.casefold()),None)
            if selected:return "COALESCE((SELECT value FROM metadata WHERE key="+self.parameter(PREFIX)+"||n.guid),n.model_kind)="+self.parameter(selected['id'])
            return self.like('n.model_kind',name)
        if key=='card':
            if value.isdecimal() and 1<=int(value)<=500: return 'c.ordinal='+self.parameter(int(value)-1)
            from .note_types import definitions
            from .note_schema import load
            matches=[]
            for definition in definitions(self.db):
                for i,template in enumerate(load(self.db,definition['id'])['templates']):
                    if template['name'].casefold()==value.casefold():matches.append('('+TYPE+'='+self.parameter(definition['id'])+' AND c.ordinal='+self.parameter(i)+')')
            return '('+' OR '.join(matches)+')' if matches else '0'
        if key in ('cid','nid'):
            ids=value.split(',')
            if len(ids)>200000 or any(not v.isdecimal() or not 0<int(v)<2**63-1 for v in ids): raise CollectionError('Los identificadores de la búsqueda no son válidos.')
            field='c.id' if key=='cid' else 'n.id'
            return field+' IN (SELECT value FROM json_each('+self.parameter(json.dumps([int(v) for v in ids]))+'))'
        if key=='flag':
            if not value.isdecimal() or not 0<=int(value)<=7: raise CollectionError('Elegí un marcador del 0 al 7.')
            return 'c.flag='+self.parameter(int(value))
        if key=='is':
            states={'filtered':"EXISTS(SELECT 1 FROM custom_session_cards sc JOIN custom_sessions s ON s.id=sc.session_id WHERE sc.card_id=c.id AND sc.done=0 AND s.active=1 AND s.mode='filtered')",'new':'c.card_type=0','learn':'c.card_type IN (1,3)','review':'c.card_type IN (2,3)',
                    'suspended':'c.suspended=1','buried':'c.buried_until>'+str(self.now),
                    'due':'c.card_type<>0 AND c.suspended=0 AND c.buried_until<='+str(self.now)+' AND c.due_at<='+str(self.now)}
            if value.casefold() not in states: raise CollectionError('Ese estado de tarjeta no está disponible.')
            return '('+states[value.casefold()]+')'
        if key=='resched':
            if not value.isdecimal() or not 1<=int(value)<=36500:raise CollectionError('El intervalo de reprogramación no es válido.')
            start=int((datetime.fromtimestamp(self.start)-timedelta(days=int(value)-1)).timestamp())
            return 'EXISTS(SELECT 1 FROM browser_manual_events e WHERE e.card_id=c.id AND e.at BETWEEN '+self.parameter(start)+' AND '+self.parameter(self.now)+')'
        if key in ('added','edited','rated','introduced'):
            parts=value.split(':')
            if not parts[0].isdecimal() or not 1<=int(parts[0])<=36500 or len(parts)>2 or (len(parts)==2 and (key!='rated' or parts[1] not in ('1','2','3','4'))):
                raise CollectionError('El intervalo de búsqueda no es válido.')
            start=int((datetime.fromtimestamp(self.start)-timedelta(days=int(parts[0])-1)).timestamp())
            if key in ('added','edited'):
                field='c.added_at' if key=='added' else 'n.modified_at'
                return field+' BETWEEN '+self.parameter(start)+' AND '+self.parameter(self.now)
            if key=='introduced':
                return '(SELECT MIN(r.reviewed_at) FROM reviews r WHERE r.card_id=c.id) BETWEEN '+self.parameter(start)+' AND '+self.parameter(self.now)
            clause='EXISTS(SELECT 1 FROM reviews r WHERE r.card_id=c.id AND r.reviewed_at BETWEEN '+self.parameter(start)+' AND '+self.parameter(self.now)
            if len(parts)==2: clause+=' AND r.rating='+self.parameter({'1':'again','2':'hard','3':'good','4':'easy'}[parts[1]])
            return clause+')'
        if key=='prop':
            match=re.fullmatch(r'(due|ivl|reps|lapses)(<=|>=|!=|=|<|>)(-?\d+)',value)
            if not match: raise CollectionError('La propiedad no está disponible en Explorar.')
            name,op,number=match.groups()
            if abs(int(number))>10000000: raise CollectionError('La propiedad está fuera de rango.')
            if name=='due':
                expr="CAST(julianday(date(c.due_at,'unixepoch','localtime',"+self.parameter("-"+str(self.collection.full_preferences()["rollover_hour"])+" hours")+"))-julianday("+self.parameter(self.day)+") AS INTEGER)"
                return '(c.card_type<>0 AND '+expr+op+self.parameter(int(number))+')'
            field={'ivl':'c.interval_days','reps':'c.reps','lapses':'c.lapses'}[name]
            return field+op+self.parameter(int(number))
        if key in ('front','back','pregunta','respuesta'):
            index=0 if key in ('front','pregunta') else 1
            if value.startswith('re:'):return self.regular(f"json_extract(n.fields_json,'$[{index}]')",value[3:])
            return self.like(f"browser_plain(json_extract(n.fields_json,'$[{index}]'))",value)
        from .note_types import definitions
        from .note_schema import load
        if any(field['name'].casefold()==key for definition in definitions(self.db) for field in load(self.db,definition['id'])['fields']):
            if value.startswith('re:'):
                pattern_placeholder=self.parameter(value[3:]);expression='browser_named_raw(n.fields_json,'+TYPE+','+self.parameter(key)+')'
                from .browser_patterns import compile_pattern
                compile_pattern(value[3:])
                return 'browser_regex('+pattern_placeholder+','+expression+')'
            return self.like('browser_named_field(n.fields_json,'+TYPE+','+self.parameter(key)+')',value)
        raise CollectionError('Filtro no admitido: '+key+'. Usá texto, deck:, tag:, note:, card:, is:, flag:, added:, edited:, rated:, introduced:, cid:, nid: o prop:.')
