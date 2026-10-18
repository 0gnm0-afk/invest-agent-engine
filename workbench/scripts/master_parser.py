import ast
import re
def parse_master(data, header):
    # Read literal field widths, never execute downloaded Python examples.
    literal = re.search(r'FIELDS\s*=\s*(\[.*?\])', header, re.S)
    if not literal:
        raise ValueError('master_fields_missing')
    fields = ast.literal_eval(literal.group(1))
    if not all(isinstance(n, str) and isinstance(w, int) and w > 0 for n, w in fields):
        raise ValueError('invalid_fields')
    size = sum(w for _, w in fields)
    declared = int(re.search(r'@record\s+(\d+)', header).group(1))
    if size != declared or not data or len(data) % size:
        raise ValueError('master_record_size')
    rows = []
    for start in range(0, len(data), size):
        row = data[start:start + size]
        if row[-1:] != b'\n':
            raise ValueError('master_record_ending')
        offset, result = 0, {}
        for name, width in fields:
            result[name] = row[offset:offset + width].decode('cp949').rstrip()
            offset += width
        rows.append(result)
    return rows


