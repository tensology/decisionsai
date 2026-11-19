"""Preserve WhatsApp link rows while allowing one conversation on several boards."""
import re

_IDENTIFIER = r'(?:"[^"]+"|`[^`]+`|\[[^\]]+\]|[\w]+)'


def _without_global_jid_constraint(sql):
    """Edit table definitions, never text inside maintained DEFAULT literals."""
    start, end = sql.index('('), sql.rfind(')')
    body = sql[start + 1:end]
    definitions, previous, depth, quote, index = [], 0, 0, None, 0
    while index < len(body):
        char = body[index]
        if quote:
            if char == quote:
                if index + 1 < len(body) and body[index + 1] == quote and quote != ']':
                    index += 1
                else:
                    quote = None
        elif char in "'\"`[":
            quote = ']' if char == '[' else char
        elif char == '(':
            depth += 1
        elif char == ')':
            depth -= 1
        elif char == ',' and depth == 0:
            definitions.append(body[previous:index])
            previous = index + 1
        index += 1
    definitions.append(body[previous:])
    constraint = r'(?:CONSTRAINT\s+' + _IDENTIFIER + r'\s+)?UNIQUE\s*\(\s*["`\[]?phone_jid["`\]]?\s*\)(?:\s+ON\s+CONFLICT\s+\w+)?'
    kept = []
    for definition in definitions:
        if re.fullmatch(r'\s*' + constraint + r'\s*', definition, re.I):
            continue
        prefix = re.match(r'\s*(?:"phone_jid"|`phone_jid`|\[phone_jid\]|phone_jid)\s+', definition, re.I)
        if prefix:
            quote, depth, index = None, 0, prefix.end()
            while index < len(definition):
                char = definition[index]
                if quote:
                    if char == quote:
                        if index + 1 < len(definition) and definition[index + 1] == quote and quote != ']':
                            index += 1
                        else:
                            quote = None
                elif char in "'\"`[":
                    quote = ']' if char == '[' else char
                elif char == '(':
                    depth += 1
                elif char == ')':
                    depth -= 1
                elif depth == 0 and (index == 0 or not (definition[index - 1].isalnum() or definition[index - 1] == '_')):
                    unique = re.match(r'UNIQUE\b(?:\s+ON\s+CONFLICT\s+\w+)?', definition[index:], re.I)
                    if unique:
                        definition = definition[:index] + definition[index + unique.end():]
                        break
                index += 1
        kept.append(definition)
    return sql[:start + 1] + ','.join(kept) + sql[end:]


def ensure_whatsapp_board_link_schema(db_engine):
    """Replace only global JID uniqueness, preserving columns, indexes and triggers."""
    if db_engine.dialect.name != 'sqlite':
        raise RuntimeError('WhatsApp board-link migration requires the local SQLite database.')
    raw = db_engine.raw_connection()
    cursor = raw.cursor()
    foreign_keys = cursor.execute('PRAGMA foreign_keys').fetchone()[0]
    try:
        cursor.execute('PRAGMA foreign_keys=OFF')
        cursor.execute('BEGIN IMMEDIATE')
        record = cursor.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='whatsapp_phone_links'").fetchone()
        if not record:
            raw.rollback()
            return
        indexes = cursor.execute('PRAGMA index_list("whatsapp_phone_links")').fetchall()
        global_indexes = []
        rebuild = False
        for row in indexes:
            columns = [item[2] for item in cursor.execute('PRAGMA index_info("' + row[1].replace('"', '""') + '")')]
            if row[2] and columns == ['phone_jid']:
                global_indexes.append(row[1])
                rebuild |= row[3] == 'u'
        if rebuild:
            sql = _without_global_jid_constraint(record[0])
            temporary = 'whatsapp_phone_links__board_scope'
            sql = re.sub(r'^\s*CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?' + _IDENTIFIER,
                         'CREATE TABLE "' + temporary + '"', sql, count=1, flags=re.I)
            objects = cursor.execute("SELECT type,name,sql FROM sqlite_master WHERE tbl_name='whatsapp_phone_links' AND sql IS NOT NULL AND type IN ('index','trigger')").fetchall()
            columns = [row[1] for row in cursor.execute('PRAGMA table_info("whatsapp_phone_links")')]
            names = ','.join('"' + value.replace('"', '""') + '"' for value in columns)
            has_sequence = cursor.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='sqlite_sequence'").fetchone()
            sequence = cursor.execute("SELECT seq FROM sqlite_sequence WHERE name='whatsapp_phone_links'").fetchone() if has_sequence else None
            previous_violations = set(cursor.execute('PRAGMA foreign_key_check').fetchall())
            cursor.execute(sql)
            cursor.execute(f'INSERT INTO "{temporary}" ({names}) SELECT {names} FROM whatsapp_phone_links')
            before = cursor.execute('SELECT COUNT(*) FROM whatsapp_phone_links').fetchone()[0]
            after = cursor.execute(f'SELECT COUNT(*) FROM "{temporary}"').fetchone()[0]
            mismatch = cursor.execute(f'SELECT {names} FROM whatsapp_phone_links EXCEPT SELECT {names} FROM "{temporary}"').fetchone()
            if before != after or mismatch is not None:
                raise RuntimeError('WhatsApp link migration did not preserve every existing row.')
            cursor.execute('DROP TABLE whatsapp_phone_links')
            cursor.execute(f'ALTER TABLE "{temporary}" RENAME TO whatsapp_phone_links')
            for _, name, ddl in objects:
                if name not in global_indexes:
                    cursor.execute(ddl)
            if sequence:
                cursor.execute("UPDATE sqlite_sequence SET seq=MAX(seq,?) WHERE name='whatsapp_phone_links'", (sequence[0],))
            if set(cursor.execute('PRAGMA foreign_key_check').fetchall()) - previous_violations:
                raise RuntimeError('WhatsApp link migration changed existing foreign-key relationships.')
        else:
            for name in global_indexes:
                cursor.execute('DROP INDEX "' + name.replace('"', '""') + '"')
        scoped = False
        for row in cursor.execute('PRAGMA index_list("whatsapp_phone_links")').fetchall():
            columns = [item[2] for item in cursor.execute('PRAGMA index_info("' + row[1].replace('"', '""') + '")')]
            if row[2] and columns == ['phone_jid']:
                raise RuntimeError('Could not safely remove the old global WhatsApp JID constraint.')
            scoped |= bool(row[2] and columns == ['board_id', 'phone_jid'])
        if not scoped:
            cursor.execute('CREATE UNIQUE INDEX IF NOT EXISTS uq_whatsapp_phone_links_board_jid ON whatsapp_phone_links (board_id,phone_jid)')
        raw.commit()
    except Exception:
        raw.rollback()
        raise
    finally:
        cursor.execute(f'PRAGMA foreign_keys={int(bool(foreign_keys))}')
        cursor.close()
        raw.close()
