"""Readable instrument labels; keep stable symbols in data and links."""

def stock_label(row, market=None):
    symbol = str(row.get('symbol') or '')
    name = str(row.get('name') or '').strip()
    if (market or row.get('market')) == 'KR':
        return name or symbol
    return symbol + (' ' + name if name and name != symbol else '')
