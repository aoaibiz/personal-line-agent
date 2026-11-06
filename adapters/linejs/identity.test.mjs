import {test} from 'node:test';import assert from 'node:assert/strict';import {createHash} from 'node:crypto';import {ownsPublicKey} from './identity.mjs';
const bytes=Buffer.alloc(32,255),text='a'.repeat(32);
const proof=data=>[{key_id:7,sha256:createHash('sha256').update(data).digest('hex')}];
test('raw Thrift binary and UTF8 variants match only owned key',()=>{
 assert.equal(ownsPublicKey([{2:7,4:bytes}],proof(bytes)),true);
 assert.equal(ownsPublicKey([{2:7,4:text}],proof(text)),true);
 assert.equal(ownsPublicKey([{2:8,4:bytes}],proof(bytes)),false);
 assert.equal(ownsPublicKey([{2:7,4:bytes}],proof(text)),false);
 assert.equal(ownsPublicKey([{2:7,4:bytes.toString('base64')}],proof(bytes)),false);
 assert.equal(ownsPublicKey([{keyId:7,keyData:bytes}],proof(bytes)),false);
 assert.equal(ownsPublicKey(null,proof(bytes)),false);
});
import {sharedMessageCount} from './identity.mjs';
test('group mapping proof counts distinct IDs, not duplicate rows or names',()=>{
 assert.equal(sharedMessageCount([{id:'1'},{id:'1'}],['1','2']),1);
 assert.equal(sharedMessageCount([{id:'1'},{id:'2'}],['1','2']),2);
 assert.equal(sharedMessageCount([{id:'3'},{id:'4'}],['1','2']),0);
});
