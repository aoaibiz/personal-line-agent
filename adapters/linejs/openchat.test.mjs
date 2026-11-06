import {test} from 'node:test';
import assert from 'node:assert/strict';
import {messageOptions,messagePage} from './openchat.mjs';
const target='m'+'1'.repeat(32);
test('preserves separate sync and page cursor for a backward history request',()=>{
 const first=messagePage({events:[],syncToken:'sync1',continuationToken:'page2'},target);
 const next=messageOptions(target,first.syncToken,first.continuationToken);
 assert.deepEqual(next,{squareChatMid:target,limit:100,direction:'BACKWARD',syncToken:'sync1',continuationToken:'page2'});
 const second=messagePage({events:[{type:'RECEIVE_MESSAGE',payload:{receiveMessage:{squareMessage:{message:{id:'2',to:target,text:'synthetic'}}}}}],syncToken:'sync2'},target);
 assert.equal(second.messages.length,1);
 assert.equal(messageOptions(target,second.syncToken,second.continuationToken).syncToken,'sync2');
});
test('missing or cross-chat message payload is never converted to successful empty page',()=>{
 for(const event of [{type:'RECEIVE_MESSAGE'}, {type:'SEND_MESSAGE',payload:{sendMessage:{squareMessage:{message:{id:'1',to:'m'+'2'.repeat(32)}}}}}]) assert.throws(()=>messagePage({events:[event]},target));
 assert.equal(messagePage({events:[{type:'NOTIFIED_UPDATE_SQUARE_CHAT'}]},target).messages.length,0);
});
