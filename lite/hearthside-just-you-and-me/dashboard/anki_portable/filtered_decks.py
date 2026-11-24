"""Named filtered queues over source cards; no native Anki core dependency."""
import json
import uuid
import random
from datetime import datetime, timezone
from .errors import CollectionError
from .browser import configure
from .browser_search import Search

PREFIX = 'filtered_deck:'
MAX_CARDS = 10000


def read(db, deck_id):
    row = db.execute('SELECT value FROM metadata WHERE key=?', (PREFIX + str(deck_id),)).fetchone()
    return json.loads(row[0]) if row else None


def augment_overview(db, summary, now):
    for deck in summary['decks']:
        config = read(db, deck['id'])
        if config is None:
            continue
        rows = list(db.execute('SELECT c.*,sc.next_at FROM custom_session_cards sc JOIN cards c ON c.id=sc.card_id JOIN custom_sessions s ON s.id=sc.session_id WHERE sc.session_id=? AND sc.done=0 AND s.active=1', (config['session_id'],)))
        available = [r for r in rows if not r['suspended'] and r['buried_until'] <= now and r['next_at'] <= now]
        counts = {'new_count': sum(r['card_type'] == 0 for r in available), 'learning_count': sum(r['queue'] in (1, 3) for r in available), 'review_count': sum(r['card_type'] != 0 and r['queue'] not in (1, 3) for r in available)}
        deck.update(filtered=True, filtered_session_id=config['session_id'], card_count=len(rows), subtree_card_count=len(rows), due_count=len(available), subtree_due_count=len(available), **counts)
        deck.update({'subtree_' + k: value for k, value in counts.items()})


def select_cards(collection, db, terms, at, own_session='', current_deck_id=None):
    if current_deck_id is not None and (type(current_deck_id) is not int or not 0 < current_deck_id < 2**63-1 or not db.execute('SELECT 1 FROM decks WHERE id=?', (current_deck_id,)).fetchone()):
        raise CollectionError('El mazo actual no es válido.')
    configure(db)
    selected, seen, excluded = [], set(), []
    for term in terms:
        where, args = Search(collection, db, at, current_deck_id).compile(term['search'])
        rows = list(db.execute('SELECT c.*,d.name AS deck_name,n.tags_json FROM cards c JOIN decks d ON d.id=c.deck_id JOIN notes n ON n.id=c.note_id WHERE ' + where, args))
        eligible = []
        for row in rows:
            if row['id'] in seen:
                continue
            reason = 'Suspendida' if row['suspended'] else 'Enterrada' if row['buried_until'] > at else 'En aprendizaje' if row['queue'] in (1, 3) else None
            if reason is None and db.execute('SELECT 1 FROM custom_session_cards sc JOIN custom_sessions s ON s.id=sc.session_id WHERE sc.card_id=? AND sc.done=0 AND s.active=1 AND s.id!=?', (row['id'], own_session)).fetchone():
                reason = 'En otra sesión o mazo filtrado'
            if reason:
                excluded.append({'card_id': row['id'], 'reason': reason})
            else:
                eligible.append(row)
        order = term['order']
        if order == 1:
            random.SystemRandom().shuffle(eligible)
        else:
            last_reviews = {r[0]: r[1] for r in db.execute('SELECT card_id,MAX(reviewed_at) FROM reviews GROUP BY card_id')}
            engines = {}
            def key(row):
                cid = row['id']
                if order == 0:
                    return (last_reviews.get(cid, 0), cid)
                if order in (2, 3):
                    return ((1 if order == 2 else -1) * row['interval_days'], cid)
                if order == 4:
                    return (-row['lapses'], cid)
                if order in (5, 7):
                    return ((1 if order == 5 else -1) * row['added_at'], (1 if order == 5 else -1) * cid)
                if order == 6:
                    return (row['due_at'], cid)
                from . import scheduler as fsrs
                options = collection._options(db, row['deck_id'])
                if row['deck_id'] not in engines:
                    engines[row['deck_id']] = fsrs.scheduler(options, collection._parameters(db, row['deck_id']))
                engine = engines[row['deck_id']]
                memory = fsrs.restore(row, [], engine, options)
                probability = engine.get_card_retrievability(memory, datetime.fromtimestamp(at, timezone.utc)) if memory.last_review else None
                return (probability is None, (1 if order == 8 else -1) * (probability or 0), cid)
            eligible.sort(key=key)
        for row in eligible[:term['limit']]:
            selected.append(row['id'])
            seen.add(row['id'])
    return selected, list({row['card_id']:row for row in excluded}.values())


