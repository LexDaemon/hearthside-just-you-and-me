"""Own custom-study selection and temporary quotas; never moves home decks."""
import json
import uuid
from datetime import datetime, timedelta
from .errors import CollectionError

MAX_CARDS=10000
MODES=('increase_new','increase_review','forgotten','ahead','preview_new','by_state')
STATES=('new','due','reviews_random','all_preview')


def initialize(db):
    db.execute('CREATE TABLE IF NOT EXISTS daily_limit_overrides (deck_id INTEGER NOT NULL REFERENCES decks(id) ON DELETE CASCADE, day TEXT NOT NULL, new_extra INTEGER NOT NULL DEFAULT 0, review_extra INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(deck_id,day))')
    db.execute('CREATE TABLE IF NOT EXISTS custom_sessions (id TEXT PRIMARY KEY, deck_id INTEGER NOT NULL REFERENCES decks(id) ON DELETE CASCADE, include_children INTEGER NOT NULL, mode TEXT NOT NULL, preview INTEGER NOT NULL, config_json TEXT NOT NULL, created_at INTEGER NOT NULL, active INTEGER NOT NULL DEFAULT 1)')
    db.execute('CREATE TABLE IF NOT EXISTS custom_session_cards (session_id TEXT NOT NULL REFERENCES custom_sessions(id) ON DELETE CASCADE, card_id INTEGER NOT NULL REFERENCES cards(id) ON DELETE CASCADE, position INTEGER NOT NULL, next_at INTEGER NOT NULL, done INTEGER NOT NULL DEFAULT 0, token INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(session_id,card_id))')
    db.execute('CREATE INDEX IF NOT EXISTS idx_custom_session_card ON custom_session_cards(card_id,done)')
    db.execute('CREATE INDEX IF NOT EXISTS idx_custom_session_deck ON custom_sessions(deck_id,active)')


def integer(value,low,high,label):
    if type(value) is not int or not low<=value<=high: raise CollectionError(label+' no es válido.')
    return value


def scope(db,deck_id,include_children):
    integer(deck_id,1,2**63-2,'El mazo')
    if type(include_children) is not bool: raise CollectionError('El alcance del mazo no es válido.')
    deck=db.execute('SELECT * FROM decks WHERE id=?',(deck_id,)).fetchone()
    if deck is None: raise CollectionError('No existe ese mazo.')
    return deck,'(c.deck_id=? OR (? AND substr(d.name,1,?)=?))',[deck_id,include_children,len(deck['name'])+2,deck['name']+'::']


def available(where):
    return where+' AND c.suspended=0 AND c.buried_until<=? AND NOT EXISTS (SELECT 1 FROM custom_session_cards sc JOIN custom_sessions s ON s.id=sc.session_id WHERE sc.card_id=c.id AND sc.done=0 AND s.active=1 AND s.deck_id!=?)'


def tags(value):
    if not isinstance(value,(list,tuple)) or len(value)>500 or any(not isinstance(t,str) or not t or len(t)>256 or any(ch.isspace() for ch in t) for t in value):
        raise CollectionError('Las etiquetas no son válidas.')
    return list(dict.fromkeys(value))


def tag_filter(values,exclude=False):
    if not values: return '',[]
    clauses=[];args=[]
    for tag in values:
        clauses.append('(t.value=? OR substr(t.value,1,?)=?)');args.extend((tag,len(tag)+2,tag+'::'))
    return ' AND '+('NOT ' if exclude else '')+'EXISTS (SELECT 1 FROM json_each(n.tags_json) t WHERE '+' OR '.join(clauses)+')',args


