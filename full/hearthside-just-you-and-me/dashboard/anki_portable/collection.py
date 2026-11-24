"""Local-first flashcard collection with Anki package interchange.

The first cut supports Anki Basic and Basic (and reversed card) notes. It keeps
its own SQLite collection so the scheduler and imported collection can evolve
without depending on Anki being installed.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import tempfile
import time
import uuid
import zipfile
from datetime import datetime
from contextlib import contextmanager
from pathlib import Path, PurePosixPath
from typing import Any, Dict, Iterator, List, Optional, Tuple

MAX_APKG_BYTES = 256 * 1024 * 1024
MAX_APKG_UNCOMPRESSED_BYTES = 1024 * 1024 * 1024
MAX_COLLECTION_BYTES = 512 * 1024 * 1024
MAX_MEDIA_BYTES = 64 * 1024 * 1024
MAX_MEDIA_INDEX_BYTES = 32 * 1024 * 1024
SCHEDULER_VERSION = "fsrs-6"
DEFAULT_DECK_NAME = "Predeterminado"
FIELD_SEPARATOR = "\x1f"


from .errors import CollectionError, UnsupportedNoteType, MissingZstandard
from .study import StudyMixin
from .browser import BrowserMixin, initialize as initialize_browser
from .custom_study import CustomStudyMixin, initialize as initialize_custom_study
from .custom_sessions import CustomSessionMixin
from .statistics import StatisticsMixin
from .authoring import AuthoringMixin
from .review_actions import ReviewActionsMixin
from .backups import BackupsMixin
from .filtered_decks import FilteredDeckMixin
from .activity import initialize as initialize_activity, archive_card_reviews


def _ensure_default_deck(db: sqlite3.Connection, now: int) -> None:
    """Insert exactly one ordinary deck only when no decks remain."""
    db.execute(
        "INSERT INTO decks(name, description, created_at) "
        "SELECT ?, '', ? WHERE NOT EXISTS (SELECT 1 FROM decks)",
        (DEFAULT_DECK_NAME, now),
    )


@contextmanager
def _connect(path: Path) -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(str(path), timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=10000")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _tags(value: Any) -> List[str]:
    if isinstance(value, str):
        raw = value.split()
    elif isinstance(value, (list, tuple, set)):
        raw = [str(item) for item in value]
    else:
        raw = []
    return sorted({item.strip() for item in raw if item.strip()})


def _parse_json(value: Any, fallback: Any) -> Any:
    if not value:
        return fallback
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return fallback


def _safe_media_name(name: str) -> str:
    # Anki stores media at the archive root. Never permit paths to escape it.
    candidate = PurePosixPath(str(name).replace("\\", "/"))
    if candidate.is_absolute() or len(candidate.parts) != 1 or candidate.name in ("", ".", ".."):
        raise CollectionError("El paquete contiene un nombre de medio no válido.")
    if "\x00" in candidate.name:
        raise CollectionError("El paquete contiene un nombre de medio no válido.")
    return candidate.name


def _read_varint(data: bytes, offset: int) -> Tuple[int, int]:
    value = 0
    shift = 0
    while offset < len(data) and shift < 70:
        byte = data[offset]
        offset += 1
        value |= (byte & 0x7F) << shift
        if byte < 0x80:
            return value, offset
        shift += 7
    raise CollectionError("El índice protobuf de medios está truncado o malformado.")


def _protobuf_fields(data: bytes) -> Iterator[Tuple[int, int, Any]]:
    offset = 0
    while offset < len(data):
        tag, offset = _read_varint(data, offset)
        number, wire = tag >> 3, tag & 7
        if number == 0:
            raise CollectionError("El índice protobuf de medios está malformado.")
        if wire == 0:
            value, offset = _read_varint(data, offset)
        elif wire == 1:
            end = offset + 8
            if end > len(data):
                raise CollectionError("El índice protobuf de medios está truncado.")
            value, offset = data[offset:end], end
        elif wire == 2:
            length, offset = _read_varint(data, offset)
            end = offset + length
            if end > len(data):
                raise CollectionError("El índice protobuf de medios está truncado.")
            value, offset = data[offset:end], end
        elif wire == 5:
            end = offset + 4
            if end > len(data):
                raise CollectionError("El índice protobuf de medios está truncado.")
            value, offset = data[offset:end], end
        else:
            raise CollectionError("El índice protobuf de medios usa un campo no compatible.")
        yield number, wire, value


def _parse_modern_media_index(data: bytes) -> List[Tuple[str, str, int, bytes]]:
    entries: List[Tuple[str, str, int, bytes]] = []
    seen_names = set()
    for field, wire, payload in _protobuf_fields(data):
        if field != 1 or wire != 2:
            continue
        name = ""
        size = 0
        checksum = b""
        for entry_field, entry_wire, value in _protobuf_fields(payload):
            if entry_field == 1 and entry_wire == 2:
                try:
                    name = value.decode("utf-8")
                except UnicodeDecodeError as exc:
                    raise CollectionError("El índice protobuf contiene un nombre de medio inválido.") from exc
            elif entry_field == 2 and entry_wire == 0:
                size = int(value)
            elif entry_field == 3 and entry_wire == 2:
                checksum = bytes(value)
        clean_name = _safe_media_name(name)
        if clean_name in seen_names:
            raise CollectionError("El índice protobuf repite un nombre de medio.")
        if size < 0 or size > MAX_MEDIA_BYTES or (checksum and len(checksum) != 20):
            raise CollectionError("El índice protobuf de medios contiene datos fuera de rango.")
        seen_names.add(clean_name)
        entries.append((str(len(entries)), clean_name, size, checksum))
    return entries


def _zstd_decompress(data: bytes, max_output_size: int, description: str) -> bytes:
    try:
        import zstandard
    except ImportError as exc:
        raise MissingZstandard(
            "Este paquete .apkg usa compresión Zstandard. "
            "Instalá la dependencia declarada del plugin y volvé a intentar."
        ) from exc
    try:
        limit = max(0, max_output_size)
        declared_size = zstandard.frame_content_size(data)
        # max_output_size only bounds frames without a declared content size.
        # Reject oversized known-size frames before the decoder allocates them.
        if declared_size not in (zstandard.CONTENTSIZE_UNKNOWN, zstandard.CONTENTSIZE_ERROR) and declared_size > limit:
            raise CollectionError("El tamaño descomprimido de %s supera el límite permitido." % description)
        decoded = zstandard.ZstdDecompressor().decompress(data, max_output_size=max(1, limit))
        if len(decoded) > limit:
            raise CollectionError("El tamaño descomprimido de %s supera el límite permitido." % description)
        return decoded
    except CollectionError:
        raise
    except Exception as exc:
        raise CollectionError("No se pudo descomprimir %s del paquete .apkg." % description) from exc


def _model_kind(model):
    from .note_models import kind_for_model
    return kind_for_model(model)


def _fields_for_note(note_row, model, kind):
    from .note_models import fields_for_note
    return fields_for_note(note_row,model,kind)


def _basic_model(model_id: int, name: str, now: int, reversed_cards: bool = False) -> Dict[str, Any]:
    fields = [
        {"name": "Front", "ord": 0, "sticky": False, "rtl": False, "font": "Arial", "size": 20, "media": []},
        {"name": "Back", "ord": 1, "sticky": False, "rtl": False, "font": "Arial", "size": 20, "media": []},
    ]
    templates = [{
        "name": "Card 1", "ord": 0, "qfmt": "{{Front}}",
        "afmt": "{{FrontSide}}\n\n<hr id=answer>\n\n{{Back}}",
        "bqfmt": "", "bafmt": "", "did": None, "bfont": "", "bsize": 0,
    }]
    required = [[0, "all", [0]]]
    if reversed_cards:
        templates.append({
            "name": "Card 2", "ord": 1, "qfmt": "{{Back}}",
            "afmt": "{{FrontSide}}\n\n<hr id=answer>\n\n{{Front}}",
            "bqfmt": "", "bafmt": "", "did": None, "bfont": "", "bsize": 0,
        })
        required.append([1, "all", [1]])
    return {
        "vers": [], "name": name, "tags": [], "did": 1, "usn": -1,
        "req": required, "flds": fields, "sortf": 0, "tmpls": templates,
        "mod": now, "type": 0, "id": model_id,
        "css": ".card { font-family: Arial; font-size: 20px; text-align: center; color: black; background-color: white; }",
        "latexPre": "\\documentclass[12pt]{article}\n\\special{papersize=3in,5in}\n\\usepackage[utf8]{inputenc}\n\\usepackage{amssymb,amsmath}\n\\pagestyle{empty}\n\\setlength{\\parindent}{0in}\n\\begin{document}",
        "latexPost": "\\end{document}",
    }


from .note_types import NoteTypesMixin


class AnkiCollection(FilteredDeckMixin, BackupsMixin, ReviewActionsMixin, StudyMixin, BrowserMixin, CustomStudyMixin, CustomSessionMixin, StatisticsMixin, AuthoringMixin, NoteTypesMixin):
    """SQLite-backed collection with a deliberately bounded Anki interchange layer."""

    def __init__(self, path: Path | str):
        self.path = Path(path).expanduser()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _initialize(self) -> None:
        with _connect(self.path) as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS decks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL UNIQUE,
                    description TEXT NOT NULL DEFAULT '',
                    created_at INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS notes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guid TEXT NOT NULL UNIQUE,
                    model_kind TEXT NOT NULL,
                    fields_json TEXT NOT NULL,
                    tags_json TEXT NOT NULL,
                    created_at INTEGER NOT NULL,
                    modified_at INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS cards (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    note_id INTEGER NOT NULL REFERENCES notes(id),
                    deck_id INTEGER NOT NULL REFERENCES decks(id),
                    ordinal INTEGER NOT NULL DEFAULT 0,
                    front TEXT NOT NULL,
                    back TEXT NOT NULL,
                    due_at INTEGER NOT NULL,
                    interval_days INTEGER NOT NULL DEFAULT 0,
                    ease_factor INTEGER NOT NULL DEFAULT 2500,
                    reps INTEGER NOT NULL DEFAULT 0,
                    lapses INTEGER NOT NULL DEFAULT 0,
                    card_type INTEGER NOT NULL DEFAULT 0,
                    queue INTEGER NOT NULL DEFAULT 0,
                    suspended INTEGER NOT NULL DEFAULT 0,
                    UNIQUE(note_id, ordinal)
                );
                CREATE INDEX IF NOT EXISTS cards_due_idx ON cards(suspended, due_at, id);
                CREATE TABLE IF NOT EXISTS reviews (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    card_id INTEGER NOT NULL REFERENCES cards(id),
                    reviewed_at INTEGER NOT NULL,
                    rating TEXT NOT NULL,
                    interval_before INTEGER NOT NULL,
                    interval_after INTEGER NOT NULL,
                    ease_factor INTEGER NOT NULL,
                    response_ms INTEGER NOT NULL DEFAULT 0,
                    source_id INTEGER UNIQUE
                );
                CREATE TABLE IF NOT EXISTS media (
                    name TEXT PRIMARY KEY,
                    data BLOB NOT NULL
                );
                """
            )
            initialize_activity(db)
            self._initialize_study(db)
            initialize_browser(db)
            initialize_custom_study(db)
            db.execute(
                "INSERT OR IGNORE INTO metadata(key, value) VALUES('schema_version', '1')"
            )
            db.execute(
                "INSERT OR IGNORE INTO metadata(key, value) VALUES('scheduler_version', ?)",
                (SCHEDULER_VERSION,),
            )
            _ensure_default_deck(db, int(time.time()))

    @staticmethod
    def _ensure_parents(db, now):
        names = [row["name"] for row in db.execute("SELECT name FROM decks").fetchall()]
        for name in names:
            parts = name.split("::")
            for depth in range(1, len(parts)):
                parent = "::".join(parts[:depth])
                if parent:
                    if db.execute("SELECT 1 FROM decks d JOIN metadata m ON m.key='filtered_deck:'||d.id WHERE d.name=?", (parent,)).fetchone():
                        raise CollectionError('Los mazos filtrados no pueden contener submazos.')
                    db.execute("INSERT OR IGNORE INTO decks(name, description, created_at) VALUES(?, '', ?)", (parent, now))

    def create_deck(self, name: str, description: str = "", now: Optional[int] = None, parent_id: Optional[int] = None) -> int:
        clean_name = str(name or "").strip()
        if not clean_name or len(clean_name) > 128:
            raise CollectionError("El nombre del mazo debe tener entre 1 y 128 caracteres.")
        if any(not part.strip() for part in clean_name.split("::")):
            raise CollectionError("Cada mazo y submazo necesita un nombre.")
        with _connect(self.path) as db:
            if parent_id is not None:
                parent = db.execute("SELECT name FROM decks WHERE id=?", (parent_id,)).fetchone()
                if parent is None:
                    raise CollectionError("No existe el mazo de destino.")
                clean_name = parent["name"] + "::" + clean_name
                if len(clean_name) > 128:
                    raise CollectionError("La ruta del mazo supera los 128 caracteres.")
            if len(clean_name.split("::")) > 5:
                raise CollectionError("La jerarquía admite un máximo de 5 niveles, contando el mazo principal.")
            db.execute(
                "INSERT OR IGNORE INTO decks(name, description, created_at) VALUES(?, ?, ?)",
                (clean_name, str(description or "").strip(), self._now(now)),
            )
            row = db.execute("SELECT id FROM decks WHERE name=?", (clean_name,)).fetchone()
            self._ensure_parents(db, self._now(now))
            return int(row["id"])

    @staticmethod
    def _branch(db, deck_id):
        root = db.execute("SELECT * FROM decks WHERE id=?", (deck_id,)).fetchone()
        if root is None:
            raise CollectionError("No existe ese mazo.")
        return [row for row in db.execute("SELECT * FROM decks ORDER BY id") if row['name'] == root['name'] or row['name'].startswith(root['name'] + '::')]

    @staticmethod
    def _rewrite_branch(db, branch, root_id, new_name):
        old_name = next(row['name'] for row in branch if row['id'] == root_id)
        if new_name == old_name:
            return
        changed = {row['id']: new_name + row['name'][len(old_name):] for row in branch}
        for name in changed.values():
            if len(name) > 128 or len(name.split('::')) > 5:
                raise CollectionError("El traslado o renombrado supera los 5 niveles o los 128 caracteres de ruta.")
            if any(not part.strip() for part in name.split('::')):
                raise CollectionError("Cada mazo necesita un nombre.")
        for row in db.execute('SELECT id,name FROM decks'):
            if row['id'] not in changed and row['name'] in changed.values():
                raise CollectionError("Ya existe un mazo con ese nombre en el destino. No se fusionaron tarjetas.")
        for deck_id, name in changed.items():
            db.execute('UPDATE decks SET name=? WHERE id=?', (name, deck_id))

    def rename_deck(self, deck_id: int, name: str):
        clean = str(name or '').strip()
        if not clean or '::' in clean:
            raise CollectionError("Usá un nombre simple; para cambiar de padre, arrastrá el mazo.")
        with _connect(self.path) as db:
            db.execute('BEGIN IMMEDIATE')
            branch = self._branch(db, deck_id)
            old = next(row['name'] for row in branch if row['id'] == deck_id)
            parent = old.rsplit('::', 1)[0] + '::' if '::' in old else ''
            self._rewrite_branch(db, branch, deck_id, parent + clean)
        return {'id': deck_id}

    def move_deck(self, deck_id: int, parent_id: Optional[int]):
        with _connect(self.path) as db:
            db.execute('BEGIN IMMEDIATE')
            branch = self._branch(db, deck_id)
            if parent_id in {row['id'] for row in branch}:
                raise CollectionError("Un mazo no puede quedar dentro de sí mismo ni de sus submazos.")
            root = next(row for row in branch if row['id'] == deck_id)
            prefix = ''
            if parent_id is not None:
                parent = db.execute('SELECT name FROM decks WHERE id=?', (parent_id,)).fetchone()
                if parent is None:
                    raise CollectionError("No existe el mazo de destino.")
                prefix = parent['name'] + '::'
            self._rewrite_branch(db, branch, deck_id, prefix + root['name'].rsplit('::', 1)[-1])
        return {'id': deck_id, 'parent_id': parent_id}

    def delete_deck(self, deck_id: int, expected_card_count: int, expected_deck_count: int):
        with _connect(self.path) as db:
            db.execute('BEGIN IMMEDIATE')
            branch = self._branch(db, deck_id)
            ids = {row['id'] for row in branch}
            cards = [row for row in db.execute('SELECT id,note_id,deck_id FROM cards') if row['deck_id'] in ids]
            borrowed_count = db.execute("SELECT COUNT(*) FROM custom_session_cards sc JOIN custom_sessions s ON s.id=sc.session_id WHERE s.deck_id=? AND s.mode='filtered' AND s.active=1 AND sc.done=0", (deck_id,)).fetchone()[0] if len(branch)==1 else 0
            actual_count = borrowed_count if db.execute('SELECT 1 FROM metadata WHERE key=?', ('filtered_deck:'+str(deck_id),)).fetchone() and len(branch)==1 else len(cards)
            if actual_count != expected_card_count or len(branch) != expected_deck_count:
                raise CollectionError("El mazo cambió desde la confirmación. Revisá sus cantidades y volvé a confirmar.")
            note_ids = {row['note_id'] for row in cards}
            for card in cards:
                archive_card_reviews(db, card['id'])
                db.execute('DELETE FROM reviews WHERE card_id=?', (card['id'],))
                db.execute('DELETE FROM cards WHERE id=?', (card['id'],))
            for note_id in note_ids:
                db.execute('DELETE FROM notes WHERE id=? AND NOT EXISTS(SELECT 1 FROM cards WHERE note_id=?)', (note_id, note_id))
            from .daily_limits import remove_decks
            remove_decks(db,ids)
            for row in reversed(branch):
                db.execute('DELETE FROM metadata WHERE key=?', ('filtered_deck:'+str(row['id']),))
                db.execute('DELETE FROM decks WHERE id=?', (row['id'],))
            _ensure_default_deck(db, int(time.time()))
        return {'id': deck_id, 'cards_deleted': len(cards), 'decks_deleted': len(branch)}

    def add_basic_card(
        self,
        deck_id: int,
        front: str,
        back: str,
        tags: Optional[List[str]] = None,
        now: Optional[int] = None,
    ) -> int:
        clean_front = str(front or "").strip()
        clean_back = str(back or "").strip()
        if not clean_front or not clean_back:
            raise CollectionError("La pregunta y la respuesta no pueden estar vacías.")
        if len(clean_front) > 100_000 or len(clean_back) > 100_000:
            raise CollectionError("El contenido de una tarjeta supera el límite permitido.")
        current = self._now(now)
        clean_tags = _tags(tags or [])
        with _connect(self.path) as db:
            if db.execute('SELECT 1 FROM metadata WHERE key=?', ('filtered_deck:'+str(deck_id),)).fetchone():
                raise CollectionError('Las notas se añaden a un mazo de origen, no a un mazo filtrado.')
            if not db.execute("SELECT 1 FROM decks WHERE id=?", (int(deck_id),)).fetchone():
                raise CollectionError("No existe ese mazo.")
            cursor = db.execute(
                "INSERT INTO notes(guid, model_kind, fields_json, tags_json, created_at, modified_at) "
                "VALUES(?, 'basic', ?, ?, ?, ?)",
                (uuid.uuid4().hex, _json([clean_front, clean_back]), _json(clean_tags), current, current),
            )
            note_id = int(cursor.lastrowid)
            cursor = db.execute(
                "INSERT INTO cards(note_id, deck_id, ordinal, front, back, due_at) VALUES(?, ?, 0, ?, ?, ?)",
                (note_id, int(deck_id), clean_front, clean_back, current),
            )
            card_id = int(cursor.lastrowid)
            position = db.execute('SELECT COALESCE(MAX(new_position),0)+1 FROM cards').fetchone()[0]
            db.execute('UPDATE cards SET new_position=?,added_at=? WHERE id=?',(position,current,card_id))
            if self._options(db,deck_id)['new_order'] == 'random':
                from .order_policy import reposition
                reposition(db,deck_id,'random')
            return card_id

    def overview(self, now: Optional[int] = None) -> Dict[str, Any]:
        current = self._now(now)
        with _connect(self.path) as db:
            self._ensure_parents(db, current)
            decks = db.execute(
                "SELECT d.id, d.name, d.description, COUNT(c.id) AS card_count, "
                "SUM(CASE WHEN c.suspended=0 AND c.due_at<=? THEN 1 ELSE 0 END) AS due_count "
                "FROM decks d LEFT JOIN cards c ON c.deck_id=d.id GROUP BY d.id ORDER BY d.name COLLATE NOCASE",
                (current,),
            ).fetchall()
            due_total = db.execute(
                "SELECT COUNT(*) FROM cards WHERE suspended=0 AND due_at<=?", (current,)
            ).fetchone()[0]
            total_cards = db.execute("SELECT COUNT(*) FROM cards").fetchone()[0]
            by_name = {row["name"]: row for row in decks}
            totals = {name: [0, 0] for name in by_name}
            for row in decks:
                parts = row["name"].split("::")
                for depth in range(1, len(parts) + 1):
                    ancestor = "::".join(parts[:depth])
                    if ancestor in totals:
                        totals[ancestor][0] += int(row["card_count"] or 0)
                        totals[ancestor][1] += int(row["due_count"] or 0)
            summary = {
                "scheduler_version": SCHEDULER_VERSION,
                "deck_lifecycle_version": 1,
                "due_count": int(due_total),
                "card_count": int(total_cards),
                "decks": [
                    {
                        "id": int(row["id"]), "name": row["name"],
                        "description": row["description"],
                        "card_count": int(row["card_count"] or 0),
                        "due_count": int(row["due_count"] or 0),
                        "parent_id": by_name[row["name"].rsplit("::", 1)[0]]["id"] if "::" in row["name"] and row["name"].rsplit("::", 1)[0] in by_name else None,
                        "label": row["name"].rsplit("::", 1)[-1],
                        "subtree_card_count": totals[row["name"]][0],
                        "subtree_due_count": totals[row["name"]][1],
                    }
                    for row in decks
                ],
            }
            from . import study_queue
            ctx = study_queue.context(self, db, current)
            all_due = study_queue.due_rows(db, current, None, False,ctx['end'])
            status = study_queue.select(self,db,current,ctx=ctx,rows=all_due)[1]
            count_keys = ('new_count', 'learning_count', 'review_count')
            summary.update(study_counts_version=1, due_count=status['available'])
            summary.update({key: status[key] for key in count_keys})
            for deck in summary['decks']:
                own = [row for row in all_due if row['deck_id'] == deck['id']]
                branch = [row for row in all_due if row['deck_name'] == deck['name'] or row['deck_name'].startswith(deck['name']+'::')]
                status = study_queue.select(self,db,current,deck['id'],ctx=ctx,rows=own)[1]
                subtree = study_queue.select(self,db,current,deck['id'],True,ctx=ctx,rows=branch)[1]
                deck.update(due_count=status['available'],subtree_due_count=subtree['available'],limited_count=status['limited'],subtree_limited_count=subtree['limited'])
                deck.update({key: status[key] for key in count_keys})
                deck.update({'subtree_'+key: subtree[key] for key in count_keys})
            from .activity import activity
            summary['activity'] = activity(self, db, current)
            from .filtered_decks import augment_overview
            augment_overview(db, summary, current)
            return summary

    def _card_dict(self,row: sqlite3.Row) -> Dict[str, Any]:
        from .note_generation import answer_text
        import re,html
        typed=re.search(r'data-memoria-type-answer="([^"]*)"',row['front'])
        typing={'answer_text':html.unescape(typed[1])} if typed else ({'answer_text':answer_text(row['back'])} if 'model_kind' in row.keys() and row['model_kind']=='basic-typing' and 'data-memoria-template="1"' not in row['front'] else {})
        result = {
            "id": int(row["id"]), "note_id": int(row["note_id"]),
            "deck_id": int(row["deck_id"]), "deck": row["deck_name"],
            "ordinal": int(row["ordinal"]), "front": row["front"], "back": row["back"],
            "tags": _parse_json(row["tags_json"], []),
            "due_at": int(row["due_at"]), "interval_days": int(row["interval_days"]),
            "ease_factor": int(row["ease_factor"]), "reps": int(row["reps"]),
            "lapses": int(row["lapses"]), "suspended": bool(row["suspended"]), "flag": int(row["flag"]),
            "fsrs": _parse_json(row["fsrs_json"], None),
            "classic": _parse_json(row["classic_json"], None),
            **typing,
            **({'generation_kind':'image-occlusion'} if 'model_kind' in row.keys() and row['model_kind']=='image-occlusion' else {}),
        }

        if 'data-memoria-template="1"' in row['front']:
            from .note_types import for_note
            from .note_schema import generate
            from .latex import Renderer
            with _connect(self.path) as db:
                note=db.execute('SELECT * FROM notes WHERE id=?',(row['note_id'],)).fetchone()
                definition=for_note(db,note['guid'],note['model_kind'])
                rendered=Renderer(db,definition['id']).cards(generate(db,definition,_parse_json(note['fields_json'],[]),tags=_parse_json(note['tags_json'],[]),deck_name=row['deck_name'],editing=True,preserve_ordinals={row['ordinal']}))
                card=next((c for c in rendered if c['ordinal']==row['ordinal']),None)
                if card:result.update(front=card['front'],back=card['back'],full_back=card.get('full_back',card['back']),template_rendered=True)
        return result

    def next_due(self, now: Optional[int] = None, deck_id: Optional[int] = None, include_children: bool = False) -> Optional[Dict[str, Any]]:
        from . import study_queue
        current = self._now(now)
        with _connect(self.path) as db:
            rows, _ = study_queue.select(self, db, current, deck_id, include_children)
            if not rows:
                return None
            card = self._card_dict(rows[0])
            card['queue_kind']={'new':'new','learning':'learning','interday':'learning','review':'review'}[study_queue.category(rows[0],{'start':self._day(current)[1]})]
            card['intervals'] = self._answer_intervals(db, rows[0], current)
            return card

    def card(self, card_id: int) -> Dict[str, Any]:
        with _connect(self.path) as db:
            row = db.execute(
                "SELECT c.*, d.name AS deck_name, n.tags_json,n.model_kind FROM cards c "
                "JOIN decks d ON d.id=c.deck_id JOIN notes n ON n.id=c.note_id WHERE c.id=?",
                (int(card_id),),
            ).fetchone()
            if row is None:
                raise CollectionError("No existe esa tarjeta.")
            return self._card_dict(row)

    def grade(self, card_id: int, rating: str, now: Optional[int] = None, response_ms: int = 0, expected_reps: Optional[int] = None, review_deck_id: Optional[int] = None, include_children: bool = False) -> Dict[str, Any]:
        return self.grade_fsrs(card_id, rating, now, response_ms, expected_reps,review_deck_id,include_children)

    def review_count(self, card_id: int) -> int:
        with _connect(self.path) as db:
            return int(db.execute("SELECT COUNT(*) FROM reviews WHERE card_id=?", (int(card_id),)).fetchone()[0])

    def add_media(self, name: str, data: bytes) -> None:
        clean_name = _safe_media_name(name)
        raw = bytes(data)
        if len(raw) > 64 * 1024 * 1024:
            raise CollectionError("Un archivo multimedia supera el límite de 64 MiB.")
        with _connect(self.path) as db:
            existing = db.execute("SELECT data FROM media WHERE name=?", (clean_name,)).fetchone()
            if existing is not None and bytes(existing["data"]) != raw:
                raise CollectionError("Ya existe un medio con ese nombre y contenido distinto.")
            db.execute("INSERT OR REPLACE INTO media(name, data) VALUES(?, ?)", (clean_name, raw))

    def import_apkg(self, package_path: Path | str, now: Optional[int] = None) -> Dict[str, int]:
        package = Path(package_path).expanduser()
        if package.suffix.casefold() != ".apkg":
            raise CollectionError("Seleccioná un archivo .apkg de tarjetas.")
        if package.is_symlink() or not package.is_file():
            raise CollectionError("No se pudo leer el archivo seleccionado.")
        if package.stat().st_size > MAX_APKG_BYTES:
            raise CollectionError("El archivo .apkg supera el límite de 256 MiB.")
        current = self._now(now)
        parsed = self._read_apkg(package, current)
        return self._merge_package(parsed, current)

    def _read_apkg(self, package: Path, now: int) -> Dict[str, Any]:
        try:
            archive = zipfile.ZipFile(str(package), "r")
        except (OSError, zipfile.BadZipFile) as exc:
            raise CollectionError("El archivo no es un paquete .apkg válido.") from exc
        with archive:
            infos = archive.infolist()
            if sum(info.file_size for info in infos) > MAX_APKG_UNCOMPRESSED_BYTES:
                raise CollectionError("El contenido descomprimido supera el límite de 1 GiB.")
            if archive.testzip() is not None:
                raise CollectionError("El paquete .apkg está dañado.")
            names = {info.filename for info in infos}
            db_name = next((name for name in ("collection.anki21b", "collection.anki21", "collection.anki2") if name in names), None)
            if db_name is None:
                raise CollectionError("El paquete no contiene una base de tarjetas reconocible.")
            db_bytes = archive.read(db_name)
            is_latest = db_name == "collection.anki21b"
            if is_latest:
                db_bytes = _zstd_decompress(db_bytes, MAX_COLLECTION_BYTES, "la colección")
            if len(db_bytes) > MAX_COLLECTION_BYTES:
                raise CollectionError("La base de colección supera el límite de 512 MiB.")

            media: Dict[str, bytes] = {}
            if "media" in names:
                if is_latest:
                    encoded_index = _zstd_decompress(
                        archive.read("media"), MAX_MEDIA_INDEX_BYTES, "el índice de medios"
                    )
                    media_entries = _parse_modern_media_index(encoded_index)
                    total_media = 0
                    for index, clean_name, expected_size, expected_sha1 in media_entries:
                        if index not in names:
                            raise CollectionError("Falta un archivo multimedia declarado por el paquete.")
                        raw = _zstd_decompress(archive.read(index), MAX_MEDIA_BYTES, "un archivo multimedia")
                        if len(raw) != expected_size or (expected_sha1 and hashlib.sha1(raw).digest() != expected_sha1):
                            raise CollectionError("Un archivo multimedia no coincide con su tamaño o hash declarado.")
                        total_media += len(raw)
                        if total_media > MAX_APKG_UNCOMPRESSED_BYTES:
                            raise CollectionError("Los medios descomprimidos superan el límite de 1 GiB.")
                        media[clean_name] = raw
                else:
                    try:
                        media_map = _parse_json(archive.read("media").decode("utf-8"), None)
                    except UnicodeDecodeError as exc:
                        raise CollectionError("El índice de medios del paquete no es válido.") from exc
                    if isinstance(media_map, list):
                        media_entries = [(str(index), name) for index, name in enumerate(media_map) if name]
                    elif isinstance(media_map, dict):
                        media_entries = [(str(key), name) for key, name in media_map.items()]
                    else:
                        raise CollectionError("El índice de medios del paquete no es válido.")
                    for index, name in media_entries:
                        if not index.isdigit():
                            raise CollectionError("El índice de medios del paquete no es válido.")
                        clean_name = _safe_media_name(str(name))
                        if clean_name in media:
                            raise CollectionError("El paquete repite un nombre de medio.")
                        if index not in names:
                            raise CollectionError("Falta un archivo multimedia declarado por el paquete.")
                        raw = archive.read(index)
                        if len(raw) > MAX_MEDIA_BYTES:
                            raise CollectionError("Un archivo multimedia supera el límite de 64 MiB.")
                        media[clean_name] = raw
            return self._parse_collection_database(db_bytes, media, now)

    def _parse_collection_database(self, db_bytes: bytes, media: Dict[str, bytes], now: int) -> Dict[str, Any]:
        with tempfile.TemporaryDirectory(prefix="anki-import-", dir=str(self.path.parent)) as temp_dir:
            db_path = Path(temp_dir) / "collection.sqlite3"
            db_path.write_bytes(db_bytes)
            try:
                source = sqlite3.connect("file:%s?mode=ro" % db_path.as_posix(), uri=True)
                source.row_factory = sqlite3.Row
                col = source.execute("SELECT crt, models, decks, conf FROM col LIMIT 1").fetchone()
                if col is None:
                    raise CollectionError("La base del paquete no contiene una colección.")
                models = _parse_json(col["models"], {})
                decks = _parse_json(col["decks"], {})
                created = int(col["crt"] or now)
                note_rows = source.execute("SELECT id, guid, mid, tags, flds, data FROM notes ORDER BY id").fetchall()
                card_rows = source.execute(
                    "SELECT id, nid, did, ord, type, queue, due, ivl, factor, reps, lapses, flags, data "
                    "FROM cards ORDER BY id"
                ).fetchall()
                revlog_rows = source.execute(
                    "SELECT id, cid, ease, ivl, lastIvl, factor, time, type FROM revlog ORDER BY id"
                ).fetchall()
            except (sqlite3.Error, KeyError) as exc:
                raise CollectionError("La base contenida en el .apkg no tiene un esquema compatible.") from exc
            finally:
                if "source" in locals():
                    source.close()

        native_models={int(n['id']):models[str(n['mid'])] for n in note_rows}
        notes_by_id: Dict[int, Dict[str, Any]] = {}
        for row in note_rows:
            model = models.get(str(row["mid"]))
            if not isinstance(model, dict):
                raise CollectionError("Una nota del paquete referencia un tipo inexistente.")
            kind = _model_kind(model)
            fields = _fields_for_note(row, model, kind)
            from .schema_package import KEY as SCHEMA_KEY,document as schema_document
            editable_schema=schema_document(model[SCHEMA_KEY]) if SCHEMA_KEY in model else None
            rendered_fields=fields
            source_data=_parse_json(row['data'],{})
            if isinstance(source_data,dict) and 'hearthside_source_fields' in source_data:
                from .note_generation import prepare
                fields,_=prepare(kind,source_data['hearthside_source_fields'])
                if len(fields)!=len(rendered_fields):raise CollectionError('Los campos originales no coinciden.')
            from .latex import validate as validate_latex,defaults as latex_defaults
            latex_options=validate_latex(model.get('hearthside_latex',latex_defaults()))
            authoring_type=model.get("hearthside_note_type")
            if authoring_type is not None:
                from .note_types import validate_definition
                authoring_type=validate_definition(authoring_type)
                if authoring_type["kind"]!=kind:raise CollectionError("El tipo de nota importado no coincide con sus plantillas.")
            guid = str(row["guid"] or uuid.uuid4().hex)
            notes_by_id[int(row["id"])] = {
                "guid": guid, "model_kind": kind, "fields": fields,
                "tags": _tags(row["tags"]), "authoring_type":authoring_type, "rendered_fields":rendered_fields,"latex_options":latex_options, "builtin_definition":model.get("hearthside_builtin"), 'editable_schema':editable_schema,
            }

        deck_names: Dict[int, str] = {}
        for key, value in decks.items():
            if isinstance(value, dict):
                deck_names[int(key)] = str(value.get("name") or "Default")
        if 1 not in deck_names:
            deck_names[1] = "Default"

        from . import interchange_state
        imported_options = interchange_state.deck_settings(decks)
        review_info = interchange_state.review_metadata(col['conf'])
        parsed_cards: List[Dict[str, Any]] = []
        card_ids: Dict[int, Tuple[str, int]] = {}
        for row in card_rows:
            note_id = int(row["nid"])
            note = notes_by_id.get(note_id)
            if note is None:
                raise CollectionError("Una tarjeta del paquete no tiene una nota asociada.")
            ordinal = int(row["ord"])
            if note['model_kind']=='image-occlusion':
                from .note_models import io_ordinals
                ordinals=io_ordinals(native_models[note_id])
                if not 0<=ordinal<len(ordinals):raise CollectionError('La máscara no corresponde a una plantilla.')
                ordinal=ordinals[ordinal]
            from .note_generation import generate
            from .generation_edit import retired_from_package
            retired=retired_from_package(row['data'],note['model_kind'])
            if note.get('editable_schema') and not retired:
                from .note_templates import cards as schema_cards
                source_cards=schema_cards(None,dict(note['editable_schema'],name=native_models[note_id]['name']),note.get('rendered_fields',note['fields']),tags=note['tags'],editing=True)
            else:source_cards=generate(note['model_kind'],retired['fields'] if retired else note.get('rendered_fields',note['fields']))
            generated={c['ordinal']:c for c in source_cards}
            rendered=generated.get(ordinal)
            if rendered is None:raise UnsupportedNoteType('La tarjeta no corresponde a una variante de esta nota.')
            front,back=rendered['front'],rendered['back']
            queue = int(row["queue"])
            card_type = int(row["type"])
            raw_due = int(row["due"] or 0)
            if queue == 1:
                due_at = raw_due
            elif queue == 2 or queue == 3:
                due_at = created + raw_due * 86400
            else:
                due_at = now
            suspended = 1 if queue < 0 else 0
            deck_name = deck_names.get(int(row["did"]), "Default")
            parsed_cards.append({
                "source_id": int(row["id"]), "note_guid": note["guid"], "ordinal": ordinal, 'retired_generation':retired,
                "deck_name": deck_name, "front": front, "back": back,
                "due_at": due_at, "interval_days": max(0, int(row["ivl"] or 0)),
                "ease_factor": max(1300, int(row["factor"] or 2500)),
                "reps": max(0, int(row["reps"] or 0)), "lapses": max(0, int(row["lapses"] or 0)),
                "card_type": card_type, "queue": queue, "suspended": suspended, "flag": int(row["flags"]) & 7,
                "fsrs": interchange_state.card_state(row["data"]),
                "classic": interchange_state.classic_state(row["data"]),
                "review_history_skip": interchange_state.history_skip(row["data"]),
                "order": interchange_state.order_state(row["data"],raw_due if card_type == 0 else int(row['id']),int(row['id'])//1000),
            })
            card_ids[int(row["id"])] = (note["guid"], ordinal)

        parsed_reviews: List[Dict[str, Any]] = []
        for row in revlog_rows:
            mapped = card_ids.get(int(row["cid"]))
            if mapped is None:
                continue
            parsed_reviews.append({
                "source_id": int(row["id"]), "card": mapped,
                "reviewed_at": review_info.get(str(row['id']),{}).get('reviewed_at',max(0, int(row["id"]) // 1000)),
                "scheduler": review_info.get(str(row['id']),{}).get('scheduler','legacy'),
                "fsrs_log": review_info.get(str(row['id']),{}).get('fsrs_log'),
                "rating": {1: "again", 2: "hard", 3: "good", 4: "easy"}.get(int(row["ease"]), "good"),
                "interval_before": max(0, abs(int(row["lastIvl"] or 0))),
                "interval_after": max(0, abs(int(row["ivl"] or 0))),
                "ease_factor": max(1300, int(row["factor"] or 2500)),
                "response_ms": max(0, int(row["time"] or 0)),
            })
        return {
            "notes": list(notes_by_id.values()), "cards": parsed_cards,
            "reviews": parsed_reviews, "media": media, "deck_options": imported_options,
        }

    def _merge_package(self, parsed: Dict[str, Any], now: int) -> Dict[str, int]:
        with _connect(self.path) as db:
            db.execute('BEGIN IMMEDIATE')
            from .schema_package import resolve_imports
            resolve_imports(db,parsed['notes'])
            # Refuse conflicting media names before changing any records.
            for name, raw in parsed["media"].items():
                existing = db.execute("SELECT data FROM media WHERE name=?", (name,)).fetchone()
                if existing is not None and bytes(existing["data"]) != raw:
                    raise CollectionError(
                        "El paquete incluye un medio llamado «%s» distinto del existente; "
                        "renombralo en un editor compatible antes de importarlo." % name
                    )
            deck_ids: Dict[str, int] = {}
            for card in parsed["cards"]:
                deck_name = card["deck_name"].strip() or DEFAULT_DECK_NAME
                row = db.execute("SELECT id FROM decks WHERE name=?", (deck_name,)).fetchone()
                if row is None:
                    if len(deck_name.split("::")) > 5:
                        raise CollectionError("El paquete incluye una jerarquía nueva de más de 5 niveles; reorganizala en un editor compatible antes de importarla.")
                    cursor = db.execute(
                        "INSERT INTO decks(name, description, created_at) VALUES(?, '', ?)",
                        (deck_name, now),
                    )
                    deck_ids[deck_name] = int(cursor.lastrowid)
                else:
                    deck_ids[deck_name] = int(row["id"])

            for name, settings in parsed.get('deck_options', {}).items():
                row = db.execute('SELECT id FROM decks WHERE name=?', (name,)).fetchone()
                if row is None:
                    if len(name.split('::')) > 5:
                        raise CollectionError('Las opciones incluyen una nueva jerarquía de más de 5 niveles.')
                    db.execute("INSERT INTO decks(name,description,created_at) VALUES(?,'',?)", (name,now))
                    row = db.execute('SELECT id FROM decks WHERE name=?', (name,)).fetchone()
                parameters = _json(settings['parameters']) if settings['parameters'] is not None else None
                db.execute('INSERT INTO deck_options(deck_id,options_json,parameters_json) VALUES(?,?,?) ON CONFLICT(deck_id) DO UPDATE SET options_json=excluded.options_json,parameters_json=excluded.parameters_json', (row['id'],_json(settings['options']),parameters))
            note_ids: Dict[str, int] = {}
            for note in parsed["notes"]:
                existing = db.execute("SELECT id FROM notes WHERE guid=?", (note["guid"],)).fetchone()
                if existing is None:
                    cursor = db.execute(
                        "INSERT INTO notes(guid, model_kind, fields_json, tags_json, created_at, modified_at) "
                        "VALUES(?, ?, ?, ?, ?, ?)",
                        (note["guid"], note["model_kind"], _json(note["fields"]), _json(note["tags"]), now, now),
                    )
                    note_ids[note["guid"]] = int(cursor.lastrowid)
                else:
                    note_id = int(existing["id"])
                    db.execute(
                        "UPDATE notes SET model_kind=?, fields_json=?, tags_json=?, modified_at=? WHERE id=?",
                        (note["model_kind"], _json(note["fields"]), _json(note["tags"]), now, note_id),
                    )
                    note_ids[note["guid"]] = note_id

            from .note_types import register, assign
            for note in parsed["notes"]:
                definition=note.get("authoring_type")
                if definition:register(db,definition)
                builtin=note.get('builtin_definition')
                if builtin:
                    from .note_admin import rename,load,store,OVERRIDES
                    overrides=load(db,OVERRIDES);overrides.setdefault(builtin['id'],{}).pop('deleted',None);store(db,OVERRIDES,overrides);rename(db,builtin['id'],builtin['name'])
                assign(db,note["guid"],note.get("resolved_type",definition["id"] if definition else note["model_kind"]))
                from .note_admin import load,store
                from .latex import SETTINGS
                options=load(db,SETTINGS);options[definition['id'] if definition else note['model_kind']]=note['latex_options'];store(db,SETTINGS,options)

            card_ids: Dict[Tuple[str, int], int] = {}
            for card in parsed["cards"]:
                key = (card["note_guid"], card["ordinal"])
                note_id = note_ids[card["note_guid"]]
                deck_id = deck_ids[card["deck_name"].strip() or DEFAULT_DECK_NAME]
                existing = db.execute(
                    "SELECT id FROM cards WHERE note_id=? AND ordinal=?", (note_id, card["ordinal"])
                ).fetchone()
                values = (
                    deck_id, card["front"], card["back"], card["due_at"], card["interval_days"],
                    card["ease_factor"], card["reps"], card["lapses"], card["card_type"],
                    card["queue"], card["suspended"],
                )
                if existing is None:
                    cursor = db.execute(
                        "INSERT INTO cards(note_id, deck_id, ordinal, front, back, due_at, interval_days, ease_factor, reps, lapses, card_type, queue, suspended) "
                        "VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (note_id, deck_id, card["ordinal"], card["front"], card["back"], card["due_at"],
                         card["interval_days"], card["ease_factor"], card["reps"], card["lapses"],
                         card["card_type"], card["queue"], card["suspended"]),
                    )
                    card_ids[key] = int(cursor.lastrowid)
                else:
                    card_id = int(existing["id"])
                    db.execute(
                        "UPDATE cards SET deck_id=?, front=?, back=?, due_at=?, interval_days=?, ease_factor=?, reps=?, lapses=?, card_type=?, queue=?, suspended=? WHERE id=?",
                        values + (card_id,),
                    )
                    card_ids[key] = card_id

            for card in parsed['cards']:
                local_id = card_ids[(card['note_guid'],card['ordinal'])]
                from .generation_edit import RETIRED
                if card.get('retired_generation'):
                    db.execute('INSERT INTO metadata(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',(RETIRED+str(local_id),_json(card['retired_generation'])))
                else:db.execute('DELETE FROM metadata WHERE key=?',(RETIRED+str(local_id),))
                db.execute('UPDATE cards SET new_position=?,added_at=?,flag=?,review_history_skip=? WHERE id=?',(*card['order'],card.get('flag',0),card.get('review_history_skip',0),local_id))
                state = card.get('fsrs')
                if state is not None:
                    state = dict(state,card_id=local_id)
                    db.execute('UPDATE cards SET fsrs_json=?,due_at=? WHERE id=?', (_json(state),int(datetime.fromisoformat(state['due']).timestamp()),local_id))
                else:
                    db.execute('UPDATE cards SET fsrs_json=NULL WHERE id=?', (local_id,))
                classic = card.get('classic')
                db.execute('UPDATE cards SET classic_json=? WHERE id=?',(_json(dict(classic,card_id=local_id)) if classic else None,local_id))
                if classic:
                    if state is not None:raise CollectionError('La tarjeta contiene dos estados de motor incompatibles.')
                    db.execute('UPDATE cards SET due_at=? WHERE id=?',(int(datetime.fromisoformat(classic['due']).timestamp()),local_id))
            for review in parsed["reviews"]:
                mapped_id = card_ids.get(review["card"])
                if mapped_id is None:
                    continue
                source_id = review["source_id"]
                serialized_log=None
                if review.get('fsrs_log'):
                    raw_log=json.loads(review['fsrs_log']);raw_log['card_id']=mapped_id;serialized_log=_json(raw_log)
                existing = db.execute("SELECT id FROM reviews WHERE source_id=?", (source_id,)).fetchone()
                values = (
                    mapped_id, review["reviewed_at"], review["rating"], review["interval_before"],
                    review["interval_after"], review["ease_factor"], review["response_ms"],
                )
                if existing is None:
                    duplicate = db.execute(
                        "SELECT id FROM reviews WHERE source_id IS NULL AND card_id=? AND reviewed_at=? "
                        "AND rating=? AND interval_before=? AND interval_after=? AND ease_factor=? AND response_ms=? LIMIT 1",
                        values,
                    ).fetchone()
                    if duplicate is not None:
                        continue
                    db.execute(
                        "INSERT OR IGNORE INTO reviews(card_id, reviewed_at, rating, interval_before, interval_after, ease_factor, response_ms, source_id, scheduler,fsrs_log) "
                        "VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", values + (source_id,review['scheduler'],serialized_log)
                    )
                else:
                    db.execute(
                        "UPDATE reviews SET card_id=?, reviewed_at=?, rating=?, interval_before=?, interval_after=?, ease_factor=?, response_ms=?, scheduler=?,fsrs_log=? WHERE source_id=?",
                        values + (review['scheduler'],serialized_log,source_id,),
                    )
            for name, raw in parsed["media"].items():
                db.execute("INSERT OR IGNORE INTO media(name, data) VALUES(?, ?)", (name, raw))
        return {
            "decks_imported": len({card["deck_name"] for card in parsed["cards"]}),
            "notes_imported": len(parsed["notes"]),
            "cards_imported": len(parsed["cards"]),
            "reviews_imported": len(parsed["reviews"]),
            "media_imported": len(parsed["media"]),
        }

    def export_apkg(self, package_path: Path | str, now: Optional[int] = None, deck_id: Optional[int] = None, note_ids: Optional[List[int]] = None) -> Path:
        destination = Path(package_path).expanduser()
        if destination.suffix.casefold() != ".apkg":
            destination = destination.with_suffix(".apkg")
        if not destination.parent.is_dir():
            raise CollectionError("La carpeta elegida para exportar no existe.")
        if note_ids is not None and (deck_id is not None or not isinstance(note_ids,list) or not 1<=len(note_ids)<=200000 or len(set(note_ids))!=len(note_ids) or any(type(n) is not int or not 0<n<2**63-1 for n in note_ids)):raise CollectionError('La selección de notas para exportar no es válida.')
        selected_notes=set(note_ids) if note_ids is not None else None
        current = self._now(now)
        with _connect(self.path) as db:
            db.execute('BEGIN')
            decks = [dict(row) for row in db.execute("SELECT * FROM decks ORDER BY id")]
            for deck in decks:
                deck['study_options'] = self._options(db,deck['id'])
                deck['fsrs_parameters'] = self._parameters(db,deck['id'])
            notes = [dict(row) for row in db.execute("SELECT * FROM notes ORDER BY id")] if selected_notes is None else [dict(row) for row in db.execute('SELECT * FROM notes WHERE id IN(SELECT value FROM json_each(?)) ORDER BY id',(json.dumps(note_ids),))]
            if selected_notes is not None and {n['id'] for n in notes}!=selected_notes:raise CollectionError('La selección de notas cambió antes de exportar.')
            from .note_types import for_note
            from .latex import settings,Renderer,PATTERN
            for note in notes:
                note['authoring_type']=for_note(db,note['guid'],note['model_kind'])
                note['latex_options']=settings(db,note['authoring_type']['id'])
                from .note_schema import active as schema_active,load as schema_load
                if schema_active(db,note['authoring_type']['id']):note['editable_schema']=schema_load(db,note['authoring_type']['id'])
                fields=_parse_json(note['fields_json'],[])
                if any(PATTERN.search(f) for i,f in enumerate(fields) if not (note['model_kind']=='image-occlusion' and i==2)):
                    renderer=Renderer(db,note['authoring_type']['id'],persist=True,allow_compile=False)
                    note['export_fields']=[renderer.markup(f) if not (note['model_kind']=='image-occlusion' and i==2) else f for i,f in enumerate(fields)]
            cards = [dict(row) for row in db.execute("SELECT * FROM cards ORDER BY id")]
            from .generation_edit import RETIRED
            for card in cards:
                retired=db.execute('SELECT value FROM metadata WHERE key=?',(RETIRED+str(card['id']),)).fetchone()
                if retired:card['retired_generation']=json.loads(retired[0])
            reviews = [dict(row) for row in db.execute("SELECT * FROM reviews ORDER BY reviewed_at, id")]
            media = [(row["name"], bytes(row["data"])) for row in db.execute("SELECT name, data FROM media ORDER BY name")]

        if deck_id is not None:
            root = next((deck for deck in decks if deck['id'] == deck_id), None)
            if root is None:
                raise CollectionError("No existe el mazo que querés exportar.")
            decks = [deck for deck in decks if deck['name'] == root['name'] or deck['name'].startswith(root['name'] + '::')]
            deck_ids = {deck['id'] for deck in decks}
            cards = [card for card in cards if card['deck_id'] in deck_ids]
        if selected_notes is not None:
            cards=[card for card in cards if card['note_id'] in selected_notes]
            used={card['deck_id'] for card in cards};names=[deck['name'] for deck in decks if deck['id'] in used]
            decks=[deck for deck in decks if deck['id'] in used or any(n.startswith(deck['name']+'::') for n in names)]
        if deck_id is not None or selected_notes is not None:
            exported_note_ids = {card['note_id'] for card in cards}
            notes = [note for note in notes if note['id'] in exported_note_ids]
            card_ids = {card['id'] for card in cards}
            reviews = [review for review in reviews if review['card_id'] in card_ids]
            # Ship referenced media only: a scoped export must not leak other decks' images.
            from html import unescape
            from urllib.parse import unquote
            markup = unquote(unescape(' '.join(str(field) for note in notes for field in _parse_json(note['fields_json'], []))))
            markup += ' ' + unquote(unescape(' '.join(str(card[field]) for card in cards for field in ['front', 'back'])))
            from html.parser import HTMLParser
            import re
            references = set()
            class MediaReferences(HTMLParser):
                def handle_starttag(self, tag, attrs):
                    for key, value in attrs:
                        if key in {'src', 'href', 'poster', 'data'} and value:
                            references.add(unquote(value))
            parser = MediaReferences()
            parser.feed(markup)
            references.update(re.findall(r'\[sound:([^\]]+)\]', markup))
            references.update(match[0] or match[1] or match[2] for match in re.findall(r"""url\(\s*(?:"([^"]+)"|'([^']+)'|([^\s)]+))\s*\)""", markup))
            media = [(name, raw) for name, raw in media if name in references]

        temporary_db = None
        temporary_apkg = None
        try:
            db_file = tempfile.NamedTemporaryFile(prefix="anki-export-", suffix=".sqlite3", dir=str(self.path.parent), delete=False)
            temporary_db = Path(db_file.name)
            db_file.close()
            self._write_anki_database(temporary_db, decks, notes, cards, reviews, current)
            out_file = tempfile.NamedTemporaryFile(prefix=".anki-export-", suffix=".apkg", dir=str(destination.parent), delete=False)
            temporary_apkg = Path(out_file.name)
            out_file.close()
            with zipfile.ZipFile(str(temporary_apkg), "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
                archive.write(str(temporary_db), "collection.anki2")
                media_index: Dict[str, str] = {}
                for index, (name, raw) in enumerate(media):
                    clean_name = _safe_media_name(name)
                    media_index[str(index)] = clean_name
                    archive.writestr(str(index), raw)
                archive.writestr("media", _json(media_index).encode("utf-8"))
            os.replace(str(temporary_apkg), str(destination))
            temporary_apkg = None
            return destination
        except OSError as exc:
            raise CollectionError("No se pudo escribir el paquete .apkg en la ubicación elegida.") from exc
        finally:
            if temporary_db is not None:
                try:
                    temporary_db.unlink()
                except OSError:
                    pass
            if temporary_apkg is not None:
                try:
                    temporary_apkg.unlink()
                except OSError:
                    pass

    def _write_anki_database(
        self,
        path: Path,
        decks: List[Dict[str, Any]],
        notes: List[Dict[str, Any]],
        cards: List[Dict[str, Any]],
        reviews: List[Dict[str, Any]],
        now: int,
    ) -> None:
        created = now - (now % 86400)
        deck_map = {int(deck["id"]): deck for deck in decks}
        names = {str(deck["name"]) for deck in decks}
        if 1 not in deck_map and "Default" not in names:
            deck_map[1] = {"id": 1, "name": "Default", "description": ""}
        from .note_models import export_models
        models,model_ids=export_models(notes,now,cards)
        deck_json: Dict[str, Any] = {}
        for deck_id, deck in deck_map.items():
            deck_json[str(deck_id)] = {
                "id": deck_id, "name": deck["name"], "desc": deck.get("description", ""),
                "mod": now, "usn": -1, "newToday": [0, 0], "revToday": [0, 0],
                "lrnToday": [0, 0], "timeToday": [0, 0], "dyn": 0,
                "collapsed": False, "conf": 1, "extendNew": 10, "extendRev": 50,
                "hearthside": {"version":1,"options":deck.get('study_options',self.default_deck_options()),"parameters":deck.get('fsrs_parameters')},
            }
        config = {
            "activeDecks": [1], "curDeck": 1, "newSpread": 0,
            "collapseTime": 1200, "timeLim": 0, "estTimes": True,
            "dueCounts": True, "curModel": 1, "nextPos": 1,
            "sortType": "noteFld", "sortBackwards": False,
            "addToCur": True, "dayLearnFirst": False,
        }
        deck_config = {
            "1": {
                "id": 1, "name": "Default", "mod": now, "usn": -1,
                "maxTaken": 60, "autoplay": True, "timer": 0, "replayq": True,
                "new": {"bury": True, "delays": [1, 10], "initialFactor": 2500,
                        "ints": [1, 4], "order": 1, "perDay": 20},
                "rev": {"bury": True, "ease4": 1.3, "fuzz": 0.05,
                        "ivlFct": 1.0, "maxIvl": 36500, "perDay": 200},
                "lapse": {"delays": [10], "leechAction": 0, "leechFails": 8,
                          "minInt": 1, "mult": 0.0},
            }
        }
        from copy import deepcopy
        template = deck_config['1']
        deck_config = {}
        for deck_id, deck in deck_map.items():
            options = deck.get('study_options', self.default_deck_options())
            config_for_deck = deepcopy(template)
            config_for_deck.update(id=deck_id, name=deck['name'])
            config_for_deck['new'].update(perDay=options['new_per_day'],delays=[s/60 for s in options['learning_steps']],bury=options['bury_siblings'],order=0 if options['new_order']=='random' else 1)
            config_for_deck['rev'].update(perDay=options['reviews_per_day'],maxIvl=options['maximum_interval'],bury=options['bury_siblings'])
            config_for_deck['lapse'].update(delays=[s/60 for s in options['relearning_steps']],leechFails=options['leech_threshold'],leechAction=0 if options['leech_action']=='suspend' else 1)
            deck_config[str(deck_id)] = config_for_deck
            deck_json[str(deck_id)]['conf'] = deck_id
        config['hearthside_reviews'] = {str(int(r['reviewed_at'])*1000+i):{'scheduler':r['scheduler'],'reviewed_at':r['reviewed_at'],'fsrs_log':r.get('fsrs_log')} for i,r in enumerate(reviews)}
        tags = sorted({tag for note in notes for tag in _parse_json(note["tags_json"], [])})
        notes_by_id = {int(note["id"]): note for note in notes}
        cards_by_note: Dict[int, List[Dict[str, Any]]] = {}
        for card in cards:
            cards_by_note.setdefault(int(card["note_id"]), []).append(card)
        db = sqlite3.connect(str(path))
        try:
            db.executescript(
                """
                CREATE TABLE col (id integer primary key, crt integer not null, mod integer not null, scm integer not null, ver integer not null, dty integer not null, usn integer not null, ls integer not null, conf text not null, models text not null, decks text not null, dconf text not null, tags text not null);
                CREATE TABLE notes (id integer primary key, guid text not null, mid integer not null, mod integer not null, usn integer not null, tags text not null, flds text not null, sfld integer not null, csum integer not null, flags integer not null, data text not null);
                CREATE TABLE cards (id integer primary key, nid integer not null, did integer not null, ord integer not null, mod integer not null, usn integer not null, type integer not null, queue integer not null, due integer not null, ivl integer not null, factor integer not null, reps integer not null, lapses integer not null, left integer not null, odue integer not null, odid integer not null, flags integer not null, data text not null);
                CREATE TABLE revlog (id integer primary key, cid integer not null, usn integer not null, ease integer not null, ivl integer not null, lastIvl integer not null, factor integer not null, time integer not null, type integer not null);
                CREATE TABLE graves (usn integer not null, oid integer not null, type integer not null);
                CREATE INDEX ix_notes_csum ON notes (csum);
                CREATE INDEX ix_notes_usn ON notes (usn);
                CREATE INDEX ix_cards_nid ON cards (nid);
                CREATE INDEX ix_cards_sched ON cards (did, queue, due);
                CREATE INDEX ix_cards_usn ON cards (usn);
                CREATE INDEX ix_revlog_cid ON revlog (cid);
                CREATE INDEX ix_revlog_usn ON revlog (usn);
                """
            )
            db.execute(
                "INSERT INTO col VALUES(1, ?, ?, ?, 11, 0, -1, 0, ?, ?, ?, ?, ?)",
                (created, now, now * 1000, _json(config), _json(models), _json(deck_json),
                 _json(deck_config), _json({tag: 0 for tag in tags})),
            )
            for note in notes:
                fields = note.get("export_fields",_parse_json(note["fields_json"], ["", ""]))
                model_id = model_ids[note['id']]
                tags_text = " " + " ".join(_tags(_parse_json(note["tags_json"], []))) + " "
                front = str(fields[0])
                checksum = int(hashlib.sha1(front.encode("utf-8")).hexdigest()[:8], 16)
                db.execute(
                    "INSERT INTO notes VALUES(?, ?, ?, ?, -1, ?, ?, ?, ?, 0, ?)",
                    (int(note["id"]), note["guid"], model_id, now, tags_text,
                     FIELD_SEPARATOR.join(str(value) for value in fields), front, checksum, _json({"hearthside_source_fields":_parse_json(note["fields_json"],[])}) if note.get("export_fields") else ""),
                )
            fallback_due = 1
            for card in cards:
                due_at = int(card["due_at"])
                export_ordinal=int(card['ordinal'])
                if notes_by_id[card['note_id']]['model_kind']=='image-occlusion':
                    from .note_models import io_ordinals
                    export_ordinal=io_ordinals(models[str(model_ids[card['note_id']])]).index(export_ordinal)
                queue = int(card["queue"])
                if queue == 1:
                    due = due_at
                elif queue in (2,3):
                    due = max(0, int((due_at - created) // 86400))
                else:
                    queue = 0
                    due = card.get('new_position') if card.get('new_position') is not None else fallback_due
                    fallback_due += 1
                if card['suspended']:
                    queue = -1
                metadata = {'version':1,'fsrs':_parse_json(card.get('fsrs_json'),None),
                            'classic':_parse_json(card.get('classic_json'),None),
                            'retired_generation':card.get('retired_generation'),
                            'review_history_skip':card.get('review_history_skip',0),
                            'new_position':card['new_position'] if card.get('new_position') is not None else card['id'],
                            'added_at':card['added_at'] if card.get('added_at') is not None else notes_by_id[card['note_id']]['created_at']}
                db.execute(
                    "INSERT INTO cards VALUES(?, ?, ?, ?, ?, -1, ?, ?, ?, ?, ?, ?, ?, 0, 0, 0, ?, ?)",
                    (int(card["id"]), int(card["note_id"]), int(card["deck_id"]), export_ordinal,
                     now, int(card["card_type"]), queue, due, int(card["interval_days"]),
                     int(card["ease_factor"]), int(card["reps"]), int(card["lapses"]), int(card.get("flag",0)), _json({"hearthside":metadata})),
                )
            ease_values = {"again": 1, "hard": 2, "good": 3, "easy": 4}
            for index, review in enumerate(reviews):
                reviewed_ms = int(review["reviewed_at"]) * 1000 + index
                db.execute(
                    "INSERT INTO revlog VALUES(?, ?, -1, ?, ?, ?, ?, ?, ?)",
                    (reviewed_ms, int(review["card_id"]), ease_values.get(review["rating"], 3),
                     int(review["interval_after"]), int(review["interval_before"]),
                     int(review["ease_factor"]), int(review["response_ms"]), 1),
                )
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def media_bytes(self, name: str) -> bytes:
        with _connect(self.path) as db:
            row = db.execute("SELECT data FROM media WHERE name=?", (_safe_media_name(name),)).fetchone()
            if row is None:
                raise CollectionError("No existe ese medio.")
            return bytes(row["data"])
