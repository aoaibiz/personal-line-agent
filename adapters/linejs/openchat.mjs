/** Bounded read-only Square events. Sync and page cursors are distinct. */
export function messageOptions(target, syncToken, continuationToken) {
  const options={squareChatMid:target,limit:100,direction:'BACKWARD'};
  for(const [key,value] of Object.entries({syncToken,continuationToken})) {
    if(value!==undefined && value!==null) {
      if(typeof value!=='string'||value.length>2048) throw Error('input_invalid');
      if(value) options[key]=value;
    }
  }
  return options;
}
export function messagePage(page, target) {
  if(!page||!Array.isArray(page.events)||page.events.length>100) throw Error('unexpected_schema');
  for(const key of ['syncToken','continuationToken']) if(page[key]!=null&&(typeof page[key]!=='string'||page[key].length>2048)) throw Error('unexpected_schema');
  const messages=[];
  for(const event of page.events) {
    if(!event||typeof event!=='object') throw Error('unexpected_schema');
    const name=event.type==='RECEIVE_MESSAGE'?'receiveMessage':event.type==='SEND_MESSAGE'?'sendMessage':null;
    if(!name) continue;
    const message=event.payload?.[name]?.squareMessage?.message;
    if(!message||typeof message!=='object'||message.to!==target||!/^\d{1,30}$/.test(String(message.id))) throw Error('unexpected_schema');
    messages.push(message);
  }
  return {messages,syncToken:page.syncToken,continuationToken:page.continuationToken,eventCount:page.events.length};
}
