"""Compact deck options and the collection's local study policy."""
from __future__ import annotations

import json
import math
import time
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from .errors import CollectionError
from . import scheduler as fsrs
from . import study_queue

DEFAULT_OPTIONS = {
    'new_per_day': 20, 'reviews_per_day': 200, 'desired_retention': .9, 'fsrs_enabled': False,
    'new_position': 'mixed', 'new_order': 'created', 'bury_siblings': True,
    'learning_steps': [60, 600], 'relearning_steps': [600], 'maximum_interval': 36500,
    'new_gather': 'deck', 'new_sort': 'template', 'review_sort': 'due_random',
    'interday_position': 'mixed', 'bury_new': True, 'bury_review': True,
    'bury_interday': True, 'historical_retention': .9, 'ignore_before': '1970-01-01',
    'leech_threshold': 8, 'leech_action': 'suspend',
    'new_ignore_review_limit': False, 'limits_start_top': False,
    'max_answer_seconds': 60, 'show_answer_timer': False, 'stop_timer_on_answer': False,
    'question_seconds': 0, 'answer_seconds': 0, 'question_action': 'show_answer', 'answer_action': 'bury',
}


def validate_options(values):
    if not isinstance(values, dict) or set(values) - set(DEFAULT_OPTIONS):
        raise CollectionError('Las opciones del mazo contienen campos no admitidos.')
    for name, value in values.items():
        if name in ('new_per_day', 'reviews_per_day', 'maximum_interval'):
            minimum = 1 if name == 'maximum_interval' else 0
            if type(value) is not int or not minimum <= value <= 36500:
                raise CollectionError('Los límites deben ser enteros entre %s y 36500.' % minimum)
        elif name == 'max_answer_seconds':
            if type(value) is not int or not 1 <= value <= 3600:
                raise CollectionError('El tiempo máximo debe ser un entero entre 1 y 3600 segundos.')
        elif name in ('question_seconds','answer_seconds'):
            if type(value) is not int or not 0 <= value <= 3600:
                raise CollectionError('El tiempo de avance debe ser un entero entre 0 y 3600 segundos.')
        elif name in ('question_action','answer_action','leech_action'):
            choices = {'question_action':('show_answer','reminder'),'answer_action':('bury','again','hard','good','reminder'),'leech_action':('tag','suspend')}
            if not isinstance(value,str) or value not in choices[name]:
                raise CollectionError('Elegí una acción válida.')
        elif name == 'leech_threshold':
            if type(value) is not int or not 0 <= value <= 1000:
                raise CollectionError('El umbral para olvidadas debe ser un entero entre 0 y 1000.')

        elif name in ('desired_retention', 'historical_retention'):
            if type(value) not in (float, int) or not math.isfinite(value) or not .7 <= value <= .97:
                raise CollectionError('La retención debe estar entre 70 % y 97 %.')
        elif name in ('learning_steps', 'relearning_steps'):
            if not isinstance(value, list) or len(value) > 8 or any(type(v) is not int or not 1 <= v < 86400 for v in value):
                raise CollectionError('Usá hasta 8 pasos, cada uno de al menos 1 segundo y menor a un día.')
        elif name in ('new_position', 'interday_position') and value not in ('before', 'after', 'mixed'):
            raise CollectionError('Elegí un orden de estudio válido.')
        elif name == 'new_order' and value not in ('created', 'random'):
            raise CollectionError('Elegí un orden de tarjetas nuevas válido.')
        elif name in ('bury_siblings','bury_new','bury_review','bury_interday','new_ignore_review_limit','limits_start_top','show_answer_timer','stop_timer_on_answer','fsrs_enabled') and type(value) is not bool:
            raise CollectionError('Los interruptores deben estar activados o desactivados.')
        elif name in ('new_gather','new_sort','review_sort'):
            from .order_policy import CHOICES
            if not isinstance(value,str) or value not in CHOICES[name]:
                raise CollectionError('Elegí un orden válido.')
        elif name == 'ignore_before':
            from datetime import date
            try:
                if not isinstance(value,str) or (value and date.fromisoformat(value).isoformat() != value):
                    raise ValueError()
            except ValueError as exc:
                raise CollectionError('La fecha de corte debe ser una fecha válida AAAA-MM-DD.') from exc
    return values


