import {createHash} from 'node:crypto';
// getE2EEPublicKeys uses parse=false in pinned LINEJS: fields 2 and 4.
// Its Thrift reader emits bytes as Buffer, or UTF-8 string when lossless.
export function ownsPublicKey(keys, proof) {
  if(!Array.isArray(keys)||keys.length>100||!Array.isArray(proof)||proof.length<1||proof.length>100) return false;
  return keys.some(k=>{
    if(!k||typeof k!=='object') return false;
    const keyId=k[2];const data=k[4];
    if(!Number.isSafeInteger(keyId)||!(typeof data==='string'||data instanceof Uint8Array)) return false;
    const raw=typeof data==='string'?Buffer.from(data,'utf8'):Buffer.from(data);
    return raw.length===32&&proof.some(p=>p&&p.key_id===keyId&&typeof p.sha256==='string'&&/^[a-f0-9]{64}$/.test(p.sha256)&&p.sha256===createHash('sha256').update(raw).digest('hex'));
  });
}

export function sharedMessageCount(messages, requested) {
  if(!Array.isArray(messages)||messages.length>100||!Array.isArray(requested)) return 0;
  const expected=new Set(requested);
  return new Set(messages.filter(m=>m&&typeof m.id==='string'&&expected.has(m.id)).map(m=>m.id)).size;
}
