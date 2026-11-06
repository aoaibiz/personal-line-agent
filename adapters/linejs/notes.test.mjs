import test from 'node:test';
import assert from 'node:assert/strict';
import {noteUpdateBody} from './notes.mjs';
test('text replacement preserves attachments and styles without mutating snapshot',()=>{
 const original={text:'old',media:[{objectId:'fixture',type:'PHOTO'}],stickers:[{id:'1'}],locations:[{latitude:1}],contentsStyle:{textStyle:{textSizeMode:'AUTO'}},textMeta:[{start:0}],sticonMetas:[{start:0}]};
 const body=noteUpdateBody('c'+'1'.repeat(32),'123',original,'replacement');
 assert.deepEqual(body.contents.media,original.media);
 assert.deepEqual(body.contents.stickers,original.stickers);
 assert.deepEqual(body.contents.locations,original.locations);
 assert.deepEqual(body.contents.contentsStyle,original.contentsStyle);
 assert.equal(body.contents.text,'replacement');assert.equal(original.text,'old');
 assert.deepEqual(body.contents.textMeta,[]);assert.deepEqual(body.contents.sticonMetas,[]);
 assert.deepEqual(body.postInfo.readPermission,{homeID:'c'+'1'.repeat(32)});
});