class CustomStudyMixin:
    def custom_study_info(self,deck_id,include_children=False,now=None):
        now=self._now(now)
        with self._study_connection() as db:
            deck,where,args=scope(db,deck_id,include_children)
            rows=db.execute('SELECT c.*,n.tags_json FROM cards c JOIN decks d ON d.id=c.deck_id JOIN notes n ON n.id=c.note_id WHERE '+available(where),args+[now,deck_id]).fetchall()
            active=db.execute('SELECT s.id,s.mode,s.preview,s.deck_id,s.include_children FROM custom_sessions s WHERE s.active=1 AND (s.deck_id=? OR EXISTS (SELECT 1 FROM custom_session_cards sc JOIN cards c ON c.id=sc.card_id JOIN decks d ON d.id=c.deck_id WHERE sc.session_id=s.id AND sc.done=0 AND '+where+')) ORDER BY (s.deck_id=?) DESC,s.created_at DESC LIMIT 1',[deck_id]+args+[deck_id]).fetchone()
            extra=db.execute('SELECT new_extra,review_extra FROM daily_limit_overrides WHERE deck_id=? AND day=?',(deck_id,self._day(now)[0])).fetchone()
            return {'deck_id':deck_id,'description':deck['description'],
                    'new_available':sum(r['card_type']==0 and r['reps']==0 for r in rows),
                    'review_available':sum(r['card_type']!=0 and r['due_at']<=now for r in rows),
                    'tags':sorted({t for r in rows for t in json.loads(r['tags_json'])},key=str.casefold),
                    'active_session':dict(active) if active else None,
                    'today_extra':dict(extra) if extra else {'new_extra':0,'review_extra':0},
                    'resets_at':self._day(now)[2]}

    def start_custom_study(self,deck_id,mode,*,include_children=False,amount=0,days=1,limit=100,state='new',include_tags=(),exclude_tags=(),now=None):
        now=self._now(now)
        if mode not in MODES: raise CollectionError('La modalidad de estudio no es válida.')
        integer(amount,0,100000,'El aumento diario');integer(days,1,36500,'La cantidad de días');integer(limit,1,MAX_CARDS,'El límite de tarjetas')
        if state not in STATES: raise CollectionError('El estado de tarjetas no es válido.')
        include_tags=tags(include_tags);exclude_tags=tags(exclude_tags)
        if set(include_tags)&set(exclude_tags): raise CollectionError('Una etiqueta no puede incluirse y excluirse a la vez.')
        with self._study_connection() as db:
            db.execute('BEGIN IMMEDIATE')
            _,where,args=scope(db,deck_id,include_children)
            if mode in ('increase_new','increase_review'):
                return self._increase_today(db,deck_id,mode,amount,now)
            where=available(where);args.extend((now,deck_id))
            _,start,end=self._day(now)
            since=int((datetime.fromtimestamp(start)-timedelta(days=days-1)).timestamp())
            until=int((datetime.fromtimestamp(end)+timedelta(days=days)).timestamp())
            order='c.due_at,c.id'
            if mode=='forgotten':
                where+=' AND EXISTS (SELECT 1 FROM reviews r WHERE r.card_id=c.id AND r.rating="again" AND r.reviewed_at>=? AND r.reviewed_at<=?)';args.extend((since,now))
            elif mode=='ahead':
                where+=' AND c.card_type=2 AND c.queue=2 AND c.due_at>? AND c.due_at<?';args.extend((now,until))
            elif mode=='preview_new':
                where+=' AND c.card_type=0 AND c.reps=0 AND c.added_at>=? AND c.added_at<=?';args.extend((since,now));order='c.new_position,c.id'
            else:
                where+= {'new':' AND c.card_type=0 AND c.reps=0','due':' AND c.card_type!=0 AND c.due_at<=?','reviews_random':' AND c.card_type IN (2,3)','all_preview':''}[state]
                if state=='due': args.append(now)
                if state in ('reviews_random','all_preview'): order='RANDOM()'
                if state=='new': order='c.new_position,c.id'
                for vals,exclude in ((include_tags,False),(exclude_tags,True)):
                    clause,params=tag_filter(vals,exclude);where+=clause;args.extend(params)
            cap=limit if mode=='by_state' else MAX_CARDS+1
            rows=db.execute('SELECT c.id FROM cards c JOIN decks d ON d.id=c.deck_id JOIN notes n ON n.id=c.note_id WHERE '+where+' ORDER BY '+order+' LIMIT ?',args+[cap]).fetchall()
            if not rows: raise CollectionError('No hay tarjetas disponibles con esos criterios. Las suspendidas, enterradas o reservadas en otra sesión no se incluyen.')
            if len(rows)>MAX_CARDS: raise CollectionError('La selección supera 10.000 tarjetas. Reducí los días o seleccioná por estado y etiquetas.')
            sid=uuid.uuid4().hex
            preview=mode in ('forgotten','preview_new') or (mode=='by_state' and state=='all_preview')
            config={'days':days,'limit':limit,'state':state,'include_tags':include_tags,'exclude_tags':exclude_tags}
            db.execute('UPDATE custom_sessions SET active=0 WHERE deck_id=? AND active=1',(deck_id,))
            db.execute('DELETE FROM custom_session_cards WHERE session_id IN (SELECT id FROM custom_sessions WHERE deck_id=? AND active=0)',(deck_id,))
            db.execute('INSERT INTO custom_sessions(id,deck_id,include_children,mode,preview,config_json,created_at) VALUES(?,?,?,?,?,?,?)',(sid,deck_id,include_children,mode,int(preview),json.dumps(config,ensure_ascii=False),now))
            db.executemany('INSERT INTO custom_session_cards(session_id,card_id,position,next_at) VALUES(?,?,?,?)',[(sid,r['id'],i,now) for i,r in enumerate(rows)])
            return {'mode':mode,'session_id':sid,'selected':len(rows),'preview':preview}

    def _increase_today(self,db,deck_id,mode,amount,now):
        day=self._day(now)[0]
        db.execute('INSERT OR IGNORE INTO daily_limit_overrides(deck_id,day) VALUES(?,?)',(deck_id,day))
        column='new_extra' if mode=='increase_new' else 'review_extra'
        current=db.execute('SELECT '+column+' FROM daily_limit_overrides WHERE deck_id=? AND day=?',(deck_id,day)).fetchone()[0]
        if current+amount>100000: raise CollectionError('El aumento acumulado supera el máximo diario permitido.')
        from .daily_limits import increase
        increase(self,db,deck_id,'new_per_day' if mode=='increase_new' else 'reviews_per_day',amount,day)
        db.execute('UPDATE daily_limit_overrides SET '+column+'=? WHERE deck_id=? AND day=?',(current+amount,deck_id,day))
        return {'mode':mode,'amount':amount,'resets_at':self._day(now)[2],'session_id':None}

    def set_deck_description(self,deck_id,description):
        if not isinstance(description,str) or len(description)>100000: raise CollectionError('La descripción no es válida.')
        with self._study_connection() as db:
            scope(db,deck_id,False)
            db.execute('UPDATE decks SET description=? WHERE id=?',(description,deck_id))
        return {'deck_id':deck_id,'description':description}