class FilteredDeckMixin:
    def preview_filtered_deck(self, terms, *, deck_id=None, current_deck_id=None, now=None):
        if not isinstance(terms, list) or not 1 <= len(terms) <= 2 or any(not isinstance(t, dict) or set(t) != {'search','limit','order'} or not isinstance(t['search'], str) or len(t['search']) > 4096 or type(t['limit']) is not int or not 1 <= t['limit'] <= MAX_CARDS or type(t['order']) is not int or not 0 <= t['order'] <= 9 for t in terms):
            raise CollectionError('El filtro no es válido.')
        with self._study_connection() as db:
            db.execute('PRAGMA query_only=ON')
            db.execute('BEGIN')
            previous = read(db, deck_id) if deck_id is not None else None
            if deck_id is not None and previous is None:
                raise CollectionError('No existe ese mazo filtrado.')
            selected, excluded = select_cards(self, db, terms, self._now(now), previous['session_id'] if previous else '', current_deck_id)
            return {'selected': len(selected), 'excluded': excluded}

    def filtered_deck(self, deck_id):
        if type(deck_id) is not int or deck_id < 1:
            raise CollectionError('El mazo filtrado no es válido.')
        with self._study_connection() as db:
            config = read(db, deck_id)
            deck = db.execute('SELECT name FROM decks WHERE id=?', (deck_id,)).fetchone()
            if config is None or deck is None:
                raise CollectionError('No existe ese mazo filtrado.')
            return {**config, 'deck_id': deck_id, 'name': deck['name']}

    def rebuild_filtered_deck(self, deck_id, now=None):
        config = self.filtered_deck(deck_id)
        return self.build_filtered_deck(config['name'], config['terms'], reschedule=config['reschedule'], allow_empty=config['allow_empty'], deck_id=deck_id, preview_again=config['preview_again'], preview_hard=config['preview_hard'], preview_good=config['preview_good'], current_deck_id=config.get('current_deck_id'), now=now)

    def empty_filtered_deck(self, deck_id):
        with self._study_connection() as db:
            db.execute('BEGIN IMMEDIATE')
            config = read(db, deck_id)
            if config is None:
                raise CollectionError('No existe ese mazo filtrado.')
            db.execute('DELETE FROM custom_session_cards WHERE session_id=?', (config['session_id'],))
            return {'deck_id': deck_id, 'emptied': True}

    def build_filtered_deck(self, name, terms, *, reschedule=True, allow_empty=False, deck_id=None, preview_again=60, preview_hard=600, preview_good=0, current_deck_id=None, now=None):
        if not isinstance(name, str) or not name.strip() or len(name) > 128 or any(not p.strip() for p in name.split('::')) or len(name.split('::')) > 5:
            raise CollectionError('El nombre del mazo no es válido.')
        if type(reschedule) is not bool or type(allow_empty) is not bool:
            raise CollectionError('Las opciones del mazo filtrado no son válidas.')
        if not isinstance(terms, list) or not 1 <= len(terms) <= 2:
            raise CollectionError('El mazo necesita uno o dos filtros.')
        for term in terms:
            if not isinstance(term, dict) or set(term) != {'search', 'limit', 'order'} or not isinstance(term['search'], str) or len(term['search']) > 4096 or type(term['limit']) is not int or not 1 <= term['limit'] <= MAX_CARDS or type(term['order']) is not int or not 0 <= term['order'] <= 9:
                raise CollectionError('El filtro no es válido.')
        for delay in (preview_again, preview_hard, preview_good):
            if type(delay) is not int or not 0 <= delay <= 86400:
                raise CollectionError('El tiempo de previsualización no es válido.')
        at = self._now(now)
        with self._study_connection() as db:
            db.execute('BEGIN IMMEDIATE')
            previous = read(db, deck_id) if deck_id is not None else None
            if deck_id is not None and (type(deck_id) is not int or previous is None):
                raise CollectionError('No existe ese mazo filtrado.')
            if db.execute('SELECT 1 FROM decks WHERE name=? AND id!=?', (name.strip(), deck_id or -1)).fetchone():
                raise CollectionError('Ya existe un mazo con ese nombre.')
            own_session = previous['session_id'] if previous else ''
            selected, excluded = select_cards(self, db, terms, at, own_session, current_deck_id)
            if len(selected) > MAX_CARDS:
                raise CollectionError('Los filtros juntos superan el límite de 10000 tarjetas.')
            if not selected and not allow_empty:
                raise CollectionError('No hay tarjetas disponibles para estos filtros.')
            if deck_id is None:
                deck_id = db.execute("INSERT INTO decks(name,description,created_at) VALUES(?,'',?)", (name.strip(), at)).lastrowid
                self._ensure_parents(db, at)
            else:
                db.execute('UPDATE decks SET name=? WHERE id=?', (name.strip(), deck_id))
                db.execute('UPDATE custom_sessions SET active=0 WHERE id=?', (own_session,))
                db.execute('DELETE FROM custom_session_cards WHERE session_id=?', (own_session,))
            sid = uuid.uuid4().hex
            config = {'terms': terms, 'reschedule': reschedule, 'allow_empty': allow_empty, 'preview_again': preview_again, 'preview_hard': preview_hard, 'preview_good': preview_good, 'session_id': sid, 'current_deck_id': current_deck_id}
            db.execute('INSERT INTO custom_sessions(id,deck_id,include_children,mode,preview,config_json,created_at) VALUES(?,?,0,?,?,?,?)', (sid, deck_id, 'filtered', int(not reschedule), json.dumps(config, ensure_ascii=False), at))
            db.executemany('INSERT INTO custom_session_cards(session_id,card_id,position,next_at) VALUES(?,?,?,?)', [(sid, cid, index, at) for index, cid in enumerate(selected)])
            db.execute('INSERT INTO metadata(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value', (PREFIX + str(deck_id), json.dumps(config, ensure_ascii=False)))
            return {'deck_id': deck_id, 'name': name.strip(), 'session_id': sid, 'selected': len(selected), 'excluded': excluded}