class StudyMixin:
    path: Path

    @staticmethod
    def _now(now):
        return int(time.time()) if now is None else int(now)

    def _study_connection(self):
        # Delayed import keeps the SQLite/interchange module independent of FSRS.
        from .collection import _connect
        return _connect(self.path)

    def _initialize_study(self, db):
        from .review_queue import initialize
        queue_missing=not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='review_learning_order'").fetchone()
        columns = {row['name'] for row in db.execute('PRAGMA table_info(cards)')}
        suffix = '.before-fsrs.sqlite3' if 'fsrs_json' not in columns else '.before-study-order.sqlite3' if 'new_position' not in columns else '.before-dual-engine.sqlite3' if 'classic_json' not in columns else '.before-review-queue.sqlite3'
        backup = self.path.with_name(self.path.stem + suffix)
        if ('fsrs_json' not in columns or 'new_position' not in columns or 'classic_json' not in columns or queue_missing) and db.execute('SELECT COUNT(*) FROM cards').fetchone()[0] and not backup.exists():
            import os
            import sqlite3
            import tempfile
            fd, name = tempfile.mkstemp(prefix='.before-fsrs-', suffix='.sqlite3', dir=self.path.parent)
            os.close(fd)
            try:
                target = sqlite3.connect(name)
                try:
                    db.backup(target)
                    if target.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
                        raise CollectionError('No se pudo verificar el respaldo previo a FSRS.')
                finally:
                    target.close()
                try:
                    os.link(name, backup)
                except FileExistsError:
                    pass  # Another first opener already preserved the pre-migration snapshot.
            finally:
                if os.path.exists(name): os.unlink(name)
        db.execute('BEGIN IMMEDIATE')
        initialize(db)
        db.execute('CREATE TABLE IF NOT EXISTS deck_options (deck_id INTEGER PRIMARY KEY REFERENCES decks(id) ON DELETE CASCADE, options_json TEXT NOT NULL, parameters_json TEXT, optimized_at INTEGER)')
        for table, additions in {
            'cards': {'manual_buried_until': 'INTEGER NOT NULL DEFAULT 0', 'review_history_skip': 'INTEGER NOT NULL DEFAULT 0', 'classic_json': 'TEXT', 'fsrs_json': 'TEXT', 'buried_until': 'INTEGER NOT NULL DEFAULT 0', 'new_position': 'INTEGER', 'added_at': 'INTEGER'},
            'reviews': {'scheduler': "TEXT NOT NULL DEFAULT 'legacy'", 'fsrs_log': 'TEXT', 'study_kind': 'TEXT', 'study_day': 'TEXT', 'deck_id_at_review': 'INTEGER'},
        }.items():
            columns = {row['name'] for row in db.execute('PRAGMA table_info(%s)' % table)}
            for name, declaration in additions.items():
                if name not in columns:
                    db.execute('ALTER TABLE %s ADD COLUMN %s %s' % (table, name, declaration))
        db.execute("UPDATE reviews SET scheduler='fsrs-6' WHERE scheduler='legacy' AND fsrs_log IS NOT NULL")
        db.execute('UPDATE cards SET new_position=id WHERE new_position IS NULL')
        db.execute('UPDATE cards SET added_at=(SELECT created_at FROM notes WHERE notes.id=cards.note_id) WHERE added_at IS NULL')
        db.execute('CREATE INDEX IF NOT EXISTS reviews_day_idx ON reviews(study_day,deck_id_at_review)')
        db.execute('CREATE INDEX IF NOT EXISTS reviews_card_time_idx ON reviews(card_id,reviewed_at,id)')
        db.execute('CREATE INDEX IF NOT EXISTS reviews_time_idx ON reviews(reviewed_at)')
        for key,value in [('schema_version','2'),('scheduler_version','fsrs-6')]:
            db.execute('INSERT INTO metadata(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value WHERE metadata.value<>excluded.value', (key,value))

    @staticmethod
    def _options(db, deck_id):
        if not db.execute('SELECT 1 FROM decks WHERE id=?', (deck_id,)).fetchone():
            raise CollectionError('No existe ese mazo.')
        from .daily_limits import config
        options = deepcopy(DEFAULT_OPTIONS)
        options.update(config(db))
        row = db.execute('SELECT options_json FROM deck_options WHERE deck_id=?', (deck_id,)).fetchone()
        if row:
            saved = json.loads(row['options_json'])
            options.update(saved)
            for key in ('bury_new','bury_review','bury_interday'):
                if key not in saved: options[key] = saved.get('bury_siblings',True)
            if 'new_gather' not in saved and saved.get('new_order') == 'random':
                options['new_gather'] = 'random_cards'
        return options

    @staticmethod
    def default_deck_options():
        return deepcopy(DEFAULT_OPTIONS)

    def deck_options(self, deck_id):
        with self._study_connection() as db:
            return self._options(db, deck_id)

    def daily_limit_options(self, deck_id, *, now=None):
        from .daily_limits import info
        with self._study_connection() as db:
            return info(self,db,deck_id,self._now(now))

    def set_deck_options(self, deck_id, values, *, reset=False, daily_limits=None, now=None):
        from . import daily_limits as limits
        validate_options(values)
        now = self._now(now)
        day = self._day(now)[0]
        with self._study_connection() as db:
            db.execute('BEGIN IMMEDIATE')
            options = self._options(db, deck_id)
            if daily_limits is not None:
                limits.validate_patch(daily_limits,day)
                if reset: raise CollectionError('Restablecer no admite cambios adicionales de alcance.')
            row = db.execute('SELECT options_json FROM deck_options WHERE deck_id=?',(deck_id,)).fetchone()
            previous = json.loads(row['options_json']) if row else {}
            if reset or any(key in values for key in limits.KEYS) or daily_limits is not None:
                limits.freeze_legacy(self,db,day)
            if reset:
                options = deepcopy(DEFAULT_OPTIONS)
            options.update(values)
            if 'bury_siblings' in values and not any(k in values for k in ('bury_new','bury_review','bury_interday')):
                for key in ('bury_new','bury_review','bury_interday'): options[key] = values['bury_siblings']
            options['bury_siblings'] = any(options[k] for k in ('bury_new','bury_review','bury_interday'))
            if 'new_order' in values and values['new_order'] != self._options(db,deck_id)['new_order']:
                from .order_policy import reposition
                reposition(db,deck_id,values['new_order'])
                if 'new_gather' not in values:
                    options['new_gather'] = 'position_asc'
            stored = dict(options)
            for key in limits.KEYS:
                if not reset and key not in values and key not in previous: stored.pop(key,None)
            db.execute('INSERT INTO deck_options(deck_id,options_json) VALUES(?,?) ON CONFLICT(deck_id) DO UPDATE SET options_json=excluded.options_json', (deck_id, json.dumps(stored)))
            if reset:
                db.execute('UPDATE deck_options SET parameters_json=NULL,optimized_at=NULL WHERE deck_id=?', (deck_id,))
                limits.clear_today(db,deck_id,day)
            if daily_limits is not None: limits.save(db,deck_id,daily_limits,day)
            return self._options(db,deck_id)

    def optimization_status(self, deck_id):
        from . import optimization
        with self._study_connection() as db:
            return optimization.status(self, db, deck_id)

    def optimize_fsrs(self, deck_id, now=None):
        from . import optimization
        return optimization.optimize(self, deck_id, self._now(now))

    def _day(self,now):
        hour=self.full_preferences()['rollover_hour']
        date = (datetime.fromtimestamp(now) - timedelta(hours=hour)).date()
        start = datetime.combine(date, datetime.min.time()) + timedelta(hours=hour)
        return date.isoformat(), int(start.timestamp()), int((start + timedelta(days=1)).timestamp())

    @staticmethod
    def _parameters(db, deck_id):
        row = db.execute('SELECT parameters_json FROM deck_options WHERE deck_id=?', (deck_id,)).fetchone()
        return json.loads(row['parameters_json']) if row and row['parameters_json'] else None

    def _evaluate(self, db, row, rating, now, response_ms=0):
        options = self._options(db,row['deck_id'])
        if not options['fsrs_enabled']:
            from . import classic_scheduler
            result=classic_scheduler.evaluate(row,options,rating,now)
        else:
            from .review_actions import history as scheduler_history
            history = scheduler_history(db,row['id']) if not row['fsrs_json'] else []
            result=fsrs.evaluate(row, history, options, self._parameters(db, row['deck_id']), rating, now, response_ms)
        updated=result[1]
        if int(updated.state)!=2 and int(updated.due.timestamp())>=self._day(now)[2]:
            updated.due=datetime.fromtimestamp(self._day(int(updated.due.timestamp()))[1],timezone.utc)
        return result

    def answer_intervals(self, card_id, now=None):
        now = self._now(now)
        with self._study_connection() as db:
            row = db.execute('SELECT * FROM cards WHERE id=?', (card_id,)).fetchone()
            if row is None:
                raise CollectionError('No existe esa tarjeta.')
            return self._answer_intervals(db, row, now)

    def full_preferences(self):
        from .preferences import load
        with self._study_connection() as db:return load(db)

    def preferences_state(self):
        from .preferences import load,version
        with self._study_connection() as db:
            value=load(db)
            decks={r['key'][len('authoring_last_deck:'):]:int(r['value']) for r in db.execute("SELECT key,value FROM metadata WHERE key LIKE 'authoring_last_deck:%'")}
            return {'preferences':value,'version':version(value),'last_decks':decks}

    def set_full_preferences(self,value,expected_version=None):
        from .preferences import load,save
        with self._study_connection() as db:
            db.execute('BEGIN IMMEDIATE')
            previous=load(db)
            result=save(db,value,expected_version)
            from .browser import configure
            from .browser_search import Search
            configure(db);Search(self,db,self._now(None),None).compile(result['default_search'])
            if any(previous[k]!=result[k] for k in ('rollover_hour','learn_ahead_seconds')):db.execute('DELETE FROM review_learning_order')
            return result

    def review_preferences(self):
        from .review_queue import preferences
        with self._study_connection() as db:return preferences(db)

    def set_review_preferences(self,value):
        from .review_queue import save_preferences
        with self._study_connection() as db:return save_preferences(db,value)

    def study_status(self, now=None, deck_id=None, include_children=False):
        now = self._now(now)
        with self._study_connection() as db:
            _, status = study_queue.select(self, db, now, deck_id, include_children)
            scope = '' if deck_id is None else ' AND (deck_id=? OR (? AND deck_id IN (SELECT id FROM decks WHERE substr(name,1,length((SELECT name FROM decks WHERE id=?))+2)=(SELECT name FROM decks WHERE id=?) || \'::\')))'
            args = () if deck_id is None else (deck_id,include_children,deck_id,deck_id)
            reserved = ' AND EXISTS (SELECT 1 FROM custom_session_cards sc JOIN custom_sessions s ON s.id=sc.session_id WHERE sc.card_id=cards.id AND sc.done=0 AND s.active=1)'
            status['reserved'] = db.execute('SELECT COUNT(*) FROM cards WHERE suspended=0' + reserved + scope,args).fetchone()[0]
            next_time = db.execute('SELECT MIN(due_at) FROM cards WHERE suspended=0 AND due_at>? AND NOT EXISTS (SELECT 1 FROM custom_session_cards sc JOIN custom_sessions s ON s.id=sc.session_id WHERE sc.card_id=cards.id AND sc.done=0 AND s.active=1)' + scope, (now,)+args).fetchone()[0]
            status['next_due_at'] = next_time
            if status['reason'] == 'complete' and status['reserved']:
                status['reason'] = 'custom_session'
            elif status['reason'] == 'complete' and next_time is not None:
                status['reason'] = 'waiting'
            return status

    def _answer_intervals(self, db, row, now):
        result = {}
        for rating in fsrs.RATINGS:
            _, updated, _ = self._evaluate(db, row, rating, now)
            due = int(updated.due.timestamp())
            result[rating] = {'due_at': due, 'seconds': max(1, due-now)}
        return result

    def _apply_leech(self,db,row,lapses,now):
        options = self._options(db,row['deck_id'])
        threshold = options['leech_threshold']
        if not threshold or lapses < threshold or (lapses-threshold) % max(1,(threshold+1)//2):
            return False
        note = db.execute('SELECT tags_json FROM notes WHERE id=?',(row['note_id'],)).fetchone()
        tags = json.loads(note['tags_json'])
        if not any(tag.casefold() == 'leech' for tag in tags):
            tags.append('leech')
            db.execute('UPDATE notes SET tags_json=?,modified_at=? WHERE id=?',(json.dumps(tags),now,row['note_id']))
        if options['leech_action'] == 'suspend':
            db.execute('UPDATE cards SET suspended=1 WHERE id=?',(row['id'],))
        return True

    def bury_card(self,card_id,now=None,expected_reps=None):
        now = self._now(now)
        with self._study_connection() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM cards WHERE id=?',(card_id,)).fetchone()
            if row is None or row['suspended']:
                raise CollectionError('La tarjeta no existe o está suspendida.')
            if expected_reps is not None and row['reps'] != expected_reps:
                raise CollectionError('La tarjeta cambió. Actualizá el repaso.')
            end = self._day(now)[2]
            db.execute('UPDATE cards SET buried_until=MAX(buried_until,?),manual_buried_until=MAX(manual_buried_until,?) WHERE id=?',(end,end,card_id))
            return {'card_id':card_id,'buried_until':max(row['buried_until'],end)}

    def grade_fsrs(self, card_id, rating, now=None, response_ms=0, expected_reps=None, review_deck_id=None, include_children=False):
        if (review_deck_id is not None and (type(review_deck_id) is not int or review_deck_id<1)) or type(include_children) is not bool:raise CollectionError('El alcance del repaso no es válido.')
        with self._study_connection() as db:
            db.execute('BEGIN IMMEDIATE')
            reserved=db.execute('SELECT 1 FROM custom_session_cards sc JOIN custom_sessions s ON s.id=sc.session_id WHERE sc.card_id=? AND sc.done=0 AND s.active=1',(card_id,)).fetchone()
            if reserved: raise CollectionError('La tarjeta pertenece a una sesión de estudio personalizado. Actualizá el repaso.')
            return self._grade_in_transaction(db,card_id,rating,now,response_ms,expected_reps,review_deck_id,include_children)

    def _grade_in_transaction(self, db, card_id, rating, now=None, response_ms=0, expected_reps=None, review_deck_id=None, include_children=False):
        if rating not in fsrs.RATINGS:
            raise CollectionError('Calificación no válida.')
        now = self._now(now)
        elapsed = max(0, min(int(response_ms or 0), 86400000))
        row = db.execute('SELECT * FROM cards WHERE id=?', (card_id,)).fetchone()
        if row is None or row['suspended']:
            raise CollectionError('La tarjeta no existe o está suspendida.')
        if expected_reps is not None and row['reps'] != expected_reps:
            raise CollectionError('Esta respuesta ya se guardó o la tarjeta cambió. Actualizá el repaso.')
        elapsed = min(elapsed, self._options(db,row['deck_id'])['max_answer_seconds'] * 1000)
        before, updated, log = self._evaluate(db, row, rating, now, elapsed)
        due = int(updated.due.timestamp())
        interval = max(0, (due-now)//86400)
        queue = 2 if updated.state.value == 2 else 3 if due>=self._day(now)[2] else 1
        card_type = 2 if updated.state.value in (2,3) else 1
        lapses = row['lapses'] + (rating == 'again' and before.state.value == 2)
        leech = self._apply_leech(db,row,lapses,now) if lapses > row['lapses'] else False
        day, start, end = self._day(now)
        kind = study_queue.category(row, {'start':start})
        study_queue.bury_siblings(self,db,row,now,start,end)
        enabled = self._options(db,row['deck_id'])['fsrs_enabled']
        engine = 'fsrs-6' if enabled else 'sm2-classic-v1'
        ease = row['ease_factor'] if enabled else updated.ease_factor
        # Invalidate the other engine's state only after a real answer.
        # A later FSRS answer replays ALL history, including classic answers.
        db.execute('UPDATE cards SET due_at=?,interval_days=?,reps=reps+1,lapses=?,card_type=?,queue=?,fsrs_json=?,classic_json=?,ease_factor=? WHERE id=?', (due, interval, lapses, card_type, queue, updated.to_json() if enabled else None, None if enabled else updated.to_json(),ease,card_id))
        serialized_log=None
        if enabled:
            detail=json.loads(log.to_json())
            detail['_memoria_info_v1']={'interval_seconds':max(0,due-now),'difficulty_after':updated.difficulty,'state_before':before.state.value}
            serialized_log=json.dumps(detail,separators=(',',':'))
        db.execute('INSERT INTO reviews(card_id,reviewed_at,rating,interval_before,interval_after,ease_factor,response_ms,fsrs_log,study_kind,study_day,deck_id_at_review,scheduler) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)', (card_id,now,rating,row['interval_days'],interval,ease,elapsed,serialized_log,kind,day,row['deck_id'],engine))
        from .review_queue import requeue
        requeue(self,db,card_id,now,review_deck_id,include_children)
        return {'card_id':card_id,'rating':rating,'reviewed_at':now,'due_at':due,'interval_days':interval,'ease_factor':ease,'scheduler':engine,'reps':row['reps']+1,'lapses':lapses,'leech':leech,'suspended':bool(db.execute('SELECT suspended FROM cards WHERE id=?',(card_id,)).fetchone()[0])}
