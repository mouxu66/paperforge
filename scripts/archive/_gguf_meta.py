import struct, sys

p = 'D:/llama-b9878-bin-win-cuda-13.3-x64/Qwen3.5-9B-Q4_K_M.gguf'
with open(p, 'rb') as f:
    assert f.read(4) == b'GGUF'
    ver, n_tensors, n_kv = struct.unpack('<IQQ', f.read(20))
    # ---- tensor infos (v3: name len is uint32) ----
    for _ in range(n_tensors):
        n = struct.unpack('<I', f.read(4))[0]
        name = f.read(n + 1).decode('utf-8', 'ignore')
        nd = struct.unpack('<I', f.read(4))[0]
        dims = struct.unpack('<%dI' % nd, f.read(4 * nd)) if nd else ()
        struct.unpack('<I', f.read(4))  # type
    # ---- kv ----
    rows = []
    for _ in range(n_kv):
        klen = struct.unpack('<Q', f.read(8))[0]
        key = f.read(klen).decode('utf-8', 'ignore')
        vkind = struct.unpack('<I', f.read(4))[0]
        if vkind == 8:
            vlen = struct.unpack('<Q', f.read(8))[0]
            val = f.read(vlen).decode('utf-8', 'ignore')
        elif vkind == 0:
            val = struct.unpack('<B', f.read(1))[0]
        elif vkind == 2:
            val = struct.unpack('<H', f.read(2))[0]
        elif vkind == 3:
            val = struct.unpack('<h', f.read(2))[0]
        elif vkind == 4:
            val = struct.unpack('<I', f.read(4))[0]
        elif vkind == 5:
            val = struct.unpack('<i', f.read(4))[0]
        elif vkind == 6:
            val = struct.unpack('<f', f.read(4))[0]
        elif vkind == 7:
            val = struct.unpack('<?', f.read(1))[0]
        elif vkind == 9:
            st = struct.unpack('<I', f.read(4))[0]
            alen = struct.unpack('<Q', f.read(8))[0]
            val = f.read(alen).decode('utf-8', 'ignore') if st == 8 else f.read(alen)
        else:
            val = '(unk vkind %d)' % vkind
        rows.append((key, val))

print('GGUF v%d | n_kv=%d' % (ver, n_kv))
for k, v in rows:
    kl = k.lower()
    if any(t in kl for t in ('mtp', 'draft', 'multi', 'predict', 'architecture',
                             'block_count', 'context_length', 'vocab', 'chat_template', 'rope')):
        print('  %s = %s' % (k, str(v)[:90]))

mtp = [k for k, _ in rows if 'mtp' in k.lower()]
draft = [k for k, _ in rows if 'draft' in k.lower()]
print('--- MTP keys:', mtp)
print('--- draft keys:', draft)
print('--- CONCLUSION: model has embedded MTP/draft head?' , bool(mtp or draft))
