"""Typed NBT read/write. Values are (kind, payload) pairs so round-tripping
keeps byte/short/int/long/float/double distinct -- Minecraft cares.

kinds: b s i l f d ba str list comp ia la
  list payload is (element_tag_id, [values])
  comp payload is an ordered dict {name: value}
"""
import gzip, struct, io

TAG_END, TAG_BYTE, TAG_SHORT, TAG_INT, TAG_LONG = 0, 1, 2, 3, 4
TAG_FLOAT, TAG_DOUBLE, TAG_BA, TAG_STR = 5, 6, 7, 8
TAG_LIST, TAG_COMP, TAG_IA, TAG_LA = 9, 10, 11, 12

KIND = {TAG_BYTE: 'b', TAG_SHORT: 's', TAG_INT: 'i', TAG_LONG: 'l',
        TAG_FLOAT: 'f', TAG_DOUBLE: 'd', TAG_BA: 'ba', TAG_STR: 'str',
        TAG_LIST: 'list', TAG_COMP: 'comp', TAG_IA: 'ia', TAG_LA: 'la'}
TAG = {v: k for k, v in KIND.items()}


class _Reader:
    def __init__(self, buf):
        self.b = buf
        self.i = 0

    def _u(self, fmt, n):
        v = struct.unpack_from(fmt, self.b, self.i)[0]
        self.i += n
        return v

    def string(self):
        n = self._u('>H', 2)
        s = self.b[self.i:self.i + n].decode('utf-8', 'replace')
        self.i += n
        return s

    def payload(self, t):
        if t == TAG_BYTE:   return ('b', self._u('>b', 1))
        if t == TAG_SHORT:  return ('s', self._u('>h', 2))
        if t == TAG_INT:    return ('i', self._u('>i', 4))
        if t == TAG_LONG:   return ('l', self._u('>q', 8))
        if t == TAG_FLOAT:  return ('f', self._u('>f', 4))
        if t == TAG_DOUBLE: return ('d', self._u('>d', 8))
        if t == TAG_STR:    return ('str', self.string())
        if t == TAG_BA:
            n = self._u('>i', 4)
            v = list(struct.unpack_from('>%db' % n, self.b, self.i)); self.i += n
            return ('ba', v)
        if t == TAG_IA:
            n = self._u('>i', 4)
            v = list(struct.unpack_from('>%di' % n, self.b, self.i)); self.i += 4 * n
            return ('ia', v)
        if t == TAG_LA:
            n = self._u('>i', 4)
            v = list(struct.unpack_from('>%dq' % n, self.b, self.i)); self.i += 8 * n
            return ('la', v)
        if t == TAG_LIST:
            it = self._u('>b', 1); n = self._u('>i', 4)
            return ('list', (it, [self.payload(it) for _ in range(n)]))
        if t == TAG_COMP:
            d = {}
            while True:
                tt = self._u('>b', 1)
                if tt == TAG_END:
                    return ('comp', d)
                key = self.string()          # read name before payload: RHS evals first
                d[key] = self.payload(tt)
        raise ValueError('unknown tag id %d at offset %d' % (t, self.i))


def _write_string(out, s):
    e = s.encode('utf-8')
    out.write(struct.pack('>H', len(e)))
    out.write(e)


def _write_payload(out, val):
    k, p = val
    if k == 'b':   out.write(struct.pack('>b', p))
    elif k == 's': out.write(struct.pack('>h', p))
    elif k == 'i': out.write(struct.pack('>i', p))
    elif k == 'l': out.write(struct.pack('>q', p))
    elif k == 'f': out.write(struct.pack('>f', p))
    elif k == 'd': out.write(struct.pack('>d', p))
    elif k == 'str': _write_string(out, p)
    elif k == 'ba':
        out.write(struct.pack('>i', len(p)))
        if p: out.write(struct.pack('>%db' % len(p), *p))
    elif k == 'ia':
        out.write(struct.pack('>i', len(p)))
        if p: out.write(struct.pack('>%di' % len(p), *p))
    elif k == 'la':
        out.write(struct.pack('>i', len(p)))
        if p: out.write(struct.pack('>%dq' % len(p), *p))
    elif k == 'list':
        it, items = p
        if not items:
            it = TAG_END
        out.write(struct.pack('>b', it))
        out.write(struct.pack('>i', len(items)))
        for x in items:
            _write_payload(out, x)
    elif k == 'comp':
        for name, v in p.items():
            out.write(struct.pack('>b', TAG[v[0]]))
            _write_string(out, name)
            _write_payload(out, v)
        out.write(struct.pack('>b', TAG_END))
    else:
        raise ValueError('unknown kind %r' % k)


def load(path):
    """-> (root_name, value). Handles gzipped and raw NBT."""
    raw = open(path, 'rb').read()
    if raw[:2] == b'\x1f\x8b':
        raw = gzip.decompress(raw)
    r = _Reader(raw)
    t = r._u('>b', 1)
    name = r.string()
    return name, r.payload(t)


def save(path, name, root, compress=True):
    out = io.BytesIO()
    out.write(struct.pack('>b', TAG[root[0]]))
    _write_string(out, name)
    _write_payload(out, root)
    data = out.getvalue()
    with open(path, 'wb') as f:
        f.write(gzip.compress(data) if compress else data)


# --- small helpers for building values -----------------------------------
def comp(d):        return ('comp', d)
def i32(v):         return ('i', int(v))
def string(v):      return ('str', v)
def int_list(vals): return ('list', (TAG_INT, [('i', int(v)) for v in vals]))
def dbl_list(vals): return ('list', (TAG_DOUBLE, [('d', float(v)) for v in vals]))
def comp_list(vals): return ('list', (TAG_COMP, [('comp', v) for v in vals]))
